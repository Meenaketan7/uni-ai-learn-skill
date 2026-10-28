import unittest
import copy
from uni_learn.contracts import ContractError, validate_handoff, validate_state
from uni_learn.state import project_default


class ContractTests(unittest.TestCase):
    def test_unknown_state_field_rejected(self):
        state = project_default(".")
        state["extra"] = True
        with self.assertRaises(ContractError):
            validate_state(state, "project")

    def test_handoff_source_reference_checked(self):
        record = {"schema_version": 1, "task_id": "t", "task_class": "plan", "objective": "x",
                  "constraints": [], "findings": [{"source_ids": ["missing"]}], "sources": [],
                  "uncertainties": [], "failed_attempts": [], "next_decision": "review"}
        with self.assertRaises(ContractError):
            validate_handoff(record)

    def test_handoff_requires_reconstructable_evidence_and_typed_fields(self):
        from uni_learn.handoff import make
        valid = make("t", "research", "compare", ["offline"], [{"claim": "A", "source_ids": ["s"]}],
                     [{"id": "s", "path": "a.py", "revision": "123"}], [], [], "check")
        mutations = {"task_class": "bogus", "objective": None, "constraints": "offline",
                     "findings": [{}], "sources": [{"id": "s"}], "uncertainties": None,
                     "failed_attempts": False, "next_decision": 3}
        for key, changed in mutations.items():
            record = copy.deepcopy(valid)
            record[key] = changed
            with self.subTest(field=key), self.assertRaises(ContractError):
                validate_handoff(record)

    def test_wire_schema_covers_actual_state_fields(self):
        import tempfile
        from pathlib import Path
        from uni_learn.package import read_json
        from uni_learn.state import new_task
        schema = read_json("core/schemas/state.schema.json")["oneOf"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = new_task(root, "s", "t", "test schema", application_roots={"state": root / "state"})
            self.assertEqual(set(task), set(schema[1]["required"]))
        self.assertEqual(set(project_default(".")), set(schema[0]["required"]))
