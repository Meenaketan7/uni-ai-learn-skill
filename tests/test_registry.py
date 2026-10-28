import copy
import unittest
from uni_learn.contracts import ContractError
from uni_learn.package import read_json
from uni_learn.routing import validate_registry

NOW = "2026-10-22T00:00:00Z"


def model(price=1, tier="efficient"):
    return {
        "tier": tier, "qualified_tasks": ["discover"],
        "bindings": {"fixture": {"native_id": "test-only-model", "provider": "fixture",
                                 "availability": "available", "checked_at": NOW, "source": "synthetic fixture"}},
        "limits": {"context_tokens": 100000, "max_output_tokens": 10000, "tools": ["read"], "source": "synthetic fixture"},
        "reasoning": {"control": "configurable", "levels_by_host": {"fixture": {"routine": "low", "deliberate": "medium"}}},
        "pricing": {"currency": "USD", "per_tokens": 1000000, "input": price, "output": price,
                    "cached_input": None, "source": "synthetic fixture", "checked_at": NOW},
        "relative_cost_rank": None,
        "qualification": [{"task_class": "discover", "host_profile": "fixture", "scenario": "test-only",
                           "result": "pass", "evidence": "synthetic fixture, not real qualification", "checked_at": NOW}],
        "verified_at": NOW,
    }


def catalogue():
    data = read_json("registry/models.json")
    data["models"] = {"a": model(1), "b": model(2)}
    return data


class RegistryTests(unittest.TestCase):
    def test_initial_catalogue_is_empty(self):
        data = validate_registry(read_json("registry/models.json"))
        self.assertEqual(data["models"], {})

    def test_complete_synthetic_record_validates(self):
        self.assertEqual(set(validate_registry(catalogue())["models"]), {"a", "b"})

    def test_cycles_unknown_prices_and_override_references(self):
        data = catalogue()
        data["tier_fallbacks"]["frontier"] = ["efficient"]
        with self.assertRaises(ContractError):
            validate_registry(data)
        data = catalogue()
        data["models"]["a"]["pricing"]["input"] = None
        self.assertIsNone(validate_registry(data)["models"]["a"]["pricing"]["input"])
        data["overrides"]["global"] = {"discover": {"model_key": "absent"}}
        with self.assertRaises(ContractError):
            validate_registry(data)

    def test_bad_limits_prices_effort_and_provenance_rejected(self):
        mutations = [
            lambda model: model["limits"].update(context_tokens=-1),
            lambda model: model["pricing"].update(per_tokens=1000),
            lambda model: model["pricing"].update(input=True),
            lambda model: model["pricing"].update(output=float("nan")),
            lambda model: model["reasoning"]["levels_by_host"]["fixture"].update(imaginary="x"),
            lambda model: model["bindings"]["fixture"].update(native_id=""),
            lambda model: model["qualification"][0].update(evidence=""),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                data = catalogue()
                mutate(data["models"]["a"])
                with self.assertRaises(ContractError):
                    validate_registry(data)
