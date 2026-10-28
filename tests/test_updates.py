"""Verified remote runtime updates and opt in notifications."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import subprocess
import threading
import time
import tempfile
import unittest
import zipfile
from pathlib import Path

from uni_learn import __version__, install, package, state, updates


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="uni updates ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.project = self.base / "project"
        self.project.mkdir()
        self.roots = {key: self.base / key / "uni-learn"
                      for key in ("config", "data", "state", "cache")}
        for path in self.roots.values():
            path.mkdir(parents=True)

    def release(self, *, version=None, commit="a" * 40, runtime=None):
        version = version or __version__
        runtime = package.build_bytes() if runtime is None else runtime
        manifest = json.dumps({
            "schema_version": 1, "repository": updates.REPOSITORY,
            "commit": commit, "version": version,
            "assets": {"uni-learn.pyz": {"bytes": len(runtime),
                                           "sha256": hashlib.sha256(runtime).hexdigest()}},
        }).encode()
        tag = "uni-learn-build-" + commit
        release = {
            "id": 10, "tag_name": tag, "body": updates.MARKER + "\n",
            "draft": False, "prerelease": False, "published_at": "2026-01-01T00:00:00Z",
            "assets": [{"name": name, "state": "uploaded",
                        "browser_download_url": f"https://github.com/{updates.REPOSITORY}/releases/download/{tag}/{name}"}
                       for name in ("build.json", "uni-learn.pyz")],
        }
        blobs = {"build.json": manifest, "uni-learn.pyz": runtime}

        def fetch(url, limit):
            if url.endswith("/releases?per_page=100&page=1"):
                return json.dumps([release]).encode()
            for name, blob in blobs.items():
                if url.endswith("/" + name):
                    return blob
            raise AssertionError("unexpected URL: " + url)
        return fetch, runtime

    def test_disabled_notifications_do_not_touch_network_or_cache(self):
        calls = []
        result = updates.notification(project=str(self.project), application_roots=self.roots,
                                      fetch=lambda *args: calls.append(args))
        self.assertIsNone(result)
        self.assertEqual(calls, [])
        self.assertFalse((self.roots["cache"] / "update-check.json").exists())

    def test_opt_in_notification_caches_daily_and_explicit_check_reports_same_build(self):
        receipt = install.setup("codex", scope="user", application_roots=self.roots,
                                user_home=self.home, codex_home=self.home / ".codex")["receipt"]
        fetch, _ = self.release()
        calls = []

        def counted(url, limit):
            calls.append(url)
            return fetch(url, limit)

        updates.configure(True, application_roots=self.roots)
        self.assertIsNone(updates.notification(project=str(self.project), application_roots=self.roots,
                                               fetch=counted, now=1000))
        first_count = len(calls)
        self.assertGreater(first_count, 0)
        self.assertIsNone(updates.notification(project=str(self.project), application_roots=self.roots,
                                               fetch=counted, now=1001))
        self.assertEqual(len(calls), first_count)
        checked = updates.check_available(receipt, application_roots=self.roots, fetch=fetch, now=1002)
        self.assertEqual(checked["status"], "current")
        self.assertEqual(updates.status(application_roots=self.roots)["enabled"], True)

    def test_update_runs_verified_zip_and_preserves_settings_instructions_and_other_skills(self):
        codex = self.home / ".codex"
        codex.mkdir()
        agents = codex / "AGENTS.md"
        agents.write_text("# Personal instructions\n\nKeep my review style.\n", encoding="utf-8")
        unrelated = self.home / ".agents" / "skills" / "other" / "SKILL.md"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("name: other\n", encoding="utf-8")
        receipt = install.setup("codex", scope="user", application_roots=self.roots,
                                user_home=self.home, codex_home=codex)["receipt"]
        agents.write_text(agents.read_text(encoding="utf-8") + "\nMy later note stays.\n", encoding="utf-8")
        state.set_project(self.project, learning=True, application_roots=self.roots)
        state.new_task(self.project, "session-one", "task-one", "understand the project",
                       application_roots=self.roots)
        task_before = state.show_task(self.project, "session-one", "task-one", self.roots)

        runtime = package.build_bytes()
        repacked = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(runtime)) as source, zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as target:
            target.comment = b"verified update test build"
            for name in source.namelist():
                target.writestr(name, source.read(name))
        fetch, _ = self.release(runtime=repacked.getvalue())
        updates.configure(True, application_roots=self.roots)
        notice = updates.notification(project=str(self.project), application_roots=self.roots, fetch=fetch, now=2000)
        self.assertEqual(notice["status"], "available")
        command_args = notice["command_argv"]
        self.assertEqual(command_args[1:3], ["-m", "uni_learn"])
        self.assertEqual(command_args[3:6], ["update", "--receipt", receipt])
        if os.name != "nt":
            self.assertEqual(shlex.split(notice["command"]), command_args)
        before = json.loads(Path(receipt).read_text("utf-8"))
        before_sha = next(item["sha256"] for item in before["owned"] if item["path"].endswith("uni-learn.pyz"))
        def outside_checkout(args, **kwargs):
            return subprocess.run(args, cwd=self.base, **kwargs)

        result = updates.update(receipt, application_roots=self.roots, fetch=fetch,
                                run=outside_checkout)

        self.assertEqual(result["status"], "updated")
        after = json.loads(Path(receipt).read_text("utf-8"))
        after_sha = next(item["sha256"] for item in after["owned"] if item["path"].endswith("uni-learn.pyz"))
        self.assertNotEqual(before_sha, after_sha)
        self.assertEqual(after_sha, hashlib.sha256(repacked.getvalue()).hexdigest())
        self.assertIn("My later note stays.", agents.read_text(encoding="utf-8"))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "name: other\n")
        self.assertTrue(state.show_project(self.project, self.roots)["learning"])
        self.assertEqual(state.show_task(self.project, "session-one", "task-one", self.roots), task_before)

    def test_runtime_zipapp_contains_shared_standalone_release_client(self):
        with zipfile.ZipFile(io.BytesIO(package.build_bytes())) as archive:
            self.assertIn("install.py", archive.namelist())

    def test_latest_refuses_semver_downgrade_before_setup(self):
        receipt = install.setup("codex", scope="user", application_roots=self.roots,
                                user_home=self.home, codex_home=self.home / ".codex")["receipt"]
        record = json.loads(Path(receipt).read_text("utf-8"))
        archive_entry = next(item for item in record["owned"] if item["path"].endswith("uni-learn.pyz"))
        old_path = Path(archive_entry["path"])
        future_path = Path(record["data_root"]) / "releases" / "1.0.0" / record["install_id"] / "uni-learn.pyz"
        future_path.parent.mkdir(parents=True)
        old_path.rename(future_path)
        archive_entry["path"] = str(future_path)
        record["runtime_version"] = "1.0.0"
        Path(receipt).write_text(json.dumps(record), encoding="utf-8")
        fetch, _ = self.release()
        with self.assertRaisesRegex(ValueError, "older"):
            updates.update(receipt, application_roots=self.roots, fetch=fetch,
                           run=lambda *args, **kwargs: self.fail("setup must not run"))

    def test_ambiguous_receipts_require_explicit_selection(self):
        receipt_a = install.setup("codex", scope="user", application_roots=self.roots,
                                  user_home=self.home, codex_home=self.home / ".codex")["receipt"]
        receipt_b = install.setup("cursor", str(self.project), application_roots=self.roots)["receipt"]
        self.assertNotEqual(receipt_a, receipt_b)
        with self.assertRaisesRegex(Exception, "select one"):
            updates.check_available(application_roots=self.roots, fetch=lambda *a: self.fail("must fail before network"))
        updates.configure(True, receipt=receipt_a, application_roots=self.roots)
        self.assertEqual(updates.preferences(application_roots=self.roots)["receipt"], receipt_a)
        fetch, _ = self.release()
        self.assertEqual(updates.check_available(application_roots=self.roots, fetch=fetch)["status"], "current")
        self.assertEqual(updates.update(application_roots=self.roots, fetch=fetch)["status"], "current")

    def test_failed_notice_is_cached_and_bad_preferences_fail_soft(self):
        install.setup("codex", scope="user", application_roots=self.roots,
                      user_home=self.home, codex_home=self.home / ".codex")
        updates.configure(True, application_roots=self.roots)
        release = {"id": 10, "tag_name": "uni-learn-build-" + "a" * 40,
                   "body": updates.MARKER + "\n", "draft": False, "prerelease": False,
                   "published_at": "2026-01-01T00:00:00Z", "assets": [None]}
        calls = []

        def malformed_release(url, limit):
            calls.append(url)
            return json.dumps([release]).encode()

        cache = self.roots["cache"] / "update-check.json"
        cache.write_text(json.dumps({"schema_version": 1, "checked_at": 9_999_999,
                                     "repository": updates.REPOSITORY, "channels": None}), encoding="utf-8")
        self.assertIsNone(updates.notification(project=str(self.project), application_roots=self.roots,
                                               fetch=malformed_release, now=1000))
        self.assertEqual(len(calls), 1)
        self.assertIsNone(updates.notification(project=str(self.project), application_roots=self.roots,
                                               fetch=lambda *args: self.fail("negative result should be cached"),
                                               now=1001))
        preferences = self.roots["config"] / updates.CONFIG_NAME
        preferences.write_text("{broken", encoding="utf-8")
        self.assertIsNone(updates.notification(project=str(self.project), application_roots=self.roots,
                                               fetch=lambda *args: self.fail("bad preferences must stay offline"),
                                               now=1002))

    def test_update_rejects_malformed_child_setup_output_and_reports_child_error(self):
        receipt = install.setup("codex", scope="user", application_roots=self.roots,
                                user_home=self.home, codex_home=self.home / ".codex")["receipt"]
        runtime = package.build_bytes()
        repacked = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(runtime)) as source, zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as target:
            target.comment = b"different build"
            for name in source.namelist():
                target.writestr(name, source.read(name))
        fetch, _ = self.release(runtime=repacked.getvalue())
        with self.assertRaisesRegex(RuntimeError, "invalid or incomplete result"):
            updates.update(receipt, application_roots=self.roots, fetch=fetch,
                           run=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "{}", ""))

        def failed(*args, **kwargs):
            raise subprocess.CalledProcessError(2, args[0], output="", stderr="setup conflict")

        with self.assertRaisesRegex(RuntimeError, "setup conflict"):
            updates.update(receipt, application_roots=self.roots, fetch=fetch, run=failed)

        reported_ready = json.dumps({"status": "ready", "receipt": receipt,
                                     "profile": "codex", "scope": "user"})
        with self.assertRaisesRegex(RuntimeError, "did not install the selected runtime"):
            updates.update(receipt, application_roots=self.roots, fetch=fetch,
                           run=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, reported_ready, ""))

    def test_corrupt_release_is_rejected_before_setup(self):
        receipt = install.setup("codex", scope="user", application_roots=self.roots,
                                user_home=self.home, codex_home=self.home / ".codex")["receipt"]
        fetch, _ = self.release()
        for defect in ("hash", "size", "version", "assets"):
            with self.subTest(defect=defect):
                def corrupt(url, limit):
                    blob = fetch(url, limit)
                    if url.endswith("/build.json"):
                        manifest = json.loads(blob)
                        if defect == "hash":
                            manifest["assets"]["uni-learn.pyz"]["sha256"] = "0" * 64
                        elif defect == "size":
                            manifest["assets"]["uni-learn.pyz"]["bytes"] += 1
                        elif defect == "version":
                            manifest["version"] = "9.0.0"
                        else:
                            manifest["assets"] = None
                        return json.dumps(manifest).encode()
                    return blob
                with self.assertRaises(ValueError):
                    updates.update(receipt, application_roots=self.roots, fetch=corrupt,
                                   run=lambda *args, **kwargs: self.fail("corrupt release must not run"))

    def test_notice_keeps_fork_and_formats_windows_paths_for_powershell(self):
        receipt = install.setup("codex", scope="user", application_roots=self.roots,
                                user_home=self.home, codex_home=self.home / ".codex")["receipt"]
        fork = "example/uni-learn"
        updates.configure(True, receipt=receipt, repository=fork, application_roots=self.roots)
        state.atomic_json(self.roots["cache"] / "update-check.json", {
            "schema_version": 1, "checked_at": 1000, "repository": fork,
            "channels": {"latest": {"version": __version__, "sha256": "0" * 64,
                "bytes": 1, "commit": "a" * 40, "published_at": "2026-10-27T00:00:00Z"}},
        })
        notice = updates.notification(self.project, application_roots=self.roots, now=1001,
                                      fetch=lambda *args: self.fail("fresh cache must stay offline"))
        self.assertEqual(notice["command_argv"][-2:], ["--repository", fork])
        self.assertEqual(notice["command_argv"][1:3], ["-m", "uni_learn"])
        command = updates._format_command([r"C:\Program Files\Python\python.exe",
                                           r"C:\O'Brien\uni-learn.pyz", "update"], platform="nt")
        self.assertTrue(command.startswith("& 'C:\\Program Files"))
        self.assertIn("O''Brien", command)

    def test_concurrent_notices_share_one_network_check(self):
        install.setup("codex", scope="user", application_roots=self.roots,
                      user_home=self.home, codex_home=self.home / ".codex")
        updates.configure(True, application_roots=self.roots)
        fetch, _ = self.release()
        entered = threading.Event()
        calls = []

        def slow(url, limit):
            calls.append(url)
            entered.set()
            threading.Event().wait(.15)
            return fetch(url, limit)

        result = []
        worker = threading.Thread(target=lambda: result.append(
            updates.notification(str(self.project), application_roots=self.roots, fetch=slow, now=3000)))
        worker.start()
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        self.assertIsNone(updates.notification(str(self.project), application_roots=self.roots,
                                               fetch=lambda *args: self.fail("second check must not fetch"),
                                               now=3000))
        self.assertLess(time.monotonic() - started, .5)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls), 3)

    def test_notice_recovers_abandoned_cache_lock(self):
        install.setup("codex", scope="user", application_roots=self.roots,
                      user_home=self.home, codex_home=self.home / ".codex")
        updates.configure(True, application_roots=self.roots)
        lock = self.roots["cache"] / "update-check.lock"
        lock.touch()
        old = time.time() - 120
        os.utime(lock, (old, old))
        fetch, _ = self.release()
        calls = []

        def counted(url, limit):
            calls.append(url)
            return fetch(url, limit)

        self.assertIsNone(updates.notification(self.project, application_roots=self.roots,
                                               fetch=counted, now=4000))
        self.assertEqual(len(calls), 3)
        self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
