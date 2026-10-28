import copy
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from uni_learn import adapters
from uni_learn.contracts import ContractError
from uni_learn.package import build, read_json, read_resource


COMMANDS = {
    "learn", "learn-hint", "learn-debug", "learn-review",
    "learn-design", "learn-recall", "learn-compact",
}


def context(profile, base=None):
    base = Path(base or Path(tempfile.gettempdir()) / "uni learn α").resolve()
    runtime = [sys.executable, str(base / "data/uni-learn.pyz")]
    return {
        "schema_version": 1, "profile": profile,
        "project_root": str(base / "project"),
        "runtime_path": str(base / "data/uni-learn.pyz"),
        "resource_root": str(base / "data/resources"),
        "runtime_command": subprocess.list2cmdline(runtime) if os.name == "nt" else shlex.join(runtime),
        "version": "0.1.0", "generation": "devin-desktop",
    }


def command_artifact(result, ident="learn-hint"):
    path = result["commands"][ident]["path"]
    return next(item for item in result["artifacts"] if item["path"] == path)


def dispatcher_artifact(result, ident="learn-hint"):
    return next((item for item in result["artifacts"]
                 if item.get("command") == ident and item["path"].endswith("/SKILL.md")),
                command_artifact(result, ident))


class ContractTests(unittest.TestCase):
    def test_crlf_templates_render_identically(self):
        profile = "cursor"
        expected = adapters.render(profile, context(profile))

        def windows_resource(name):
            return read_resource(name).replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")

        with patch.object(adapters, "read_resource", side_effect=windows_resource):
            actual = adapters.render(profile, context(profile))
        self.assertEqual(actual, expected)

    def test_all_profiles_render_without_writes(self):
        registry = read_json("registry/adapters.json")
        self.assertEqual(len(registry["profiles"]), 12)
        with tempfile.TemporaryDirectory() as directory:
            before = list(Path(directory).rglob("*"))
            for profile in registry["profiles"]:
                result = adapters.render(profile, context(profile, directory))
                self.assertEqual(set(result["commands"]), COMMANDS)
                paths = [item["path"] for item in result["artifacts"]]
                self.assertEqual(len(paths), len(set(paths)))
                for item in result["artifacts"]:
                    self.assertNotIn("..", Path(item["path"]).parts)
                    self.assertFalse(Path(item["path"]).is_absolute())
                    if item["role"] == "command":
                        self.assertNotIn("Give a complete answer only after", item["content"])
                        dispatcher = dispatcher_artifact(result, item["command"])
                        self.assertIn("state show", dispatcher["content"])
                        self.assertIn("learn --project", dispatcher["content"])
            self.assertEqual(list(Path(directory).rglob("*")), before)

    def test_every_profile_binds_project_and_loads_on_mode_lazily(self):
        for profile in read_json("registry/adapters.json")["profiles"]:
            with self.subTest(profile=profile):
                fixture = context(profile, Path(tempfile.gettempdir()) / "a worker's α")
                project = fixture["project_root"]
                project_arg = subprocess.list2cmdline([project]) if os.name == "nt" else shlex.quote(project)
                result = adapters.render(profile, fixture)
                entry = next(item["content"] for item in result["artifacts"] if item["role"] == "persistent")
                self.assertIn("state show --project " + project_arg + " --json", entry)
                self.assertIn("core/learning/session.md", entry)
                self.assertIn("If learning is on", entry)
                self.assertIn("While off", entry)
                self.assertNotIn("hint level 3", entry)
                command = dispatcher_artifact(result)["content"]
                if profile == "gemini-cli":
                    command = tomllib.loads(command)["prompt"]
                for example in re.findall(r"`([^`]+)`", command):
                    if example.startswith(fixture["runtime_command"] + " "):
                        self.assertNotIn("\n", example, (profile, example))
                self.assertNotIn("--project .", command)
                self.assertIn("state show --project " + project_arg, command)
                self.assertIn("task start --project " + project_arg, command)
                self.assertIn("task update --project " + project_arg, command)
                self.assertIn("learn --project " + project_arg, command)

    def test_renderer_requires_absolute_project_binding(self):
        value = context("codex")
        value.pop("project_root")
        with self.assertRaises(ContractError):
            adapters.render("codex", value)

    def test_rendered_commands_work_from_other_working_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            project = base / "a worker's α project"
            nested = project / "nested"
            other = base / "elsewhere"
            nested.mkdir(parents=True)
            other.mkdir()
            archive = base / "standalone.pyz"
            build(archive)
            argv = [sys.executable, str(archive)]
            runtime = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
            result = adapters.render("codex", {
                "project_root": str(project), "runtime_path": str(archive),
                "resource_root": str(base / "resources"), "runtime_command": runtime,
                "generation": None,
            })
            command = command_artifact(result)["content"]
            embedded = re.findall(r"`([^`]+)`", command)
            def selected(marker):
                return next(text for text in embedded if text.startswith(runtime + " ") and marker in text)
            def run(text, cwd):
                env = os.environ.copy()
                for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
                    env["XDG_" + kind + "_HOME"] = str(base / ("xdg-" + kind.lower()))
                args = text if os.name == "nt" else shlex.split(text)
                return json.loads(subprocess.run(args, cwd=cwd, env=env,
                                                 capture_output=True, text=True, check=True).stdout)
            show = selected("state show")
            self.assertFalse(run(show, nested)["learning"])
            project_arg = subprocess.list2cmdline([str(project)]) if os.name == "nt" else shlex.quote(str(project))
            self.assertIn("--project " + project_arg + " --json", show)
            set_on = selected("--argument on")
            self.assertTrue(run(set_on, nested)["project_learning"])
            self.assertTrue(run(show, other)["learning"])
            quote = lambda value: subprocess.list2cmdline([value]) if os.name == "nt" else shlex.quote(value)
            start = selected("task start").replace("SESSION_ID", "stable-session").replace("TEXT", quote("Explain invariant"))
            created = run(start, nested)
            task_id = created["task_id"]
            attempt = selected("task update").replace("SESSION_ID", "stable-session").replace("TASK_ID", task_id).replace("TEXT", quote("Total stays fixed"))
            run(attempt, other)
            move = selected("--command learn-hint").replace("SESSION_ID", "stable-session").replace("TASK_ID", task_id).replace("TEXT", quote("next hint"))
            dispatched = run(move, nested)
            self.assertEqual(dispatched["action"], "hint")
            self.assertEqual(dispatched["hint_level"], 1)
            self.assertEqual(dispatched["task"]["attempts"], ["Total stays fixed"])
            off = selected("--argument off") + " --session stable-session --task " + task_id
            self.assertFalse(run(off, other)["project_learning"])
            request = base / "compact.json"
            request.write_text(json.dumps({"project": str(project), "session_id": "stable-session", "task_id": task_id}))
            prepared = run(runtime + " compact prepare --request " + quote(str(request)), nested)
            self.assertEqual(prepared["checkpoint"]["mode"], "off")
            self.assertIsNone(prepared["checkpoint"]["learning"])

    def fixture(self, mutation=None):
        descriptor = {
            "schema_version": 1, "profile": "fixture", "display_name": "Fixture",
            "capabilities": {"model_control": "current_model_only"},
            "commands": {
                ident: {"invocation": "/fixture:" + ident, "path": ".fixture/" + ident + ".md"}
                for ident in COMMANDS
            },
            "files": [
                {"path": ".fixture/" + ident + ".md", "template": "fixture/template.md",
                 "mode": "command", "command": ident}
                for ident in sorted(COMMANDS)
            ],
            "warnings": [],
        }
        if mutation:
            mutation(descriptor)
        resources = {
            "fixture/adapter.json": json.dumps(descriptor).encode(),
            "fixture/template.md": b"# ${id}\n${description}\n${runtime_command}\n",
            "fixture/render.py": (
                "import json\nfrom uni_learn.adapters import render_spec\n"
                "def render(context, read_resource):\n"
                "    return render_spec(context, read_resource, "
                "json.loads(read_resource('fixture/adapter.json')))\n"
            ).encode(),
        }
        registry = {"schema_version": 1, "profiles": {
            "fixture": {"descriptor": "fixture/adapter.json", "renderer": "fixture/render.py"}
        }}

        def resource(path):
            return resources[path] if path in resources else read_resource(path)

        def load_json(path):
            return json.loads(resource(path))

        return registry, resources, resource, load_json

    def render_fixture(self, mutation=None):
        registry, _, resource, load_json = self.fixture(mutation)
        with patch.object(adapters, "read_resource", side_effect=resource), patch.object(
            adapters, "read_json", side_effect=load_json
        ):
            return adapters.render("fixture", context("fixture"), registry)

    def test_new_adapter_needs_no_core_change(self):
        before = read_resource("core/learning/workflow.md")
        result = self.render_fixture()
        self.assertEqual(set(result["commands"]), COMMANDS)
        self.assertEqual(before, read_resource("core/learning/workflow.md"))

    def test_duplicate_paths_rejected(self):
        with self.assertRaises(ContractError):
            self.render_fixture(lambda spec: spec["files"].append(copy.deepcopy(spec["files"][0])))

    def test_duplicate_invocations_rejected(self):
        def mutate(spec):
            spec["commands"]["learn"]["invocation"] = spec["commands"]["learn-hint"]["invocation"]
        with self.assertRaises(ContractError):
            self.render_fixture(mutate)

    def test_unsafe_paths_rejected_on_every_platform(self):
        for unsafe in ("../escape", "/absolute", "C:/escape", "C:relative", ".", "\\\\server\\share"):
            with self.subTest(path=unsafe), self.assertRaises(ContractError):
                self.render_fixture(lambda spec: spec["files"][0].update(path=unsafe))

    def test_alias_rejected(self):
        def mutate(spec):
            spec["commands"]["hint"] = spec["commands"].pop("learn-hint")
        with self.assertRaises(ContractError):
            self.render_fixture(mutate)

    def test_custom_renderer_output_cannot_bypass_validation(self):
        registry, resources, resource, load_json = self.fixture()
        resources["fixture/render.py"] = (
            "import json\nfrom uni_learn.adapters import render_spec\n"
            "def render(context, read_resource):\n"
            "    result = render_spec(context, read_resource, "
            "json.loads(read_resource('fixture/adapter.json')))\n"
            "    result['artifacts'][0]['path'] = '../escape'\n"
            "    return result\n"
        ).encode()
        with patch.object(adapters, "read_resource", side_effect=resource), patch.object(
            adapters, "read_json", side_effect=load_json
        ), self.assertRaises(ContractError):
            adapters.render("fixture", context("fixture"), registry)


