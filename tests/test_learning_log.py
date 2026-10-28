import tempfile
import unittest
from pathlib import Path
from uni_learn.contracts import ContractError
from uni_learn.learning_log import LogFullError, append, log_path, make_event, scan
from uni_learn.state import set_project


class LearningLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.roots = {"state": Path(self.temp.name) / "state"}

    def test_opt_in_cap_and_append_only(self):
        event = make_event(self.project, "t", ["proof"], "why", "try", "fix", "pass")
        self.assertEqual(append(self.project, event, application_roots=self.roots)["status"], "disabled")
        self.assertFalse(log_path(self.project, self.roots).exists())
        set_project(self.project, logging=True, log_cap_bytes=1000, application_roots=self.roots)
        append(self.project, event, application_roots=self.roots)
        before = log_path(self.project, self.roots).read_bytes()
        set_project(self.project, log_cap_bytes=len(before), application_roots=self.roots)
        with self.assertRaises(LogFullError):
            append(self.project, make_event(self.project, "t", [], "new", "", "", "fail"), application_roots=self.roots)
        self.assertEqual(before, log_path(self.project, self.roots).read_bytes())

    def test_interrupted_final_line_diagnosed_without_repair(self):
        set_project(self.project, logging=True, application_roots=self.roots)
        path = log_path(self.project, self.roots)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"broken":')
        with self.assertRaises(ContractError):
            append(self.project, make_event(self.project, "t", [], "x", "", "", ""), application_roots=self.roots)
        self.assertEqual(path.read_bytes(), b'{"broken":')

    def test_unicode_cap_counts_utf8_and_newline_exactly(self):
        from uni_learn.contracts import canonical
        event = make_event(self.project, "t", ["数学"], "为什么", "尝试", "改正", "pass")
        size = len(canonical(event)) + 1
        set_project(self.project, logging=True, log_cap_bytes=size - 1, application_roots=self.roots)
        with self.assertRaises(LogFullError):
            append(self.project, event, application_roots=self.roots)
        self.assertFalse(log_path(self.project, self.roots).exists())
        set_project(self.project, log_cap_bytes=size, application_roots=self.roots)
        append(self.project, event, application_roots=self.roots)
        self.assertEqual(log_path(self.project, self.roots).stat().st_size, size)

    def test_concurrent_writers_append_complete_unique_events(self):
        import concurrent.futures
        set_project(self.project, logging=True, application_roots=self.roots)
        def write(number):
            return append(self.project, make_event(self.project, "t", [], str(number), "", "", "pass"),
                          application_roots=self.roots)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(write, range(6)))
        records = list(scan(log_path(self.project, self.roots)))
        self.assertEqual(len(records), 6)
        self.assertEqual(len({row[2]["entry_id"] for row in records}), 6)
        self.assertTrue(all(result["status"] == "appended" for result in results))
