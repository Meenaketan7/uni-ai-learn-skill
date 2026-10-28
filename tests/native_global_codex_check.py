"""Check one-command Codex user setup through native offline loading.

Run ``python -m tests.native_global_codex_check --output FILE``. This builds a
temporary zipapp, installs it in an isolated home, and never sends a model turn.
Only Uni Learn observations and hashes are written to the output file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from uni_learn import __version__
from uni_learn.budget import counter_for_model, measure
from uni_learn.package import build_bytes, read_json


def _run(command, *, env, cwd, timeout=30):
    result = subprocess.run(command, env=env, cwd=cwd, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Offline command failed: " + result.stderr[-1200:])
    return result.stdout


def _native_skills(executable, projects, env):
    with tempfile.TemporaryFile(mode="w+") as errors:
        process = subprocess.Popen(
            ["rtk", "proxy", executable, "-C", str(projects[0]), "app-server"],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
            bufsize=0,
        )
        try:
            requests = [
                {"id": 1, "method": "initialize", "params": {
                    "clientInfo": {"name": "uni-learn-global-offline-check", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True}}},
                {"method": "initialized"},
                {"id": 2, "method": "skills/list", "params": {
                    "cwds": [str(project) for project in projects], "forceReload": True}},
            ]
            for request in requests:
                process.stdin.write((json.dumps(request) + "\n").encode())
            process.stdin.flush()
            pending = b""
            deadline = time.monotonic() + 20
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while time.monotonic() < deadline:
                    if not selector.select(max(0, deadline - time.monotonic())):
                        break
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        message = json.loads(line)
                        if message.get("id") == 2:
                            if "error" in message:
                                raise RuntimeError("Native skills/list failed: " + json.dumps(message["error"]))
                            return message["result"]
            raise RuntimeError("Native skills/list did not respond within 20 seconds")
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def check(budget_model=None):
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex is unavailable; native global loading is unverified")
    archive_bytes = build_bytes()
    with tempfile.TemporaryDirectory(prefix="uni global α ") as directory:
        base = Path(directory).resolve()
        user_home = base / "isolated home"
        user_home.mkdir()
        codex_home = user_home / ".codex"
        codex_home.mkdir()
        global_entry = codex_home / "AGENTS.override.md"
        sentinel = "Existing global instruction sentinel α.\n"
        global_entry.write_text(sentinel, encoding="utf-8")
        (codex_home / "config.toml").write_text(
            'model_provider = "offline"\nmodel = "gpt-5"\n'
            '[model_providers.offline]\nname = "Offline loading check"\n'
            'base_url = "http://127.0.0.1:9/v1"\nwire_api = "responses"\n',
            encoding="utf-8",
        )
        archive = base / "uni-learn.pyz"
        archive.write_bytes(archive_bytes)
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "SystemRoot"}}
        env.update(HOME=str(user_home), CODEX_HOME=str(codex_home))
        for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
            location = base / ("xdg-" + kind.lower())
            location.mkdir()
            env["XDG_" + kind + "_HOME"] = str(location)
        projects = [base / "project one α", base / "project two β"]
        for project in projects:
            project.mkdir()
            _run(["rtk", "proxy", "git", "init", "-q", str(project)],
                 env=env, cwd=base)
            (project / "nested").mkdir()

        raw_setup = _run([sys.executable, str(archive), "setup", "--host", "codex",
                          "--json"], env=env, cwd=base)
        setup = json.loads(raw_setup)
        if setup.get("status") not in {"ready", "installed"} or setup.get("manual_pending"):
            raise AssertionError("One-command global setup did not finish automatically: " + raw_setup[-1200:])
        entry_text = global_entry.read_text("utf-8")
        start_marker = "<!-- uni-learn:codex -->"
        end_marker = "<!-- /uni-learn:codex -->"
        if not entry_text.startswith(sentinel) or entry_text.count(start_marker) != 1:
            raise AssertionError("Global override was not merged while preserving existing instructions")
        if not entry_text.rstrip().endswith(end_marker):
            raise AssertionError("Global override managed block is incomplete")
        managed_text = entry_text[entry_text.index(start_marker):]
        counter, counter_source = counter_for_model(budget_model) if budget_model else (None, None)
        entry_budget = measure(entry_text, exact_counter=counter)
        managed_budget = measure(managed_text, exact_counter=counter)
        if counter_source:
            entry_budget["counter"] = counter_source
            managed_budget["counter"] = counter_source
            if not entry_budget["under_1000"]:
                raise AssertionError("Complete global instruction file exceeds 1000 tokens")
        receipt_path = Path(setup["receipt"])
        if not receipt_path.is_relative_to(base):
            raise AssertionError("Global setup escaped the isolated home")
        receipt = json.loads(receipt_path.read_text("utf-8"))
        owned_paths = {Path(item["path"]) for item in receipt["owned"]}
        if not all(path.is_file() and path.is_relative_to(base) for path in owned_paths):
            raise AssertionError("Global setup recorded a missing or external owned file")
        owned_bytes = sum(path.stat().st_size for path in owned_paths)
        expected = set(read_json("core/learning/commands.json")["commands"])
        installed_skills = user_home / ".agents" / "skills"
        for name in expected:
            if not (installed_skills / name / "SKILL.md").is_file():
                raise AssertionError("Global Codex skill missing: " + name)
        dispatcher = (installed_skills / "learn" / "SKILL.md").read_text("utf-8")
        if "project locate" not in dispatcher:
            raise AssertionError("Global dispatcher does not resolve the active project")
        for project in projects:
            if (project / "AGENTS.md").exists() or (project / ".agents").exists():
                raise AssertionError("Global setup unexpectedly wrote a project")
            if str(project) in dispatcher:
                raise AssertionError("Global dispatcher was bound to one project")

        located = []
        for project in projects:
            value = json.loads(_run([sys.executable, str(archive), "project", "locate",
                                     "--start", str(project / "nested"), "--json"],
                                    env=env, cwd=base))
            if value.get("project_root") != str(project) or value.get("source") != "git":
                raise AssertionError("Project resolver did not locate the nested Git root")
            located.append(value)
        if located[0]["project_id"] == located[1]["project_id"]:
            raise AssertionError("Different Git projects received the same identity")

        _run([sys.executable, str(archive), "state", "set", "--project", located[0]["project_root"],
              "--learning", "on", "--json"], env=env, cwd=base)
        states = [json.loads(_run([sys.executable, str(archive), "state", "show", "--project",
                                  value["project_root"], "--json"], env=env, cwd=base))
                  for value in located]
        if states[0].get("learning") is not True or states[1].get("learning") is not False:
            raise AssertionError("Global installation did not preserve per-project mode isolation")

        native_result = _native_skills(executable, projects, env)
        discoveries = []
        for project in projects:
            groups = [group for group in native_result.get("data", [])
                      if group.get("cwd") == str(project)]
            if len(groups) != 1:
                raise AssertionError("Native skills/list omitted a project group")
            group = groups[0]
            if group.get("errors"):
                raise AssertionError("Native host rejected a global skill")
            found = [item for item in group.get("skills", [])
                     if item.get("name") in expected and
                     str(installed_skills) in item.get("path", "")]
            if {item["name"] for item in found} != expected or not all(item.get("enabled") for item in found):
                raise AssertionError("Native host did not enable all seven global skills")
            discoveries.append({"project_id": next(value["project_id"] for value in located
                                                    if value["project_root"] == str(project)),
                                "enabled_names": sorted(item["name"] for item in found)})

        prompts = []
        for project in projects:
            raw = _run(["rtk", "proxy", executable, "-C", str(project), "debug", "prompt-input",
                        "Summarize this project without teaching."], env=env, cwd=base)
            rendered = json.loads(raw)
            if not isinstance(rendered, list) or not rendered:
                raise AssertionError("Native renderer returned no prompt inputs")
            body = json.dumps(rendered, ensure_ascii=False)
            observation = {
                "project_id": next(value["project_id"] for value in located
                                   if value["project_root"] == str(project)),
                "input_items": len(rendered),
                "prompt_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "global_entry_loaded": "<!-- uni-learn:codex -->" in body,
                "existing_global_instruction_preserved": sentinel.strip() in body,
                "teaching_body_loaded": ("# Ordinary session bootstrap when project learning is on" in body
                                         or "# Guided learning" in body),
            }
            if not observation["global_entry_loaded"] or not observation["existing_global_instruction_preserved"]:
                raise AssertionError("Native prompt did not load the merged global instruction entry")
            if observation["teaching_body_loaded"]:
                raise AssertionError("Teaching body was loaded without skill invocation")
            prompts.append(observation)

        version = _run(["rtk", "proxy", executable, "--version"], env=env, cwd=base).strip()
        return {
            "case": "Codex-global-offline-loading", "at": datetime.now(timezone.utc).isoformat(),
            "os": platform.platform(), "python": platform.python_version(),
            "host_version": version, "runtime_version": __version__,
            "runtime_sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "setup": {"status": setup.get("status"), "scope": "user",
                      "manual_pending": setup.get("manual_pending", []),
                      "existing_override_preserved": True,
                      "global_entry_budget": entry_budget,
                      "managed_block_budget": managed_budget,
                      "owned_file_count": len(owned_paths), "owned_total_bytes": owned_bytes,
                      "budget_scope": "Exact for this generated file and recorded model only; other user instructions and native models vary."},
            "projects": [{"project_id": value["project_id"], "source": value["source"],
                          "mode": "on" if state.get("learning") else "off"}
                         for value, state in zip(located, states)],
            "native_skill_discovery": discoveries, "native_prompt_loading": prompts,
            "scope": "Temporary isolated home and two Git projects; native offline discovery and prompt rendering only.",
            "limitations": ["No model inference or instruction-following assertion.",
                            "No operating-system global files or existing credentials were used."],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-model", help="Optional exact count using a prepared local tokenizer")
    arguments = parser.parse_args()
    report = check(arguments.budget_model)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
