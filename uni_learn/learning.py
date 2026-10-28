"""Task-scoped activation and explicit learning workflow gates."""
from __future__ import annotations

import copy
from .contracts import ContractError, INTENTS as VALID_INTENTS, text, validate_state
from .state import show_project, set_project, show_task, update_task, ship_task

INTENTS = {"learn": "learn", "learn-hint": "hint", "learn-debug": "debug",
           "learn-review": "review", "learn-design": "design", "learn-recall": "recall",
           "learn-compact": "compact"}
RESOURCES = {key: "core/learning/" + name + ".md" for key, name in (
    ("learn", "workflow"), ("learn-hint", "hint"), ("learn-debug", "debug"),
    ("learn-review", "review"), ("learn-design", "design"), ("learn-recall", "recall"))}
RESOURCES["learn-compact"] = "core/compaction/prepare.md"
DOMAINS = {"coding", "math-proof", "product-system", "research-projects"}
UNKNOWN = {"i don't know", "i do not know", "no idea", "unknown"}


def domain_resources(domains):
    if not isinstance(domains, list) or any(domain not in DOMAINS for domain in domains):
        raise ContractError("unknown domain pack")
    return ["core/domains/" + domain + ".md" for domain in dict.fromkeys(domains)]


def _attempted(task):
    return any(attempt.strip().lower() not in UNKNOWN for attempt in task["attempts"])


def transition(task, event):
    value = copy.deepcopy(validate_state(task, "task"))
    if value["status"] != "active":
        raise ContractError("closed task")
    kind = event.get("kind")
    if kind in {"attempt", "prediction", "hypothesis", "misconception"}:
        content = text(event.get("text"), kind)
        field = {"attempt": "attempts", "prediction": "predictions", "hypothesis": "hypotheses",
                 "misconception": "misconceptions"}[kind]
        value[field].append(content)
        if kind == "prediction":
            value["prediction_pending"] = True
        value["workflow_position"] = kind
    elif kind == "hint":
        if not _attempted(value) and not value["predictions"]:
            raise ContractError("hint requires an attempt or prerequisite question")
        value["hint_level"] = min(3, value["hint_level"] + 1)
        value["workflow_position"] = "hint"
    elif kind == "request_answer":
        if not _attempted(value) and not value["bypass"]:
            raise ContractError("full answer requires an adequate attempt")
        value["full_answer_requested"] = True
    elif kind == "reveal":
        if not value["bypass"] and not value["prediction_pending"]:
            raise ContractError("obtain a prediction for this action first")
        value["prediction_pending"] = False
        value["workflow_position"] = "inspect-evidence"
    elif kind == "observe":
        hypothesis = text(event.get("hypothesis"), "hypothesis")
        observation = text(event.get("observation"), "observation")
        explanation = text(event.get("explanation"), "explanation")
        if type(event.get("unresolved")) is not bool:
            raise ContractError("observation requires unresolved status")
        value["failed_approaches"].append({
            "attempt": hypothesis, "observation": observation,
            "explanation": explanation, "unresolved": event["unresolved"]})
        value["workflow_position"] = "revise-belief"
    elif kind in {"decision", "failure", "open_question"}:
        field = {"decision": "decisions", "failure": "failed_approaches", "open_question": "open_questions"}[kind]
        value[field].append(event.get("record"))
    elif kind == "ship":
        value["bypass"] = True
    elif kind == "accept":
        value["status"], value["bypass"] = "closed", False
    elif kind == "intent":
        intent = text(event.get("intent"), "intent")
        if intent not in VALID_INTENTS:
            raise ContractError("unknown internal intent")
        value["intent"] = intent
    elif kind == "teach_back":
        value["difficulty_evidence"].append(text(event.get("text"), "teach-back evidence"))
        value["workflow_position"] = "transfer"
    else:
        raise ContractError("unknown learning event")
    return validate_state(value, "task")


