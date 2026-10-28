"""Publish one tested build, retaining the latest two managed GitHub releases.

The caller supplies GITHUB_TOKEN with contents:write. This module uses only the
standard library and never modifies releases it cannot identify as its own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlparse

from uni_learn.package import build_bytes
from scripts.build_release import ASSETS, REPOSITORY, SHA

MARKER = "<!-- uni-learn:managed-build:v1 -->"
TAG = re.compile(r"uni-learn-build-([0-9a-f]{40})\Z")


def managed(release: dict) -> bool:
    return (isinstance(release, dict) and TAG.fullmatch(str(release.get("tag_name", ""))) is not None
            and str(release.get("body", "")).splitlines()[:1] == [MARKER])


def _published_order(release: dict) -> tuple[str, int]:
    release_id = release.get("id")
    if type(release_id) is not int or release_id <= 0:
        raise ValueError("managed release has no numeric ID")
    return release["published_at"], release_id


class GitHub:
    def __init__(self, repository: str, token: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("repository must be OWNER/NAME")
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        self.repository = repository
        self.token = token
        self.base = f"https://api.github.com/repos/{repository}"

    def _request(self, method: str, url: str, payload: bytes | None = None,
                 content_type: str = "application/vnd.github+json"):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "uploads.github.com"}:
            raise ValueError("unexpected GitHub API URL")
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}",
                   "User-Agent": "uni-learn-release", "X-GitHub-Api-Version": "2026-03-10"}
        if payload is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            message = exc.read(512).decode("utf-8", "replace")
            raise RuntimeError(f"GitHub {method} failed ({exc.code}): {message}") from exc
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("GitHub API response exceeds size limit")
        return json.loads(raw) if raw else None

    def list_releases(self) -> list[dict]:
        found = []
        for page in range(1, 21):
            rows = self._request("GET", f"{self.base}/releases?per_page=100&page={page}")
            if not isinstance(rows, list):
                raise ValueError("GitHub did not return a release list")
            found.extend(rows)
            if len(rows) < 100:
                return found
        raise ValueError("release list exceeds supported page limit")

    def branch_head(self) -> str:
        result = self._request("GET", f"{self.base}/branches/main")
        return result["commit"]["sha"]

    def create(self, tag: str, commit: str, version: str) -> dict:
        body = f"{MARKER}\nCommit: {commit}\nVersion: {version}\n"
        payload = {"tag_name": tag, "target_commitish": commit,
                   "name": f"Uni Learn {version} ({commit[:12]})", "body": body,
                   "draft": True, "prerelease": False}
        return self._request("POST", f"{self.base}/releases", json.dumps(payload).encode())

    def get(self, release_id: int) -> dict:
        return self._request("GET", f"{self.base}/releases/{release_id}")

    def upload(self, release: dict, name: str, content: bytes) -> dict:
        upload_url = str(release["upload_url"]).split("{", 1)[0]
        expected = f"https://uploads.github.com/repos/{self.repository}/releases/{release['id']}/assets"
        if upload_url != expected:
            raise ValueError("unexpected GitHub upload endpoint")
        content_type = "application/zip" if name.endswith((".zip", ".pyz")) else "application/octet-stream"
        return self._request("POST", f"{upload_url}?name={quote(name)}", content, content_type)

    def publish(self, release_id: int) -> dict:
        return self._request("PATCH", f"{self.base}/releases/{release_id}",
                             json.dumps({"draft": False, "make_latest": "true"}).encode())

    def delete(self, release_id: int) -> None:
        self._request("DELETE", f"{self.base}/releases/{release_id}")

    def delete_asset(self, asset_id: int) -> None:
        if type(asset_id) is not int or asset_id <= 0:
            raise ValueError("invalid release asset ID")
        self._request("DELETE", f"{self.base}/releases/assets/{asset_id}")

    def delete_tag(self, tag: str) -> None:
        if not TAG.fullmatch(tag):
            raise ValueError("refusing to delete an unmanaged tag")
        self._request("DELETE", f"{self.base}/git/refs/tags/{tag}")


def _verify_assets(release: dict, payload: dict[str, bytes], *, complete: bool) -> set[str]:
    present = set()
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("release has no asset inventory")
    for asset in assets:
        name = asset.get("name")
        if name not in payload:
            raise ValueError(f"unexpected asset in managed release: {name}")
        if name in present:
            raise ValueError(f"duplicate release asset: {name}")
        present.add(name)
        content = payload[name]
        if (asset.get("state") != "uploaded" or asset.get("size") != len(content)
                or asset.get("digest") != "sha256:" + hashlib.sha256(content).hexdigest()):
            raise ValueError(f"release asset differs from tested build: {name}")
    if complete and present != set(payload):
        raise ValueError("published release is missing an asset")
    return present


def _clear_empty_starters(api, release: dict, payload: dict[str, bytes]) -> dict:
    """Recover only GitHub's documented empty starter assets in our draft."""
    if not managed(release) or release.get("draft") is not True:
        raise ValueError("starter recovery requires an owned draft release")
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("release has no asset inventory")
    seen = set()
    starters = []
    for asset in assets:
        name = asset.get("name")
        if name not in payload:
            raise ValueError(f"unexpected asset in managed release: {name}")
        if name in seen:
            raise ValueError(f"duplicate release asset: {name}")
        seen.add(name)
        if asset.get("state") == "starter":
            empty_hash = "sha256:" + hashlib.sha256(b"").hexdigest()
            if (asset.get("size") != 0 or asset.get("digest") not in (None, empty_hash)
                    or type(asset.get("id")) is not int or asset["id"] <= 0):
                raise ValueError(f"starter asset is not proven empty: {name}")
            starters.append(asset["id"])
    # Check every uploaded asset before deleting anything from the draft.
    _verify_assets({"assets": [asset for asset in assets if asset.get("state") != "starter"]},
                   payload, complete=False)
    for asset_id in starters:
        api.delete_asset(asset_id)
    return api.get(release["id"]) if starters else release


