"""Build reproducible release assets from the checked-out source."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

from uni_learn import __version__
from uni_learn.package import ROOT, build_bytes

ASSETS = ("uni-learn.pyz", "uni-learn.zip", "install.py", "build.json", "SHA256SUMS")
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = "Meenaketan7/uni-ai-learn-skill"
INSTALL_TEXT = """# Install Uni Learn

Python 3.11 or newer is required. Run from the extracted directory:

    python3 uni-learn.pyz setup --host codex

For another editor, choose a profile and the target project:

    python3 uni-learn.pyz setup --host cursor --project /path/to/project

Profiles and learning commands: https://github.com/Meenaketan7/uni-ai-learn-skill#readme
"""


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _zip(entries: dict[str, bytes]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return target.getvalue()


def build(output_dir: str | Path, commit: str, repository: str = REPOSITORY) -> dict:
    if not SHA.fullmatch(commit):
        raise ValueError("commit must be a full lowercase Git SHA-1")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be OWNER/NAME")
    installer = (ROOT / "install.py").read_bytes()
    runtime = build_bytes()
    archive = _zip({"INSTALL.md": INSTALL_TEXT.encode(), "LICENSE": (ROOT / "LICENSE").read_bytes(),
                    "install.py": installer, "uni-learn.pyz": runtime})
    payload = {"install.py": installer, "uni-learn.pyz": runtime, "uni-learn.zip": archive}
    manifest = {"schema_version": 1, "repository": repository, "commit": commit,
                "version": __version__, "assets": {
                    name: {"sha256": _digest(content), "bytes": len(content)}
                    for name, content in sorted(payload.items())}}
    payload["build.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    payload["SHA256SUMS"] = "".join(f"{_digest(content)}  {name}\n"
                                    for name, content in sorted(payload.items())).encode()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for name, content in payload.items():
        (output / name).write_bytes(content)
    return {"commit": commit, "version": __version__, "output": str(output),
            "assets": {name: {"sha256": _digest(content), "bytes": len(content)}
                       for name, content in sorted(payload.items())}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="dist/release")
    parser.add_argument("--commit", help="Full commit to publish; defaults to Git HEAD")
    parser.add_argument("--repository", default=REPOSITORY)
    args = parser.parse_args(argv)
    commit = args.commit or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    print(json.dumps(build(args.output_dir, commit, args.repository), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
