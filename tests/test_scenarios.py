"""Deterministic scenario acceptance; no model-generated transcript is implied."""

import datetime as dt
import json
import re
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from uni_learn import compaction, learning, learning_log, recall, state


SCENARIOS = Path(__file__).parent / "scenarios"


def scenario(domain):
    return json.loads((SCENARIOS / (domain + ".json")).read_text("utf-8"))


class ScenarioLifecycleTests(unittest.TestCase):
    def exercise(self, domain):
        fixture = scenario(domain)
        learner, reviewer = fixture["learner"], fixture["reviewer_only"]
        with tempfile.TemporaryDirectory(prefix="uni scenario α ") as directory:
            base = Path(directory).resolve()
            project = base / "learner's project"
            project.mkdir()
            roots = {key: base / key for key in ("config", "data", "state", "cache")}
            session, task_id = "learner", fixture["scenario_id"]
            def task():
                return state.show_task(project, session, task_id, roots)
            def update(changes):
                return state.update_task(project, session, task_id, changes,
                                         expected_revision=task()["revision"], application_roots=roots)
            def move(command="learn", **options):
                return learning.dispatch(project, session, task_id, command,
                                         domains=[domain], application_roots=roots, **options)
            def event(kind, **options):
                current = task()
                changed = learning.transition(current, {"kind": kind, **options})
                update({key: value for key, value in changed.items() if value != current[key]})

            self.assertFalse(state.show_project(project, roots)["learning"])
            self.assertFalse(roots["state"].exists())
            state.new_task(project, session, task_id, learner["prompt"], application_roots=roots)
            update({"constraints": learner["constraints"], "acceptance_criteria": reviewer["checks"]
                    if domain != "coding" else reviewer["rubric"]})
            compact = move("learn-compact")
            self.assertFalse(compact["task"]["explicit_learning"])
            self.assertEqual(move(full_answer=True)["action"], "ask_attempt")
            self.assertFalse(state.show_project(project, roots)["learning"])
            self.assertEqual(move("learn-hint")["hint_level"], 0)
            event("attempt", text="I don't know")
            self.assertEqual(move(full_answer=True)["action"], "ask_prerequisite")
            event("attempt", text=reviewer["attempt"])
            self.assertEqual(move(revealing_action=True)["action"], "ask_prediction")
            event("prediction", text=reviewer["failed_hypothesis"])
            event("hypothesis", text=reviewer["failed_hypothesis"])
            revealed = move("learn-debug", revealing_action=True)
            self.assertNotEqual(revealed["action"], "ask_prediction")
            self.assertFalse(revealed["task"]["prediction_pending"])
            self.assertEqual(move(revealing_action=True)["action"], "ask_prediction")
            event("observe", hypothesis=reviewer["failed_hypothesis"],
                  observation=json.dumps(reviewer["worked_outcome"], ensure_ascii=False),
                  explanation="The supplied counterevidence refutes this hypothesis.", unresolved=False)
            event("misconception", text=reviewer["failed_hypothesis"])
            for level in (1, 2):
                result = move("learn-hint")
                self.assertEqual(result["hint_level"], level)
                self.assertEqual(result["action"], "hint")
                self.assertEqual([path for path in result["resources"] if "/domains/" in path],
                                 ["core/domains/" + domain + ".md"])
            self.assertFalse(learning_log.log_path(project, roots).exists())
            before = task()
            prepared = compaction.prepare({"project": str(project), "session_id": session,
                                            "task_id": task_id}, application_roots=roots)
            self.assertFalse(prepared["native_compaction_executed"])
            resumed = compaction.resume(project, prepared["checkpoint"]["checkpoint_id"], application_roots=roots)
            self.assertTrue(resumed["safe"], resumed)
            checkpoint = resumed["checkpoint"]
            for key in ("objective", "constraints", "acceptance_criteria", "failed_approaches"):
                self.assertEqual(checkpoint[key], before[key])
            for key in compaction.LEARNING_FIELDS:
                self.assertEqual(checkpoint["learning"][key], before[key])
            for level in (3, 3):
                result = move("learn-hint")
                self.assertEqual(result["hint_level"], level)
                self.assertEqual(result["action"], "hint")
                self.assertFalse(result["task"]["full_answer_requested"])
            self.assertEqual(move(full_answer=True)["action"], "full_answer")
            event("teach_back", text=reviewer["transfer_answer"])
            update({"intent": "teach-back"})
            reviewed = move("learn-review")
            self.assertEqual(reviewed["task"]["intent"], "teach-back")
            self.assertIn(reviewer["transfer_answer"], reviewed["task"]["difficulty_evidence"])
            self.assertIn("autopsy", move("learn-debug")["task"]["next_action"])

            clock = dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc)
            logged = learning_log.make_event(project, task_id, [domain], reviewer["transfer_question"],
                                             reviewer["attempt"], reviewer["transfer_answer"], "pass",
                                             at=clock.isoformat().replace("+00:00", "Z"))
            self.assertEqual(learning_log.append(project, logged, application_roots=roots)["status"], "disabled")
            self.assertFalse(learning_log.log_path(project, roots).exists())
            state.set_project(project, learning=True, logging=True, application_roots=roots)
            learning_log.append(project, logged, application_roots=roots)
            due = recall.recall(project, topic=domain, now=clock + dt.timedelta(days=2), application_roots=roots)
            self.assertEqual([item["entry_id"] for item in due["items"]], [logged["entry_id"]])
            log_bytes = learning_log.log_path(project, roots).read_bytes()
            state.set_project(project, logging=False, application_roots=roots)
            self.assertEqual(recall.recall(project, session=session, task=task_id,
                                          application_roots=roots)["status"], "session")
            self.assertEqual(learning_log.log_path(project, roots).read_bytes(), log_bytes)
            state.ship_task(project, session, task_id, application_roots=roots)
            event("attempt", text="Correction within the same objective")
            self.assertEqual(move("learn-hint")["action"], "ship")
            self.assertEqual(move("learn-compact")["action"], "compact")
            state.close_task(project, session, task_id, application_roots=roots)
            fresh = state.new_task(project, session, "next-objective", "Transfer to a new case", application_roots=roots)
            self.assertFalse(fresh["bypass"])
            self.assertFalse(fresh["explicit_learning"])
            self.assertTrue(state.show_project(project, roots)["learning"])

    def test_coding_lifecycle(self):
        self.exercise("coding")

    def test_proof_lifecycle(self):
        self.exercise("math-proof")

    def test_design_lifecycle(self):
        self.exercise("product-system")

    def test_research_lifecycle(self):
        self.exercise("research-projects")


