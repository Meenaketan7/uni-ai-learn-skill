"""Project preferences and isolated task records."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from .contracts import ContractError, canonical, validate_state
from .paths import project_dir, project_id, safe_id


class ConflictError(RuntimeError):
    pass


@contextmanager
def locked(path: Path, timeout=2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            owned_lock = os.fstat(fd)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ConflictError(f"busy lock: {path}")
            time.sleep(.025)
    try:
        yield
    finally:
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (owned_lock.st_dev, owned_lock.st_ino):
                path.unlink()
        except FileNotFoundError:
            pass


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".uni-learn-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def project_path(project, application_roots=None):
    return project_dir(project, application_roots) / "project.json"


def task_path(project, session, task, application_roots=None):
    return project_dir(project, application_roots) / "sessions" / safe_id(session) / "tasks" / (safe_id(task) + ".json")


def project_default(project):
    return {"schema_version": 1, "revision": 0, "project_id": project_id(project), "learning": False, "logging": False, "log_cap_bytes": 1048576}


def show_project(project, application_roots=None):
    path = project_path(project, application_roots)
    value = json.loads(path.read_text("utf-8")) if path.exists() else project_default(project)
    validate_state(value, "project")
    if value["project_id"] != project_id(project):
        raise ContractError("project identity mismatch")
    return value


def set_project(project, *, learning=None, logging=None, log_cap_bytes=None, expected_revision=None, application_roots=None):
    path = project_path(project, application_roots)
    with locked(path.with_suffix(".lock")):
        value = show_project(project, application_roots)
        if expected_revision is not None and value["revision"] != expected_revision:
            raise ConflictError("project revision changed")
        for key, update in (("learning", learning), ("logging", logging), ("log_cap_bytes", log_cap_bytes)):
            if update is not None:
                value[key] = update
        value["revision"] += 1
        validate_state(value, "project")
        atomic_json(path, value)
        return value


def new_task(project, session, task, objective, *, application_roots=None):
    path = task_path(project, session, task, application_roots)
    with locked(path.with_suffix(".lock")):
        if path.exists():
            raise ConflictError("task ID already exists")
        value = {"schema_version": 1, "revision": 0, "project_id": project_id(project), "session_id": safe_id(session),
                 "task_id": safe_id(task), "objective": objective, "status": "active", "bypass": False, "explicit_learning": False,
                 "hint_level": 0, "attempts": [], "predictions": [], "hypotheses": [], "misconceptions": [],
                 "intent": "learn", "constraints": [], "acceptance_criteria": [], "next_action": "continue objective under project preference",
                 "decisions": [], "failed_approaches": [], "open_questions": [], "difficulty_evidence": [],
                 "workflow_position": "start", "full_answer_requested": False, "prediction_pending": False, "checkpoint": None}
        validate_state(value, "task")
        atomic_json(path, value)
        return value


def show_task(project, session, task, application_roots=None):
    path = task_path(project, session, task, application_roots)
    value = json.loads(path.read_text("utf-8"))
    validate_state(value, "task")
    if (value["project_id"], value["session_id"], value["task_id"]) != (project_id(project), session, task):
        raise ContractError("task identity mismatch")
    return value


def update_task(project, session, task, changes, *, expected_revision=None, application_roots=None):
    path = task_path(project, session, task, application_roots)
    allowed = {"objective", "status", "bypass", "explicit_learning", "hint_level", "attempts", "predictions", "hypotheses", "misconceptions", "intent", "constraints", "acceptance_criteria", "next_action", "decisions", "failed_approaches", "open_questions", "difficulty_evidence", "workflow_position", "full_answer_requested", "prediction_pending"}
    if not set(changes) <= allowed:
        raise ContractError("disallowed task update")
    with locked(path.with_suffix(".lock")):
        value = show_task(project, session, task, application_roots)
        if expected_revision is not None and value["revision"] != expected_revision:
            raise ConflictError("task revision changed")
        if "objective" in changes and changes["objective"] != value["objective"]:
            raise ConflictError("a different objective requires a new task")
        if value["status"] == "closed" and changes.get("status") != "closed":
            raise ConflictError("closed task")
        if "predictions" in changes and changes["predictions"] != value["predictions"]:
            changes = {**changes, "prediction_pending": True}
        value.update(changes)
        value["revision"] += 1
        validate_state(value, "task")
        atomic_json(path, value)
        return value


def ship_task(project, session, task, *, application_roots=None):
    return update_task(project, session, task, {"bypass": True, "next_action": "deliver direct answer and corrections"}, application_roots=application_roots)


def close_task(project, session, task, *, application_roots=None):
    return update_task(project, session, task, {"status": "closed", "bypass": False, "next_action": "none"}, application_roots=application_roots)
