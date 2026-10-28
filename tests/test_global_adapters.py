import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from uni_learn import adapters, package
from uni_learn.contracts import ContractError
from uni_learn.install import subprocess_command


class UserAdapterTests(unittest.TestCase):
    def context(self, root):
        return {"scope": "user", "project_root": None,
                "runtime_path": str(root / "uni-learn.pyz"),
                "runtime_command": subprocess_command(sys.executable, root / "uni-learn.pyz"),
                "resource_root": str(root / "resources"), "generation": None}

    def test_user_skills_are_lazy_and_do_not_bind_one_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = adapters.render("codex", self.context(root))
            self.assertEqual(len(result["commands"]), 7)
            self.assertEqual(result["capabilities"]["compaction"]["strategy"], "checkpoint_around_native")
            entry = next(item["content"] for item in result["artifacts"] if item["role"] == "persistent")
            self.assertIn("project locate --start . --json", entry)
            self.assertIn("--project PROJECT_ROOT", entry)
            self.assertIn("Only when learning is on", entry)
            self.assertNotIn("Give a complete answer", entry)
            self.assertLess(len(entry.encode("utf-8")), 1600)
            for item in result["artifacts"]:
                if item["role"] == "command":
                    self.assertIn("project locate", item["content"])
                    self.assertIn("--project PROJECT_ROOT", item["content"])
                    self.assertNotIn("prediction comes before", item["content"].lower())
                if item["role"] == "metadata":
                    self.assertIn("allow_implicit_invocation: false", item["content"])

    def test_user_scope_requires_a_declared_host_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ContractError):
                adapters.render("cursor", self.context(Path(directory).resolve()))
            value = self.context(Path(directory).resolve())
            value["scope"] = "unknown"
            with self.assertRaises(ContractError):
                adapters.render("codex", value)

    def test_generated_user_resolver_and_verbs_work_across_projects(self):
        with tempfile.TemporaryDirectory(prefix="uni global α ") as directory:
            root = Path(directory).resolve()
            package.build(root / "uni-learn.pyz")
            context = self.context(root)
            result = adapters.render("codex", context)
            command = next(item["content"] for item in result["artifacts"]
                           if item["role"] == "command" and item["command"] == "learn")
            embedded = re.findall(r"`([^`]+)`", command)
            resolver = next(text for text in embedded if " project locate " in text)
            on = next(text for text in embedded if text.startswith("learn --project"))
            env = dict(os.environ)
            for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
                env["XDG_" + kind + "_HOME"] = str(root / kind.lower())
            env.pop("PYTHONPATH", None)
            def run(text, cwd):
                argv = text if os.name == "nt" else shlex.split(text)
                return json.loads(subprocess.run(argv, cwd=cwd, env=env, text=True,
                                                 capture_output=True, check=True).stdout)
            def quote(value):
                return subprocess.list2cmdline([str(value)]) if os.name == "nt" else shlex.quote(str(value))
            projects = [root / "first person's project", root / "second α project"]
            for project in projects:
                (project / "src/nested").mkdir(parents=True)
                (project / ".git").mkdir()
                self.assertEqual(run(resolver, project / "src/nested")["project_root"], str(project))
            # The first project's retained binding stays correct after cwd changes.
            bound = on.replace("PROJECT_ROOT", quote(projects[0]))
            self.assertTrue(run(context["runtime_command"] + " " + bound, projects[1])["project_learning"])
            for project, expected in zip(projects, (True, False)):
                shown = run(context["runtime_command"] + " state show --project " + quote(project), root)
                self.assertEqual(shown["learning"], expected)
