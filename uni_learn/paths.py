"""Per-user application paths and stable project identity."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def home_directory(env=None, home=None) -> Path:
    env = os.environ if env is None else env
    requested_home = home if home is not None else env.get("HOME")
    if requested_home and Path(requested_home).is_absolute():
        home = Path(requested_home)
    elif os.name == "posix":
        import pwd
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    else:
        try:
            home = Path(env.get("USERPROFILE") or Path.home())
        except RuntimeError as exc:
            raise ValueError("an absolute user home directory is required") from exc
    if not home.is_absolute():
        raise ValueError("an absolute user home directory is required")
    return home


def roots(env=None, home=None, platform=None) -> dict[str, Path]:
    env = os.environ if env is None else env
    def absolute_or(value, fallback):
        path = Path(value) if value else None
        return path if path is not None and path.is_absolute() else Path(fallback)

    home = home_directory(env, home)
    platform = os.name if platform is None else platform
    if platform == "nt":
        roaming = absolute_or(env.get("APPDATA"), home / "AppData" / "Roaming")
        local = absolute_or(env.get("LOCALAPPDATA"), home / "AppData" / "Local")
        defaults = {"config": roaming, "data": local / "data", "state": local / "state", "cache": local / "cache"}
    else:
        defaults = {"config": home / ".config", "data": home / ".local" / "share",
                    "state": home / ".local" / "state", "cache": home / ".cache"}
    return {k: absolute_or(env.get("XDG_" + k.upper() + "_HOME"), defaults[k]) / "uni-learn"
            for k in defaults}


def project_id(project: str | Path) -> str:
    path = str(Path(project).expanduser().resolve())
    if os.name == "nt":
        path = os.path.normcase(path)
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]


def locate_project(start: str | Path = ".") -> dict:
    directory = Path(start).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("project start must be a directory")
    for candidate in (directory, *directory.parents):
        marker = candidate / ".git"
        if marker.is_dir() or marker.is_file():
            return {"project_root": str(candidate), "project_id": project_id(candidate), "source": "git"}
    return {"project_root": str(directory), "project_id": project_id(directory), "source": "directory"}


def project_dir(project: str | Path, application_roots=None) -> Path:
    return (application_roots or roots())["state"] / "projects" / project_id(project)


def safe_id(value: str) -> str:
    if not value or len(value) > 128 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in value):
        raise ValueError("identifier must use 1-128 ASCII letters, digits, hyphens, or underscores")
    return value
