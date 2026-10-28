import tempfile
import unittest
from pathlib import Path
from uni_learn.contracts import ContractError
from uni_learn.learning import dispatch, domain_resources, transition
from uni_learn.state import new_task, set_project, ship_task, show_project, show_task, update_task


class LearningFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.roots = {"state": Path(self.temp.name) / "state"}
        new_task(self.project, "s", "t", "prove claim", application_roots=self.roots)

    def dispatch(self, command, **kwargs):
        return dispatch(self.project, "s", "t", command, application_roots=self.roots, **kwargs)


class ActivationTests(LearningFixture, unittest.TestCase):
    def test_explicit_move_does_not_persist_project_on(self):
        result = self.dispatch("learn")
        self.assertTrue(result["task"]["explicit_learning"])
        self.assertFalse(show_project(self.project, self.roots)["learning"])

    def test_on_off_and_no_objective_do_not_require_a_task(self):
        result = dispatch(self.project, None, None, "learn", "", application_roots=self.roots)
        self.assertEqual(result["action"], "ask_objective")
        self.assertFalse(show_project(self.project, self.roots)["learning"])
        dispatch(self.project, None, None, "learn", "on", application_roots=self.roots)
        self.assertTrue(show_project(self.project, self.roots)["learning"])
        result = dispatch(self.project, None, None, "learn", "off", application_roots=self.roots)
        self.assertTrue(result["fresh_session_recommended"])

    def test_compact_preserves_mode_and_runs_while_shipping(self):
        result = self.dispatch("learn-compact")
        self.assertFalse(result["task"]["explicit_learning"])
        ship_task(self.project, "s", "t", application_roots=self.roots)
        self.assertEqual(self.dispatch("learn-compact")["action"], "compact")
        self.assertEqual(self.dispatch("learn-hint")["action"], "ship")

    def test_generic_alias_is_rejected(self):
        with self.assertRaises(ContractError):
            self.dispatch("hint")


class WorkflowTests(LearningFixture, unittest.TestCase):
    def test_hint_ladder_requires_attempt_and_never_auto_answers(self):
        first = self.dispatch("learn-hint", domains=["math-proof"])
        self.assertEqual(first["action"], "ask_attempt")
        self.assertEqual(first["hint_level"], 0)
        update_task(self.project, "s", "t", {"attempts": ["try zero"]}, application_roots=self.roots)
        self.assertEqual([self.dispatch("learn-hint")["hint_level"] for _ in range(4)], [1, 2, 3, 3])

    def test_answer_requires_attempt_and_explicit_request(self):
        self.assertEqual(self.dispatch("learn", full_answer=True)["action"], "ask_attempt")
        update_task(self.project, "s", "t", {"attempts": ["I don't know"]}, application_roots=self.roots)
        self.assertEqual(self.dispatch("learn", full_answer=True)["action"], "ask_prerequisite")
        update_task(self.project, "s", "t", {"attempts": ["I think the denominator can be zero"]}, application_roots=self.roots)
        self.assertNotEqual(self.dispatch("learn")["action"], "full_answer")
        self.assertEqual(self.dispatch("learn", full_answer=True)["action"], "full_answer")

    def test_prediction_is_consumed_for_each_revealing_action(self):
        self.assertEqual(self.dispatch("learn", revealing_action=True)["action"], "ask_prediction")
        update_task(self.project, "s", "t", {"predictions": ["zero will fail"]}, application_roots=self.roots)
        self.assertNotEqual(self.dispatch("learn", revealing_action=True)["action"], "ask_prediction")
        self.assertEqual(self.dispatch("learn", revealing_action=True)["action"], "ask_prediction")

    def test_only_selected_domain_loaded(self):
        self.assertEqual(domain_resources(["coding", "coding", "math-proof"]),
                         ["core/domains/coding.md", "core/domains/math-proof.md"])
        self.assertEqual([p for p in self.dispatch("learn", domains=["coding"])["resources"] if "/domains/" in p],
                         ["core/domains/coding.md"])


class DebugTests(LearningFixture, unittest.TestCase):
    def test_hypothesis_observation_and_failed_belief_are_retained(self):
        task = show_task(self.project, "s", "t", self.roots)
        task = transition(task, {"kind": "hypothesis", "text": "the write is atomic"})
        task = transition(task, {"kind": "prediction", "text": "a rejected call leaves state unchanged"})
        task = transition(task, {"kind": "reveal"})
        task = transition(task, {"kind": "observe", "hypothesis": "write is atomic",
                                "observation": "balance dropped on rejection", "explanation": "mutation preceded lookup",
                                "unresolved": False})
        self.assertEqual(task["workflow_position"], "revise-belief")
        self.assertFalse(task["failed_approaches"][0]["unresolved"])
        self.assertEqual(task["hypotheses"], ["the write is atomic"])
        with self.assertRaises(ContractError):
            transition(task, {"kind": "reveal"})

    def test_resolved_incident_can_start_at_autopsy(self):
        result = self.dispatch("learn-debug", argument="autopsy: I already fixed the failed lookup")
        self.assertIn("autopsy", result["task"]["next_action"])


class ReviewDesignTests(LearningFixture, unittest.TestCase):
    def test_review_intents_stay_distinct(self):
        update_task(self.project, "s", "t", {"intent": "teach-back"}, application_roots=self.roots)
        result = self.dispatch("learn-review")
        self.assertEqual(result["task"]["intent"], "teach-back")
        self.assertIn("explanation", result["task"]["next_action"])
        update_task(self.project, "s", "t", {"intent": "artifact-critique"}, application_roots=self.roots)
        self.assertIn("self-assessment", self.dispatch("learn-review")["task"]["next_action"])

    def test_contract_and_tradeoff_design_stay_distinct(self):
        update_task(self.project, "s", "t", {"intent": "contract-design"}, application_roots=self.roots)
        self.assertIn("acceptance criteria", self.dispatch("learn-design")["task"]["next_action"])
        update_task(self.project, "s", "t", {"intent": "tradeoff-design"}, application_roots=self.roots)
        self.assertIn("alternatives", self.dispatch("learn-design")["task"]["next_action"])

    def test_transfer_records_observed_evidence_without_ability_score(self):
        task = show_task(self.project, "s", "t", self.roots)
        task = transition(task, {"kind": "teach_back", "text": "correctly identified a different failing lookup"})
        self.assertEqual(task["workflow_position"], "transfer")
        self.assertEqual(len(task["difficulty_evidence"]), 1)
