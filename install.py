"""Download a verified Uni Learn release and run its one-command host setup.

Run with Python 3.11+; the file itself needs no packages or repository checkout.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

REPOSITORY = "Meenaketan7/uni-ai-learn-skill"
MARKER = "<!-- uni-learn:managed-build:v1 -->"
TAG = re.compile(r"uni-learn-build-([0-9a-f]{40})\Z")
PROFILE_NAMES = ("codex", "claude-code", "copilot-vscode", "copilot-cli", "cursor",
                 "opencode", "antigravity", "gemini-cli", "kimi-code", "windsurf", "cline", "aider")
MAX_JSON = 2 * 1024 * 1024
MAX_RUNTIME = 16 * 1024 * 1024


def _repository(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("repository must be OWNER/NAME")
    return value


def _fetch(url: str, limit: int, timeout: float = 30) -> bytes:
    if urlparse(url).scheme != "https":
        raise ValueError("release download must use HTTPS")
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "uni-learn-installer"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if urlparse(response.geturl()).scheme != "https":
            raise ValueError("release redirect must use HTTPS")
        result = response.read(limit + 1)
    if len(result) > limit:
        raise ValueError("release response exceeds size limit")
    return result


def _managed(release: dict) -> bool:
    return (isinstance(release, dict) and TAG.fullmatch(str(release.get("tag_name", ""))) is not None
            and str(release.get("body", "")).splitlines()[:1] == [MARKER]
            and release.get("draft") is False and release.get("prerelease") is False
            and bool(release.get("published_at")))


def _published_order(release: dict) -> tuple[str, int]:
    if not isinstance(release, dict) or not isinstance(release.get("published_at"), str):
        raise ValueError("managed release has invalid publication metadata")
    release_id = release.get("id")
    if type(release_id) is not int or release_id <= 0:
        raise ValueError("managed release has no numeric ID")
    return release["published_at"], release_id


def _releases(repository: str, fetch=_fetch) -> list[dict]:
    base = f"https://api.github.com/repos/{repository}/releases"
    found = []
    for page in range(1, 21):
        rows = json.loads(fetch(f"{base}?per_page=100&page={page}", MAX_JSON))
        if not isinstance(rows, list):
            raise ValueError("GitHub did not return a release list")
        found.extend(row for row in rows if _managed(row))
        if len(rows) < 100:
            break
    else:
        raise ValueError("release list exceeds supported page limit")
    return sorted(found, key=_published_order, reverse=True)


def _asset_url(release: dict, repository: str, name: str) -> str:
    if not isinstance(release, dict) or not isinstance(release.get("assets"), list):
        raise ValueError("release has invalid assets")
    tag = release["tag_name"]
    if any(not isinstance(asset, dict) for asset in release["assets"]):
        raise ValueError("release has invalid asset metadata")
    matches = [asset for asset in release["assets"] if asset.get("name") == name]
    if len(matches) != 1 or matches[0].get("state") != "uploaded":
        raise ValueError(f"release asset missing: {name}")
    url = matches[0].get("browser_download_url")
    expected = f"https://github.com/{repository}/releases/download/{tag}/{name}"
    if url != expected:
        raise ValueError(f"unexpected release asset URL: {name}")
    return url


def _checked_asset(release: dict, repository: str, name: str, limit: int, fetch) -> bytes:
    url = _asset_url(release, repository, name)
    content = fetch(url, limit)
    asset = next(row for row in release["assets"] if row["name"] == name)
    advertised = asset.get("digest")
    if advertised is not None and advertised != "sha256:" + hashlib.sha256(content).hexdigest():
        raise ValueError(f"GitHub asset digest mismatch: {name}")
    return content


def _release_details(release: dict, repository: str, fetch=_fetch) -> dict:
    """Fetch and verify a managed release payload for both setup and updates."""
    commit = TAG.fullmatch(release["tag_name"]).group(1)
    manifest = json.loads(_checked_asset(release, repository, "build.json", MAX_JSON, fetch))
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("repository") != repository or manifest.get("commit") != commit):
        raise ValueError("release manifest does not match the selected release")
    assets = manifest.get("assets")
    if not isinstance(assets, dict):
        raise ValueError("release manifest has invalid asset metadata")
    expected = assets.get("uni-learn.pyz")
    if not isinstance(expected, dict):
        raise ValueError("release manifest has invalid runtime metadata")
    digest, size = expected.get("sha256"), expected.get("bytes")
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(size) is not int or not 0 < size <= MAX_RUNTIME):
        raise ValueError("release manifest has invalid runtime metadata")
    runtime = _checked_asset(release, repository, "uni-learn.pyz", MAX_RUNTIME, fetch)
    if len(runtime) != size or hashlib.sha256(runtime).hexdigest() != digest:
        raise ValueError("downloaded runtime does not match release SHA-256")
    try:
        with zipfile.ZipFile(io.BytesIO(runtime)) as archive:
            member = archive.getinfo("uni_learn/__init__.py")
            if member.file_size > 64 * 1024:
                raise ValueError("release version metadata exceeds size limit")
            source = archive.read(member).decode("utf-8")
        tree = ast.parse(source)
        embedded_version = next(ast.literal_eval(node.value) for node in tree.body
                                if isinstance(node, ast.Assign)
                                and any(isinstance(target, ast.Name) and target.id == "__version__"
                                        for target in node.targets))
    except (KeyError, UnicodeError, SyntaxError, StopIteration, zipfile.BadZipFile, ValueError, TypeError) as exc:
        raise ValueError("release runtime is not a valid Uni Learn package") from exc
    version = manifest.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("release manifest has invalid version")
    if embedded_version != version:
        raise ValueError("release runtime version does not match its manifest")
    return {"version": version, "commit": commit, "sha256": digest, "bytes": size,
            "runtime": runtime, "manifest": manifest}


def install(host: str, *, project: str | None = None, scope: str | None = None,
            generation: str | None = None, channel: str = "latest",
            repository: str = REPOSITORY, fetch=_fetch, run=subprocess.run) -> dict:
    repository = _repository(repository)
    if host not in PROFILE_NAMES:
        raise ValueError("unsupported host: " + host)
    if channel not in {"latest", "previous"}:
        raise ValueError("channel must be latest or previous")
    releases = _releases(repository, fetch)
    index = 0 if channel == "latest" else 1
    if len(releases) <= index:
        raise ValueError(f"no {channel} managed release is published")
    release = releases[index]
    details = _release_details(release, repository, fetch)
    manifest, runtime = details["manifest"], details["runtime"]
    args = [sys.executable, "setup", "--host", host]
    if scope:
        args.extend(("--scope", scope))
    if project:
        args.extend(("--project", str(Path(project).expanduser().resolve())))
    elif host != "codex" or scope == "project":
        args.extend(("--project", str(Path.cwd().resolve())))
    if generation:
        args.extend(("--generation", generation))
    with tempfile.TemporaryDirectory(prefix="uni-learn-install-") as directory:
        archive = Path(directory) / "uni-learn.pyz"
        archive.write_bytes(runtime)
        args.insert(1, str(archive))
        run(args, check=True)
    return {"version": manifest.get("version"), "commit": manifest["commit"],
            "channel": channel, "host": host, "runtime_sha256": details["sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, choices=PROFILE_NAMES)
    parser.add_argument("--project")
    parser.add_argument("--scope", choices=("project", "user"))
    parser.add_argument("--generation")
    parser.add_argument("--channel", choices=("latest", "previous"), default="latest")
    parser.add_argument("--repository", default=REPOSITORY)
    args = parser.parse_args(argv)
    try:
        result = install(args.host, project=args.project, scope=args.scope,
                         generation=args.generation, channel=args.channel,
                         repository=args.repository)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"Uni Learn setup failed: {exc}\n")
    print(f"Verified Uni Learn {result['version']} ({result['channel']}, {result['host']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
