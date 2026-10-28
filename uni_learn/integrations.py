"""Read-only local integration inventory and safe tool wrappers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from .contracts import ContractError
from .package import read_json
from .contracts import utc


def configured_references(project=None, paths=None):
    candidates = list(paths) if paths is not None else []
    if paths is None and project is not None:
        root = Path(project)
        candidates = [root / name for name in (
            ".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json", ".gemini/settings.json",
            ".agents/mcp_config.json", "opencode.json", ".opencode/opencode.json")]
    found = {"context7": [], "context-mode": []}
    diagnostics = []
    for path in candidates:
        path = Path(path)
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 2097152:
                diagnostics.append({"path": str(path), "status": "manual_inspection_required"})
                continue
            value = json.loads(path.read_text("utf-8"))
            servers = value.get("mcpServers", value.get("mcp", value.get("servers", {})))
            if not isinstance(servers, dict):
                raise ValueError("server map unavailable")
            for name, server in servers.items():
                identity = re.sub(r"[^a-z0-9]", "", str(name).lower())
                if isinstance(server, dict):
                    identity += re.sub(r"[^a-z0-9]", "", str(server.get("command", "")).lower())
                    identity += re.sub(r"[^a-z0-9]", "", " ".join(str(a) for a in server.get("args", [])).lower())
                    identity += re.sub(r"[^a-z0-9]", "", str(server.get("url", "")).lower())
                for key, normalized in (("context7", "context7"), ("context-mode", "contextmode")):
                    if normalized in identity:
                        found[key].append(str(path))
        except (OSError, ValueError, TypeError, AttributeError):
            diagnostics.append({"path": str(path), "status": "manual_inspection_required"})
    return found, diagnostics


def registry():
    value = read_json("registry/integrations.json")
    if value.get("schema_version") != 1 or not isinstance(value.get("integrations"), dict):
        raise ContractError("invalid integrations registry")
    return value


def detect(*, project=None, data=None, which=shutil.which, run=subprocess.run, configured=None):
    data = data or registry()
    if configured is None:
        configured, diagnostics = configured_references(project)
    else:
        diagnostics = []
    report = {}
    for key, record in data["integrations"].items():
        executable = which(record["executable"]) if record.get("executable") else None
        refs = configured.get(key, [])
        if isinstance(refs, str):
            refs = [refs]
        status = "reused" if refs else ("found" if executable else "missing")
        version = None
        if executable and record.get("version_args"):
            try:
                result = run([executable, *record["version_args"]], capture_output=True, text=True, timeout=3, check=False)
                version = (result.stdout or result.stderr).strip().splitlines()[:1]
                if result.returncode != 0:
                    status = "incompatible"
            except (OSError, subprocess.TimeoutExpired):
                status = "incompatible"
        report[key] = {"status": status, "executable": executable, "version": version,
                       "configured_references": len(refs), "network_health": "unverified", "source": record.get("source"),
                       "configuration_diagnostics": diagnostics}
        if len(refs) > 1:
            report[key]["duplicate"] = True
    if project is not None:
        graph = Path(project) / "graphify-out" / "GRAPH_REPORT.md"
        if "graphify" in report:
            report["graphify"]["graph_report"] = str(graph) if graph.is_file() else None
    return report


def run_rtk(argv, *, rtk_path=None, run=subprocess.run, read_only=False):
    if not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("argv required")
    binary = rtk_path or shutil.which("rtk")
    if binary is None:
        result = run(argv, capture_output=True, text=True, check=False)
        return {"filtered": False, "returncode": result.returncode, "output": result.stdout, "error": result.stderr}
    result = run([binary, "proxy", *argv], capture_output=True, text=True, check=False)
    return {"filtered": False, "transport": "rtk_proxy", "returncode": result.returncode, "output": result.stdout,
            "error": result.stderr, "raw_recovery": "saved output or explicit read-only rerun" if read_only else "saved output only"}


def graph_pointer(project, source_path, source_hash, *, graph_revision=None):
    graph = Path(project) / "graphify-out" / "GRAPH_REPORT.md"
    if not graph.is_file() or not graph_revision or graph_revision != source_hash:
        return {"status": "fallback_file", "path": str(source_path), "reason": "graph absent or stale"}
    return {"status": "graph_candidate", "path": str(source_path), "graph_report": str(graph), "revision": graph_revision}


def documentation_pointer(library, version, query, source, retrieved_at):
    if not all((library, version, query, source, retrieved_at)):
        raise ValueError("complete documentation provenance required")
    utc(retrieved_at)
    return {"library": library, "version": version, "query": query, "source": source,
            "retrieved_at": retrieved_at, "connectivity": "unverified"}
