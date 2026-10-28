"""Qualified, cost-aware recommendations without claiming native execution."""
from __future__ import annotations

import datetime as dt
import math
from .contracts import ContractError, TASK_CLASSES, integer, strings, text, utc
from .package import read_json

TASKS = TASK_CLASSES
TIERS = {"efficient", "strong", "frontier"}
EFFORTS = {"routine", "deliberate", "deep", "maximum"}
CONTROLS = {"subagent_selection", "session_switch", "manual_selection", "current_model_only"}


def _override(value, models):
    if not isinstance(value, dict) or not set(value) <= {"model_key", "tier", "reasoning"}:
        raise ContractError("invalid override")
    if "model_key" in value and value["model_key"] not in models:
        raise ContractError("override refers to missing model")
    if "tier" in value and value["tier"] not in TIERS:
        raise ContractError("invalid override tier")
    if "reasoning" in value and value["reasoning"] not in EFFORTS:
        raise ContractError("invalid override effort")
    return value


def validate_registry(value):
    keys = {"schema_version", "task_tiers", "tier_fallbacks", "models", "overrides", "fallback"}
    if not isinstance(value, dict) or set(value) != keys or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("invalid registry fields/version")
    if not isinstance(value["task_tiers"], dict) or set(value["task_tiers"]) != TASKS or any(t not in TIERS for t in value["task_tiers"].values()):
        raise ContractError("invalid task tiers")
    graph = value["tier_fallbacks"]
    if not isinstance(graph, dict) or set(graph) != TIERS:
        raise ContractError("invalid fallback tiers")
    for tiers in graph.values():
        strings(tiers, "tier fallbacks")
        if len(tiers) != len(set(tiers)) or not set(tiers) <= TIERS:
            raise ContractError("invalid or duplicate fallback tier")

    def visit(tier, active):
        if tier in active:
            raise ContractError("tier fallback cycle")
        for next_tier in graph[tier]:
            visit(next_tier, active | {tier})
    for tier in TIERS:
        visit(tier, set())
    if not isinstance(value["models"], dict):
        raise ContractError("models must be an object")
    for key, model in value["models"].items():
        text(key, "model key")
        fields = {"tier", "qualified_tasks", "bindings", "limits", "reasoning", "pricing",
                  "relative_cost_rank", "qualification", "verified_at"}
        if not isinstance(model, dict) or set(model) != fields or model["tier"] not in TIERS:
            raise ContractError("invalid model record")
        tasks = strings(model["qualified_tasks"], "qualified tasks")
        if len(tasks) != len(set(tasks)) or not set(tasks) <= TASKS:
            raise ContractError("invalid qualified tasks")
        if model["verified_at"] is not None:
            utc(model["verified_at"])
        if not isinstance(model["bindings"], dict):
            raise ContractError("invalid bindings")
        for profile, binding in model["bindings"].items():
            text(profile, "host profile")
            if not isinstance(binding, dict) or set(binding) != {"native_id", "provider", "availability", "checked_at", "source"}:
                raise ContractError("invalid native binding")
            for field in ("native_id", "provider", "source"):
                text(binding[field], field)
            if binding["availability"] not in {"available", "unavailable", "unknown"}:
                raise ContractError("invalid availability")
            if binding["checked_at"] is not None:
                utc(binding["checked_at"])
        limits = model["limits"]
        if not isinstance(limits, dict) or set(limits) != {"context_tokens", "max_output_tokens", "tools", "source"}:
            raise ContractError("invalid limits")
        for field in ("context_tokens", "max_output_tokens"):
            if limits[field] is not None:
                integer(limits[field], field, 1)
        strings(limits["tools"], "tools")
        text(limits["source"], "limit source")
        reasoning = model["reasoning"]
        if not isinstance(reasoning, dict) or set(reasoning) != {"control", "levels_by_host"} or reasoning["control"] not in {"fixed", "configurable", "unknown"}:
            raise ContractError("invalid reasoning record")
        if not isinstance(reasoning["levels_by_host"], dict):
            raise ContractError("invalid effort mappings")
        for levels in reasoning["levels_by_host"].values():
            if not isinstance(levels, dict) or not set(levels) <= EFFORTS:
                raise ContractError("invalid effort level")
            for native in levels.values():
                text(native, "native effort")
        price = model["pricing"]
        if not isinstance(price, dict) or set(price) != {"currency", "per_tokens", "input", "output", "cached_input", "source", "checked_at"} or type(price["per_tokens"]) is not int or price["per_tokens"] != 1000000:
            raise ContractError("invalid price unit/fields")
        for field in ("input", "output", "cached_input"):
            number = price[field]
            if number is not None and (type(number) not in (int, float) or not math.isfinite(number) or number < 0):
                raise ContractError("invalid price")
        for field in ("currency", "source"):
            if price[field] is not None:
                text(price[field], field)
        if price["checked_at"] is not None:
            utc(price["checked_at"])
        if model["relative_cost_rank"] is not None:
            integer(model["relative_cost_rank"], "cost rank", 1)
        if not isinstance(model["qualification"], list):
            raise ContractError("invalid qualification")
        for evidence in model["qualification"]:
            fields = {"task_class", "host_profile", "scenario", "result", "evidence", "checked_at"}
            if not isinstance(evidence, dict) or set(evidence) != fields:
                raise ContractError("invalid qualification fields")
            if evidence["task_class"] not in TASKS or evidence["result"] not in {"pass", "fail", "unverified"}:
                raise ContractError("invalid qualification result/task")
            for field in ("host_profile", "scenario", "evidence"):
                text(evidence[field], field)
            utc(evidence["checked_at"])
    overrides = value["overrides"]
    if not isinstance(overrides, dict) or set(overrides) != {"global", "projects"} or not isinstance(overrides["projects"], dict):
        raise ContractError("invalid overrides")
    for mapping in [overrides["global"], *overrides["projects"].values()]:
        if not isinstance(mapping, dict):
            raise ContractError("override map must be an object")
        for task, override in mapping.items():
            if task not in TASKS:
                raise ContractError("override task unknown")
            _override(override, value["models"])
    if value["fallback"] != {"action": "keep_current_host_model", "report": True}:
        raise ContractError("fallback must retain current model and report")
    return value


