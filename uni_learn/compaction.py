"""Durable retention, explicit native procedures, and revision-checked resume."""
from __future__ import annotations

import hashlib
import copy
import json
import uuid
from .contracts import (ContractError, canonical, evidence_records, integer,
                        require_fields, strings, text, validate_state)
from .paths import project_dir, project_id, safe_id
from .pointers import validate as validate_pointer, relocate
from .state import atomic_json, locked, show_project, show_task, task_path

REQUIRED = {
    "schema_version", "checkpoint_id", "revision", "task_revision", "task_sha256",
    "project_revision", "project_id", "session_id", "task_id", "mode", "bypass",
    "objective", "constraints", "acceptance_criteria", "workflow_position",
    "next_action", "decisions", "failed_approaches", "open_questions",
    "learning", "pointers", "audit",
}
LEARNING_FIELDS = {"attempts", "predictions", "hypotheses", "misconceptions",
                   "hint_level", "intent", "difficulty_evidence", "prediction_pending"}


def task_digest(task):
    return hashlib.sha256(canonical({key: value for key, value in task.items() if key != "checkpoint"})).hexdigest()


def checkpoint_path(project, checkpoint_id, application_roots=None):
    return project_dir(project, application_roots) / "checkpoints" / (safe_id(checkpoint_id) + ".json")


