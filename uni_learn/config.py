"""Read-only local configuration and model override loading."""
from __future__ import annotations

import json
from pathlib import Path
from .contracts import ContractError
from .paths import roots


def load(path=None):
    path = Path(path) if path is not None else roots()["config"] / "config.json"
    if not path.exists():
        return {"schema_version": 1}
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ContractError("invalid user configuration")
    return value


def model_overrides(application_roots=None):
    path = Path((application_roots or roots())["config"]) / "model-overrides.json"
    if not path.exists():
        return {"schema_version": 1, "models": {}, "global": {}, "projects": {}}
    value = json.loads(path.read_text("utf-8"))
    if (not isinstance(value, dict) or set(value) != {"schema_version", "models", "global", "projects"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or any(not isinstance(value[key], dict) for key in ("models", "global", "projects"))):
        raise ContractError("invalid model override file")
    return value
