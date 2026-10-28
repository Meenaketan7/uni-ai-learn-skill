import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uni_learn.contracts import ContractError
from uni_learn.compaction import checkpoint_path, prepare, resume, validate_checkpoint
from uni_learn.pointers import file_pointer, url_pointer
from uni_learn.state import new_task, set_project, update_task


class CheckpointFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.roots = {"state": Path(self.temp.name) / "state"}
        new_task(self.project, "s", "t", "fix parser", application_roots=self.roots)
        update_task(self.project, "s", "t", {"attempts": ["tried regex"], "hypotheses": ["newline missing"],
                    "hint_level": 2, "explicit_learning": True, "constraints": ["preserve unicode"]},
                    application_roots=self.roots)
        self.request = {"project": str(self.project), "session_id": "s", "task_id": "t"}

    def prepare(self, **extra):
        return prepare({**self.request, **extra}, application_roots=self.roots)


class CheckpointTests(CheckpointFixture, unittest.TestCase):
    def test_learning_retained_without_optional_log(self):
        value = self.prepare()
        self.assertFalse(value["native_compaction_executed"])
        self.assertEqual(value["checkpoint"]["learning"]["hint_level"], 2)
        self.assertIn("preserve unicode", value["resume_capsule"]["constraints"])
        self.assertTrue(value["checkpoint"]["audit"]["after_valid"])

    def test_every_mandatory_top_level_and_learning_field_is_validated(self):
        value = self.prepare()["checkpoint"]
        for key in value:
            broken = copy.deepcopy(value)
            del broken[key]
            with self.subTest(field=key), self.assertRaises(ContractError):
                validate_checkpoint(broken)
        for key in value["learning"]:
            broken = copy.deepcopy(value)
            del broken["learning"][key]
            with self.subTest(learning_field=key), self.assertRaises(ContractError):
                validate_checkpoint(broken)

    def test_ordinary_off_checkpoint_does_not_invent_learning(self):
        new_task(self.project, "s", "ordinary", "ordinary task", application_roots=self.roots)
        value = prepare({**self.request, "task_id": "ordinary"}, application_roots=self.roots)
        self.assertIsNone(value["checkpoint"]["learning"])
        self.assertEqual(value["checkpoint"]["mode"], "off")

    def test_decisions_failures_and_questions_survive_by_default(self):
        facts = {
            "decisions": [{"choice": "retain encoding", "reason": "required contract", "source_ids": []}],
            "failed_approaches": [{"attempt": "regex", "observation": "fails", "explanation": "encoding", "unresolved": True}],
            "open_questions": [{"unknown": "input shape", "why": "could change parsing", "evidence_needed": "fixture"}],
        }
        update_task(self.project, "s", "t", facts, application_roots=self.roots)
        value = self.prepare(constraints=[])["checkpoint"]
        for key, record in facts.items():
            self.assertEqual(value[key], record)
        self.assertEqual(value["constraints"], ["preserve unicode"])

    def test_unknown_native_strategy_or_unverified_pointer_does_not_save(self):
        with self.assertRaises(ContractError):
            self.prepare(strategy="invented")
        with self.assertRaises(ContractError):
            self.prepare(pointers=[url_pointer("https://example.test", "2026-10-22T00:00:00Z")])
        self.assertFalse(list(self.roots["state"].rglob("checkpoints/*.json")))

    def test_failed_new_write_does_not_damage_previous_checkpoint(self):
        first = self.prepare()
        with patch("uni_learn.compaction.atomic_json", side_effect=OSError("injected")), self.assertRaises(OSError):
            self.prepare()
        self.assertTrue(resume(self.project, first["checkpoint"]["checkpoint_id"], application_roots=self.roots)["safe"])

    def test_supplied_inventory_survives_later_checkpoint(self):
        question = {"unknown": "new input format", "why": "affects parser", "evidence_needed": "sample"}
        self.prepare(constraints=["preserve whitespace"], open_questions=[question])
        second = self.prepare()["checkpoint"]
        self.assertIn("preserve whitespace", second["constraints"])
        self.assertIn(question, second["open_questions"])
        self.assertTrue(resume(self.project, second["checkpoint_id"], application_roots=self.roots)["safe"])


class ResumeTests(CheckpointFixture, unittest.TestCase):
    def test_missing_task_blocks_resume(self):
        value = self.prepare()["checkpoint"]
        task = self.roots["state"] / "projects" / value["project_id"] / "sessions/s/tasks/t.json"
        task.unlink()
        self.assertFalse(resume(self.project, value["checkpoint_id"], application_roots=self.roots)["safe"])

    def test_changed_task_same_objective_blocks_resume(self):
        value = self.prepare()["checkpoint"]
        update_task(self.project, "s", "t", {"hint_level": 3}, application_roots=self.roots)
        self.assertFalse(resume(self.project, value["checkpoint_id"], application_roots=self.roots)["safe"])

    def test_project_mode_changes_block_stale_resume(self):
        value = self.prepare()["checkpoint"]
        set_project(self.project, learning=True, application_roots=self.roots)
        self.assertFalse(resume(self.project, value["checkpoint_id"], application_roots=self.roots)["safe"])

    def test_known_missing_context_blocks_resume(self):
        value = self.prepare(known_missing_context=["unrecorded acceptance decision"])["checkpoint"]
        self.assertFalse(resume(self.project, value["checkpoint_id"], application_roots=self.roots)["safe"])

    def test_changed_source_is_located_but_conclusion_not_silently_revalidated(self):
        source = self.project / "code.py"
        source.write_text("def parser(): pass\n")
        value = self.prepare(pointers=[file_pointer(source, symbol="parser")])["checkpoint"]
        self.assertTrue(resume(self.project, value["checkpoint_id"], application_roots=self.roots)["safe"])
        source.write_text("# inserted\ndef parser(): return 1\n")
        result = resume(self.project, value["checkpoint_id"], application_roots=self.roots)
        self.assertFalse(result["safe"])
        self.assertEqual(result["pointer_audit"][0]["lines"], [2])

    def test_corrupt_checkpoint_is_diagnostic(self):
        value = self.prepare()["checkpoint"]
        checkpoint_path(self.project, value["checkpoint_id"], self.roots).write_text("{broken")
        self.assertFalse(resume(self.project, value["checkpoint_id"], application_roots=self.roots)["safe"])

    def test_valid_json_checkpoint_tampering_is_detected(self):
        value = self.prepare()["checkpoint"]
        path = checkpoint_path(self.project, value["checkpoint_id"], self.roots)
        for key, changed in (("objective", "different objective"), ("constraints", []), ("workflow_position", "invented")):
            broken = copy.deepcopy(value)
            broken[key] = changed
            path.write_text(json.dumps(broken))
            with self.subTest(field=key):
                self.assertFalse(resume(self.project, value["checkpoint_id"], application_roots=self.roots)["safe"])