class CodexTests(unittest.TestCase):
    def test_native_metadata(self):
        result = adapters.render("codex", context("codex"))
        self.assertEqual(result["commands"]["learn-hint"]["invocation"], "$learn-hint")
        metadata = [a for a in result["artifacts"] if a["path"].endswith("agents/openai.yaml")]
        self.assertEqual(len(metadata), 7)
        self.assertTrue(all("allow_implicit_invocation: false" in a["content"] for a in metadata))


class ClaudeCodeTests(unittest.TestCase):
    def test_explicit_skill_and_shared_entry(self):
        result = adapters.render("claude-code", context("claude-code"))
        self.assertIn("disable-model-invocation: true", command_artifact(result)["content"])
        self.assertTrue(command_artifact(result)["path"].startswith(".claude/skills/"))
        entry = next(a for a in result["artifacts"] if a["role"] == "persistent")
        self.assertEqual(entry["path"], "CLAUDE.md")
        self.assertNotIn("hint ladder", entry["content"].lower())


class CopilotVscodeTests(unittest.TestCase):
    def test_editor_entry_and_explicit_flag(self):
        result = adapters.render("copilot-vscode", context("copilot-vscode"))
        self.assertIn("disable-model-invocation: true", command_artifact(result)["content"])
        self.assertIn(".github/copilot-instructions.md", [a["path"] for a in result["artifacts"]])


