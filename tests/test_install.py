import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from uni_learn.contracts import ContractError, canonical
from uni_learn import install
from uni_learn.state import ConflictError, set_project


class InstallFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="uni learn α ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project space α"
        self.project.mkdir()
        self.roots = {kind: self.root / kind for kind in ("config", "data", "state", "cache")}

    def plan(self, profile="codex"):
        return install.plan_install(profile, self.project, application_roots=self.roots)["plan"]

    def apply(self, profile="codex", **kwargs):
        plan = self.plan(profile)
        return install.apply_install(profile, self.project, plan["plan_digest"],
                                     application_roots=self.roots, **kwargs)

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file()}


class DryRunTests(InstallFixture, unittest.TestCase):
    def test_alternate_folder_with_canonical_skill_name_blocks_install(self):
        for declared in ('learn-hint', '"learn-hint"', "'learn-hint'"):
            with self.subTest(declared=declared):
                foreign = self.project / ".agents/skills/user-alias/SKILL.md"
                foreign.parent.mkdir(parents=True, exist_ok=True)
                foreign.write_text(f"---\nname: {declared}\ndescription: user skill\n---\nKeep me.\n")
                plan = self.plan()
                self.assertEqual(plan["status"], "blocked")
                self.assertEqual(plan["skill_conflicts"][0]["name"], "learn-hint")
                self.assertEqual(foreign.read_text().splitlines()[-1], "Keep me.")

    def test_crlf_native_skill_frontmatter_blocks_alias_before_writes(self):
        foreign = self.project / ".agents/skills/user-alias/SKILL.md"
        foreign.parent.mkdir(parents=True)
        original = b"---\r\nname: 'learn-hint'\r\ndescription: user skill\r\n---\r\nKeep me.\r\n"
        foreign.write_bytes(original)
        render = install.render

        def crlf_native_frontmatter(profile, context):
            result = render(profile, context)
            for artifact in result["artifacts"]:
                if artifact["role"] == "command":
                    artifact["content"] = artifact["content"].replace("\n", "\r\n")
            return result

        before = self.inventory()
        with patch("uni_learn.install.render", side_effect=crlf_native_frontmatter):
            plan = self.plan()
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["skill_conflicts"][0]["name"], "learn-hint")
        self.assertEqual(foreign.read_bytes(), original)
        self.assertEqual(self.inventory(), before)

    def test_complex_yaml_names_require_manual_collision_review(self):
        for declaration in ("name: >-\n  learn-hint", "name: |\n  learn-hint",
                            'name: "learn\\u002dhint"', '"name": learn-hint',
                            "base: &base {name: learn-hint}\n<<: *base",
                            "  name: learn-hint", "{name: learn-hint}",
                            "description: no safely parsed name"):
            with self.subTest(declaration=declaration):
                foreign = self.project / ".agents/skills/user-alias/SKILL.md"
                foreign.parent.mkdir(parents=True, exist_ok=True)
                original = f"---\n{declaration}\ndescription: user skill\n---\nKeep me.\n"
                foreign.write_text(original)
                plan = self.plan()
                self.assertEqual(plan["status"], "blocked")
                self.assertEqual(plan["skill_conflicts"][0]["reason"], "ambiguous_native_skill_name")
                self.assertTrue(plan["skill_inventory"][0]["ambiguous_name"])
                self.assertEqual(foreign.read_text(), original)

    def test_unrelated_native_skill_does_not_block(self):
        foreign = self.project / ".agents/skills/unrelated/SKILL.md"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("---\nname: custom-research\n---\nKeep me.\n")
        plan = self.plan()
        self.assertEqual(plan["status"], "partial")
        self.assertEqual(plan["skill_conflicts"], [])
        self.assertEqual(plan["skill_inventory"][0]["path"], str(foreign))

    def test_native_skill_added_after_review_invalidates_digest(self):
        plan = self.plan()
        foreign = self.project / ".agents/skills/alias/SKILL.md"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("---\nname: learn\n---\nMy own skill.\n")
        with self.assertRaises(ConflictError):
            install.apply_install("codex", self.project, plan["plan_digest"], application_roots=self.roots)
        self.assertEqual(foreign.read_text().splitlines()[-1], "My own skill.")
        self.assertFalse(any((self.project / ".agents/skills/learn").rglob("SKILL.md")))

    def test_deterministic_and_read_only(self):
        before = self.inventory()
        first, second = self.plan(), self.plan()
        self.assertEqual(first, second)
        self.assertEqual(before, self.inventory())
        self.assertFalse(self.roots["data"].exists())
        self.assertEqual(first["integration_status"], "partial")
        self.assertIn("persistent_entry_budget", first["unresolved_requirements"])

    def test_manual_entry_preserves_complete_user_instructions(self):
        entry = self.project / "AGENTS.md"
        entry.write_text("User requirement: preserve this.\n")
        plan = self.plan()
        patch_ = next(item for item in plan["manual_patches"] if item["path"] == str(entry))
        self.assertTrue(patch_["content"].startswith("User requirement: preserve this."))
        self.assertIn("state show", patch_["content"])
        self.assertEqual(entry.read_text(), "User requirement: preserve this.\n")

    def test_command_collision_blocks_apply(self):
        collision = self.project / ".agents/skills/learn-hint/SKILL.md"
        collision.parent.mkdir(parents=True)
        collision.write_text("A user's existing skill")
        plan = self.plan()
        self.assertEqual(plan["status"], "blocked")
        with self.assertRaises(ConflictError):
            install.apply_install("codex", self.project, plan["plan_digest"], application_roots=self.roots)
        self.assertEqual(collision.read_text(), "A user's existing skill")

    def test_cursor_proposal_preserves_native_frontmatter_and_user_body(self):
        entry = self.project / ".cursor/rules/uni-learn.mdc"
        proposal = self.plan("cursor")["manual_patches"][0]["content"]
        self.assertTrue(proposal.startswith("---\n"))
        header, body = proposal.split("\n---\n", 1)
        self.assertIn("alwaysApply: true", header)
        self.assertNotIn("<!--", header)
        self.assertIn("<!-- uni-learn:cursor -->", body)
        entry.parent.mkdir(parents=True)
        original = (header + "\n---\n" + "User rule: preserve this.\n" + body).encode("utf-8")
        entry.write_bytes(original)
        repeated = self.plan("cursor")["manual_patches"][0]["content"]
        self.assertTrue(repeated.startswith(header + "\n---\n"))
        self.assertEqual(repeated.count("<!-- uni-learn:cursor -->"), 1)
        self.assertIn("User rule: preserve this.", repeated)
        self.assertEqual(entry.read_bytes(), original)

    def test_incompatible_cursor_frontmatter_requires_manual_merge(self):
        entry = self.project / ".cursor/rules/uni-learn.mdc"
        entry.parent.mkdir(parents=True)
        original = "---\nalwaysApply: false\n---\nUser rule\n"
        entry.write_text(original)
        with self.assertRaisesRegex(ContractError, "frontmatter"):
            self.plan("cursor")
        self.assertEqual(entry.read_text(), original)

    def test_crlf_cursor_frontmatter_is_recognized_and_user_bytes_are_preserved(self):
        entry = self.project / ".cursor/rules/uni-learn.mdc"
        entry.parent.mkdir(parents=True)
        original = (b"---\r\ndescription: Uni Learn efficiency and preference dispatch\r\n"
                    b"alwaysApply: true\r\n---\r\nUser rule stays CRLF.\r\n")
        entry.write_bytes(original)
        render = install.render

        def crlf_cursor_frontmatter(profile, context):
            result = render(profile, context)
            for artifact in result["artifacts"]:
                if artifact["role"] == "persistent":
                    artifact["content"] = artifact["content"].replace("\n", "\r\n")
            return result

        with patch("uni_learn.install.render", side_effect=crlf_cursor_frontmatter):
            proposal = self.plan("cursor")["manual_patches"][0]["content"]
        self.assertTrue(proposal.startswith("---\r\ndescription: Uni Learn efficiency and preference dispatch\r\n"
                                            "alwaysApply: true\r\n---\r\n"))
        self.assertIn("User rule stays CRLF.\r\n", proposal)
        self.assertIn("<!-- uni-learn:cursor -->", proposal)
        self.assertEqual(entry.read_bytes(), original)

    def test_crlf_cursor_frontmatter_mismatch_requires_manual_merge(self):
        entry = self.project / ".cursor/rules/uni-learn.mdc"
        entry.parent.mkdir(parents=True)
        original = b"---\r\nalwaysApply: false\r\n---\r\nUser rule\r\n"
        entry.write_bytes(original)
        render = install.render

        def crlf_cursor_frontmatter(profile, context):
            result = render(profile, context)
            for artifact in result["artifacts"]:
                if artifact["role"] == "persistent":
                    artifact["content"] = artifact["content"].replace("\n", "\r\n")
            return result

        with patch("uni_learn.install.render", side_effect=crlf_cursor_frontmatter):
            with self.assertRaisesRegex(ContractError, "frontmatter"):
                self.plan("cursor")
        self.assertEqual(entry.read_bytes(), original)