def dispatch(project, session, task, command, argument="", *, domains=None,
             full_answer=False, revealing_action=False, application_roots=None):
    if command not in INTENTS:
        raise ContractError("unknown learning command")
    mode = show_project(project, application_roots)
    argument = text(argument, "argument", empty=True)
    if command == "learn" and argument.strip().lower() in {"on", "off"}:
        changed = set_project(project, learning=argument.strip().lower() == "on",
                              expected_revision=mode["revision"], application_roots=application_roots)
        if session and task and not changed["learning"]:
            current = show_task(project, session, task, application_roots)
            update_task(project, session, task, {"explicit_learning": False},
                        expected_revision=current["revision"], application_roots=application_roots)
        return {"action": "mode_changed", "project_learning": changed["learning"],
                "resources": ["core/efficiency/entry.md"], "fresh_session_recommended": not changed["learning"]}
    if not session or not task:
        if command == "learn" and not argument.strip():
            return {"action": "ask_objective", "project_learning": mode["learning"],
                    "resources": [RESOURCES["learn"]]}
        raise ContractError("start a task and supply its session/task IDs")
    current = show_task(project, session, task, application_roots)
    if current["status"] != "active":
        raise ContractError("task is closed")
    if argument.strip().lower() == "ship this":
        current = ship_task(project, session, task, application_roots=application_roots)
    if command == "learn-compact":
        return {"action": "compact", "task": current, "project_learning": mode["learning"],
                "resources": [RESOURCES[command]], "native_compaction_executed": False}
    if current["bypass"]:
        return {"action": "ship", "project_learning": mode["learning"], "task": current,
                "resources": ["core/efficiency/entry.md"]}
    selected_packs = domain_resources(domains or [])
    alternatives = {
        "learn": {"learn", "comprehension", "conceptual-inquiry"},
        "learn-review": {"artifact-critique", "teach-back"},
        "learn-design": {"contract-design", "tradeoff-design"},
    }
    defaults = {"learn-review": "artifact-critique", "learn-design": "tradeoff-design"}
    intent = current["intent"] if current["intent"] in alternatives.get(command, set()) else defaults.get(command, INTENTS[command])
    change = {"intent": intent, "explicit_learning": True, "workflow_position": intent}
    action = INTENTS[command]
    if revealing_action and not current["prediction_pending"]:
        action = "ask_prediction"
        change["next_action"] = "obtain a prediction for the next revealing action"
    elif full_answer:
        if not _attempted(current):
            action = "ask_prerequisite" if any(a.strip().lower() in UNKNOWN for a in current["attempts"]) else "ask_attempt"
            change["next_action"] = "ask a smaller prerequisite question before a complete answer"
        else:
            action = "full_answer"
            change["full_answer_requested"] = True
            change["next_action"] = "provide requested answer, then teach-back and transfer"
    elif command == "learn-hint":
        if not _attempted(current) and not current["predictions"]:
            action = "ask_attempt"
            change["next_action"] = "ask an attempt or smaller prerequisite question"
        else:
            change["hint_level"] = min(3, current["hint_level"] + 1)
            change["next_action"] = "give one bounded hint; no automatic full answer"
    else:
        change["next_action"] = {
            "debug": "ask hypothesis, predicted observation and smallest test; compare actual evidence",
            "artifact-critique": "ask self-assessment before evidence-based critique",
            "teach-back": "ask explanation, probe one gap, then test transfer",
            "contract-design": "derive acceptance criteria and boundary examples before implementation",
            "tradeoff-design": "ask constraints and alternatives before defending a choice",
            "recall": "ask unaided retrieval before hints",
            "comprehension": "predict a bounded artifact's behavior, inspect and reconstruct",
            "conceptual-inquiry": "state the problem solved, predict behavior and examine assumptions",
        }.get(intent, "ask an attempt or prediction before revealing")
        if command == "learn-debug" and (argument.strip().lower().startswith("autopsy") or
                any(not failure["unresolved"] for failure in current["failed_approaches"])):
            change["next_action"] = "start autopsy from existing resolution evidence; ask mistaken belief, correction and prevention"
    if revealing_action and action != "ask_prediction":
        change["prediction_pending"] = False
    current = update_task(project, session, task, change, expected_revision=current["revision"],
                          application_roots=application_roots)
    return {"action": action, "argument": argument, "project_learning": mode["learning"],
            "hint_level": current["hint_level"], "task": current,
            "resources": [RESOURCES[command], *selected_packs]}