class CopilotCliTests(unittest.TestCase):
    def test_cli_entry_is_separate(self):
        result = adapters.render("copilot-cli", context("copilot-cli"))
        paths = [a["path"] for a in result["artifacts"]]
        self.assertIn("AGENTS.md", paths)
        self.assertNotIn(".github/copilot-instructions.md", paths)
        self.assertEqual(result["commands"]["learn-debug"]["invocation"], "/learn-debug")


class CursorTests(unittest.TestCase):
    def test_rule_frontmatter(self):
        result = adapters.render("cursor", context("cursor"))
        entry = next(a for a in result["artifacts"] if a["role"] == "persistent")
        self.assertTrue(entry["content"].startswith("---\n"))
        self.assertIn("alwaysApply: true", entry["content"])
        self.assertIn("disable-model-invocation: true", command_artifact(result)["content"])


class OpenCodeTests(unittest.TestCase):
    def test_native_slash_commands_load_matching_skills_with_arguments(self):
        result = adapters.render("opencode", context("opencode"))
        artifacts = {item["path"]: item for item in result["artifacts"]}
        self.assertEqual(set(result["commands"]), COMMANDS)
        for ident in COMMANDS:
            with self.subTest(command=ident):
                command = result["commands"][ident]
                self.assertEqual(command["invocation"], "/" + ident)
                self.assertEqual(command["path"], ".opencode/commands/" + ident + ".md")
                wrapper = artifacts[command["path"]]["content"]
                self.assertIn("`" + ident + "` skill", wrapper)
                self.assertIn("skill tool", wrapper)
                self.assertIn("$ARGUMENTS", wrapper)
                self.assertNotIn("$$ARGUMENTS", wrapper)
                skill = artifacts[".opencode/skills/" + ident + "/SKILL.md"]["content"]
                self.assertIn("name: " + ident, skill)
                self.assertIn("state show", skill)
                self.assertNotIn("opencode/autoinvoke", skill)
        self.assertFalse(any(".opencode/plugins/" in a["path"] for a in result["artifacts"]))

    def test_setup_reinstall_and_removal_own_commands_and_skills(self):
        from uni_learn import install

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            project = base / "project"
            project.mkdir()
            roots = {kind: base / kind for kind in ("config", "data", "state", "cache")}
            for _ in range(2):
                result = install.setup("opencode", project, scope="project", application_roots=roots)
                self.assertEqual(result["status"], "ready")
            for ident in COMMANDS:
                self.assertTrue((project / ".opencode/commands" / (ident + ".md")).is_file())
                self.assertTrue((project / ".opencode/skills" / ident / "SKILL.md").is_file())
            removal = install.plan_uninstall(result["receipt"], application_roots=roots)
            install.apply_uninstall(result["receipt"], removal["plan_digest"], application_roots=roots)
            for ident in COMMANDS:
                self.assertFalse((project / ".opencode/commands" / (ident + ".md")).exists())
                self.assertFalse((project / ".opencode/skills" / ident / "SKILL.md").exists())

    def test_existing_native_skill_name_blocks_setup(self):
        from uni_learn import install

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            project = base / "project"
            foreign = project / ".opencode/skills/custom/SKILL.md"
            foreign.parent.mkdir(parents=True)
            foreign.write_text("---\nname: learn-hint\n---\nUser skill.\n")
            roots = {kind: base / kind for kind in ("config", "data", "state", "cache")}
            plan = install.plan_install("opencode", project, application_roots=roots)["plan"]
            self.assertEqual(plan["status"], "blocked")
            self.assertEqual(plan["skill_conflicts"][0]["path"], str(foreign))
            self.assertFalse((project / ".opencode/commands/learn.md").exists())


