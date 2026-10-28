"""Explicit real-counter acceptance: python -m tests.budget_check --model MODEL --output FILE.

Uses an already installed tokenizer only. Counts complete generated entries,
tests owned installation and reversal, and never invokes a model or real host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from uni_learn.budget import counter_for_model
from uni_learn.install import apply_install, apply_uninstall, plan_install, plan_uninstall
from uni_learn.package import build, build_bytes, read_json


def check(model):
    _, source = counter_for_model(model)
    observations = []
    with tempfile.TemporaryDirectory(prefix="uni budgets α ") as directory:
        base = Path(directory).resolve()
        for profile in read_json("registry/adapters.json")["profiles"]:
            project = base / profile / "project space α"
            project.mkdir(parents=True)
            roots = {name: base / profile / name for name in ("config", "data", "state", "cache")}
            kwargs = {"application_roots": roots, "generation": "devin-desktop" if profile == "windsurf" else None,
                      "budget_model": model}
            plan = plan_install(profile, project, **kwargs)["plan"]
            entry = next(row for row in plan["actions"] if row["role"] == "persistent")
            if plan["status"] != "ready" or not entry["budget"]["under_1000"]:
                raise AssertionError("Generated entry failed budget for " + profile)
            installed = apply_install(profile, project, plan["plan_digest"], **kwargs)
            path = Path(entry["path"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["desired_sha256"]:
                raise AssertionError("Verified entry was not installed exactly")
            if profile == "cursor" and not path.read_text().startswith("---\n"):
                raise AssertionError("Native Cursor frontmatter was displaced")
            before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob("*") if p.is_file()}
            repeated = plan_install(profile, project, **kwargs)["plan"]
            apply_install(profile, project, repeated["plan_digest"], **kwargs)
            after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob("*") if p.is_file()}
            if before != after:
                raise AssertionError("Verified installation is not idempotent")
            removal = plan_uninstall(installed["receipt"], application_roots=roots)
            apply_uninstall(installed["receipt"], removal["plan_digest"], application_roots=roots)
            if path.exists():
                raise AssertionError("Owned entry survived unchanged-file removal")
            observations.append({"profile": profile, "budget": entry["budget"],
                                 "apply_reinstall_remove": "pass"})
        archive = base / "standalone.pyz"
        build(archive)
        project = base / "packaged CLI project α"
        project.mkdir()
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
            env["XDG_" + kind + "_HOME"] = str(base / ("cli-" + kind.lower()))
        def cli(*args):
            completed = subprocess.run(["rtk", "proxy", sys.executable, str(archive), *args, "--json"],
                                       cwd=project, env=env, capture_output=True, text=True, timeout=30, check=True)
            return json.loads(completed.stdout)
        args = ["install", "--host", "codex", "--project", str(project), "--budget-model", model]
        planned = cli(*args, "--dry-run")
        if planned["status"] != "ready":
            raise AssertionError("Packaged CLI did not use the explicit tokenizer")
        installed = cli(*args, "--apply", "--plan-digest", planned["plan_digest"])
        if not (project / "AGENTS.md").is_file():
            raise AssertionError("Packaged CLI did not install the verified entry")
        removal = cli("uninstall", "--receipt", installed["receipt"], "--dry-run")
        cli("uninstall", "--receipt", installed["receipt"], "--apply", "--plan-digest", removal["plan_digest"])
        if (project / "AGENTS.md").exists():
            raise AssertionError("Packaged CLI did not reverse the verified entry")
    return {"case": "R05-real-counter-generated-entries", "at": datetime.now(timezone.utc).isoformat(),
            "os": platform.platform(), "python": platform.python_version(), "counter": source,
            "runtime_sha256": hashlib.sha256(build_bytes()).hexdigest(), "observations": observations,
            "outside_checkout_packaged_budget_cli": "pass",
            "scope": "Complete generated files including inline core and managed markers; no always-expanded imports in generated entries.",
            "limitations": ["Counts apply only to the selected model/tokenizer; other host tokenizers are unverified.",
                            "Existing user instruction files/imports remain manual and are not certified by these empty-project cases.",
                            "No native inference, teaching-quality, platform, or savings claim follows from these counts."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check(args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"profiles": len(report["observations"]), "counter": report["counter"],
                      "tokens": {row["profile"]: row["budget"]["tokens"] for row in report["observations"]},
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
