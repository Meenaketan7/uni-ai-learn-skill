"""Offline, stdlib-only validator for paired acceptance evidence.

This module checks supplied records. It never runs a host or infers missing usage.
Run ``python -m tests.evaluation baseline.json candidate.json`` to inspect a pair.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from pathlib import Path


HASH = re.compile(r"[0-9a-f]{64}\Z")
KINDS = {"baseline", "efficiency", "routing"}
GRADES = {"pass", "fail", "unverified"}
SOURCES = {"observed", "estimated", "unavailable"}
USAGE = ("input_tokens", "output_tokens", "reasoning_tokens", "tool_output_tokens",
         "retries", "handoffs", "compaction_tokens", "latency_ms")
FIELDS = {"schema_version", "scenario_id", "scenario_sha256", "rubric_sha256", "task_class",
          "run_kind", "checked_at", "package_sha256", "host_profile", "model_binding",
          "reasoning", "tools", "cache_conditions", "evidence", "criteria", "critical_criteria",
          "usage", "cost", "currency", "cost_source", "synthetic"}


class EvidenceError(ValueError):
    """Record is incomplete, inconsistent, or not comparable."""


def _string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{name} must be a nonempty string")


def _hash(value, name):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise EvidenceError(f"{name} must be a lowercase SHA-256 digest")


def _date(value):
    _string(value, "checked_at")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("checked_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise EvidenceError("checked_at must be UTC")


def validate(record):
    """Validate one complete run record, preserving explicit unknowns."""
    if not isinstance(record, dict) or set(record) != FIELDS or type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise EvidenceError("invalid evidence fields/version")
    for key in ("scenario_id", "task_class", "host_profile", "model_binding", "reasoning",
                "cache_conditions", "evidence"):
        _string(record[key], key)
    for key in ("scenario_sha256", "rubric_sha256", "package_sha256"):
        _hash(record[key], key)
    _date(record["checked_at"])
    if not isinstance(record["run_kind"], str) or record["run_kind"] not in KINDS:
        raise EvidenceError("invalid run_kind")
    if type(record["synthetic"]) is not bool:
        raise EvidenceError("synthetic must be boolean")
    tools = record["tools"]
    if not isinstance(tools, list) or any(not isinstance(tool, str) or not tool.strip() for tool in tools) or len(tools) != len(set(tools)):
        raise EvidenceError("tools must be unique nonempty strings")
    criteria = record["criteria"]
    if not isinstance(criteria, dict) or not criteria:
        raise EvidenceError("criteria must be a nonempty grading map")
    for key, grade in criteria.items():
        _string(key, "criterion")
        if not isinstance(grade, str) or grade not in GRADES:
            raise EvidenceError("invalid criterion grade")
    critical = record["critical_criteria"]
    if (not isinstance(critical, list) or any(not isinstance(key, str) for key in critical)
            or len(critical) != len(set(critical)) or not critical or not set(critical) <= set(criteria)):
        raise EvidenceError("critical_criteria must name graded criteria")
    usage = record["usage"]
    if not isinstance(usage, dict) or set(usage) != set(USAGE):
        raise EvidenceError("usage categories are incomplete")
    for key in USAGE:
        metric = usage[key]
        if (not isinstance(metric, dict) or set(metric) != {"value", "source"}
                or not isinstance(metric["source"], str) or metric["source"] not in SOURCES):
            raise EvidenceError(f"invalid {key} provenance")
        value = metric["value"]
        if metric["source"] == "unavailable":
            if value is not None:
                raise EvidenceError(f"unavailable {key} must be null")
        elif type(value) is not int or value < 0:
            raise EvidenceError(f"{key} must be a nonnegative integer")
    cost = record["cost"]
    if cost is None:
        if record["currency"] is not None or record["cost_source"] != "unavailable":
            raise EvidenceError("unknown cost requires null currency and unavailable source")
    else:
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise EvidenceError("cost must be finite and nonnegative")
        _string(record["currency"], "currency")
        if not isinstance(record["cost_source"], str) or record["cost_source"] not in {"observed", "estimated"}:
            raise EvidenceError("cost requires observed or estimated provenance")
    return record


def compare(baseline, candidate):
    """Compare matched quality and cost evidence; routing is a distinct experiment."""
    baseline, candidate = validate(baseline), validate(candidate)
    if baseline["run_kind"] != "baseline" or candidate["run_kind"] not in {"efficiency", "routing"}:
        raise EvidenceError("pair must be baseline plus efficiency or routing")
    if baseline["synthetic"] != candidate["synthetic"]:
        raise EvidenceError("cannot mix synthetic and observed runs")
    shared = ("scenario_id", "scenario_sha256", "rubric_sha256", "task_class",
              "package_sha256", "host_profile", "tools", "cache_conditions")
    if candidate["run_kind"] == "efficiency":
        shared += ("model_binding", "reasoning")
    differences = [key for key in shared if baseline[key] != candidate[key]]
    if differences:
        raise EvidenceError("unmatched pair: " + ", ".join(differences))
    if set(baseline["criteria"]) != set(candidate["criteria"]) or set(baseline["critical_criteria"]) != set(candidate["critical_criteria"]):
        raise EvidenceError("pair has different graded criteria")
    regressions = [key for key, grade in baseline["criteria"].items()
                   if grade == "pass" and candidate["criteria"][key] != "pass"]
    critical_failures = [key for key in candidate["critical_criteria"] if candidate["criteria"][key] == "fail"]
    unverified = [key for key, grade in candidate["criteria"].items() if grade == "unverified"]
    grades = set(candidate["criteria"].values())
    quality = "fail" if regressions or critical_failures or "fail" in grades else "unverified" if unverified else "pass"
    usage_delta = {key: candidate["usage"][key]["value"] - baseline["usage"][key]["value"]
                   if baseline["usage"][key]["value"] is not None and candidate["usage"][key]["value"] is not None else None
                   for key in USAGE}
    usage_sources = {key: {"baseline": baseline["usage"][key]["source"],
                           "candidate": candidate["usage"][key]["source"]} for key in USAGE}
    if baseline["cost"] is not None and candidate["cost"] is not None and baseline["currency"] == candidate["currency"]:
        cost_delta = candidate["cost"] - baseline["cost"]
    else:
        cost_delta = None
    return {"comparison": candidate["run_kind"], "synthetic": candidate["synthetic"],
            "quality": quality, "regressions": regressions, "critical_failures": critical_failures,
            "unverified_criteria": unverified, "usage_delta": usage_delta,
            "usage_sources": usage_sources, "cost_delta": cost_delta,
            "cost_currency": candidate["currency"] if cost_delta is not None else None,
            "cost_sources": [baseline["cost_source"], candidate["cost_source"]],
            "claim": "synthetic fixture only" if candidate["synthetic"] else "supplied evidence only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare(json.loads(args.baseline.read_text("utf-8")), json.loads(args.candidate.read_text("utf-8")))
    except (EvidenceError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"evidence error: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
