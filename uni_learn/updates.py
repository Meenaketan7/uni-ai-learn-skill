"""Optional, cached checks for verified Uni Learn releases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .contracts import ContractError
from .package import _archive
from .paths import roots
from .state import ConflictError, atomic_json, locked
import install as _bootstrap

REPOSITORY = _bootstrap.REPOSITORY
MARKER = _bootstrap.MARKER
TAG = _bootstrap.TAG
MAX_JSON = _bootstrap.MAX_JSON
MAX_RUNTIME = _bootstrap.MAX_RUNTIME
CHECK_INTERVAL = 24 * 60 * 60
NOTIFICATION_TIMEOUT = 2
EXPLICIT_TIMEOUT = 30
CONFIG_NAME = "update-preferences.json"

_fetch = _bootstrap._fetch
_repository = _bootstrap._repository


def _releases(repository=REPOSITORY, fetch=_fetch):
    return _bootstrap._releases(repository, fetch)


def _release_details(release, fetch, repository=REPOSITORY):
    result = _bootstrap._release_details(release, repository, fetch)
    return {key: result[key] for key in ("version", "commit", "sha256", "bytes", "runtime")} | {
        "published_at": release["published_at"]}


def _bounded(fetch, *, seconds=EXPLICIT_TIMEOUT):
    deadline = time.monotonic() + seconds

    def request(url, limit):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("update check timed out")
        if fetch is _fetch:
            return fetch(url, limit, timeout=min(EXPLICIT_TIMEOUT, remaining))
        return fetch(url, limit)
    return request


def _version(value: str) -> tuple[tuple[int, int, int], tuple[str, ...] | None]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", value)
    if not match:
        raise ContractError("invalid installed runtime version")
    core = tuple(int(part) for part in match.groups()[:3])
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else None
    return core, prerelease


def _compare_versions(left: str, right: str) -> int:
    left_core, left_pre = _version(left)
    right_core, right_pre = _version(right)
    if left_core != right_core:
        return (left_core > right_core) - (left_core < right_core)
    if left_pre is None or right_pre is None:
        return 0 if left_pre is right_pre else (1 if left_pre is None else -1)
    for a, b in zip(left_pre, right_pre):
        if a == b:
            continue
        a_num, b_num = a.isdigit(), b.isdigit()
        if a_num and b_num:
            return (int(a) > int(b)) - (int(a) < int(b))
        if a_num != b_num:
            return -1 if a_num else 1
        return (a > b) - (a < b)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def _receipt(path: str | Path, application_roots=None) -> tuple[dict, dict, Path]:
    from . import install

    path = Path(path).expanduser().absolute()
    data = Path((application_roots or roots())["data"]).expanduser().resolve()
    record = install._record(path)
    if record is None:
        raise ContractError("installation receipt is missing")
    install._validate_record(path, record, data)
    archive_path = data / "releases" / record["runtime_version"] / record["install_id"] / "uni-learn.pyz"
    archive_entry = next((item for item in record["owned"] if item.get("path") == str(archive_path)), None)
    if archive_entry is None or not archive_path.is_file():
        raise ContractError("installed runtime is missing from the receipt")
    current_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if current_sha != archive_entry.get("sha256"):
        raise ContractError("installed runtime no longer matches its ownership receipt")
    return record, {"version": record["runtime_version"], "sha256": current_sha}, data


def _receipts(application_roots=None) -> list[Path]:
    data = Path((application_roots or roots())["data"]).expanduser().resolve()
    directory = data / "receipts"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if not path.name.endswith(".journal.json"))


def _cache_path(application_roots=None) -> Path:
    return Path((application_roots or roots())["cache"]) / "update-check.json"


def _config_path(application_roots=None) -> Path:
    return Path((application_roots or roots())["config"]) / CONFIG_NAME


def preferences(*, application_roots=None) -> dict:
    path = _config_path(application_roots)
    try:
        value = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        value = {}
    if not isinstance(value, dict) or value.get("schema_version", 1) != 1:
        raise ContractError("invalid update preferences")
    if type(value.get("enabled", False)) is not bool:
        raise ContractError("invalid update preference value")
    receipt = value.get("receipt")
    if receipt is not None and (not isinstance(receipt, str) or not Path(receipt).is_absolute()):
        raise ContractError("invalid update receipt preference")
    return {"enabled": value.get("enabled", False),
            "repository": _repository(value.get("repository", REPOSITORY)), "receipt": receipt}


def status(*, application_roots=None) -> dict:
    value = preferences(application_roots=application_roots)
    return {"status": "enabled" if value["enabled"] else "disabled", **value}


def configure(enabled: bool, *, repository: str = REPOSITORY, receipt: str | Path | None = None,
              application_roots=None) -> dict:
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    selected = None
    if receipt is not None:
        path = Path(receipt).expanduser().absolute()
        _receipt(path, application_roots)
        selected = str(path)
    value = {"schema_version": 1, "enabled": enabled, "repository": _repository(repository),
             "receipt": selected}
    atomic_json(_config_path(application_roots), value)
    return {"status": "enabled" if enabled else "disabled", "repository": value["repository"],
            "receipt": selected}


def _select_receipt(receipt, project=None, application_roots=None) -> Path:
    if receipt:
        paths = [Path(receipt).expanduser().absolute()]
    else:
        selected = preferences(application_roots=application_roots).get("receipt")
        paths = [Path(selected)] if selected else _receipts(application_roots)
    if project:
        resolved = str(Path(project).expanduser().resolve())
        matching = []
        for path in paths:
            try:
                record, _, _ = _receipt(path, application_roots)
                if record.get("project") == resolved:
                    matching.append(path)
            except (OSError, ValueError, KeyError, TypeError, ContractError):
                continue
        paths = matching
        if not paths:
            user_paths = []
            for candidate in _receipts(application_roots):
                try:
                    record, _, _ = _receipt(candidate, application_roots)
                    if record.get("scope") == "user":
                        user_paths.append(candidate)
                except (OSError, ValueError, KeyError, TypeError, ContractError):
                    continue
            paths = user_paths
    if not paths:
        raise ContractError("no Uni Learn installation receipt was found")
    if len(paths) != 1:
        raise ContractError("select one installation receipt with --receipt")
    return paths[0]


def _cached(path: Path, now: int) -> dict | None:
    try:
        value = json.loads(path.read_text("utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            return None
        checked = value.get("checked_at")
        if type(checked) is not int:
            return None
        age = now - checked
        channels = value.get("channels")
        if (-300 <= age < CHECK_INTERVAL and isinstance(channels, dict)
                and all(key in {"latest", "previous"} and _valid_cached_release(item)
                        for key, item in channels.items())):
            return value
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def _valid_cached_release(value) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    try:
        _version(value["version"])
        return (isinstance(value.get("commit"), str) and re.fullmatch(r"[0-9a-f]{40}", value["commit"]) is not None
                and isinstance(value.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None
                and type(value.get("bytes")) is int and value["bytes"] > 0
                and isinstance(value.get("published_at"), str))
    except (KeyError, TypeError, ContractError):
        return False


def check_available(receipt: str | Path | None = None, *, project=None, repository: str = REPOSITORY,
                    channel: str = "latest", application_roots=None, fetch=_fetch,
                    now: int | None = None, cached: bool = False) -> dict:
    """Explicitly check a selected installation against a published build."""
    if channel not in {"latest", "previous"}:
        raise ValueError("channel must be latest or previous")
    path = _select_receipt(receipt, project, application_roots)
    record, current, _ = _receipt(path, application_roots)
    now = int(time.time()) if now is None else int(now)
    cache_path = _cache_path(application_roots)
    if cached:
        value = _cached(cache_path, now)
        if value and value.get("repository") == repository:
            detail = value.get("channels", {}).get(channel, {})
            if detail:
                return _availability(record, current, detail, cached=True, channel=channel)
    try:
        bounded_fetch = _bounded(fetch)
        releases = _releases(repository, bounded_fetch)
        index = 0 if channel == "latest" else 1
        if len(releases) <= index:
            raise ValueError(f"no {channel} managed release is published")
        detail = _release_details(releases[index], bounded_fetch, repository)
        stored = {key: value for key, value in detail.items() if key != "runtime"}
        previous = _cached(cache_path, now) or {"schema_version": 1, "checked_at": now, "repository": repository, "channels": {}}
        channels = previous.get("channels") if previous.get("repository") == repository else {}
        channels = dict(channels) if isinstance(channels, dict) else {}
        channels[channel] = stored
        atomic_json(cache_path, {"schema_version": 1, "checked_at": now,
                                 "repository": repository, "channels": channels})
        return _availability(record, current, stored, cached=False, channel=channel)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return {"status": "unavailable", "available": False, "message": str(exc),
                "current_version": current["version"], "current_sha256": current["sha256"]}


def _availability(record: dict, current: dict, latest: dict, *, cached: bool, channel="latest") -> dict:
    try:
        version_order = _compare_versions(latest["version"], current["version"])
        if channel == "previous":
            available = version_order != 0 or latest["sha256"] != current["sha256"]
        else:
            available = (version_order > 0 or
                         (version_order == 0 and latest["sha256"] != current["sha256"]))
    except (KeyError, TypeError, ContractError):
        return {"status": "unavailable", "available": False, "message": "invalid cached release metadata"}
    return {"status": "available" if available else "current", "available": available,
            "current_version": current["version"], "current_sha256": current["sha256"],
            "version": latest["version"], "commit": latest.get("commit"),
            "sha256": latest["sha256"], "channel": channel, "cached": cached,
            "install_id": record["install_id"]}


def notification(project=None, *, application_roots=None, fetch=_fetch,
                 now: int | None = None) -> dict | None:
    """Return an update notice when opted in; network access is limited to once daily."""
    try:
        settings = preferences(application_roots=application_roots)
    except Exception:
        return None
    if not settings["enabled"]:
        return None
    try:
        path = (Path(settings["receipt"]) if settings["receipt"] else
                _select_receipt(None, project, application_roots))
        record, current, _ = _receipt(path, application_roots)
    except Exception:
        return None
    now = int(time.time()) if now is None else int(now)
    cache_path = _cache_path(application_roots)
    try:
        notice_lock = cache_path.with_suffix(".lock")
        _recover_notice_lock(notice_lock)
        with locked(notice_lock, timeout=0.05):
            value = _cached(cache_path, now)
            same_repository = bool(value and value.get("repository") == settings["repository"])
            channels = value.get("channels", {}) if same_repository else {}
            if "latest" in channels:
                detail = channels["latest"]
                if detail is None:
                    return None
            else:
                try:
                    bounded_fetch = _bounded(fetch, seconds=NOTIFICATION_TIMEOUT)
                    releases = _releases(settings["repository"], bounded_fetch)
                    if not releases:
                        raise ValueError("no published Uni Learn build")
                    fresh = _release_details(releases[0], bounded_fetch, settings["repository"])
                    detail = {key: item for key, item in fresh.items() if key != "runtime"}
                    atomic_json(cache_path, {"schema_version": 1, "checked_at": now,
                                             "repository": settings["repository"],
                                             "channels": {"latest": detail}})
                except Exception as exc:
                    old_channels = dict(channels) if isinstance(channels, dict) else {}
                    old_channels["latest"] = None
                    try:
                        atomic_json(cache_path, {"schema_version": 1, "checked_at": now,
                                                 "repository": settings["repository"],
                                                 "channels": old_channels,
                                                 "failure": str(exc)[:500]})
                    except Exception:
                        pass
                    return None
    except (ConflictError, OSError, ValueError, TypeError):
        return None
    availability = _availability(record, current, detail, cached=True)
    if not availability.get("available"):
        return None
    archive = _archive()
    argv = ([sys.executable, str(archive)] if archive else
            [sys.executable, "-m", "uni_learn"])
    argv.extend(("update", "--receipt", str(path), "--repository", settings["repository"]))
    command = _format_command(argv)
    return {"status": "available", "version": detail["version"], "receipt": str(path),
            "command": command, "command_argv": argv,
            "message": f"Uni Learn {detail['version']} is available. Run: {command}", "cached": True}


def _recover_notice_lock(path):
    """Recover only an abandoned cache lock; project and installer locks are untouched."""
    try:
        observed = path.lstat()
        if (not stat.S_ISREG(observed.st_mode) or observed.st_size != 0
                or time.time() - observed.st_mtime < 60):
            return
        current = path.lstat()
        if (current.st_dev, current.st_ino) == (observed.st_dev, observed.st_ino):
            path.unlink()
    except OSError:
        pass


def _format_command(argv, *, platform=None):
    if (os.name if platform is None else platform) == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in argv)
    return shlex.join(argv)


def update(receipt: str | Path | None = None, *, project=None, channel: str = "latest", repository: str = REPOSITORY,
           application_roots=None,
           fetch=_fetch, run=subprocess.run) -> dict:
    """Run a verified release against the selected installation's existing setup."""
    if channel not in {"latest", "previous"}:
        raise ValueError("channel must be latest or previous")
    repository = _repository(repository)
    from . import install

    receipt_path = _select_receipt(receipt, project, application_roots)
    record, current, _ = _receipt(receipt_path, application_roots)
    bounded_fetch = _bounded(fetch, seconds=EXPLICIT_TIMEOUT)
    releases = _releases(repository, bounded_fetch)
    index = 0 if channel == "latest" else 1
    if len(releases) <= index:
        raise ValueError(f"no {channel} managed release is published")
    detail = _release_details(releases[index], bounded_fetch, repository)
    if channel == "latest" and _compare_versions(detail["version"], current["version"]) < 0:
        raise ValueError("latest published build is older than the installed runtime")
    if detail["version"] == current["version"] and detail["sha256"] == current["sha256"]:
        return {"status": "current", "version": current["version"], "sha256": current["sha256"],
                "receipt": str(receipt_path)}
    args = [sys.executable, "setup", "--host", record["profile"], "--scope", record.get("scope", "project")]
    if record.get("project"):
        args.extend(("--project", record["project"]))
    if record.get("generation"):
        args.extend(("--generation", record["generation"]))
    environment = os.environ.copy()
    if application_roots:
        for key in ("config", "data", "state", "cache"):
            environment["XDG_" + key.upper() + "_HOME"] = str(Path(application_roots[key]).parent)
    if record.get("scope") == "user":
        environment["HOME"] = record["user_home"]
        environment["USERPROFILE"] = record["user_home"]
        environment["CODEX_HOME"] = record["codex_home"]
    with tempfile.TemporaryDirectory(prefix="uni-learn-update-") as directory:
        archive = Path(directory) / "uni-learn.pyz"
        archive.write_bytes(detail["runtime"])
        args.insert(1, str(archive))
        try:
            completed = run(args + ["--json"], check=True, env=environment,
                            capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            detail_text = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
            raise RuntimeError("verified update setup failed: " + detail_text[-1000:]) from exc
    try:
        setup_result = json.loads(completed.stdout) if completed.stdout else None
    except (ValueError, TypeError):
        setup_result = None
    expected_receipt = str(receipt_path.resolve())
    if (not isinstance(setup_result, dict) or setup_result.get("status") not in {"ready", "partial"}
            or setup_result.get("receipt") != expected_receipt
            or setup_result.get("profile") != record["profile"]
            or setup_result.get("scope") != record.get("scope", "project")):
        raise RuntimeError("verified update setup returned an invalid or incomplete result")
    _verify_applied(receipt_path, record, detail)
    state_status = setup_result["status"]
    return {"status": "updated" if state_status == "ready" else "updated_partial",
            "version": detail["version"], "commit": detail["commit"],
            "sha256": detail["sha256"], "receipt": str(receipt_path),
            "setup_status": state_status, "setup": setup_result}


def _verify_applied(receipt_path, previous, release):
    from .install import _no_links

    archive = (Path(previous["data_root"]) / "releases" / release["version"] /
               previous["install_id"] / "uni-learn.pyz")
    try:
        _no_links(archive)
        with Path(receipt_path).open("rb") as stream:
            content = stream.read(MAX_JSON + 1)
        if len(content) > MAX_JSON:
            raise ValueError("receipt exceeds size limit")
        receipt = json.loads(content)
        if (not isinstance(receipt, dict) or receipt.get("runtime_version") != release["version"]
                or receipt.get("install_id") != previous["install_id"]
                or not isinstance(receipt.get("owned"), list)
                or not any(isinstance(row, dict) and row.get("path") == str(archive)
                           and row.get("sha256") == release["sha256"] for row in receipt["owned"])
                or archive.stat().st_size != release["bytes"]
                or hashlib.sha256(archive.read_bytes()).hexdigest() != release["sha256"]):
            raise ValueError("receipt or runtime hash does not match the selected release")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("update setup did not install the selected runtime: " + str(exc)) from exc
