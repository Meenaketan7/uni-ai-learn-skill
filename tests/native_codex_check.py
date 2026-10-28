"""Explicit native loading check: python -m tests.native_codex_check --output FILE.

This invokes Codex's offline prompt renderer, never a model turn. User auth and
settings are not copied. Only Uni Learn findings and hashes are recorded because
the host can still discover ambient skills through the operating-system home.
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
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from uni_learn import __version__
from uni_learn.install import apply_install, plan_install
from uni_learn.package import build_bytes, read_json, read_resource


def check(budget_model=None):
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex is unavailable; native loading is unverified")
    with tempfile.TemporaryDirectory(prefix="uni native α ") as directory:
        base = Path(directory).resolve()
        project = base / "project space α"
        project.mkdir()
        application_roots = {name: base / ("xdg-" + name) / "uni-learn" for name in ("config", "data", "state", "cache")}
        planned = plan_install("codex", project, application_roots=application_roots, budget_model=budget_model)["plan"]
        apply_install("codex", project, planned["plan_digest"], application_roots=application_roots, budget_model=budget_model)
        # Review/apply the complete manual proposal only in this disposable fixture.
        for patch in planned["manual_patches"]:
            Path(patch["path"]).write_text(patch["content"], encoding="utf-8")
        native_home = base / "native"
        native_home.mkdir()
        (native_home / "config.toml").write_text(
            'model_provider = "offline"\nmodel = ' + json.dumps(budget_model or "gpt-5") + '\n'
            '[model_providers.offline]\nname = "Offline loading check"\n'
            'base_url = "http://127.0.0.1:9/v1"\nwire_api = "responses"\n',
            encoding="utf-8",
        )
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "SystemRoot"}}
        env["CODEX_HOME"] = str(native_home)
        for kind, path in application_roots.items():
            env["XDG_" + kind.upper() + "_HOME"] = str(path.parent)

        def invoke(*arguments):
            result = subprocess.run(["rtk", "proxy", executable, "-C", str(project), *arguments],
                                    env=env, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError("Native offline renderer failed: " + result.stderr[-1500:])
            return result.stdout

        version = invoke("--version").strip()
        # skills/list is the native discovery surface. The prompt renderer alone
        # does not expand explicit-only skills supplied as plain prompt text.
        with tempfile.TemporaryFile(mode="w+") as errors:
            process = subprocess.Popen(["rtk", "proxy", executable, "-C", str(project), "app-server"],
                                       env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=errors, bufsize=0)
            try:
                requests = [
                    {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "uni-learn-offline-check", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}}},
                    {"method": "initialized"},
                    {"id": 2, "method": "skills/list", "params": {"cwds": [str(project)], "forceReload": True}},
                ]
                for request in requests:
                    process.stdin.write((json.dumps(request) + "\n").encode())
                process.stdin.flush()
                native_result = None
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
                                native_result = message["result"]
                                break
                        if native_result is not None:
                            break
                if native_result is None:
                    raise RuntimeError("Native skills/list did not respond within 20 seconds")
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
        expected = set(read_json("core/learning/commands.json")["commands"])
        discovered = []
        native_errors = []
        for group in native_result.get("data", []):
            native_errors.extend(error for error in group.get("errors", [])
                                 if str(project) in json.dumps(error))
            for skill in group.get("skills", []):
                if skill.get("name") in expected and str(project) in skill.get("path", ""):
                    discovered.append({"name": skill["name"], "enabled": skill.get("enabled"),
                                       "available_metadata_fields": sorted(skill)})
        if {item["name"] for item in discovered} != expected:
            raise AssertionError("Native discovery did not find all seven installed skills: " + json.dumps(discovered))
        if not all(item["enabled"] for item in discovered):
            raise AssertionError("Native discovery returned a disabled Uni Learn skill")
        if native_errors:
            raise AssertionError("Native discovery rejected installed skill files")
        prompts = {
            "ordinary_off": "Summarize the files in this empty project without teaching.",
            "explicit_hint": "$learn-hint My attempt is that the total remains constant. Give one hint.",
        }
        observations = {}
        descriptions = read_json("core/learning/commands.json")["commands"]
        for name, prompt in prompts.items():
            raw = invoke("debug", "prompt-input", prompt)
            inputs = json.loads(raw)
            if not isinstance(inputs, list) or not inputs:
                raise AssertionError("Native renderer returned no prompt inputs")
            text = json.dumps(inputs, ensure_ascii=False)
            observations[name] = {
                "input_items": len(inputs),
                "prompt_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "persistent_entry_loaded": "Shared project mode and isolated task progress" in text,
                "command_descriptions_visible": [key for key, value in descriptions.items()
                                                 if value["description"] in text],
                "selected_dispatcher_loaded": "# Dispatch learn-hint" in text,
                "ordinary_bootstrap_body_loaded": "# Ordinary session bootstrap when project learning is on" in text,
                "learning_workflow_body_loaded": "# Guided learning" in text,
            }
        ordinary = observations["ordinary_off"]
        if not ordinary["persistent_entry_loaded"]:
            raise AssertionError("Native host did not load the proposed persistent entry")
        if any(ordinary[key] for key in ("selected_dispatcher_loaded", "ordinary_bootstrap_body_loaded", "learning_workflow_body_loaded")):
            raise AssertionError("Teaching/dispatcher body leaked into ordinary off-mode prompt")
        return {
            "case": "H1-native-offline-loading", "at": datetime.now(timezone.utc).isoformat(),
            "os": platform.platform(), "python": platform.python_version(), "host_version": version,
            "runtime_version": __version__, "runtime_sha256": hashlib.sha256(build_bytes()).hexdigest(),
            "profile": "codex", "observations": observations,
            "native_skill_discovery": sorted(discovered, key=lambda item: item["name"]),
            "persistent_entry_budget": next(action["budget"] for action in planned["actions"] if action["role"] == "persistent"),
            "command": "rtk proxy codex -C DISPOSABLE_PROJECT debug prompt-input PROMPT",
            "scope": "Native prompt rendering only; disposable fixture; credentials excluded; dummy loopback provider.",
            "entry_installation": "reviewed_owned_file" if not planned["manual_patches"] else "manual_proposal_applied_in_fixture",
            "limitations": ["No model inference or H2-H8 instruction-following result.",
                            "Ambient host skills may be discovered despite isolated CODEX_HOME; their content is not recorded.",
                            "Budget evidence applies only to the recorded model/tokenizer and generated entry, not other host/model bindings.",
                            "Explicit skill expansion is reported as observed, not inferred from command metadata."],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-model", help="Use an installed supported tokenizer, matching the offline model ID")
    arguments = parser.parse_args()
    report = check(arguments.budget_model)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
