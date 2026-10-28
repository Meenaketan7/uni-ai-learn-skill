import json
import unittest
from pathlib import Path
from unittest.mock import patch

from uni_learn import install
from uni_learn.state import ConflictError
from tests.test_install import InstallFixture


class NativeSkillAliasTests(InstallFixture, unittest.TestCase):
    def test_native_alias_collision_blocks_before_writes(self):
        foreign = self.project / ".agents/skills/personal/SKILL.md"
        foreign.parent.mkdir(parents=True)
        for name in ("uni-learn", '"uni-learn"', "'uni-learn'"):
            with self.subTest(name=name):
                original = f"---\r\nname: {name}\r\n---\r\nUser content.\r\n".encode("utf-8")
                foreign.write_bytes(original)
                before = self.inventory()
                with self.assertRaises(ConflictError):
                    install.setup("antigravity", self.project, application_roots=self.roots)
                self.assertEqual(self.inventory(), before)
                self.assertEqual(foreign.read_bytes(), original)

    def test_runtime_identifier_is_not_a_native_name_collision(self):
        foreign = self.project / ".agents/skills/personal/SKILL.md"
        foreign.parent.mkdir(parents=True)
        original = "---\nname: learn\n---\nUser content.\n"
        foreign.write_text(original, encoding="utf-8")
        result = install.setup("antigravity", self.project, application_roots=self.roots)
        self.assertEqual(result["status"], "ready")
        skill = self.project / ".agents/skills/learn/SKILL.md"
        content = skill.read_text("utf-8")
        self.assertIn("\nname: uni-learn\n", content)
        self.assertIn("--command learn --argument", content)
        self.assertEqual(foreign.read_text("utf-8"), original)
        receipt = json.loads(Path(result["receipt"]).read_text("utf-8"))
        self.assertIn(str(skill), {entry["path"] for entry in receipt["owned"]})

    def test_owned_alias_upgrade_rollback_reinstall_and_removal(self):
        render = install.render

        def legacy(profile, context):
            result = render(profile, context)
            for item in result["artifacts"]:
                if item["role"] == "command":
                    item["content"] = item["content"].replace("\nname: uni-", "\nname: ", 1)
            for ident, command in result["commands"].items():
                command["invocation"] = "/" + ident
            return result

        with patch("uni_learn.install.render", side_effect=legacy):
            result = install.setup("antigravity", self.project, application_roots=self.roots)
        skill = self.project / ".agents/skills/learn/SKILL.md"
        original, inode = skill.read_bytes(), skill.stat().st_ino
        receipt = Path(result["receipt"])
        receipt_before = receipt.read_bytes()
        options = {"application_roots": self.roots, "automatic": True}
        plan = install.plan_install("antigravity", self.project, **options)["plan"]
        with self.assertRaises(RuntimeError):
            install.apply_install("antigravity", self.project, plan["plan_digest"], fail_after=1, **options)
        self.assertEqual(skill.read_bytes(), original)
        self.assertEqual(skill.stat().st_ino, inode)
        self.assertEqual(receipt.read_bytes(), receipt_before)

        upgraded = install.setup("antigravity", self.project, application_roots=self.roots)
        self.assertIn("\nname: uni-learn\n", skill.read_text("utf-8"))
        self.assertEqual(len(list(self.project.rglob("SKILL.md"))), 7)
        before = self.inventory()
        install.setup("antigravity", self.project, application_roots=self.roots)
        self.assertEqual(self.inventory(), before)
        edited = self.project / ".agents/skills/learn-hint/SKILL.md"
        edited.write_text("User edit.\n", encoding="utf-8")
        removal = install.plan_uninstall(upgraded["receipt"], application_roots=self.roots)
        install.apply_uninstall(upgraded["receipt"], removal["plan_digest"], application_roots=self.roots)
        self.assertFalse(skill.exists())
        self.assertEqual(edited.read_text("utf-8"), "User edit.\n")
