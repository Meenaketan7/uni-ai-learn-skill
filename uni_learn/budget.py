"""Honest persistent context measurements."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path

from .contracts import ContractError

_TOKENIZER_LOCK = threading.RLock()


def counter_for_model(model):
    """Reuse an explicitly requested installed tokenizer; never install one.

    The caller must select the actual host model. Unsupported mappings fail
    instead of substituting a different encoding or assuming a byte bound.
    """
    if not isinstance(model, str) or not model.strip():
        raise ContractError("budget model must be a nonempty native model ID")
    try:
        import tiktoken
        import tiktoken.load
        from importlib.metadata import version
    except ImportError as exc:
        raise ContractError("budget model requires an already installed tiktoken; no dependency was installed") from exc
    def cached_asset(blobpath, expected_hash=None):
        # tiktoken's normal loader may fetch assets or rewrite corrupt caches.
        # Installation planning must only read existing, hash-verified assets.
        cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get("DATA_GYM_CACHE_DIR",
                                str(Path(tempfile.gettempdir()) / "data-gym-cache")))
        if not cache_dir or not expected_hash:
            raise ContractError("tokenizer requires a prepared, hash-verified local asset cache")
        path = Path(cache_dir) / hashlib.sha1(blobpath.encode()).hexdigest()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ContractError("tokenizer asset is not cached; prepare it separately before a read-only plan") from exc
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ContractError("cached tokenizer asset hash mismatch; planning left the cache unchanged")
        return data
    try:
        from unittest.mock import patch
        with _TOKENIZER_LOCK, patch("tiktoken.load.read_file_cached", side_effect=cached_asset):
            encoding = tiktoken.encoding_for_model(model)
    except KeyError as exc:
        raise ContractError("no verified tokenizer mapping for budget model: " + model) from exc
    return (lambda text: len(encoding.encode(text, disallowed_special=()))), {
        "library": "tiktoken", "version": version("tiktoken"),
        "encoding": encoding.name, "model": model,
    }


def measure(text: str, *, exact_counter=None, byte_bound_verified=False) -> dict:
    size = len(text.encode("utf-8"))
    if exact_counter is not None:
        count = exact_counter(text)
        if type(count) is not int or count < 0:
            raise ValueError("counter must return a nonnegative integer")
        return {"status": "verified", "tokens": count, "bytes": size, "under_1000": count < 1000, "target_500": count < 500}
    if byte_bound_verified:
        return {"status": "verified_bound", "tokens_upper_bound": size, "bytes": size, "under_1000": size < 1000, "target_500": size < 500}
    return {"status": "unverified", "bytes": size, "under_1000": None, "target_500": None}


def measure_entry(text, *, always_expanded=(), exact_counter=None, byte_bound_verified=False):
    expanded = text + "".join(always_expanded)
    result = measure(expanded, exact_counter=exact_counter, byte_bound_verified=byte_bound_verified)
    return {**result, "entry_bytes": len(text.encode("utf-8")),
            "expanded_bytes": len(expanded.encode("utf-8")), "import_count": len(always_expanded)}