class ApplyTests(InstallFixture, unittest.TestCase):
    def test_owned_runtime_and_materialized_resources(self):
        result = self.apply()
        receipt = json.loads(Path(result["receipt"]).read_text())
        runtime = next(Path(item["path"]) for item in receipt["owned"] if item["path"].endswith(".pyz"))
        self.assertTrue((runtime.parent / "resources/core/learning/workflow.md").is_file())
        skill = (self.project / ".agents/skills/learn-hint/SKILL.md").read_text()
        self.assertIn(str(runtime.parent / "resources"), skill)
        self.assertFalse((self.project / "AGENTS.md").exists())

    def test_stale_digest_and_missing_digest_rejected(self):
        plan = self.plan()
        path = self.project / ".agents/skills/learn/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("racing user content")
        with self.assertRaises(ConflictError):
            install.apply_install("codex", self.project, plan["plan_digest"], application_roots=self.roots)
        self.assertEqual(path.read_text(), "racing user content")
        with self.assertRaises(ConflictError):
            install.apply_install("codex", self.project, None, application_roots=self.roots)

    def test_rollback_preserves_prior_files(self):
        user = self.project / "user.txt"
        user.write_text("untouched")
        with self.assertRaises(RuntimeError):
            self.apply(fail_after=3)
        self.assertEqual(user.read_text(), "untouched")
        self.assertFalse(any(self.project.rglob("SKILL.md")))
        self.assertFalse(any(self.roots["data"].rglob("*.pyz")))
        self.assertFalse(any(self.roots["data"].rglob("*.journal.json")))

    def test_symlink_ancestor_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / ".agents").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ContractError):
            self.plan()
        self.assertEqual(list(outside.iterdir()), [])

    def test_commit_cannot_overwrite_a_racing_file(self):
        path = self.project / "exclusive.md"
        real_link = os.link
        def race(source, destination, *args, **kwargs):
            Path(destination).write_text("created by another process")
            return real_link(source, destination, *args, **kwargs)
        with patch("uni_learn.install.os.link", side_effect=race), self.assertRaises(FileExistsError):
            install._create_new(path, b"installer content")
        self.assertEqual(path.read_text(), "created by another process")

    def test_interrupted_journal_recovers_proven_files(self):
        plan = self.plan()
        first = next(item for item in plan["actions"] if item["action"] == "create_owned")
        planned = install.plan_install("codex", self.project, application_roots=self.roots)
        target = Path(first["path"])
        file_id = install._create_new(target, planned["contents"][str(target)])
        journal = {key: plan[key] for key in ("schema_version", "install_id", "profile", "project",
                                              "generation", "runtime_version", "data_root")}
        journal["owned"] = [{"path": str(target), "sha256": first["desired_sha256"], "file_id": file_id}]
        journal_path = Path(plan["journal"])
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_bytes(canonical(journal))
        recovered = self.plan()
        self.assertIn("recover_owned", [item["action"] for item in recovered["actions"]])
        result = install.apply_install("codex", self.project, recovered["plan_digest"], application_roots=self.roots)
        receipt = json.loads(Path(result["receipt"]).read_text())
        self.assertIn(str(target), [item["path"] for item in receipt["owned"]])