def publish(api, directory: str | Path, commit: str) -> dict:
    if not SHA.fullmatch(commit):
        raise ValueError("commit must be a full lowercase Git SHA-1")
    directory = Path(directory)
    payload = {name: (directory / name).read_bytes() for name in ASSETS}
    if payload["uni-learn.pyz"] != build_bytes():
        raise ValueError("runtime archive differs from the checked-out source")
    expected_sums = [f"{hashlib.sha256(payload[name]).hexdigest()}  {name}"
                     for name in sorted(set(payload) - {"SHA256SUMS"})]
    if payload["SHA256SUMS"].decode("ascii").splitlines() != expected_sums:
        raise ValueError("SHA256SUMS differs from release assets")
    manifest = json.loads(payload["build.json"])
    if manifest.get("commit") != commit or manifest.get("repository") != api.repository:
        raise ValueError("build manifest does not match commit/repository")
    if set(manifest.get("assets", {})) != {"install.py", "uni-learn.pyz", "uni-learn.zip"}:
        raise ValueError("build manifest has an unexpected asset inventory")
    for name, expected in manifest.get("assets", {}).items():
        content = payload.get(name)
        if (content is None or expected.get("bytes") != len(content)
                or expected.get("sha256") != hashlib.sha256(content).hexdigest()):
            raise ValueError(f"build manifest differs from asset: {name}")
    tag = f"uni-learn-build-{commit}"
    if hasattr(api, "branch_head"):
        head = api.branch_head()
        if head != commit:
            return {"skipped": "outdated branch build", "commit": commit, "current_head": head}
    releases = api.list_releases()
    matches = [item for item in releases if item.get("tag_name") == tag]
    if len(matches) > 1 or matches and not managed(matches[0]):
        raise ValueError("release tag belongs to a different publisher")
    release = matches[0] if matches else api.create(tag, commit, manifest["version"])
    if release.get("tag_name") != tag or not managed(release):
        raise ValueError("GitHub created an unexpected release")
    release = api.get(release["id"])
    if release.get("draft") is True:
        release = _clear_empty_starters(api, release, payload)
    present = _verify_assets(release, payload, complete=not release["draft"])
    if release["draft"]:
        for name, content in payload.items():
            if name not in present:
                uploaded = api.upload(release, name, content)
                _verify_assets({"assets": [uploaded]}, {name: content}, complete=True)
        release = api.get(release["id"])
        _verify_assets(release, payload, complete=True)
        release = api.publish(release["id"])
    else:
        # A repeated workflow run must never rewrite a published build.
        _verify_assets(release, payload, complete=True)

    published = [item for item in api.list_releases() if managed(item) and not item.get("draft")
                 and not item.get("prerelease") and item.get("published_at")]
    published.sort(key=_published_order, reverse=True)
    if published and published[0]["tag_name"] != tag:
        raise ValueError("a newer managed release is already published; refusing to replace it")
    removed = []
    for old in published[2:]:
        api.delete(old["id"])
        api.delete_tag(old["tag_name"])
        removed.append(old["tag_name"])
    return {"tag": tag, "version": manifest["version"], "retained":
            [item["tag_name"] for item in published[:2]], "removed": removed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="dist/release")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", REPOSITORY))
    args = parser.parse_args(argv)
    try:
        result = publish(GitHub(args.repository, os.environ.get("GITHUB_TOKEN", "")),
                         args.directory, args.commit)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Release publication failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
