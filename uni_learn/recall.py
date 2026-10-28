"""Bounded latest-concept recall from an atomic, rebuildable offset index."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from .contracts import ContractError, utc
from .learning_log import append, log_path, make_event, scan, validate_event
from .paths import project_id, roots
from .state import locked, show_project, show_task

INTERVALS = (1, 3, 7, 14, 30)


def next_due(success, successful_reviews=0, now=None):
    if type(success) is not bool or type(successful_reviews) is not int or successful_reviews < 0:
        raise ContractError("invalid recall outcome")
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ContractError("recall clock must have a timezone")
    days = INTERVALS[min(successful_reviews, len(INTERVALS) - 1)] if success else 1
    return (now.astimezone(dt.timezone.utc) + dt.timedelta(days=days)).isoformat().replace("+00:00", "Z")


def review_event(project, original, *, success, attempt, now=None):
    validate_event(original)
    clock = now or dt.datetime.now(dt.timezone.utc)
    streak = original["success_streak"] + 1 if success else 0
    return make_event(project, original["task_id"], original["tags"], original["prompt"],
                      attempt, original["correction"], "pass" if success else "fail",
                      misconception=original["misconception"], concept_id=original["concept_id"],
                      review_of=original["entry_id"], success_streak=streak,
                      at=clock.isoformat().replace("+00:00", "Z"),
                      due_at=next_due(success, streak, clock))


def index_path(project, application_roots=None):
    return Path((application_roots or roots())["cache"]) / "projects" / project_id(project) / "recall.sqlite3"


def _revision(path):
    digest = hashlib.sha256()
    if not path.exists():
        return "empty"
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _rebuild_locked(project, application_roots):
    source, target = log_path(project, application_roots), index_path(project, application_roots)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".recall-", dir=target.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.execute("CREATE TABLE meta (revision TEXT)")
            db.execute("CREATE TABLE events (entry_id TEXT PRIMARY KEY, concept_id TEXT, due_at TEXT, tags TEXT, offset INTEGER, length INTEGER, sha256 TEXT)")
            db.execute("CREATE INDEX concept_offsets ON events(concept_id, offset)")
            with source.open("rb") as stream:
                for offset, length, event in scan(source):
                    stream.seek(offset)
                    digest = hashlib.sha256(stream.read(length)).hexdigest()
                    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                               (event["entry_id"], event["concept_id"], event["due_at"],
                                json.dumps(event["tags"]), offset, length, digest))
            db.execute("INSERT INTO meta VALUES (?)", (_revision(source),))
            db.commit()
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def rebuild(project, *, application_roots=None):
    source = log_path(project, application_roots)
    if not source.exists():
        return None
    with locked(source.with_suffix(".lock")):
        return _rebuild_locked(project, application_roots)


def _session(project, topic, session, task, application_roots):
    if session and task:
        current = show_task(project, session, task, application_roots)
        return {"status": "session", "items": [{"task_id": task, "attempts": current["attempts"][-5:], "topic": topic}]}
    if topic:
        return {"status": "topic", "items": [{"topic": topic, "instruction": "Ask unaided recall before hints."}]}
    return {"status": "disabled", "items": [], "reason": "supply a session/task or topic for nonpersistent recall"}


def recall(project, *, limit=5, topic=None, session=None, task=None, application_roots=None, now=None):
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ContractError("recall limit must be 1-5")
    settings = show_project(project, application_roots)
    if not settings["logging"]:
        return _session(project, topic, session, task, application_roots)
    source = log_path(project, application_roots)
    if not source.exists():
        return _session(project, topic, session, task, application_roots) if topic or (session and task) else {"status": "empty", "items": []}
    index = index_path(project, application_roots)
    today = now or dt.datetime.now(dt.timezone.utc)
    if today.tzinfo is None:
        raise ContractError("recall clock must have a timezone")
    with locked(source.with_suffix(".lock")):
        rebuild_needed = not index.exists()
        if not rebuild_needed:
            try:
                with closing(sqlite3.connect(index)) as db:
                    meta = db.execute("SELECT revision FROM meta").fetchone()
                    rebuild_needed = not meta or meta[0] != _revision(source)
                    db.execute("SELECT sha256 FROM events LIMIT 1")
            except sqlite3.DatabaseError:
                rebuild_needed = True
        if rebuild_needed:
            _rebuild_locked(project, application_roots)
        with closing(sqlite3.connect(index)) as db:
            rows = db.execute(
                "SELECT e.entry_id,e.concept_id,e.due_at,e.tags,e.offset,e.length,e.sha256 FROM events e "
                "WHERE NOT EXISTS(SELECT 1 FROM events newer WHERE newer.concept_id=e.concept_id AND newer.offset>e.offset) "
                "ORDER BY e.due_at,e.offset"
            ).fetchall()
        items = []
        with source.open("rb") as stream:
            ordered = sorted(rows, key=lambda row: (dt.datetime.fromisoformat(row[2].replace("Z", "+00:00"))
                             if row[2] else dt.datetime.min.replace(tzinfo=dt.timezone.utc), row[4]))
            for entry_id, concept_id, due_at, tags, offset, length, digest in ordered:
                if due_at and dt.datetime.fromisoformat(due_at.replace("Z", "+00:00")) > today:
                    continue
                if topic and topic not in json.loads(tags):
                    continue
                if type(offset) is not int or type(length) is not int or offset < 0 or length < 1:
                    raise ContractError("invalid recall offset")
                stream.seek(offset)
                raw = stream.read(length)
                if len(raw) != length or not raw.endswith(b"\n") or hashlib.sha256(raw).hexdigest() != digest:
                    raise ContractError("stale recall offset/hash; rebuild the cache")
                event = validate_event(json.loads(raw))
                if event["entry_id"] != entry_id or event["concept_id"] != concept_id:
                    raise ContractError("stale recall identity")
                items.append(event)
                if len(items) == limit:
                    break
    if not items and (topic or (session and task)):
        return _session(project, topic, session, task, application_roots)
    return {"status": "due", "items": items}