class ScenarioOutcomeTests(unittest.TestCase):
    def test_code_fixture_reproduces_defect_and_repaired_invariants(self):
        fixture = scenario("coding")
        original, repaired = {}, {}
        exec(fixture["learner"]["material"], original)
        exec(fixture["reviewer_only"]["worked_outcome"]["reference_code"], repaired)
        broken = {"A": 10}
        with self.assertRaises(KeyError):
            original["transfer"](broken, "A", "B", 3)
        self.assertEqual(broken, {"A": 7})
        for case in fixture["reviewer_only"]["checks"]:
            balances = dict(case["balances"])
            before = dict(balances)
            with self.subTest(case=case):
                if case["exception"]:
                    with self.assertRaises({"KeyError": KeyError, "ValueError": ValueError}[case["exception"]]):
                        repaired["transfer"](balances, case["source"], case["target"], case["amount"])
                    self.assertEqual(balances, before)
                else:
                    self.assertIs(repaired["transfer"](balances, case["source"], case["target"], case["amount"]), balances)
                    self.assertEqual(sum(balances.values()), sum(before.values()))
                    self.assertTrue(all(value >= 0 for value in balances.values()))
                self.assertEqual(balances, case["expected_balances"])

    def test_proof_counterexample_and_repaired_cases(self):
        self.assertEqual(1 ** 2, (-1) ** 2)
        self.assertNotEqual(1, -1)
        self.assertEqual(1 + (-1), 0)
        values = {Fraction(n, d) for n in range(-12, 13) for d in range(1, 8)}
        for x in values:
            for y in (x, -x):
                self.assertEqual(x * x, y * y)
                self.assertTrue(x == y or x == -y)
                self.assertEqual(abs(x), abs(y))
                if x >= 0 and y >= 0:
                    if x + y == 0:
                        self.assertEqual((x, y), (0, 0))
                    else:
                        self.assertEqual((x * x - y * y) / (x + y), x - y)
                    self.assertEqual(x, y)

    def test_research_denominators_absolute_and_relative_changes(self):
        fixture = scenario("research-projects")
        sources = {item["id"]: item for item in fixture["learner"]["sources"]}
        rates = {}
        for source_id in ("F1", "F2"):
            pairs = re.findall(r"(\d[\d,]*)(?: missed visits)? among (\d[\d,]*)", sources[source_id]["data"])
            rates[source_id] = [Fraction(int(a.replace(",", "")), int(b.replace(",", ""))) for a, b in pairs]
        self.assertEqual(rates["F1"], [Fraction(12, 100), Fraction(20, 100)])
        self.assertEqual(rates["F2"], [Fraction(25, 100), Fraction(20, 100)])
        trial, audit = rates["F1"], rates["F2"]
        self.assertEqual(trial[1] - trial[0], Fraction(8, 100))
        self.assertEqual((trial[1] - trial[0]) / trial[1], Fraction(40, 100))
        self.assertEqual(audit[0] - audit[1], Fraction(5, 100))
        self.assertEqual((audit[0] - audit[1]) / audit[1], Fraction(25, 100))
        self.assertEqual(set(fixture["reviewer_only"]["worked_outcome"]["source_map"].values()), set(sources))