def validate_checkpoint(value):
    require_fields(value, REQUIRED, exact=True)
    for key in ("checkpoint_id", "project_id", "session_id", "task_id"):
        safe_id(value[key])
    for key in ("revision", "task_revision", "project_revision"):
        integer(value[key], key)
    text(value["task_sha256"], "task hash")
    if len(value["task_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in value["task_sha256"]):
        raise ContractError("invalid task hash")
    if value["mode"] not in ("on", "off") or type(value["bypass"]) is not bool:
        raise ContractError("invalid checkpoint mode/bypass")
    for key in ("objective", "workflow_position", "next_action"):
        text(value[key], key)
    for key in ("constraints", "acceptance_criteria"):
        strings(value[key], key)
    for key in ("decisions", "failed_approaches", "open_questions"):
        evidence_records(value[key], key)
    learning = value["learning"]
    if learning is not None:
        if not isinstance(learning, dict) or set(learning) != LEARNING_FIELDS:
            raise ContractError("learning retention fields mismatch")
        integer(learning["hint_level"], "hint level", 0, 3)
        text(learning["intent"], "intent")
        if type(learning["prediction_pending"]) is not bool:
            raise ContractError("prediction pending status must be boolean")
        for key in LEARNING_FIELDS - {"hint_level", "intent", "prediction_pending"}:
            strings(learning[key], key)
    if value["mode"] == "on" and not value["bypass"] and learning is None:
        raise ContractError("active learning checkpoint requires learning state")
    if not isinstance(value["pointers"], list):
        raise ContractError("pointers must be a list")
    for pointer in value["pointers"]:
        validate_pointer(pointer)
    audit = value["audit"]
    fields = {"retained", "pointer_converted", "dropped", "before_valid", "after_valid", "known_missing_context"}
    if not isinstance(audit, dict) or set(audit) != fields:
        raise ContractError("retention audit fields mismatch")
    for key in ("retained", "pointer_converted", "known_missing_context"):
        strings(audit[key], key)
    if type(audit["before_valid"]) is not bool or type(audit["after_valid"]) is not bool:
        raise ContractError("audit validation status must be boolean")
    if not isinstance(audit["dropped"], list):
        raise ContractError("dropped audit must be a list")
    for item in audit["dropped"]:
        if not isinstance(item, dict) or set(item) != {"id", "reason"}:
            raise ContractError("dropped item needs ID and reason")
        text(item["id"], "dropped ID")
        text(item["reason"], "drop reason")
        if item["id"] in REQUIRED:
            raise ContractError("cannot drop a mandatory retention field")
    return value


def _merge(original, extra):
    result = list(original)
    if not isinstance(extra, list):
        raise ContractError("retention additions must be lists")
    for item in extra:
        if item not in result:
            result.append(item)
    return result


def prepare(request, *, application_roots=None):
    strategy = request.get("strategy", "fresh_session")
    if strategy not in {"native_with_instructions", "checkpoint_around_native", "fresh_session"}:
        raise ContractError("unknown compaction strategy")
    project = request["project"]
    session, task_id = safe_id(request["session_id"]), safe_id(request["task_id"])
    pointer_results = [validate_pointer(p) for p in request.get("pointers", [])]
    if any(p["status"] != "current" for p in pointer_results):
        raise ContractError("material evidence must be current or have a current durable fallback")
    checkpoint_id = safe_id(request.get("checkpoint_id") or uuid.uuid4().hex)
    lock = task_path(project, session, task_id, application_roots).with_suffix(".lock")
    with locked(lock):
        state = show_task(project, session, task_id, application_roots)
        retained_task = copy.deepcopy(state)
        retained_task["revision"] += 1
        settings = show_project(project, application_roots)
        active = not state["bypass"] and (settings["learning"] or state["explicit_learning"])
        learning = {key: state[key] for key in LEARNING_FIELDS} if active else None
        if learning is not None:
            learning["difficulty_evidence"] = _merge(state["difficulty_evidence"], request.get("difficulty_evidence", []))
        value = {
            "schema_version": 1, "checkpoint_id": checkpoint_id, "revision": 0,
            "task_revision": retained_task["revision"],
            "task_sha256": task_digest(retained_task),
            "project_revision": settings["revision"],
            "project_id": project_id(project), "session_id": session, "task_id": task_id,
            "mode": "on" if active else "off", "bypass": state["bypass"],
            "objective": state["objective"],
            "workflow_position": request.get("workflow_position", state["workflow_position"]),
            "next_action": request.get("next_action", state["next_action"]),
            "learning": learning, "pointers": request.get("pointers", []),
            "audit": {
                "retained": sorted(REQUIRED - {"audit", "pointers"}) + request.get("retained", []),
                "pointer_converted": request.get("pointer_converted", []),
                "dropped": request.get("dropped", []), "before_valid": True, "after_valid": True,
                "known_missing_context": request.get("known_missing_context", []),
            },
        }
        for key in ("constraints", "acceptance_criteria", "decisions", "failed_approaches", "open_questions"):
            value[key] = _merge(state[key], request.get(key, []))
            retained_task[key] = copy.deepcopy(value[key])
        for key in ("workflow_position", "next_action"):
            retained_task[key] = value[key]
        if learning is not None:
            retained_task["difficulty_evidence"] = list(learning["difficulty_evidence"])
        value["task_sha256"] = task_digest(retained_task)
        validate_checkpoint(value)
        digest = hashlib.sha256(canonical(value)).hexdigest()
        retained_task["checkpoint"] = {"id": checkpoint_id, "revision": value["revision"], "sha256": digest}
        validate_state(retained_task, "task")
        path = checkpoint_path(project, checkpoint_id, application_roots)
        with locked(path.with_suffix(".lock")):
            if path.exists():
                raise ContractError("checkpoint ID already exists")
            try:
                atomic_json(path, value)
                reopened = validate_checkpoint(json.loads(path.read_text("utf-8")))
                if canonical(value) != canonical(reopened):
                    raise ContractError("checkpoint round-trip mismatch")
                atomic_json(task_path(project, session, task_id, application_roots), retained_task)
            except Exception:
                if path.is_file() and hashlib.sha256(path.read_bytes().rstrip(b"\n")).hexdigest() == digest:
                    path.unlink()
                raise
    capsule = {key: value[key] for key in (
        "checkpoint_id", "project_id", "session_id", "task_id", "objective", "constraints",
        "acceptance_criteria", "workflow_position", "next_action", "mode", "bypass")}
    capsule["hint_level"] = learning["hint_level"] if learning else None
    capsule["checkpoint_sha256"] = digest
    capsule["instruction"] = "Reopen and validate this checkpoint before continuing."
    return {"status": "prepared", "checkpoint": value, "resume_capsule": capsule,
            "strategy": strategy, "native_command": request.get("native_command"),
            "native_compaction_executed": False,
            "checkpoint_sha256": hashlib.sha256(canonical(value)).hexdigest()}


def resume(project, checkpoint_id, *, application_roots=None):
    path = checkpoint_path(project, checkpoint_id, application_roots)
    if not path.exists():
        return {"safe": False, "reason": "checkpoint missing"}
    try:
        value = validate_checkpoint(json.loads(path.read_text("utf-8")))
        if value["project_id"] != project_id(project):
            return {"safe": False, "reason": "project identity mismatch"}
        task = show_task(project, value["session_id"], value["task_id"], application_roots)
        settings = show_project(project, application_roots)
    except (OSError, ValueError) as exc:
        return {"safe": False, "reason": "missing or invalid retained state: " + str(exc)}
    reference = {"id": value["checkpoint_id"], "revision": value["revision"],
                 "sha256": hashlib.sha256(canonical(value)).hexdigest()}
    if task["checkpoint"] != reference:
        return {"safe": False, "reason": "checkpoint integrity/reference mismatch", "checkpoint": value}
    if task["revision"] != value["task_revision"] or task_digest(task) != value["task_sha256"]:
        return {"safe": False, "reason": "task changed since checkpoint; review and prepare again", "checkpoint": value}
    if settings["revision"] != value["project_revision"]:
        return {"safe": False, "reason": "project preferences changed since checkpoint", "checkpoint": value}
    audit = value["audit"]
    if audit["known_missing_context"] or not audit["before_valid"] or not audit["after_valid"]:
        return {"safe": False, "reason": "retention audit has missing or unvalidated context", "checkpoint": value}
    pointers = [relocate(p) for p in value["pointers"]]
    blockers = [p for p in pointers if p["status"] != "current"]
    return {"safe": not blockers, "checkpoint": value, "pointer_audit": pointers,
            "reason": "pointer review required" if blockers else "recorded state and evidence validated",
            "native_compaction_executed": None}