class ReversalTests(InstallFixture, unittest.TestCase):
    def test_owned_skill_names_do_not_conflict_on_reinstall(self):
        self.apply()
        second = self.plan()
        self.assertEqual(second["skill_conflicts"], [])
        self.assertEqual(len(second["skill_inventory"]), 7)
        self.assertGreater(self.apply()["owned_count"], 7)

    def test_reinstall_and_repeat_removal_are_idempotent(self):
        result = self.apply()
        before = self.inventory()
        self.apply()
        self.assertEqual(before, self.inventory())
        plan = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        removed = install.apply_uninstall(result["receipt"], plan["plan_digest"], application_roots=self.roots)
        self.assertGreater(removed["removed"], 0)
        again = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        self.assertEqual(install.apply_uninstall(result["receipt"], again["plan_digest"], application_roots=self.roots)["status"], "absent")

    def test_edits_and_history_preserved(self):
        result = self.apply()
        set_project(self.project, logging=True, application_roots=self.roots)
        edited = self.project / ".agents/skills/learn-hint/SKILL.md"
        edited.write_text("my edit")
        plan = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        out = install.apply_uninstall(result["receipt"], plan["plan_digest"], application_roots=self.roots)
        self.assertIn(str(edited), out["preserved"])
        self.assertEqual(edited.read_text(), "my edit")
        self.assertTrue(any(self.roots["state"].rglob("project.json")))

    def test_identical_unowned_file_is_never_removed(self):
        planned = install.plan_install("codex", self.project, application_roots=self.roots)
        action = next(item for item in planned["plan"]["actions"] if item["role"] == "command")
        target = Path(action["path"])
        target.parent.mkdir(parents=True)
        target.write_bytes(planned["contents"][str(target)])
        result = self.apply()
        receipt = json.loads(Path(result["receipt"]).read_text())
        self.assertNotIn(str(target), [item["path"] for item in receipt["owned"]])
        plan = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        install.apply_uninstall(result["receipt"], plan["plan_digest"], application_roots=self.roots)
        self.assertTrue(target.is_file())

    def test_forged_external_path_and_receipt_path_rejected(self):
        result = self.apply()
        victim = self.root / "outside.txt"
        victim.write_text("must survive")
        receipt_path = Path(result["receipt"])
        receipt = json.loads(receipt_path.read_text())
        receipt["owned"].append({"path": str(victim), "sha256": hashlib.sha256(victim.read_bytes()).hexdigest()})
        receipt_path.write_bytes(canonical(receipt))
        with self.assertRaises(ContractError):
            install.plan_uninstall(receipt_path, application_roots=self.roots)
        self.assertEqual(victim.read_text(), "must survive")
        external_receipt = self.root / "forged.json"
        external_receipt.write_bytes(canonical(receipt))
        with self.assertRaises(ContractError):
            install.plan_uninstall(external_receipt, application_roots=self.roots)

    def test_symlinked_owned_ancestor_never_deletes_external_file(self):
        result = self.apply()
        owned_root = self.project / ".agents"
        moved = self.root / "moved"
        owned_root.rename(moved)
        owned_root.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ContractError):
            install.plan_uninstall(result["receipt"], application_roots=self.roots)
        self.assertTrue((moved / "skills/learn/SKILL.md").is_file())

    def test_two_host_receipts_have_independent_payloads(self):
        codex, claude = self.apply("codex"), self.apply("claude-code")
        plan = install.plan_uninstall(codex["receipt"], application_roots=self.roots)
        install.apply_uninstall(codex["receipt"], plan["plan_digest"], application_roots=self.roots)
        receipt = json.loads(Path(claude["receipt"]).read_text())
        self.assertTrue(all(Path(item["path"]).is_file() for item in receipt["owned"]))


