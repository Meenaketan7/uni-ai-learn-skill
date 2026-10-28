"""Focused validation of versioned runtime records."""
from __future__ import annotations

import datetime as dt
import json
from .paths import safe_id


class ContractError(ValueError):
    pass


TASK_CLASSES = {"discover", "research", "transform", "diagnose", "implement", "teach", "plan", "verify"}
INTENTS = {"learn", "hint", "debug", "review", "design", "recall", "compact", "comprehension",
           "conceptual-inquiry", "artifact-critique", "teach-back", "contract-design", "tradeoff-design"}


def require_fields(value, fields, *, exact=False):
    if not isinstance(value, dict):
        raise ContractError("expected object")
    if set(fields) - set(value):
        raise ContractError("missing fields: " + ", ".join(sorted(set(fields) - set(value))))
    if exact and set(value) != set(fields):
        raise ContractError("unknown fields: " + ", ".join(sorted(set(value) - set(fields))))
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ContractError("unsupported schema_version")
    return value


def text(value, field="text", *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ContractError(field + " must be a string" + ("" if empty else " with content"))
    return value


def integer(value, field="integer", minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ContractError("invalid " + field)
    return value


def strings(value, field="list"):
    if not isinstance(value, list):
        raise ContractError(field + " must be a list")
    for entry in value:
        text(entry, field)
    return value


def utc(value):
    text(value, "UTC date-time")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("invalid date-time") from exc
    if not value.endswith("Z") or parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise ContractError("expected UTC date-time ending Z")
    return value


def evidence_records(value, kind):
    if not isinstance(value, list):
        raise ContractError(kind + " must be a list")
    fields = {
        "decisions": {"choice", "reason", "source_ids"},
        "failed_approaches": {"attempt", "observation", "explanation", "unresolved"},
        "open_questions": {"unknown", "why", "evidence_needed"},
    }[kind]
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ContractError("invalid " + kind + " record")
        for key in fields:
            if key == "source_ids":
                strings(entry[key], key)
            elif key == "unresolved":
                if type(entry[key]) is not bool:
                    raise ContractError("unresolved must be boolean")
            else:
                text(entry[key], key)
    return value


def validate_state(value, kind):
    if kind not in {"project", "task"}:
        raise ContractError("unknown state kind")
    project = {"schema_version", "revision", "project_id", "learning", "logging", "log_cap_bytes"}
    task = {"schema_version", "revision", "project_id", "session_id", "task_id", "objective",
            "status", "bypass", "explicit_learning", "hint_level", "attempts", "predictions",
            "hypotheses", "misconceptions", "intent", "constraints", "acceptance_criteria",
            "next_action", "decisions", "failed_approaches", "open_questions",
            "difficulty_evidence", "workflow_position", "full_answer_requested", "prediction_pending", "checkpoint"}
    require_fields(value, project if kind == "project" else task, exact=True)
    integer(value["revision"], "revision")
    safe_id(value["project_id"])
    if kind == "project":
        if type(value["learning"]) is not bool or type(value["logging"]) is not bool:
            raise ContractError("project toggles must be boolean")
        integer(value["log_cap_bytes"], "log cap", 1)
        return value
    for key in ("session_id", "task_id"):
        safe_id(value[key])
    text(value["objective"], "objective")
    text(value["status"], "status")
    text(value["intent"], "intent")
    if value["status"] not in {"active", "closed"} or value["intent"] not in INTENTS:
        raise ContractError("invalid task status or intent")
    for key in ("bypass", "explicit_learning", "full_answer_requested", "prediction_pending"):
        if type(value[key]) is not bool:
            raise ContractError(key + " must be boolean")
    integer(value["hint_level"], "hint level", 0, 3)
    for key in ("attempts", "predictions", "hypotheses", "misconceptions", "constraints",
                "acceptance_criteria", "difficulty_evidence"):
        strings(value[key], key)
    for key in ("decisions", "failed_approaches", "open_questions"):
        evidence_records(value[key], key)
    text(value["next_action"], "next_action")
    text(value["workflow_position"], "workflow_position")
    reference = value["checkpoint"]
    if reference is not None:
        if not isinstance(reference, dict) or set(reference) != {"id", "revision", "sha256"}:
            raise ContractError("invalid checkpoint reference")
        safe_id(reference["id"])
        integer(reference["revision"], "checkpoint revision")
        digest = text(reference["sha256"], "checkpoint digest")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ContractError("invalid checkpoint digest")
    return value


def validate_handoff(value):
    fields = {"schema_version", "task_id", "task_class", "objective", "constraints",
              "findings", "sources", "uncertainties", "failed_attempts", "next_decision"}
    require_fields(value, fields, exact=True)
    safe_id(value["task_id"])
    text(value["task_class"], "task_class")
    if value["task_class"] not in TASK_CLASSES:
        raise ContractError("unknown task class")
    for key in ("objective", "next_decision"):
        text(value[key], key)
    for key in ("constraints", "uncertainties", "failed_attempts"):
        strings(value[key], key)
    if not isinstance(value["sources"], list) or not isinstance(value["findings"], list):
        raise ContractError("sources/findings must be lists")
    ids = set()
    allowed = {"id", "path", "symbol", "query", "range", "revision", "sha256", "url", "retrieved_at", "method"}
    for source in value["sources"]:
        if not isinstance(source, dict) or not set(source) <= allowed:
            raise ContractError("invalid source")
        ident = text(source.get("id"), "source ID")
        if ident in ids:
            raise ContractError("duplicate source ID")
        ids.add(ident)
        if source.get("path"):
            text(source["path"], "source path")
            text(source.get("revision") or source.get("sha256"), "source revision")
        elif source.get("url"):
            if not text(source["url"], "URL").startswith(("https://", "http://")):
                raise ContractError("invalid source URL")
            try:
                dt.datetime.fromisoformat(text(source.get("retrieved_at")).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ContractError("invalid retrieval date") from exc
        else:
            raise ContractError("source needs a reconstructable path or URL")
    for finding in value["findings"]:
        if not isinstance(finding, dict) or not {"claim", "source_ids"} <= set(finding):
            raise ContractError("finding needs claim and sources")
        text(finding["claim"], "claim")
        sources = strings(finding["source_ids"], "source_ids")
        if not sources or not set(sources) <= ids:
            raise ContractError("finding has missing or unknown source")
    return value


def validate(kind, record):
    if kind in {"project", "task"}:
        return validate_state(record, kind)
    if kind == "handoff":
        return validate_handoff(record)
    if kind == "checkpoint":
        from .compaction import validate_checkpoint
        return validate_checkpoint(record)
    if kind == "log":
        from .learning_log import validate_event
        return validate_event(record)
    if kind == "models":
        from .routing import validate_registry
        return validate_registry(record)
    if kind == "adapter":
        from .adapters import validate_result
        return validate_result(record)
    raise ContractError("unknown record kind")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
