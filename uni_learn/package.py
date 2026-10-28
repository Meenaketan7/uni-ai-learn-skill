"""Allowlisted, deterministic source and zipapp resource access."""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent
MODULES = {
    "__init__", "__main__", "paths", "config", "contracts", "budget", "handoff", "learning",
    "adapters", "install", "integrations", "state", "routing", "pointers", "compaction",
    "learning_log", "recall", "package", "updates",
}


def _archive():
    source = getattr(globals().get("__loader__"), "archive", None)
    return Path(source) if source else None


def _safe(name):
    if (not isinstance(name, str) or not name or "\\" in name or
            PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive or
            ".." in PurePosixPath(name).parts or name == "."):
        raise ValueError("invalid resource path")
    if name != "LICENSE" and name.split("/")[0] not in {"uni_learn", "core", "registry", "adapters"}:
        raise ValueError("resource outside package namespaces")
    return name


def read_resource(name):
    _safe(name)
    archive = _archive()
    if archive:
        with zipfile.ZipFile(archive) as package:
            return package.read(name)
    return (ROOT / name).read_bytes()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(name):
    return json.loads(read_resource(name).decode("utf-8"), object_pairs_hook=_unique)


def list_resources(prefix):
    _safe(prefix)
    archive = _archive()
    if archive:
        with zipfile.ZipFile(archive) as package:
            return sorted(name for name in package.namelist() if name.startswith(prefix) and not name.endswith("/"))
    return sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / prefix).rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts)


def _source_files():
    names = {"uni_learn/" + name + ".py" for name in MODULES}
    names.update(("LICENSE", "install.py"))
    names.update("registry/" + name + ".json" for name in ("adapters", "models", "integrations"))
    for path in (ROOT / "core").rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json"} and not any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            names.add(path.relative_to(ROOT).as_posix())
    profiles = read_json("registry/adapters.json")["profiles"]
    for entry in profiles.values():
        descriptor_path = _safe(entry["descriptor"])
        names.update((descriptor_path, _safe(entry["renderer"])))
        descriptor = read_json(descriptor_path)
        names.update(_safe(file["template"]) for file in descriptor["files"])
        names.update(_safe(file["user_template"]) for file in descriptor["files"] if "user_template" in file)
    for name in names:
        path = ROOT / name
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != ROOT.parent):
            # ROOT itself is already resolved; package resource aliases must not escape it.
            if path.resolve().is_relative_to(ROOT) is False or path.is_symlink():
                raise ValueError("symlink package resource: " + name)
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("missing or escaped package resource: " + name)
    return sorted(names)


def build_bytes():
    archive = _archive()
    if archive:
        return archive.read_bytes()
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        # Every allowlisted resource is text. Keep archives identical across checkouts.
        entries = [(name, (ROOT / name).read_bytes().replace(b"\r\n", b"\n")) for name in _source_files()]
        entries.append(("__main__.py", b"from uni_learn.__main__ import main\nraise SystemExit(main())\n"))
        for name, content in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            package.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return target.getvalue()


def build(output):
    output = Path(output)
    content = build_bytes()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    return {"output": str(output), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