class VerifiedBudgetTests(InstallFixture, unittest.TestCase):
    def counter(self, count=250):
        return patch("uni_learn.install.counter_for_model", return_value=(lambda text: count, {
            "library": "fixture", "version": "1", "encoding": "test-only", "model": "fixture-model"}))

    def checked_plan(self, profile="codex"):
        return install.plan_install(profile, self.project, application_roots=self.roots,
                                    budget_model="fixture-model")["plan"]

    def test_verified_new_entry_is_owned_reinstallable_and_removable(self):
        with self.counter():
            plan = self.checked_plan()
            entry = next(row for row in plan["actions"] if row["role"] == "persistent")
            self.assertEqual(plan["status"], "ready")
            self.assertEqual(entry["action"], "create_owned")
            self.assertEqual(entry["budget"]["tokens"], 250)
            self.assertTrue(entry["budget"]["model_mapping_requires_host_match"])
            result = install.apply_install("codex", self.project, plan["plan_digest"],
                                           application_roots=self.roots, budget_model="fixture-model")
            self.assertTrue((self.project / "AGENTS.md").is_file())
            before = self.inventory()
            repeated = self.checked_plan()
            install.apply_install("codex", self.project, repeated["plan_digest"],
                                  application_roots=self.roots, budget_model="fixture-model")
            self.assertEqual(before, self.inventory())
        removal = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        install.apply_uninstall(result["receipt"], removal["plan_digest"], application_roots=self.roots)
        self.assertFalse((self.project / "AGENTS.md").exists())

    def test_complete_entry_over_limit_remains_manual(self):
        with self.counter(1000):
            plan = self.checked_plan()
        self.assertEqual(plan["status"], "partial")
        self.assertEqual(plan["manual_patches"][0]["reason"], "persistent_entry_budget_exceeded")
        self.assertFalse((self.project / "AGENTS.md").exists())

    def test_existing_user_imports_cannot_be_certified_by_file_count(self):
        entry = self.project / "AGENTS.md"
        original = b"@large-always-loaded.md\r\nUser rules\r\n"
        entry.write_bytes(original)
        with self.counter():
            plan = self.checked_plan()
        action = next(row for row in plan["actions"] if row["role"] == "persistent")
        self.assertEqual(action["action"], "manual_patch")
        self.assertEqual(action["budget"]["status"], "unverified")
        self.assertEqual(action["budget"]["file_measurement"]["tokens"], 250)
        self.assertIn(original.decode("utf-8"), action["manual_content"])
        self.assertEqual(entry.read_bytes(), original)

    def test_reviewed_counter_measurement_is_part_of_digest(self):
        with self.counter(250):
            plan = self.checked_plan()
        with self.counter(251), self.assertRaises(ConflictError):
            install.apply_install("codex", self.project, plan["plan_digest"],
                                  application_roots=self.roots, budget_model="fixture-model")
        self.assertFalse((self.project / "AGENTS.md").exists())


