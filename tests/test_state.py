import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uni_learn.state import (ConflictError, close_task, locked, new_task, project_path,
                             set_project, ship_task, show_project, show_task, update_task)


class StateFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.roots = {"state": self.root / "state"}


class ProjectSettingsTests(StateFixture, unittest.TestCase):
    def test_absent_read_is_read_only_and_toggle_shared(self):
        before = list(self.root.rglob("*"))
        self.assertFalse(show_project(self.project, self.roots)["learning"])
        self.assertEqual(before, list(self.root.rglob("*")))
        changed = set_project(self.project, learning=True, application_roots=self.roots)
        self.assertTrue(show_project(self.project, self.roots)["learning"])
        with self.assertRaises(ConflictError):
            set_project(self.project, learning=False, expected_revision=0, application_roots=self.roots)
        self.assertEqual(changed["revision"], 1)

    def test_concurrent_revision_prevents_overwrite(self):
        def change(_):
            try:
                set_project(self.project, learning=True, expected_revision=0, application_roots=self.roots)
                return "saved"
            except ConflictError:
                return "conflict"
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(change, range(6)))
        self.assertEqual(results.count("saved"), 1)
        self.assertEqual(show_project(self.project, self.roots)["revision"], 1)

    def test_interrupted_atomic_write_keeps_previous_record(self):
        set_project(self.project, learning=True, application_roots=self.roots)
        path = project_path(self.project, self.roots)
        before = path.read_bytes()
        with patch("uni_learn.state.os.replace", side_effect=OSError("injected")), self.assertRaises(OSError):
            set_project(self.project, learning=False, application_roots=self.roots)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(show_project(self.project, self.roots)["learning"])

    def test_busy_lock_is_not_deleted_by_age(self):
        path = self.root / "busy.lock"
        with locked(path):
            with self.assertRaises(ConflictError), locked(path, timeout=0.03):
                self.fail("busy lock was stolen")
            self.assertTrue(path.exists())
        self.assertFalse(path.exists())


class TaskStateTests(StateFixture, unittest.TestCase):
    def test_sessions_isolated_and_shipping_task_scoped(self):
        new_task(self.project, "s1", "t1", "build", application_roots=self.roots)
        new_task(self.project, "s2", "t2", "prove", application_roots=self.roots)
        ship_task(self.project, "s1", "t1", application_roots=self.roots)
        self.assertTrue(show_task(self.project, "s1", "t1", self.roots)["bypass"])
        self.assertFalse(show_task(self.project, "s2", "t2", self.roots)["bypass"])
        close_task(self.project, "s1", "t1", application_roots=self.roots)
        self.assertFalse(show_task(self.project, "s1", "t1", self.roots)["bypass"])

    def test_revision_and_new_objective_requirements(self):
        new_task(self.project, "s", "t", "test", application_roots=self.roots)
        update_task(self.project, "s", "t", {"attempts": ["first"]}, application_roots=self.roots)
        with self.assertRaises(ConflictError):
            update_task(self.project, "s", "t", {"attempts": ["lost"]}, expected_revision=0, application_roots=self.roots)
        with self.assertRaises(ConflictError):
            update_task(self.project, "s", "t", {"objective": "another task"}, application_roots=self.roots)
        self.assertEqual(show_task(self.project, "s", "t", self.roots)["attempts"], ["first"])
