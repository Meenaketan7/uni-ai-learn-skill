import datetime as dt
import tempfile
import unittest
from pathlib import Path
from uni_learn.learning_log import append, make_event
from uni_learn.recall import next_due, recall
from uni_learn.state import new_task, set_project, update_task


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.roots = {"state": Path(self.temp.name) / "state", "cache": Path(self.temp.name) / "cache"}

    def test_session_recall_without_log(self):
        new_task(self.project, "s", "t", "test", application_roots=self.roots)
        update_task(self.project, "s", "t", {"attempts": ["first"]}, application_roots=self.roots)
        result = recall(self.project, session="s", task="t", application_roots=self.roots)
        self.assertEqual(result["status"], "session")
        self.assertEqual(result["items"][0]["attempts"], ["first"])

    def test_due_filter_and_limit(self):
        set_project(self.project, logging=True, application_roots=self.roots)
        past = "2020-01-01T00:00:00Z"
        future = "2999-01-01T00:00:00Z"
        for i in range(7):
            append(self.project, make_event(self.project, "t", ["topic"], str(i), "", "", "pass", due_at=past), application_roots=self.roots)
        append(self.project, make_event(self.project, "t", ["topic"], "future", "", "", "pass", due_at=future), application_roots=self.roots)
        result = recall(self.project, topic="topic", application_roots=self.roots)
        self.assertEqual(len(result["items"]), 5)
        self.assertNotIn("future", [e["prompt"] for e in result["items"]])

    def test_review_intervals(self):
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        self.assertEqual(next_due(True, 0, now), "2026-01-02T00:00:00Z")
        self.assertEqual(next_due(True, 4, now), "2026-01-31T00:00:00Z")
        self.assertEqual(next_due(False, 4, now), "2026-01-02T00:00:00Z")

    def test_latest_review_suppresses_old_due_event_and_failure_resets(self):
        from uni_learn.recall import review_event
        set_project(self.project, logging=True, application_roots=self.roots)
        clock = dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc)
        original = make_event(self.project, "t", [], "question", "attempt", "correction", "pass", due_at="2020-01-01T00:00:00Z")
        append(self.project, original, application_roots=self.roots)
        review = review_event(self.project, original, success=True, attempt="recalled", now=clock)
        self.assertEqual(review["due_at"], "2026-10-25T00:00:00Z")
        append(self.project, review, application_roots=self.roots)
        self.assertEqual(recall(self.project, now=clock, application_roots=self.roots)["items"], [])
        failed = review_event(self.project, review, success=False, attempt="forgot", now=clock)
        self.assertEqual(failed["due_at"], "2026-10-23T00:00:00Z")
        self.assertEqual(failed["success_streak"], 0)

    def test_deleted_or_corrupt_cache_can_be_rebuilt_and_stale_offset_detected(self):
        import sqlite3
        from contextlib import closing
        from uni_learn.contracts import ContractError
        from uni_learn.recall import index_path, rebuild
        set_project(self.project, logging=True, application_roots=self.roots)
        event = make_event(self.project, "t", [], "question", "", "", "", due_at="2020-01-01T00:00:00Z")
        append(self.project, event, application_roots=self.roots)
        recall(self.project, application_roots=self.roots)
        index = index_path(self.project, self.roots)
        with closing(sqlite3.connect(index)) as database:
            database.execute("UPDATE events SET offset=999999")
            database.commit()
        with self.assertRaises(ContractError):
            recall(self.project, application_roots=self.roots)
        rebuild(self.project, application_roots=self.roots)
        self.assertEqual(recall(self.project, application_roots=self.roots)["items"][0]["entry_id"], event["entry_id"])
        index.unlink()
        self.assertEqual(len(recall(self.project, application_roots=self.roots)["items"]), 1)

    def test_due_order_uses_instants_including_fractional_seconds(self):
        set_project(self.project, logging=True, application_roots=self.roots)
        for prompt, due in (("fraction", "2020-01-01T00:00:00.100Z"), ("whole", "2020-01-01T00:00:00Z")):
            append(self.project, make_event(self.project, "t", [], prompt, "", "", "", due_at=due), application_roots=self.roots)
        self.assertEqual([item["prompt"] for item in recall(self.project, application_roots=self.roots)["items"]],
                         ["whole", "fraction"])

    def test_logging_off_topic_recall_remains_available(self):
        result = recall(self.project, topic="invariants", application_roots=self.roots)
        self.assertEqual(result["status"], "topic")
        self.assertEqual(result["items"][0]["topic"], "invariants")
