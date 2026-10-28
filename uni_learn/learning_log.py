"""Opt-in append-only events with exact UTF-8 byte cap accounting."""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from pathlib import Path
from .contracts import ContractError, canonical, integer, require_fields, strings, text, utc
from .paths import project_dir, project_id, safe_id
from .state import ConflictError, locked, project_path, show_project


class LogFullError(RuntimeError):
    pass


def log_path(project, application_roots=None):
    return project_dir(project, application_roots) / "learning.jsonl"


def validate_event(event):
    fields = {"schema_version", "entry_id", "at", "project_id", "task_id", "tags",
              "prompt", "attempt", "misconception", "correction", "outcome", "due_at",
              "concept_id", "review_of", "success_streak"}
    require_fields(event, fields, exact=True)
    for key in ("entry_id", "project_id", "task_id", "concept_id"):
        safe_id(event[key])
    if event["review_of"] is not None:
        safe_id(event["review_of"])
    utc(event["at"])
    if event["due_at"] is not None:
        utc(event["due_at"])
    strings(event["tags"], "tags")
    for key in ("prompt", "attempt", "misconception", "correction", "outcome"):
        text(event[key], key, empty=key != "prompt")
    integer(event["success_streak"], "success streak")
    return event


def make_event(project, task_id, tags, prompt, attempt, correction, outcome, *,
               concept_id=None, due_at=None, at=None, misconception="", review_of=None, success_streak=0):
    now = at or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    utc(now)
    ident = uuid.uuid4().hex
    if due_at is None:
        due_at = (dt.datetime.fromisoformat(now.replace("Z", "+00:00")) + dt.timedelta(days=1)).isoformat().replace("+00:00", "Z")
    return validate_event({
        "schema_version": 1, "entry_id": ident, "at": now, "project_id": project_id(project),
        "task_id": task_id, "tags": tags, "prompt": prompt, "attempt": attempt,
        "misconception": misconception, "correction": correction, "outcome": outcome,
        "due_at": due_at, "concept_id": concept_id or ident,
        "review_of": review_of, "success_streak": success_streak,
    })


def scan(path: Path):
    if not path.exists():
        return
    with path.open("rb") as stream:
        offset = 0
        for line in stream:
            if not line.endswith(b"\n"):
                raise ContractError(f"incomplete final log line at {offset}")
            try:
                event = validate_event(json.loads(line.decode("utf-8")))
            except (UnicodeError, ValueError, TypeError) as exc:
                raise ContractError(f"corrupt log line at {offset}") from exc
            yield offset, len(line), event
            offset += len(line)


def append(project, event, *, application_roots=None):
    if not show_project(project, application_roots)["logging"]:
        return {"status": "disabled"}
    validate_event(event)
    if event["project_id"] != project_id(project):
        raise ContractError("log project mismatch")
    path = log_path(project, application_roots)
    line = canonical(event) + b"\n"
    with locked(project_path(project, application_roots).with_suffix(".lock")), locked(path.with_suffix(".lock")):
        settings = show_project(project, application_roots)
        if not settings["logging"]:
            return {"status": "disabled"}
        prior = {}
        for _, _, record in scan(path):
            if record["entry_id"] in prior:
                raise ContractError("duplicate historical event ID")
            prior[record["entry_id"]] = record
        if event["entry_id"] in prior:
            raise ContractError("event ID already exists")
        if event["review_of"] is not None:
            original = prior.get(event["review_of"])
            if not original or original["concept_id"] != event["concept_id"]:
                raise ContractError("review must reference an existing event for the same concept")
            latest = next(record for record in reversed(list(prior.values())) if record["concept_id"] == event["concept_id"])
            if latest["entry_id"] != original["entry_id"]:
                raise ConflictError("concept was already reviewed; use its latest event")
        size = path.stat().st_size if path.exists() else 0
        if size + len(line) > settings["log_cap_bytes"]:
            raise LogFullError("learning log cap reached; history preserved; use session/topic recall")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
    return {"status": "appended", "entry_id": event["entry_id"], "bytes": len(line)}