class CrashRecoveryTests(InstallFixture, unittest.TestCase):
    def test_interrupt_after_destination_commit_has_preexisting_inode_journal(self):
        link = os.link
        def interrupted(source, destination, *args, **kwargs):
            link(source, destination, *args, **kwargs)
            raise KeyboardInterrupt("simulated process interruption after commit")
        plan = self.plan()
        with patch("uni_learn.install.os.link", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            install.apply_install("codex", self.project, plan["plan_digest"], application_roots=self.roots)
        recovery = self.plan()
        self.assertIn("recover_owned", [row["action"] for row in recovery["actions"]])
        self.assertNotIn("reuse_unowned", [row["action"] for row in recovery["actions"]])
        result = install.apply_install("codex", self.project, recovery["plan_digest"], application_roots=self.roots)
        self.assertTrue(Path(result["receipt"]).is_file())

    def test_interrupt_after_precommit_journal_never_leaves_unowned_destination(self):
        atomic = install.atomic_json
        def interrupted(path, value):
            atomic(path, value)
            if str(path).endswith(".journal.json") and value["owned"]:
                raise KeyboardInterrupt("simulated interruption before destination commit")
        plan = self.plan()
        with patch("uni_learn.install.atomic_json", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            install.apply_install("codex", self.project, plan["plan_digest"], application_roots=self.roots)
        recovery = self.plan()
        self.assertNotIn("reuse_unowned", [row["action"] for row in recovery["actions"]])
        install.apply_install("codex", self.project, recovery["plan_digest"], application_roots=self.roots)


class AutomaticSetupTests(InstallFixture, unittest.TestCase):
    def test_existing_instructions_without_final_newline_round_trip(self):
        home, codex_home = self.user_paths()
        entry = codex_home / "AGENTS.md"
        for original in (b"First\r\nLast", b"First\nLast"):
            with self.subTest(original=original):
                entry.write_bytes(original)
                result = install.setup("codex", application_roots=self.roots,
                                       user_home=home, codex_home=codex_home)
                removal = install.plan_uninstall(result["receipt"], application_roots=self.roots)
                install.apply_uninstall(result["receipt"], removal["plan_digest"], application_roots=self.roots)
                self.assertEqual(entry.read_bytes(), original)

    def test_cursor_header_only_uses_existing_crlf_for_owned_block(self):
        entry = self.project / ".cursor/rules/uni-learn.mdc"
        entry.parent.mkdir(parents=True)
        original = (b"---\r\ndescription: Uni Learn efficiency and preference dispatch\r\n"
                    b"alwaysApply: true\r\n---\r\n")
        entry.write_bytes(original)
        result = install.setup("cursor", self.project, application_roots=self.roots)
        content = entry.read_bytes()
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
        rewritten = content.decode("utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
        entry.write_bytes(rewritten.encode("utf-8"))
        install.setup("cursor", self.project, application_roots=self.roots)
        removal = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        install.apply_uninstall(result["receipt"], removal["plan_digest"], application_roots=self.roots)
        self.assertEqual(entry.read_bytes(), original)

    def user_paths(self):
        home = self.root / "user home"
        home.mkdir()
        codex_home = home / ".codex"
        codex_home.mkdir()
        return home, codex_home

    def test_one_call_global_setup_and_block_only_removal(self):
        home, codex_home = self.user_paths()
        entry = codex_home / "AGENTS.md"
        original = b"My own instructions.\r\n"
        entry.write_bytes(original)
        options = {"application_roots": self.roots, "user_home": home, "codex_home": codex_home}
        result = install.setup("codex", **options)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["scope"], "user")
        self.assertEqual(set(result["commands"]), {"learn", "learn-hint", "learn-debug",
                                                    "learn-review", "learn-design", "learn-recall", "learn-compact"})
        self.assertTrue((home / ".agents/skills/learn/SKILL.md").is_file())
        self.assertIn(b"My own instructions.", entry.read_bytes())
        self.assertEqual(entry.read_bytes().count(b"<!-- uni-learn:codex -->"), 1)
        receipt = json.loads(Path(result["receipt"]).read_text())
        self.assertEqual(receipt["scope"], "user")
        self.assertEqual(next(row["kind"] for row in receipt["owned"]
                              if row["path"] == str(entry.resolve())), "block")
        # Windows text-mode edits normalize every line in the file to CRLF.
        rewritten = entry.read_bytes().decode("utf-8").replace("\r\n", "\n")
        rewritten = (rewritten + "\nMore user instructions.\n").replace("\n", "\r\n")
        entry.write_bytes(rewritten.encode("utf-8"))
        install.setup("codex", **options)
        self.assertEqual(entry.read_bytes().count(b"<!-- uni-learn:codex -->"), 1)
        removal = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        self.assertEqual(removal["status"], "ready")
        install.apply_uninstall(result["receipt"], removal["plan_digest"], application_roots=self.roots)
        self.assertEqual(entry.read_bytes(), original + b"\r\nMore user instructions.\r\n")

    def test_existing_override_is_selected_without_touching_agents(self):
        home, codex_home = self.user_paths()
        agents = codex_home / "AGENTS.md"
        agents.write_text("Keep ordinary instructions.\n")
        override = codex_home / "AGENTS.override.md"
        override.write_text("Keep override.\n")
        result = install.setup("codex", application_roots=self.roots,
                               user_home=home, codex_home=codex_home)
        self.assertEqual(result["instruction_paths"], [str(override.resolve())])
        self.assertEqual(agents.read_text(), "Keep ordinary instructions.\n")
        self.assertIn("Keep override.", override.read_text())

    def test_custom_codex_home_and_empty_override_precedence(self):
        home, default_home = self.user_paths()
        custom = self.root / "custom Codex configuration"
        custom.mkdir()
        override = custom / "AGENTS.override.md"
        for empty_content in (b"", b" \r\n\t\r\n"):
            with self.subTest(override=empty_content):
                override.write_bytes(empty_content)
                with patch.dict(os.environ, {"CODEX_HOME": str(custom)}):
                    result = install.setup("codex", application_roots=self.roots, user_home=home)
                self.assertEqual(result["instruction_paths"], [str(custom / "AGENTS.md")])
                self.assertEqual(override.read_bytes(), empty_content)
                self.assertFalse((default_home / "AGENTS.md").exists())
                removal = install.plan_uninstall(result["receipt"], application_roots=self.roots)
                install.apply_uninstall(result["receipt"], removal["plan_digest"], application_roots=self.roots)
                self.assertEqual(override.read_bytes(), empty_content)

    def test_user_edited_managed_block_is_not_replaced_or_removed(self):
        home, codex_home = self.user_paths()
        options = {"application_roots": self.roots, "user_home": home, "codex_home": codex_home}
        result = install.setup("codex", **options)
        entry = codex_home / "AGENTS.md"
        changed = entry.read_bytes().replace(b"# Uni Learn", b"# My customized Uni Learn")
        entry.write_bytes(changed)
        with self.assertRaises(ConflictError):
            install.setup("codex", **options)
        self.assertEqual(entry.read_bytes(), changed)
        removal = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        removed = install.apply_uninstall(result["receipt"], removal["plan_digest"], application_roots=self.roots)
        self.assertIn(str(entry), removed["preserved"])
        self.assertEqual(entry.read_bytes(), changed)

    def test_foreign_collision_stops_automatic_setup_before_writes(self):
        home, codex_home = self.user_paths()
        foreign = home / ".agents/skills/custom/SKILL.md"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("---\nname: learn-hint\n---\nMine.\n")
        with self.assertRaises(ConflictError) as caught:
            install.setup("codex", application_roots=self.roots,
                          user_home=home, codex_home=codex_home)
        self.assertIn(str(foreign), str(caught.exception))
        self.assertEqual(foreign.read_text(), "---\nname: learn-hint\n---\nMine.\n")
        self.assertFalse((codex_home / "AGENTS.md").exists())
        self.assertFalse(self.roots["data"].exists())

    def test_unrelated_linked_skill_is_allowed(self):
        home, codex_home = self.user_paths()
        external = self.root / "external"
        external.mkdir()
        (external / "SKILL.md").write_text("---\nname: unrelated-helper\n---\nMine.\n")
        linked = home / ".agents/skills/linked"
        linked.parent.mkdir(parents=True)
        linked.symlink_to(external, target_is_directory=True)
        result = install.setup("codex", application_roots=self.roots,
                               user_home=home, codex_home=codex_home)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(linked.is_symlink())

    def test_project_setup_works_without_optional_counter(self):
        for profile in install.load_registry()["profiles"]:
            with self.subTest(profile=profile):
                project = self.project / profile
                project.mkdir()
                options = {"generation": "devin-desktop"} if profile == "windsurf" else {}
                result = install.setup(profile, project, scope="project", application_roots=self.roots, **options)
                self.assertEqual(result["status"], "ready")
                self.assertEqual(len(result["commands"]), 7)
                entry = Path(result["instruction_paths"][0])
                if profile == "cursor":
                    self.assertTrue(entry.read_text().startswith("---\n"))
                self.assertIn("<!-- uni-learn:" + profile + " -->", entry.read_text())
                removal = install.plan_uninstall(result["receipt"], application_roots=self.roots)
                install.apply_uninstall(result["receipt"], removal["plan_digest"], application_roots=self.roots)
                self.assertFalse(entry.exists())

    def test_recovered_upgrade_can_advance_to_newer_owned_block(self):
        home, codex_home = self.user_paths()
        options = {"application_roots": self.roots, "user_home": home,
                   "codex_home": codex_home, "scope": "user", "automatic": True}
        entry = codex_home / "AGENTS.md"
        entry.write_text("User instructions.\n")
        install.setup("codex", application_roots=self.roots, user_home=home, codex_home=codex_home)
        render = install.render
        replace = install.os.replace
        revision = ["first"]
        def changed(profile, context):
            rendered = render(profile, context)
            for artifact in rendered["artifacts"]:
                if artifact["role"] == "persistent":
                    artifact["content"] = artifact["content"].replace("# Uni Learn", "# Uni Learn " + revision[0])
            return rendered
        def interrupted(source, destination):
            replace(source, destination)
            if Path(destination) == entry:
                raise KeyboardInterrupt("after replacement")
        with patch("uni_learn.install.render", side_effect=changed):
            plan = install.plan_install("codex", None, **options)["plan"]
            with patch("uni_learn.install.os.replace", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
                install.apply_install("codex", None, plan["plan_digest"], **options)
            revision[0] = "second"
            recovery = install.plan_install("codex", None, **options)["plan"]
            self.assertEqual(recovery["status"], "ready")
            install.apply_install("codex", None, recovery["plan_digest"], **options)
        self.assertIn("# Uni Learn second", entry.read_text())
        self.assertTrue(entry.read_text().startswith("User instructions.\n"))
        self.assertEqual(list(codex_home.glob(".uni-learn-*")), [])

    def test_automatic_block_update_rolls_back_without_losing_user_text(self):
        home, codex_home = self.user_paths()
        entry = codex_home / "AGENTS.md"
        entry.write_text("My rules.\n")
        options = {"application_roots": self.roots, "user_home": home,
                   "codex_home": codex_home, "scope": "user", "automatic": True}
        result = install.setup("codex", application_roots=self.roots,
                               user_home=home, codex_home=codex_home)
        before = entry.read_bytes()
        receipt_before = Path(result["receipt"]).read_bytes()
        render = install.render
        def changed(profile, context):
            rendered = render(profile, context)
            for artifact in rendered["artifacts"]:
                if artifact["role"] == "persistent":
                    artifact["content"] = artifact["content"].replace("# Uni Learn", "# Uni Learn Updated")
            return rendered
        with patch("uni_learn.install.render", side_effect=changed):
            plan = install.plan_install("codex", None, **options)["plan"]
            self.assertIn("update_block", [row["action"] for row in plan["actions"]])
            with self.assertRaises(RuntimeError):
                install.apply_install("codex", None, plan["plan_digest"],
                                      fail_after=1, **options)
        self.assertEqual(entry.read_bytes(), before)
        self.assertEqual(Path(result["receipt"]).read_bytes(), receipt_before)
        self.assertEqual(install.plan_install("codex", None, **options)["plan"]["status"], "ready")

    def test_recover_committed_block_update_before_receipt(self):
        home, codex_home = self.user_paths()
        options = {"application_roots": self.roots, "user_home": home,
                   "codex_home": codex_home, "scope": "user", "automatic": True}
        install.setup("codex", application_roots=self.roots, user_home=home, codex_home=codex_home)
        entry = (codex_home / "AGENTS.md").resolve()
        render = install.render
        replace = install.os.replace
        def changed(profile, context):
            rendered = render(profile, context)
            for artifact in rendered["artifacts"]:
                if artifact["role"] == "persistent":
                    artifact["content"] += "\nUpdated guidance.\n"
            return rendered
        def interrupted(source, destination):
            replace(source, destination)
            if Path(destination) == entry:
                raise KeyboardInterrupt("after instruction replacement")
        with patch("uni_learn.install.render", side_effect=changed):
            planned = install.plan_install("codex", None, **options)["plan"]
            with patch("uni_learn.install.os.replace", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
                install.apply_install("codex", None, planned["plan_digest"], **options)
            recovery = install.plan_install("codex", None, **options)["plan"]
            self.assertIn("recover_owned", [row["action"] for row in recovery["actions"]])
            install.apply_install("codex", None, recovery["plan_digest"], **options)
        self.assertIn("Updated guidance.", entry.read_text())
        self.assertFalse(any(self.roots["data"].rglob("*.journal.json")))

    def test_failed_skill_upgrade_restores_receipt_inode_ownership(self):
        home, codex_home = self.user_paths()
        options = {"application_roots": self.roots, "user_home": home,
                   "codex_home": codex_home, "scope": "user", "automatic": True}
        result = install.setup("codex", application_roots=self.roots,
                               user_home=home, codex_home=codex_home)
        skill = (home / ".agents/skills/learn/SKILL.md").resolve()
        original = skill.read_bytes()
        original_inode = skill.stat().st_ino
        render = install.render
        def changed(profile, context):
            rendered = render(profile, context)
            for artifact in rendered["artifacts"]:
                if artifact["path"] == ".agents/skills/learn/SKILL.md":
                    artifact["content"] += "\nUpdated dispatcher.\n"
            return rendered
        with patch("uni_learn.install.render", side_effect=changed):
            plan = install.plan_install("codex", None, **options)["plan"]
            with self.assertRaises(RuntimeError):
                install.apply_install("codex", None, plan["plan_digest"], fail_after=1, **options)
        self.assertEqual(skill.read_bytes(), original)
        self.assertEqual(skill.stat().st_ino, original_inode)
        removal = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        self.assertEqual(next(row["action"] for row in removal["actions"]
                              if row["path"] == str(skill)), "remove_owned")

    def test_crlf_and_blank_lines_survive_setup_reinstall_removal(self):
        home, codex_home = self.user_paths()
        entry = codex_home / "AGENTS.md"
        original = b"User rules.\r\n\r\n"
        suffix = b"\r\n\r\nLater rules.\r\n"
        entry.write_bytes(original)
        options = {"application_roots": self.roots, "user_home": home, "codex_home": codex_home}
        first = install.setup("codex", **options)
        self.assertTrue(entry.read_bytes().startswith(original))
        entry.write_bytes(entry.read_bytes() + suffix)
        install.setup("codex", **options)
        self.assertEqual(entry.read_bytes().count(b"<!-- uni-learn:codex -->"), 1)
        removal = install.plan_uninstall(first["receipt"], application_roots=self.roots)
        install.apply_uninstall(first["receipt"], removal["plan_digest"], application_roots=self.roots)
        self.assertEqual(entry.read_bytes(), original + suffix)

    def test_cursor_existing_crlf_header_and_body_preserved(self):
        proposal = self.plan("cursor")["manual_patches"][0]["content"]
        header = proposal[:proposal.index("\n---\n") + len("\n---\n")].replace("\n", "\r\n")
        entry = self.project / ".cursor/rules/uni-learn.mdc"
        entry.parent.mkdir(parents=True)
        original = header.encode() + b"Custom cursor rule.\r\n"
        entry.write_bytes(original)
        result = install.setup("cursor", self.project, application_roots=self.roots)
        self.assertTrue(entry.read_bytes().startswith(original))
        removal = install.plan_uninstall(result["receipt"], application_roots=self.roots)
        install.apply_uninstall(result["receipt"], removal["plan_digest"], application_roots=self.roots)
        self.assertEqual(entry.read_bytes(), original)

    def test_known_oversized_instruction_file_blocks_without_writes(self):
        home, codex_home = self.user_paths()
        entry = codex_home / "AGENTS.md"
        entry.write_text("User content.\n")
        source = {"library": "fixture", "version": "1", "encoding": "test", "model": "fixture"}
        with patch("uni_learn.install.counter_for_model", return_value=(lambda text: 1000, source)):
            with self.assertRaisesRegex(ConflictError, "AGENTS.md"):
                install.setup("codex", application_roots=self.roots, budget_model="fixture",
                              user_home=home, codex_home=codex_home)
        self.assertEqual(entry.read_text(), "User content.\n")
        self.assertFalse(self.roots["data"].exists())

    def test_owned_block_does_not_certify_foreign_imports_on_reinstall(self):
        home, codex_home = self.user_paths()
        entry = codex_home / "AGENTS.md"
        entry.write_text("@huge-import.md\nUser rule.\n")
        options = {"application_roots": self.roots, "user_home": home, "codex_home": codex_home}
        install.setup("codex", **options)
        source = {"library": "fixture", "version": "1", "encoding": "test", "model": "fixture"}
        with patch("uni_learn.install.counter_for_model", return_value=(lambda text: 250, source)):
            plan = install.plan_install("codex", None, scope="user", automatic=True,
                                        budget_model="fixture", **options)["plan"]
        entry_action = next(row for row in plan["actions"] if row["role"] == "persistent")
        self.assertEqual(entry_action["budget"]["status"], "unverified")
        self.assertEqual(entry_action["budget"]["file_measurement"]["tokens"], 250)

    def test_generated_entry_loses_budget_provenance_after_external_append(self):
        home, codex_home = self.user_paths()
        entry = codex_home / "AGENTS.md"
        options = {"application_roots": self.roots, "user_home": home, "codex_home": codex_home,
                   "budget_model": "fixture"}
        source = {"library": "fixture", "version": "1", "encoding": "test", "model": "fixture"}
        with patch("uni_learn.install.counter_for_model", return_value=(lambda text: 250, source)):
            first = install.setup("codex", **options)
            self.assertEqual(first["instruction_budgets"][0]["status"], "verified")
            entry.write_bytes(entry.read_bytes() + b"@huge-import.md\n")
            second = install.setup("codex", **options)
            third = install.setup("codex", **options)
        self.assertEqual(second["instruction_budgets"][0]["status"], "unverified")
        self.assertEqual(third["instruction_budgets"][0]["status"], "unverified")

    def test_receipt_failure_with_user_append_preserves_content(self):
        home, codex_home = self.user_paths()
        entry = codex_home / "AGENTS.md"
        options = {"application_roots": self.roots, "user_home": home, "codex_home": codex_home}
        plan = install.plan_install("codex", None, scope="user", automatic=True, **options)["plan"]
        receipt = Path(plan["receipt"])
        atomic = install.atomic_json
        def interrupted(path, value):
            if Path(path) == receipt:
                entry.write_bytes(entry.read_bytes() + b"User appended text.\n")
                raise OSError("receipt write failed")
            return atomic(path, value)
        with patch("uni_learn.install.atomic_json", side_effect=interrupted), self.assertRaises(OSError):
            install.apply_install("codex", None, plan["plan_digest"], scope="user", automatic=True, **options)
        self.assertIn(b"User appended text.\n", entry.read_bytes())
        recovery = install.plan_install("codex", None, scope="user", automatic=True, **options)["plan"]
        self.assertIn("recover_owned", [row["action"] for row in recovery["actions"]])
        install.apply_install("codex", None, recovery["plan_digest"],
                              scope="user", automatic=True, **options)
        self.assertIn(b"User appended text.\n", entry.read_bytes())
