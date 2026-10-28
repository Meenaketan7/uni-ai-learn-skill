import copy
import unittest
from uni_learn.contracts import ContractError
from uni_learn.routing import choose, orchestration
from tests.test_registry import NOW, catalogue


def request(**changes):
    return {"task_class": "discover", "host_profile": "fixture", "control": "subagent_selection",
            "input_tokens": 10000, "output_tokens": 1000, "required_tools": ["read"],
            "available_models": ["a", "b"], "availability_checked_at": NOW, "now": NOW, **changes}


class SelectionTests(unittest.TestCase):
    def test_least_known_comparable_cost(self):
        result = choose(request(), catalogue())
        self.assertEqual(result["selected"], "a")
        self.assertAlmostEqual(result["estimated_cost"], 0.011)
        self.assertIsNone(result["observed_execution"])
        self.assertIsNone(result["requested_execution"])

    def test_historical_availability_is_insufficient(self):
        data = catalogue()
        call = request()
        call.pop("availability_checked_at")
        self.assertEqual(choose(call, data)["action"], "keep_current_host_model")
        self.assertEqual(choose(request(availability_checked_at="2026-10-20T00:00:00Z"), data)["action"],
                         "keep_current_host_model")

    def test_capacity_includes_handoff(self):
        data = catalogue()
        data["models"]["a"]["limits"]["context_tokens"] = 11500
        result = choose(request(handoff_input_tokens=1000), data)
        self.assertEqual(result["selected"], "b")
        self.assertIn("capacity", result["excluded"]["a"])

    def test_unknown_prices_require_explicit_rank_or_pin(self):
        data = catalogue()
        data["models"]["a"]["pricing"]["input"] = None
        self.assertEqual(choose(request(), data)["action"], "keep_current_host_model")
        data["models"]["a"]["relative_cost_rank"] = 1
        data["models"]["b"]["relative_cost_rank"] = 2
        self.assertEqual(choose(request(), data)["selected"], "a")
        data["models"]["a"]["relative_cost_rank"] = None
        data["models"]["b"]["relative_cost_rank"] = None
        pinned = choose(request(override={"model_key": "a"}), data)
        self.assertEqual(pinned["selected"], "a")
        self.assertIsNone(pinned["estimated_cost"])

    def test_different_currencies_not_compared_as_equal_units(self):
        data = catalogue()
        data["models"]["a"]["pricing"]["currency"] = "INR"
        self.assertEqual(choose(request(), data)["action"], "keep_current_host_model")

    def test_latest_failed_qualification_excludes_cheap_model(self):
        data = catalogue()
        failed = copy.deepcopy(data["models"]["a"]["qualification"][0])
        failed.update(result="fail", checked_at="2026-10-22T00:00:01Z")
        data["models"]["a"]["qualification"].append(failed)
        self.assertEqual(choose(request(), data)["selected"], "b")

    def test_project_and_task_override_precedence(self):
        data = catalogue()
        data["overrides"]["global"]["discover"] = {"model_key": "a"}
        data["overrides"]["projects"]["project"] = {"discover": {"model_key": "b"}}
        self.assertEqual(choose(request(project_id="project"), data)["selected"], "b")
        self.assertEqual(choose(request(project_id="project", override={"model_key": "a"}), data)["selected"], "a")

    def test_unavailable_pin_and_unsupported_effort_report_fallback(self):
        data = catalogue()
        data["models"]["a"]["bindings"]["fixture"]["availability"] = "unavailable"
        result = choose(request(override={"model_key": "a"}), data)
        self.assertEqual(result["action"], "keep_current_host_model")
        self.assertIn("pinned", result["fallback_reason"])
        self.assertEqual(choose(request(reasoning="maximum"), catalogue())["action"], "keep_current_host_model")

    def test_current_model_can_avoid_uneconomic_handoff(self):
        data = catalogue()
        data["models"]["a"]["pricing"].update(input=0.99, output=0.99)
        data["models"]["b"]["pricing"].update(input=1, output=1)
        result = choose(request(current_model_key="b", handoff_input_tokens=2000), data)
        self.assertEqual(result["selected"], "b")
        self.assertEqual(result["action"], "keep_current_host_model")

    def test_no_switch_or_observation_is_invented(self):
        result = choose(request(control="current_model_only", observed_model="unproven"), catalogue())
        self.assertEqual(result["action"], "keep_current_host_model")
        self.assertIsNone(result["observed_execution_binding"])

    def test_bad_request_is_rejected(self):
        with self.assertRaises(ContractError):
            choose(request(input_tokens=-1), catalogue())
        with self.assertRaises(ContractError):
            choose(request(reasoning="invented"), catalogue())


    def test_fractional_or_equal_timestamp_failure_cannot_be_hidden_by_pass(self):
        for stamp in ("2026-10-22T00:00:00.100Z", NOW):
            data = catalogue()
            failed = copy.deepcopy(data["models"]["a"]["qualification"][0])
            failed.update(result="fail", checked_at=stamp)
            data["models"]["a"]["qualification"].append(failed)
            with self.subTest(timestamp=stamp):
                self.assertEqual(choose(request(), data)["selected"], "b")

    def test_explicit_qualified_pin_establishes_tier_without_default_conflict(self):
        data = catalogue()
        data["task_tiers"]["discover"] = "strong"
        self.assertEqual(choose(request(override={"model_key": "a"}), data)["selected"], "a")
        self.assertEqual(choose(request(override={"model_key": "a", "tier": "strong"}), data)["action"], "keep_current_host_model")


class OrchestrationTests(unittest.TestCase):
    def test_approval_diff_and_check_requirements(self):
        self.assertEqual(orchestration("implement")["stage"], "await_plan")
        self.assertEqual(orchestration("verify", approved_plan=True)["stage"], "await_review_evidence")
        result = orchestration("verify", approved_plan=True, diff="change", checks=["pass"], high_impact=True)
        self.assertEqual(result["stages"][-1]["tier"], "frontier")
        self.assertFalse(result["execution_confirmed"])
        self.assertTrue(result["single_model_fallback"])

    def test_failure_escalates_without_confirming_execution(self):
        result = orchestration("diagnose", failure=True, high_impact=True)
        self.assertTrue(result["escalate"])
        self.assertEqual(result["tier"], "frontier")
        self.assertFalse(result["execution_confirmed"])
