"""Registry-based adapter discovery and generic native template rendering."""

from __future__ import annotations

import string
import types
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from .budget import measure
from .contracts import ContractError
from .package import read_json, read_resource


def _safe_relative(path):
    if (not isinstance(path, str) or not path or path in {".", "./"} or path.startswith("/")
            or "\\" in path or re.match(r"^[A-Za-z]:", path) or ".." in Path(path).parts):
        raise ContractError("adapter output path must be project relative")
    return path


def render_spec(context, read_resource_fn, spec):
    if spec.get("schema_version") != 1 or not isinstance(spec.get("files"), list) or not isinstance(spec.get("commands"), dict):
        raise ContractError("invalid adapter descriptor")
    if spec.get("required_generation") and context.get("generation") != spec["required_generation"]:
        raise ContractError("profile requires explicit generation: " + spec["required_generation"])
    command_data = json.loads(read_resource_fn("core/learning/commands.json"))["commands"]
    canonical_ids = list(command_data)
    if set(spec["commands"]) != set(canonical_ids):
        raise ContractError("adapter command IDs do not match canonical commands")
    invocations = [v["invocation"] for v in spec["commands"].values()]
    if len(invocations) != len(set(invocations)):
        raise ContractError("duplicate native invocation")
    scope = context.get("scope", "project")
    if scope not in {"project", "user"}:
        raise ContractError("invalid installation scope")
    project_resolution = ""
    installation = {}
    if scope == "user":
        installation = spec.get("installation_scopes", {}).get("user")
        if not isinstance(installation, dict) or installation.get("project_binding") != "session":
            raise ContractError("adapter does not support user installation")
        project_arg = "PROJECT_ROOT"
        project_resolution = (
            "Once per session, run `" + context["runtime_command"] + " project locate --start . --json`.\n"
            "Keep its absolute project_root as PROJECT_ROOT for this session. Substitute that path\n"
            "as one quoted argv value in every command below, even after changing directories."
        )
    else:
        project = context.get("project_root")
        if not isinstance(project, str) or not project or not Path(project).is_absolute():
            raise ContractError("renderer requires an absolute project_root")
        project = str(Path(project).expanduser().resolve())
        project_arg = subprocess.list2cmdline([project]) if os.name == "nt" else shlex.quote(project)
    values_base = {"profile": spec["profile"], "runtime_path": context["runtime_path"],
                   "resource_root": context["resource_root"], "runtime_command": context["runtime_command"],
                   "project_arg": project_arg, "project_resolution": project_resolution,
                   "core_entry": read_resource_fn("core/efficiency/entry.md").decode("utf-8")}
    artifacts = []
    seen = set()
    for item in spec["files"]:
        path = _safe_relative(item["path"])
        if path in seen:
            raise ContractError("duplicate adapter output path")
        seen.add(path)
        mode = item.get("mode")
        if mode not in ("command", "metadata", "persistent"):
            raise ContractError("invalid adapter file mode")
        ident = item.get("command")
        if ident and ident not in command_data:
            raise ContractError("unknown adapter command")
        values = {**values_base, "id": ident or "", "role": mode,
                  "description": command_data[ident]["description"] if ident else "",
                  "body": command_data[ident]["body"] if ident else ""}
        template_path = item.get("user_template", item["template"]) if scope == "user" else item["template"]
        template = read_resource_fn(template_path).decode("utf-8")
        try:
            rendered = string.Template(template).substitute(values)
        except KeyError as exc:
            raise ContractError("unknown template value " + str(exc)) from exc
        artifacts.append({"path": path, "role": mode, "command": ident, "content": rendered,
                      "budget": measure(rendered) if mode == "persistent" else None})
    for ident, item in spec["commands"].items():
        if _safe_relative(item["path"]) not in seen:
            raise ContractError("command path is not rendered: " + ident)
    return {"schema_version": 1, "manual_patches": [], "profile": spec["profile"], "display_name": spec.get("display_name", spec["profile"]),
            "capabilities": {**spec.get("capabilities", {}), **installation.get("capabilities", {})},
            "warnings": installation.get("warnings", spec.get("warnings", [])),
            "commands": spec["commands"], "artifacts": artifacts}


def load_registry(registry=None):
    value = registry or read_json("registry/adapters.json")
    if value.get("schema_version") != 1 or not isinstance(value.get("profiles"), dict):
        raise ContractError("invalid adapter registry")
    return value


def render(profile, context, registry=None):
    entry = load_registry(registry)["profiles"].get(profile)
    if not entry:
        raise ContractError("unknown host profile")
    descriptor = read_json(entry["descriptor"])
    source = read_resource(entry["renderer"]).decode("utf-8")
    module = types.ModuleType("adapter_renderer")
    module.__dict__["render_spec"] = render_spec
    exec(compile(source, entry["renderer"], "exec"), module.__dict__)
    if not callable(getattr(module, "render", None)):
        raise ContractError("adapter renderer missing render function")
    result = module.render(context, read_resource)
    return validate_result(result, profile)


def validate_result(result, profile=None):
    if not isinstance(result, dict) or type(result.get("schema_version")) is not int or result["schema_version"] != 1:
        raise ContractError("invalid adapter result version")
    if profile is None:
        profile = result.get("profile")
    if result.get("profile") != profile:
        raise ContractError("adapter profile mismatch")
    if not isinstance(result.get("artifacts"), list) or not isinstance(result.get("commands"), dict):
        raise ContractError("invalid adapter render result")
    ids = set(read_json("core/learning/commands.json")["commands"])
    if set(result["commands"]) != ids:
        raise ContractError("adapter command IDs mismatch")
    paths = set()
    for item in result["artifacts"]:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise ContractError("invalid rendered artifact")
        path = _safe_relative(item.get("path"))
        if path in paths or item.get("role") not in {"command", "metadata", "persistent"}:
            raise ContractError("duplicate path or invalid artifact role")
        paths.add(path)
    command_paths, invocations = set(), set()
    artifact_map = {item["path"]: item for item in result["artifacts"]}
    for command, value in result["commands"].items():
        if not isinstance(value, dict) or _safe_relative(value.get("path")) not in paths:
            raise ContractError("command path missing")
        if not isinstance(value.get("invocation"), str) or not value["invocation"].strip():
            raise ContractError("command invocation missing")
        if value["path"] in command_paths or value["invocation"] in invocations:
            raise ContractError("duplicate command path or invocation")
        artifact = artifact_map[value["path"]]
        if artifact["role"] != "command" or artifact.get("command") != command:
            raise ContractError("command artifact identity mismatch")
        command_paths.add(value["path"])
        invocations.add(value["invocation"])
    if not isinstance(result.get("capabilities"), dict) or not isinstance(result.get("manual_patches"), list):
        raise ContractError("adapter capabilities/manual patches invalid")
    if not isinstance(result.get("warnings"), list) or any(not isinstance(warning, str) for warning in result["warnings"]):
        raise ContractError("adapter warnings invalid")
    return result