class AntigravityTests(unittest.TestCase):
    def test_dispatcher_limit_is_visible(self):
        result = adapters.render("antigravity", context("antigravity"))
        self.assertEqual(result["capabilities"]["activation"], "dispatcher")
        self.assertTrue(command_artifact(result)["path"].startswith(".agents/skills/"))
        self.assertTrue(any("instruction-following" in w for w in result["warnings"]))


class GeminiCliTests(unittest.TestCase):
    def test_toml_escaping_and_actual_custom_commands(self):
        result = adapters.render("gemini-cli", context("gemini-cli", "/tmp/a 'quoted' α"))
        for item in result["artifacts"]:
            if item["role"] == "command":
                parsed = tomllib.loads(item["content"])
                self.assertIn("state show", parsed["prompt"])
                self.assertTrue(item["path"].endswith(".toml"))
        self.assertEqual(result["commands"]["learn-hint"]["invocation"], "/learn-hint")


class KimiCodeTests(unittest.TestCase):
    def test_current_paths_and_native_flag(self):
        result = adapters.render("kimi-code", context("kimi-code"))
        artifact = command_artifact(result)
        self.assertTrue(artifact["path"].startswith(".kimi-code/skills/"))
        self.assertIn("disableModelInvocation: true", artifact["content"])
        self.assertEqual(result["commands"]["learn-hint"]["invocation"], "/skill:learn-hint")


class WindsurfTests(unittest.TestCase):
    def test_unknown_generation_stops(self):
        value = context("windsurf")
        value.pop("generation")
        with self.assertRaises(ContractError):
            adapters.render("windsurf", value)

    def test_only_confirmed_generation_rendered(self):
        result = adapters.render("windsurf", context("windsurf"))
        self.assertTrue(command_artifact(result)["path"].startswith(".devin/skills/"))
        for ident in COMMANDS:
            with self.subTest(command=ident):
                self.assertEqual(result["commands"][ident]["invocation"], "/" + ident)
        self.assertFalse(any(".windsurf/" in a["path"] for a in result["artifacts"]))


class ClineTests(unittest.TestCase):
    def test_no_guessed_mcp_configuration(self):
        result = adapters.render("cline", context("cline"))
        self.assertEqual(result["capabilities"]["mcp_configuration"], "manual_only")
        self.assertFalse(any("mcp" in a["path"].lower() for a in result["artifacts"]))
        self.assertEqual(result["capabilities"]["activation"], "dispatcher")


class AiderTests(unittest.TestCase):
    def test_explicit_file_load_fallback(self):
        result = adapters.render("aider", context("aider"))
        self.assertEqual(result["commands"]["learn-hint"]["invocation"],
                         "/read .aider/uni-learn/learn-hint.md")
        self.assertEqual(result["capabilities"]["activation"], "manual")
        self.assertFalse(any(a["path"] == ".aider.conf.yml" for a in result["artifacts"]))