def registry():
    from .config import model_overrides
    value = read_json("registry/models.json")
    overrides = model_overrides()
    value["models"].update(overrides["models"])
    value["overrides"]["global"].update(overrides["global"])
    for project, mapping in overrides["projects"].items():
        value["overrides"]["projects"].setdefault(project, {}).update(mapping)
    return validate_registry(value)


def _choice_override(data, request):
    task = request["task_class"]
    if "override" in request:
        return _override(request["override"], data["models"]), "task_request"
    project = data["overrides"]["projects"].get(request.get("project_id"), {})
    if task in project:
        return project[task], "project_override"
    if task in data["overrides"]["global"]:
        return data["overrides"]["global"][task], "global_override"
    return {"tier": data["task_tiers"][task]}, "task_default"


def choose(request, data=None):
    data = validate_registry(registry() if data is None else data)
    task, profile = request["task_class"], text(request["host_profile"], "host profile")
    if task not in TASKS:
        raise ContractError("unknown task class")
    control = request.get("control", "current_model_only")
    if control not in CONTROLS:
        raise ContractError("invalid native model control")
    override, precedence = _choice_override(data, request)
    effort = request.get("reasoning", override.get("reasoning", "routine"))
    if effort not in EFFORTS:
        raise ContractError("invalid reasoning request")
    tokens = {key: integer(request.get(key, 0), key) for key in
              ("input_tokens", "output_tokens", "handoff_input_tokens", "handoff_output_tokens", "cached_tokens")}
    if tokens["cached_tokens"] > tokens["input_tokens"]:
        raise ContractError("cached token count exceeds input")
    required_tools = strings(request.get("required_tools", []), "required tools")
    available = strings(request.get("available_models", []), "available models")
    now = dt.datetime.fromisoformat(utc(request["now"]).replace("Z", "+00:00")) if request.get("now") else dt.datetime.now(dt.timezone.utc)
    checked = request.get("availability_checked_at")
    fresh = False
    if checked is not None:
        observed = dt.datetime.fromisoformat(utc(checked).replace("Z", "+00:00"))
        fresh = -60 <= (now - observed).total_seconds() <= 3600
    pinned_tier = data["models"][override["model_key"]]["tier"] if override.get("model_key") else data["task_tiers"][task]
    tier = override.get("tier", pinned_tier)
    allowed = {tier}
    def add_fallbacks(current):
        for next_tier in data["tier_fallbacks"][current]:
            allowed.add(next_tier)
            add_fallbacks(next_tier)
    add_fallbacks(tier)
    excluded, candidates = {}, []
    for key, model in data["models"].items():
        binding = model["bindings"].get(profile)
        latest = {}
        for item in model["qualification"]:
            if item["task_class"] == task and item["host_profile"] == profile:
                prior = latest.get(item["scenario"])
                when = dt.datetime.fromisoformat(item["checked_at"].replace("Z", "+00:00"))
                previous = dt.datetime.fromisoformat(prior["checked_at"].replace("Z", "+00:00")) if prior else None
                if previous is None or when > previous or (when == previous and item["result"] != "pass"):
                    latest[item["scenario"]] = item
        limits = model["limits"]
        extra_in = 0 if key == request.get("current_model_key") else tokens["handoff_input_tokens"]
        extra_out = 0 if key == request.get("current_model_key") else tokens["handoff_output_tokens"]
        reason = None
        if override.get("model_key") and override["model_key"] != key:
            reason = "not pinned"
        elif model["tier"] not in allowed:
            reason = "tier mismatch"
        elif task not in model["qualified_tasks"] or not latest or any(q["result"] != "pass" for q in latest.values()):
            reason = "task/profile qualification incomplete"
        elif not binding or binding["availability"] != "available" or key not in available or not fresh:
            reason = "fresh availability unconfirmed"
        elif limits["context_tokens"] is None or limits["max_output_tokens"] is None:
            reason = "capacity unknown"
        elif tokens["input_tokens"] + tokens["output_tokens"] + extra_in + extra_out > limits["context_tokens"] or tokens["output_tokens"] + extra_out > limits["max_output_tokens"]:
            reason = "capacity insufficient including handoff"
        elif not set(required_tools) <= set(limits["tools"]):
            reason = "required tools unavailable"
        elif effort != "routine" and effort not in model["reasoning"]["levels_by_host"].get(profile, {}):
            reason = "effort unsupported"
        if reason:
            excluded[key] = reason
            continue
        price = model["pricing"]
        cost = None
        if price["currency"] and price["source"] and price["checked_at"] and price["input"] is not None and price["output"] is not None:
            cached = tokens["cached_tokens"] if request.get("cache_eligible") is True and price["cached_input"] is not None else 0
            cost = ((tokens["input_tokens"] - cached + extra_in) * price["input"] +
                    cached * (price["cached_input"] or 0) +
                    (tokens["output_tokens"] + extra_out) * price["output"]) / 1000000
        candidates.append({"key": key, "model": model, "binding": binding, "cost": cost,
                           "qualification": list(latest.values())})
    base = {"selected": None, "native_binding": None, "requested_effort": effort, "mapped_effort": None,
            "precedence": precedence, "excluded": excluded, "recommended": None,
            "requested_execution": None, "observed_execution": None, "observed_execution_binding": None}
    def fallback(reason):
        return {**base, "action": "keep_current_host_model", "fallback_reason": reason}
    if control == "current_model_only":
        return fallback("host execution control unavailable")
    if not candidates:
        return fallback("pinned binding unavailable/unqualified" if override.get("model_key") else "no eligible qualified model")
    currencies = {item["model"]["pricing"]["currency"] for item in candidates}
    if override.get("model_key"):
        chosen, comparison = candidates[0], "explicit eligible pin; price may remain unknown"
    elif all(item["cost"] is not None for item in candidates) and len(currencies) == 1:
        chosen, comparison = min(candidates, key=lambda item: (item["cost"], item["key"])), "comparable estimated task plus handoff cost"
    elif all(item["model"]["relative_cost_rank"] is not None for item in candidates):
        chosen, comparison = min(candidates, key=lambda item: (item["model"]["relative_cost_rank"], item["key"])), "explicit user relative-cost ranks; monetary comparison unavailable"
    else:
        return fallback("unknown or incomparable candidate costs without complete relative ranks")
    model, binding = chosen["model"], chosen["binding"]
    action = ("keep_current_host_model" if chosen["key"] == request.get("current_model_key") else
              "recommend_manual_selection" if control == "manual_selection" else "recommend_switch")
    return {**base, "action": action, "selected": chosen["key"], "native_binding": binding["native_id"],
            "recommended": {"model_key": chosen["key"], "native_id": binding["native_id"]},
            "mapped_effort": model["reasoning"]["levels_by_host"].get(profile, {}).get(effort),
            "qualification": chosen["qualification"], "estimated_cost": chosen["cost"],
            "currency": model["pricing"]["currency"], "cost_uncertainty": comparison + "; no unverified cache discount",
            "fallback_reason": None}


