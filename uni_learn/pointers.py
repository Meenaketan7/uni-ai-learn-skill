"""Revision-aware pointers; checking a web reference never performs a network call."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from .contracts import ContractError, text, utc


def file_pointer(path, *, symbol=None, query=None, method="file_read", start=None, end=None):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ContractError("pointer source is not a file")
    pointer = {"kind": "file", "path": str(path),
               "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "symbol": symbol, "query": query, "method": method}
    if start is not None or end is not None:
        if type(start) is not int or type(end) is not int or not 1 <= start <= end:
            raise ContractError("invalid pointer range")
        pointer["range"] = [start, end]
    return pointer


def url_pointer(url, retrieved_at, *, query=None, source=None, method="web"):
    if not text(url).startswith(("https://", "http://")):
        raise ContractError("invalid URL pointer")
    utc(retrieved_at)
    return {"kind": "url", "url": url, "retrieved_at": retrieved_at, "query": query,
            "source": source or url, "method": method}


def validate(pointer):
    if not isinstance(pointer, dict):
        raise ContractError("invalid pointer")
    if pointer.get("unique_conversation") and not pointer.get("durably_stored"):
        raise ContractError("unique conversation content requires durable evidence")
    kind = pointer.get("kind")
    text(pointer.get("method"), "retrieval method")
    if kind == "file":
        path = Path(text(pointer.get("path"), "source path"))
        if not path.is_absolute() or not re.fullmatch(r"[a-f0-9]{64}", text(pointer.get("sha256"), "hash")):
            raise ContractError("file pointer needs absolute path and SHA-256")
        try:
            if not path.is_file():
                return {"status": "missing", "pointer": pointer}
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            return {"status": "inaccessible", "reason": type(exc).__name__, "pointer": pointer}
        return {"status": "current" if actual == pointer["sha256"] else "stale",
                "actual_sha256": actual, "pointer": pointer}
    if kind == "url":
        if not text(pointer.get("url"), "URL").startswith(("https://", "http://")):
            raise ContractError("invalid URL pointer")
        utc(pointer.get("retrieved_at"))
        if pointer.get("fallback"):
            return {**validate(pointer["fallback"]), "external_status": "dated_unverified"}
        return {"status": "dated_unverified", "pointer": pointer}
    if kind == "integration":
        for key in ("integration", "record_id"):
            text(pointer.get(key), key)
        utc(pointer.get("retrieved_at"))
        if pointer.get("fallback"):
            return {**validate(pointer["fallback"]), "integration_status": "unverified"}
        return {"status": "expired" if pointer.get("expired") else "integration_unverified", "pointer": pointer}
    raise ContractError("unknown pointer kind")


def relocate(pointer):
    result = validate(pointer)
    if result["status"] != "stale" or pointer.get("kind") != "file":
        return result
    data = Path(pointer["path"]).read_text("utf-8", errors="replace")
    needle = pointer.get("symbol") or pointer.get("query")
    if not needle:
        return {**result, "relocation": "manual_review_required"}
    locations = [i + 1 for i, line in enumerate(data.splitlines()) if needle in line]
    return {**result, "relocation": "candidate" if locations else "not_found",
            "lines": locations[:10], "changed_conclusion_requires_review": True}
