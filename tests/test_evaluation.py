"""Offline evidence comparisons never imply that a model was executed."""

import copy
import unittest

from tests.evaluation import EvidenceError, USAGE, compare, validate


def record(kind="baseline", **changes):
    usage = {key: {"value": None, "source": "unavailable"} for key in USAGE}
    usage["input_tokens"] = {"value": 100, "source": "observed"}
    usage["output_tokens"] = {"value": 50, "source": "observed"}
    value = {
        "schema_version": 1, "scenario_id": "S-CODE", "scenario_sha256": "a" * 64,
        "rubric_sha256": "b" * 64, "task_class": "teach", "run_kind": kind,
        "checked_at": "2026-10-22T00:00:00Z", "package_sha256": "c" * 64,
        "host_profile": "fixture", "model_binding": "synthetic-model", "reasoning": "routine",
        "tools": ["read"], "cache_conditions": "cold, no cache", "evidence": "fixture transcript 1",
        "criteria": {"final_answer": "pass", "attempt_gate": "pass", "teach_back": "pass"},
        "critical_criteria": ["final_answer", "attempt_gate"], "usage": usage,
        "cost": None, "currency": None, "cost_source": "unavailable", "synthetic": True,
    }
    value.update(changes)
    return value


class EvidenceTests(unittest.TestCase):
    def test_complete_pair_reports_quality_and_nullable_usage(self):
        candidate = record("efficiency", evidence="fixture transcript 2")
        candidate["usage"]["input_tokens"] = {"value": 70, "source": "observed"}
        result = compare(record(), candidate)
        self.assertEqual(result["quality"], "pass")
        self.assertEqual(result["usage_delta"]["input_tokens"], -30)
        self.assertEqual(result["usage_sources"]["input_tokens"],
                         {"baseline": "observed", "candidate": "observed"})
        self.assertIsNone(result["usage_delta"]["latency_ms"])
        self.assertIsNone(result["cost_delta"])
        self.assertTrue(result["synthetic"])

    def test_quality_regression_rejected_even_if_cheaper(self):
        candidate = record("efficiency")
        candidate["criteria"]["attempt_gate"] = "fail"
        candidate["usage"]["input_tokens"]["value"] = 1
        result = compare(record(), candidate)
        self.assertEqual(result["quality"], "fail")
        self.assertEqual(result["critical_failures"], ["attempt_gate"])
        self.assertEqual(result["regressions"], ["attempt_gate"])

    def test_unverified_is_preserved(self):
        candidate = record("efficiency")
        candidate["criteria"]["teach_back"] = "unverified"
        result = compare(record(), candidate)
        self.assertEqual(result["quality"], "fail")
        self.assertEqual(result["unverified_criteria"], ["teach_back"])

    def test_mismatched_efficiency_conditions_are_refused(self):
        for key, changed in (("scenario_sha256", "d" * 64), ("rubric_sha256", "d" * 64),
                             ("model_binding", "other-model"), ("reasoning", "deep"),
                             ("tools", []), ("cache_conditions", "warm"),
                             ("package_sha256", "d" * 64)):
            with self.subTest(key=key), self.assertRaises(EvidenceError):
                compare(record(), record("efficiency", **{key: changed}))

    def test_routing_is_separate_and_can_change_model_binding(self):
        result = compare(record(), record("routing", model_binding="another-model", reasoning="deep"))
        self.assertEqual(result["comparison"], "routing")
        with self.assertRaises(EvidenceError):
            compare(record("routing"), record("efficiency"))

    def test_unknown_and_malformed_numbers_cannot_be_zero_savings(self):
        invalid = [True, -1, 1.5, float("nan"), "0"]
        for value in invalid:
            with self.subTest(value=value):
                item = record()
                item["usage"]["input_tokens"]["value"] = value
                with self.assertRaises(EvidenceError):
                    validate(item)
        for value in (True, -1, float("nan"), float("inf")):
            with self.subTest(cost=value):
                item = record(cost=value, currency="USD", cost_source="observed")
                with self.assertRaises(EvidenceError):
                    validate(item)

    def test_provenance_and_synthetic_boundary_are_enforced(self):
        item = record()
        item["usage"]["latency_ms"] = {"value": 0, "source": "unavailable"}
        with self.assertRaises(EvidenceError):
            validate(item)
        with self.assertRaises(EvidenceError):
            compare(record(), record("efficiency", synthetic=False))
        item = record()
        item["cost"] = 0
        with self.assertRaises(EvidenceError):
            validate(item)

    def test_estimated_usage_is_labeled_on_a_numeric_delta(self):
        candidate = record("efficiency")
        candidate["usage"]["input_tokens"] = {"value": 80, "source": "estimated"}
        result = compare(record(), candidate)
        self.assertEqual(result["usage_delta"]["input_tokens"], -20)
        self.assertEqual(result["usage_sources"]["input_tokens"],
                         {"baseline": "observed", "candidate": "estimated"})

    def test_evidence_hash_and_criteria_are_required(self):
        item = record(rubric_sha256="unknown")
        with self.assertRaises(EvidenceError):
            validate(item)
        item = copy.deepcopy(record())
        item["critical_criteria"] = ["missing"]
        with self.assertRaises(EvidenceError):
            validate(item)
        item = copy.deepcopy(record())
        item["critical_criteria"] = [["final_answer"]]
        with self.assertRaises(EvidenceError):
            validate(item)
        item = copy.deepcopy(record())
        item["usage"]["input_tokens"]["source"] = []
        with self.assertRaises(EvidenceError):
            validate(item)


if __name__ == "__main__":
    unittest.main()