def orchestration(task_class, *, approved_plan=False, failure=False, control="current_model_only",
                  substantial=True, high_impact=False, diff=None, checks=None, handoff=None):
    if task_class not in TASKS or control not in CONTROLS:
        raise ContractError("unknown orchestration task/control")
    if handoff is not None:
        from .contracts import validate_handoff
        validate_handoff(handoff)
    if task_class in {"implement", "verify"} and not approved_plan:
        return {"stage": "await_plan", "reason": "approved plan required"}
    if task_class == "verify" and (diff is None or checks is None):
        return {"stage": "await_review_evidence", "reason": "review needs diff and relevant check evidence"}
    if failure:
        return {"stage": "diagnose", "escalate": True, "tier": "frontier" if high_impact else "strong",
                "execution_confirmed": False}
    stages = [
        {"stage": "discover", "tier": "efficient", "input": "bounded question"},
        {"stage": "plan", "tier": "frontier" if substantial else "strong", "input": "sourced compact handoff"},
        {"stage": "implement", "tier": "strong", "input": "approved plan and relevant pointers"},
        {"stage": "verify", "tier": "frontier" if high_impact else "strong", "input": "requirements, diff, checks, unresolved risks"},
    ]
    return {"stage": task_class, "stages": stages, "control": control,
            "handoff": handoff, "review_input": {"diff": diff, "checks": checks},
            "single_model_fallback": control == "current_model_only", "execution_confirmed": False}
