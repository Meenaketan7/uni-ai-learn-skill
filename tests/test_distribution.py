"""Release rotation and remote installation from isolated, in-memory GitHub fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import install as bootstrap
from scripts import build_release, publish_release


class MemoryGitHub:
    repository = build_release.REPOSITORY

    def __init__(self):
        self.releases = []
        self.tags_deleted = []
        self.assets_deleted = []
        self.fail_asset = None
        self.leave_starter = False
        self.clock = 0
        self.next_release_id = 1

    def list_releases(self):
        return [dict(item, assets=list(item["assets"])) for item in self.releases]

    def create(self, tag, commit, version):
        item = {"id": self.next_release_id, "tag_name": tag,
                "body": f"{publish_release.MARKER}\nCommit: {commit}\n",
                "draft": True, "prerelease": False, "published_at": None,
                "assets": [], "upload_url": "https://uploads.github.com/fixture"}
        self.releases.append(item)
        self.next_release_id += 1
        return dict(item)

    def get(self, release_id):
        return next(dict(item, assets=list(item["assets"])) for item in self.releases
                    if item["id"] == release_id)

    def upload(self, release, name, content):
        if self.fail_asset == name:
            if self.leave_starter:
                self.releases[-1]["assets"].append(
                    {"id": 100 + len(self.releases[-1]["assets"]), "name": name,
                     "state": "starter", "size": 0, "digest": None})
            raise RuntimeError("simulated interrupted upload")
        item = {"name": name, "state": "uploaded", "size": len(content),
                "digest": "sha256:" + hashlib.sha256(content).hexdigest()}
        next(row for row in self.releases if row["id"] == release["id"])["assets"].append(item)
        return item

    def publish(self, release_id):
        item = next(row for row in self.releases if row["id"] == release_id)
        item["draft"] = False
        self.clock += 1
        item["published_at"] = f"2026-01-{self.clock:02d}T00:00:00Z"
        return dict(item)

    def delete(self, release_id):
        self.releases = [row for row in self.releases if row["id"] != release_id]

    def delete_asset(self, asset_id):
        for release in self.releases:
            for asset in release["assets"]:
                if asset.get("id") == asset_id:
                    release["assets"].remove(asset)
                    self.assets_deleted.append(asset_id)
                    return
        raise ValueError("missing asset")

    def delete_tag(self, tag):
        self.tags_deleted.append(tag)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="uni distribution ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def assets(self, n):
        output = self.base / f"build-{n}"
        commit = f"{n:040x}"
        result = build_release.build(output, commit)
        return output, commit, result

    def release_fixture(self, commit, *, release_id=1, published_at="2026-01-01T00:00:00Z"):
        tag = f"uni-learn-build-{commit}"
        assets = [{"name": name, "state": "uploaded",
                   "browser_download_url": f"https://github.com/{build_release.REPOSITORY}/releases/download/{tag}/{name}"}
                  for name in ("build.json", "uni-learn.pyz")]
        return {"id": release_id, "tag_name": tag, "body": bootstrap.MARKER + "\n",
                "draft": False, "prerelease": False, "published_at": published_at, "assets": assets}

    def test_build_reproducible_and_contains_only_installable_files(self):
        first, commit, result = self.assets(1)
        second = self.base / "repeat"
        build_release.build(second, commit)
        self.assertEqual({p.name: p.read_bytes() for p in first.iterdir() if p.is_file()},
                         {p.name: p.read_bytes() for p in second.iterdir() if p.is_file()})
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO((first / "uni-learn.zip").read_bytes())) as package:
            self.assertEqual(sorted(package.namelist()), ["INSTALL.md", "LICENSE", "install.py", "uni-learn.pyz"])
            self.assertEqual(package.read("uni-learn.pyz"), (first / "uni-learn.pyz").read_bytes())
        self.assertEqual(result["assets"]["uni-learn.pyz"]["sha256"],
                         hashlib.sha256((first / "uni-learn.pyz").read_bytes()).hexdigest())

    def test_first_repeated_and_third_build_retains_only_two_managed_releases(self):
        api = MemoryGitHub()
        built = [self.assets(n) for n in range(1, 4)]
        extra = {"id": 99, "tag_name": "v1.0.0", "body": "User release", "draft": False,
                 "prerelease": False, "published_at": "2025-01-01T00:00:00Z", "assets": []}
        api.releases.append(extra)
        for directory, commit, _ in built:
            publish_release.publish(api, directory, commit)
        self.assertEqual([row["tag_name"] for row in api.releases if publish_release.managed(row)],
                         [f"uni-learn-build-{built[1][1]}", f"uni-learn-build-{built[2][1]}"])
        self.assertIn(extra, api.releases)
        self.assertEqual(api.tags_deleted, [f"uni-learn-build-{built[0][1]}"])
        before = [dict(row, assets=list(row["assets"])) for row in api.releases]
        publish_release.publish(api, built[2][0], built[2][1])
        self.assertEqual(api.releases, before)

    def test_publisher_uses_release_id_when_publications_share_a_second(self):
        api = MemoryGitHub()
        older_dir, older_commit, _ = self.assets(15)
        newer_dir, newer_commit, _ = self.assets(1)
        self.assertGreater(older_commit, newer_commit)  # Lexical SHA order points the wrong way.
        publish_release.publish(api, older_dir, older_commit)
        api.releases[0]["published_at"] = "2026-01-02T00:00:00Z"
        outcome = publish_release.publish(api, newer_dir, newer_commit)
        self.assertEqual(outcome["retained"],
                         [f"uni-learn-build-{newer_commit}", f"uni-learn-build-{older_commit}"])
        self.assertEqual(api.tags_deleted, [])

    def test_interrupted_upload_keeps_published_pair_and_can_resume_draft(self):
        api = MemoryGitHub()
        previous = [self.assets(n) for n in range(1, 3)]
        for directory, commit, _ in previous:
            publish_release.publish(api, directory, commit)
        next_dir, next_commit, _ = self.assets(3)
        api.fail_asset = "uni-learn.zip"
        with self.assertRaisesRegex(RuntimeError, "simulated interrupted upload"):
            publish_release.publish(api, next_dir, next_commit)
        self.assertEqual(len([r for r in api.releases if not r["draft"] and publish_release.managed(r)]), 2)
        self.assertEqual(api.tags_deleted, [])
        api.fail_asset = None
        publish_release.publish(api, next_dir, next_commit)
        self.assertEqual(len([r for r in api.releases if not r["draft"] and publish_release.managed(r)]), 2)

    def test_empty_starter_after_502_is_removed_then_draft_resumes(self):
        api = MemoryGitHub()
        builds = [self.assets(n) for n in range(1, 4)]
        for directory, commit, _ in builds[:2]:
            publish_release.publish(api, directory, commit)
        api.fail_asset = "uni-learn.zip"
        api.leave_starter = True
        with self.assertRaisesRegex(RuntimeError, "interrupted upload"):
            publish_release.publish(api, builds[2][0], builds[2][1])
        draft = api.releases[-1]
        starter = next(asset for asset in draft["assets"] if asset["state"] == "starter")
        self.assertEqual(starter["size"], 0)
        self.assertEqual(len([item for item in api.releases if not item["draft"]]), 2)
        api.fail_asset = None
        outcome = publish_release.publish(api, builds[2][0], builds[2][1])
        self.assertEqual(api.assets_deleted, [starter["id"]])
        self.assertEqual(len([item for item in api.releases if not item["draft"] and publish_release.managed(item)]), 2)
        self.assertEqual(outcome["tag"], f"uni-learn-build-{builds[2][1]}")

    def test_starter_recovery_refuses_nonempty_or_unexpected_draft_assets(self):
        api = MemoryGitHub()
        directory, commit, _ = self.assets(1)
        api.fail_asset = "uni-learn.zip"
        api.leave_starter = True
        with self.assertRaisesRegex(RuntimeError, "interrupted upload"):
            publish_release.publish(api, directory, commit)
        api.fail_asset = None
        draft = api.releases[0]
        starter = next(asset for asset in draft["assets"] if asset["state"] == "starter")
        starter["size"] = 1
        with self.assertRaisesRegex(ValueError, "not proven empty"):
            publish_release.publish(api, directory, commit)
        self.assertEqual(api.assets_deleted, [])
        starter["size"] = 0
        draft["assets"].append({"id": 999, "name": "unrelated.bin", "state": "starter", "size": 0})
        with self.assertRaisesRegex(ValueError, "unexpected asset"):
            publish_release.publish(api, directory, commit)
        self.assertEqual(api.assets_deleted, [])
        self.assertTrue(draft["draft"])

    def test_foreign_collision_and_drifted_published_asset_fail_closed(self):
        api = MemoryGitHub()
        directory, commit, _ = self.assets(1)
        api.releases.append({"id": 7, "tag_name": f"uni-learn-build-{commit}", "body": "Someone else",
                             "draft": False, "assets": [], "published_at": "2026-01-01T00:00:00Z"})
        with self.assertRaisesRegex(ValueError, "different publisher"):
            publish_release.publish(api, directory, commit)
        self.assertEqual(len(api.releases), 1)
        api.releases.clear()
        publish_release.publish(api, directory, commit)
        api.releases[0]["assets"][0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "differs"):
            publish_release.publish(api, directory, commit)

    def test_outdated_branch_or_modified_checksums_cannot_publish(self):
        api = MemoryGitHub()
        directory, commit, _ = self.assets(1)
        api.branch_head = lambda: "f" * 40
        result = publish_release.publish(api, directory, commit)
        self.assertEqual(result["skipped"], "outdated branch build")
        self.assertEqual(api.releases, [])
        del api.branch_head
        sums = directory / "SHA256SUMS"
        sums.write_text("invalid\n")
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
            publish_release.publish(api, directory, commit)
        self.assertEqual(api.releases, [])

    def test_bootstrap_latest_previous_and_argument_forwarding(self):
        first, first_commit, _ = self.assets(1)
        second, second_commit, _ = self.assets(2)
        releases = [self.release_fixture(first_commit, release_id=1),
                    self.release_fixture(second_commit, release_id=2, published_at="2026-01-02T00:00:00Z")]
        urls = {}
        for release, directory in zip(releases, (first, second)):
            for name in ("build.json", "uni-learn.pyz"):
                url = bootstrap._asset_url(release, build_release.REPOSITORY, name)
                urls[url] = (directory / name).read_bytes()

        def fetch(url, limit):
            if "?per_page=" in url:
                return json.dumps(releases).encode()
            return urls[url]

        calls = []

        def run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            self.assertEqual(kwargs, {"check": True})
            self.assertTrue(Path(argv[1]).is_file())
            return subprocess.CompletedProcess(argv, 0)

        latest = bootstrap.install("codex", fetch=fetch, run=run)
        previous = bootstrap.install("cursor", project=str(self.base / "project"), channel="previous",
                                     fetch=fetch, run=run)
        self.assertEqual((latest["commit"], previous["commit"]), (second_commit, first_commit))
        self.assertEqual(calls[0][0][2:], ["setup", "--host", "codex"])
        self.assertEqual(calls[1][0][2:], ["setup", "--host", "cursor", "--project", str(self.base / "project")])

    def test_bootstrap_uses_release_id_when_publications_share_a_second(self):
        older, older_commit, _ = self.assets(15)
        newer, newer_commit, _ = self.assets(1)
        releases = [self.release_fixture(older_commit, release_id=10),
                    self.release_fixture(newer_commit, release_id=11)]
        urls = {bootstrap._asset_url(release, build_release.REPOSITORY, name): (directory / name).read_bytes()
                for release, directory in zip(releases, (older, newer))
                for name in ("build.json", "uni-learn.pyz")}

        def fetch(url, limit):
            return json.dumps(releases).encode() if "?per_page=" in url else urls[url]

        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0)

        self.assertEqual(bootstrap.install("codex", fetch=fetch, run=run)["commit"], newer_commit)
        self.assertEqual(bootstrap.install("codex", channel="previous", fetch=fetch, run=run)["commit"], older_commit)

    def test_bootstrap_rejects_corrupt_missing_or_incomplete_release_without_execution(self):
        directory, commit, _ = self.assets(1)
        release = self.release_fixture(commit)
        urls = {bootstrap._asset_url(release, build_release.REPOSITORY, name): (directory / name).read_bytes()
                for name in ("build.json", "uni-learn.pyz")}

        def fetch(url, limit):
            if "?per_page=" in url:
                return json.dumps([release]).encode()
            return urls[url]

        def no_run(*args, **kwargs):
            self.fail("corrupt release reached setup")

        pyz_url = bootstrap._asset_url(release, build_release.REPOSITORY, "uni-learn.pyz")
        urls[pyz_url] += b"tampered"
        with self.assertRaisesRegex(ValueError, "does not match"):
            bootstrap.install("codex", fetch=fetch, run=no_run)
        urls[pyz_url] = (directory / "uni-learn.pyz").read_bytes()
        with self.assertRaisesRegex(ValueError, "previous managed"):
            bootstrap.install("codex", channel="previous", fetch=fetch, run=no_run)
        release["assets"] = [row for row in release["assets"] if row["name"] != "uni-learn.pyz"]
        with self.assertRaisesRegex(ValueError, "asset missing"):
            bootstrap.install("codex", fetch=fetch, run=no_run)

    def test_bootstrap_checks_github_asset_digest_and_url(self):
        directory, commit, _ = self.assets(1)
        release = self.release_fixture(commit)
        urls = {bootstrap._asset_url(release, build_release.REPOSITORY, name): (directory / name).read_bytes()
                for name in ("build.json", "uni-learn.pyz")}

        def fetch(url, limit):
            return json.dumps([release]).encode() if "?per_page=" in url else urls[url]

        def no_run(*args, **kwargs):
            self.fail("invalid release reached setup")

        release["assets"][0]["digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            bootstrap.install("codex", fetch=fetch, run=no_run)
        release["assets"][0].pop("digest")
        release["assets"][0]["browser_download_url"] = "https://other.example/install.py"
        with self.assertRaisesRegex(ValueError, "unexpected release asset URL"):
            bootstrap.install("codex", fetch=fetch, run=no_run)

    def test_bootstrap_runs_verifed_zipapp_outside_checkout(self):
        directory, commit, _ = self.assets(1)
        release = self.release_fixture(commit)
        urls = {bootstrap._asset_url(release, build_release.REPOSITORY, name): (directory / name).read_bytes()
                for name in ("build.json", "uni-learn.pyz")}

        def fetch(url, limit):
            return json.dumps([release]).encode() if "?per_page=" in url else urls[url]

        home = self.base / "home"
        home.mkdir()
        project = self.base / "project"
        project.mkdir()
        env = dict(os.environ, HOME=str(home), CODEX_HOME=str(home / ".codex"))
        for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
            env[f"XDG_{kind}_HOME"] = str(home / kind.lower())
        original = os.environ.copy()
        try:
            os.environ.update(env)
            def run(argv, **kwargs):
                return subprocess.run(argv, env=env, capture_output=True, text=True, **kwargs)
            result = bootstrap.install("codex", fetch=fetch, run=run)
        finally:
            os.environ.clear()
            os.environ.update(original)
        self.assertEqual(result["commit"], commit)
        self.assertTrue((home / ".agents/skills/learn/SKILL.md").is_file())
        self.assertTrue((home / ".codex/AGENTS.md").is_file())
        self.assertFalse((project / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
