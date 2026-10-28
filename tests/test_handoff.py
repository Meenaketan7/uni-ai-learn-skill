import unittest
from uni_learn.handoff import make


class HandoffTests(unittest.TestCase):
    def test_sourced_finding_survives(self):
        result = make("t", "research", "compare", ["offline"], [{"claim": "A", "source_ids": ["s"]}],
                      [{"id": "s", "path": "a.py", "revision": "123"}], ["unknown cost"], ["old failed"], "check source")
        self.assertEqual(result["findings"][0]["source_ids"], ["s"])
        self.assertEqual(result["failed_attempts"], ["old failed"])

    def test_size_report_keeps_necessary_over_budget_evidence(self):
        from uni_learn.handoff import report
        record = make("t", "research", "compare", ["x" * 900], [{"claim": "A", "source_ids": ["s"]}],
                      [{"id": "s", "path": "a.py", "revision": "123"}], [], [], "check")
        result = report(record, exact_counter=lambda _: 700)
        self.assertTrue(result["over_target"])
        self.assertEqual(result["handoff"]["constraints"], ["x" * 900])
