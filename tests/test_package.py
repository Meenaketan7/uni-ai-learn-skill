import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from uni_learn import package
from tests.test_registry import NOW, model


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="uni package α ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.archive = self.root / "uni-learn.pyz"
        package.build(self.archive)
        self.project = self.root / "disposable project"
        self.project.mkdir()
        self.environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.environment.pop("PYTHONPATH", None)
        for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
            self.environment["XDG_" + kind + "_HOME"] = str(self.root / kind.lower())

    def run_cli(self, *args, archive=None, check=True):
        result = subprocess.run([sys.executable, str(archive or self.archive), *map(str, args)],
                                cwd=self.root, env=self.environment, capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout) if result.stdout.startswith("{") else result.stdout
        return result

    def test_reproducible_allowlisted_contents(self):
        content = self.archive.read_bytes()
        self.assertEqual(content, package.build_bytes())
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIn("LICENSE", archive.namelist())
            self.assertIn("core/learning/workflow.md", archive.namelist())
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))
            self.assertFalse(any(name.startswith(("tests/", "docs/", ".codex/")) or "__pycache__" in name for name in archive.namelist()))

    def test_runs_and_renders_outside_checkout_without_pythonpath(self):
        result = self.run_cli("--help")
        self.assertIn("install", result)
        out = self.run_cli("install", "--host", "gemini-cli", "--project", self.project, "--dry-run")
        self.assertEqual(len(out["commands"]), 7)
        self.assertEqual(out["status"], "partial")
        self.assertFalse((self.root / "data").exists())

    def test_install_reinstall_resources_and_removal_in_separate_processes(self):
        plan = self.run_cli("install", "--host", "codex", "--project", self.project, "--dry-run")
        result = self.run_cli("install", "--host", "codex", "--project", self.project,
                              "--apply", "--plan-digest", plan["plan_digest"])
        receipt = json.loads(Path(result["receipt"]).read_text())
        runtime = next(Path(row["path"]) for row in receipt["owned"] if row["path"].endswith(".pyz"))
        resource = self.run_cli("resource", "--path", "core/learning/workflow.md", archive=runtime)
        self.assertIn("Guided learning", resource["content"])
        again = self.run_cli("install", "--host", "codex", "--project", self.project, "--dry-run", archive=runtime)
        self.assertFalse(any(row["action"] == "create_owned" for row in again["actions"]))
        self.run_cli("install", "--host", "codex", "--project", self.project,
                     "--apply", "--plan-digest", again["plan_digest"], archive=runtime)
        # Remove using the original distribution because the installed runtime is itself owned.
        removal = self.run_cli("uninstall", "--receipt", result["receipt"], "--dry-run")
        self.run_cli("uninstall", "--receipt", result["receipt"], "--apply", "--plan-digest", removal["plan_digest"])
        self.assertFalse(runtime.exists())

    def test_resource_escape_and_missing_digest_diagnostics(self):
        result = self.run_cli("resource", "--path", "../LICENSE", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid resource path", result.stderr)
        result = self.run_cli("install", "--host", "codex", "--project", self.project, "--apply", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("plan-digest", result.stderr)

    def test_project_read_creates_no_state(self):
        result = self.run_cli("state", "show", "--project", self.project)
        self.assertFalse(result["learning"])
        self.assertFalse((self.root / "state").exists())

    def test_archived_update_notices_preserve_mode_and_use_a_runnable_command(self):
        home = self.root / "user home"
        home.mkdir()
        self.environment.update(HOME=str(home), CODEX_HOME=str(home / ".codex"))
        installed = self.run_cli("setup", "--host", "codex", "--json")
        receipt = installed["receipt"]
        record = json.loads(Path(receipt).read_text("utf-8"))
        self.assertEqual(self.run_cli("updates", "status", "--json")["status"], "disabled")
        self.run_cli("updates", "enable", "--receipt", receipt, "--json")
        cache = self.root / "cache/uni-learn/update-check.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({
            "schema_version": 1, "checked_at": int(time.time()),
            "repository": "Meenaketan7/uni-ai-learn-skill",
            "channels": {"latest": {
                "version": record["runtime_version"], "sha256": "0" * 64,
                "bytes": 1, "commit": "a" * 40,
                "published_at": "2026-10-27T00:00:00Z",
            }},
        }), encoding="utf-8")
        before = cache.read_bytes()
        shown = self.run_cli("state", "show", "--project", self.project, "--json")
        self.assertFalse(shown["learning"])
        self.assertEqual(shown["revision"], 0)
        self.assertEqual(shown["update_notice"]["command_argv"][:2], [sys.executable, str(self.archive)])
        self.assertEqual(shown["update_notice"]["receipt"], receipt)
        self.assertEqual(cache.read_bytes(), before)
        self.run_cli("updates", "disable", "--json")
        self.assertNotIn("update_notice", self.run_cli("state", "show", "--project", self.project, "--json"))

    def test_unicode_paths_survive_legacy_pipe_encoding(self):
        environment = dict(self.environment, PYTHONIOENCODING="cp1252")
        home = self.root / "user \u03b1"
        home.mkdir()
        environment.update(HOME=str(home), CODEX_HOME=str(home / ".codex"))

        def invoke(*args):
            result = subprocess.run([sys.executable, str(self.archive), *map(str, args)],
                                    cwd=self.root, env=environment, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", "replace"))
            return result.stdout.decode("cp1252")

        located = json.loads(invoke("project", "locate", "--start", self.project, "--json"))
        self.assertEqual(located["project_root"], str(self.project))
        installed = json.loads(invoke("setup", "--host", "codex", "--json"))
        self.assertEqual(installed["status"], "ready")
        self.assertTrue(any("\u03b1" in path for path in installed["instruction_paths"]))
        self.assertIn("ready (codex, user)", invoke("setup", "--host", "codex"))

    def test_one_command_user_setup_default_output_and_reversal(self):
        home = self.root / "user home"
        home.mkdir()
        codex_home = self.root / "custom codex home"
        codex_home.mkdir()
        self.environment.update(HOME=str(home), CODEX_HOME=str(codex_home))
        entry = codex_home / "AGENTS.md"
        original = b"Keep my instructions.\r\n"
        entry.write_bytes(original)
        result = self.run_cli("setup", "--host", "codex")
        self.assertIn("ready (codex, user)", result)
        self.assertTrue(entry.read_bytes().startswith(original))
        skills = home / ".agents/skills"
        self.assertEqual(len(list(skills.glob("*/SKILL.md"))), 7)
        again = self.run_cli("setup", "--host", "codex")
        self.assertIn("ready (codex, user)", again)
        self.assertEqual(entry.read_text().count("<!-- uni-learn:codex -->"), 1)
        receipts = list((self.root / "data/uni-learn/receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        removal = self.run_cli("uninstall", "--receipt", receipts[0], "--dry-run")
        self.run_cli("uninstall", "--receipt", receipts[0], "--apply", "--plan-digest", removal["plan_digest"])
        self.assertEqual(entry.read_bytes(), original)
        self.assertEqual(list(skills.glob("*/SKILL.md")), [])

    def test_documented_learning_log_and_compact_workflow_from_archive(self):
        distribution = self.archive
        installation = self.run_cli("install", "--host", "codex", "--project", self.project, "--dry-run")
        installed = self.run_cli("install", "--host", "codex", "--project", self.project,
                                 "--apply", "--plan-digest", installation["plan_digest"])
        receipt = json.loads(Path(installed["receipt"]).read_text())
        self.archive = next(Path(row["path"]) for row in receipt["owned"] if row["path"].endswith(".pyz"))
        self.run_cli("learn", "--project", self.project, "--command", "learn", "--argument", "on")
        self.run_cli("task", "start", "--project", self.project, "--session", "demo", "--task", "balance",
                     "--objective", "Explain the invariant")
        task = ["--project", self.project, "--session", "demo", "--task", "balance"]
        self.run_cli("task", "update", *task, "--attempt", "Validation must precede mutation",
                     "--prediction", "A missing target will change the source")
        for expected in (1, 2):
            result = self.run_cli("learn", *task, "--command", "learn-hint", "--domain", "coding")
            self.assertEqual(result["hint_level"], expected)
        request = self.root / "compact-request.json"
        request.write_text(json.dumps({"project": str(self.project), "session_id": "demo", "task_id": "balance"}))
        prepared = self.run_cli("compact", "prepare", "--request", request)
        self.assertFalse(prepared["native_compaction_executed"])
        resumed = self.run_cli("compact", "resume", "--project", self.project,
                               "--checkpoint", prepared["checkpoint"]["checkpoint_id"])
        self.assertTrue(resumed["safe"])
        self.assertEqual(resumed["checkpoint"]["learning"]["hint_level"], 2)
        self.run_cli("state", "set", "--project", self.project, "--logging", "on")
        event = self.root / "learning-event.json"
        event.write_text(json.dumps({"task_id": "balance", "tags": ["invariant"], "prompt": "Why validate first?",
                                     "attempt": "To preserve rejected state", "correction": "Yes", "outcome": "pass"}))
        added = self.run_cli("log", "append", "--project", self.project, "--request", event)
        self.run_cli("log", "review", "--project", self.project, "--entry", added["entry_id"],
                     "--attempt", "Preserve state on failure", "--outcome", "pass")
        history = next((self.root / "state").rglob("learning.jsonl"))
        records = [json.loads(line) for line in history.read_text().splitlines()]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["review_of"], records[0]["entry_id"])
        self.run_cli("state", "set", "--project", self.project, "--logging", "off")
        self.assertEqual(self.run_cli("recall", *task)["status"], "session")
        self.run_cli("task", "ship", *task)
        self.assertEqual(self.run_cli("learn", *task, "--command", "learn-hint")["action"], "ship")
        self.run_cli("task", "close", *task)
        self.run_cli("learn", "--project", self.project, "--command", "learn", "--argument", "off")
        self.assertFalse(self.run_cli("state", "show", "--project", self.project)["learning"])
        again = self.run_cli("install", "--host", "codex", "--project", self.project, "--dry-run")
        self.assertFalse(again["skill_conflicts"])
        self.run_cli("install", "--host", "codex", "--project", self.project,
                     "--apply", "--plan-digest", again["plan_digest"])
        edited = self.project / ".agents/skills/learn-hint/SKILL.md"
        edited.write_text("User retained edit\n")
        removal = self.run_cli("uninstall", "--receipt", installed["receipt"], "--dry-run", archive=distribution)
        outcome = self.run_cli("uninstall", "--receipt", installed["receipt"],
                               "--apply", "--plan-digest", removal["plan_digest"], archive=distribution)
        self.assertIn(str(edited), outcome["preserved"])
        self.assertEqual(edited.read_text(), "User retained edit\n")
        self.assertTrue(history.exists())
        self.assertFalse(self.archive.exists())

    def test_packaged_route_merges_user_models_and_override_precedence(self):
        override_path = self.root / "config/uni-learn/model-overrides.json"
        override_path.parent.mkdir(parents=True)
        first, second = model(1), model(2)
        for item in (first, second):
            item["bindings"]["codex"] = item["bindings"].pop("fixture")
            item["qualification"][0]["host_profile"] = "codex"
        second["bindings"]["codex"].update(provider="synthetic-glm", native_id="glm-fixture-not-real",
                                          source="synthetic provider-binding test; no model executed")
        data = {"schema_version": 1, "models": {"a": first, "b": second},
                "global": {"discover": {"model_key": "a"}},
                "projects": {"test-project": {"discover": {"model_key": "b"}}}}
        override_path.write_text(json.dumps(data))
        request_path = self.root / "route-request.json"
        request = {"task_class": "discover", "host_profile": "codex", "control": "subagent_selection",
                   "project_id": "test-project", "input_tokens": 100, "output_tokens": 10,
                   "required_tools": ["read"], "available_models": ["a", "b"],
                   "availability_checked_at": NOW, "now": NOW}

        def route():
            request_path.write_text(json.dumps(request))
            return self.run_cli("route", "--request", request_path)

        self.assertEqual(route()["selected"], "b")
        self.assertEqual(route()["precedence"], "project_override")
        self.assertEqual(route()["native_binding"], "glm-fixture-not-real")
        self.assertIsNone(route()["observed_execution"])
        request["override"] = {"model_key": "a"}
        self.assertEqual(route()["selected"], "a")
        self.assertEqual(route()["precedence"], "task_request")
        request.pop("override")
        request.pop("project_id")
        self.assertEqual(route()["selected"], "a")
        self.assertEqual(route()["precedence"], "global_override")
        request["project_id"] = "test-project"
        second["qualification"][0]["result"] = "fail"
        override_path.write_text(json.dumps(data))
        self.assertEqual(route()["action"], "keep_current_host_model")
        second["qualification"][0]["result"] = "pass"
        second["bindings"]["codex"]["availability"] = "unavailable"
        override_path.write_text(json.dumps(data))
        self.assertEqual(route()["action"], "keep_current_host_model")
        shipped = self.run_cli("resource", "--path", "registry/models.json")
        self.assertEqual(json.loads(shipped["content"])["models"], {})
