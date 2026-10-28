"""Compact, source-linked handoffs."""

from __future__ import annotations

from .contracts import validate_handoff
from .contracts import canonical
from .budget import measure


def make(task_id, task_class, objective, constraints, findings, sources, uncertainties, failed_attempts, next_decision):
    return validate_handoff({"schema_version": 1, "task_id": task_id, "task_class": task_class, "objective": objective,
                             "constraints": constraints, "findings": findings, "sources": sources,
                             "uncertainties": uncertainties, "failed_attempts": failed_attempts, "next_decision": next_decision})


def report(record, *, exact_counter=None):
    validate_handoff(record)
    budget = measure(canonical(record).decode("utf-8"), exact_counter=exact_counter)
    return {"handoff": record, "budget": budget, "soft_target_tokens": 600,
            "over_target": budget["tokens"] > 600 if "tokens" in budget else None}
