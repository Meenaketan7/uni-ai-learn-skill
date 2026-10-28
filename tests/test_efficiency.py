import hashlib
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from uni_learn.budget import counter_for_model, measure
from uni_learn.contracts import ContractError


class BudgetTests(unittest.TestCase):
    def test_byte_count_is_not_token_compliance(self):
        result = measure("short")
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["under_1000"])

    def test_exact_counter_decides(self):
        self.assertTrue(measure("x", exact_counter=lambda _: 999)["under_1000"])
        self.assertFalse(measure("x", exact_counter=lambda _: 1000)["under_1000"])

    def test_expanded_imports_are_counted_and_invalid_counter_rejected(self):
        from uni_learn.budget import measure_entry
        result = measure_entry("x" * 100, always_expanded=["y" * 1000], exact_counter=len)
        self.assertFalse(result["under_1000"])
        self.assertEqual(result["expanded_bytes"], 1100)
        for invalid in (-1, True, 1.5):
            with self.subTest(count=invalid), self.assertRaises(ValueError):
                measure("entry", exact_counter=lambda _: invalid)


class OfflineCounterTests(unittest.TestCase):
    def fixture(self):
        tokenizer = types.ModuleType("tiktoken")
        loader = types.ModuleType("tiktoken.load")
        tokenizer.load = loader
        loader.read_file_cached = lambda *args, **kwargs: self.fail("network/cache-writing loader was called")
        asset = b"fixture-vocabulary"
        url = "https://example.invalid/encoding"
        expected = hashlib.sha256(asset).hexdigest()
        def encoding(model):
            if model != "fixture-model":
                raise KeyError(model)
            self.assertEqual(loader.read_file_cached(url, expected), asset)
            return types.SimpleNamespace(name="fixture-encoding", encode=lambda value, **kwargs: list(value))
        tokenizer.encoding_for_model = encoding
        return tokenizer, loader, asset, url

    def test_missing_asset_never_fetches_or_creates_files(self):
        tokenizer, loader, _, _ = self.fixture()
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"tiktoken": tokenizer, "tiktoken.load": loader}), patch.dict("os.environ", {"TIKTOKEN_CACHE_DIR": directory}):
            with self.assertRaisesRegex(ContractError, "not cached"):
                counter_for_model("fixture-model")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_bad_asset_is_not_deleted_or_rewritten(self):
        tokenizer, loader, _, url = self.fixture()
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"tiktoken": tokenizer, "tiktoken.load": loader}), patch.dict("os.environ", {"TIKTOKEN_CACHE_DIR": directory}):
            path = Path(directory) / hashlib.sha1(url.encode()).hexdigest()
            path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ContractError, "hash mismatch"):
                counter_for_model("fixture-model")
            self.assertEqual(path.read_bytes(), b"corrupt")

    def test_verified_local_asset_and_unknown_model(self):
        tokenizer, loader, asset, url = self.fixture()
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"tiktoken": tokenizer, "tiktoken.load": loader}), patch.dict("os.environ", {"TIKTOKEN_CACHE_DIR": directory}), patch("importlib.metadata.version", return_value="fixture"):
            path = Path(directory) / hashlib.sha1(url.encode()).hexdigest()
            path.write_bytes(asset)
            counter, source = counter_for_model("fixture-model")
            self.assertEqual(counter("sample"), 6)
            self.assertEqual(source["encoding"], "fixture-encoding")
            self.assertEqual(path.read_bytes(), asset)
            with self.assertRaisesRegex(ContractError, "no verified tokenizer mapping"):
                counter_for_model("unknown-model")

    def test_missing_optional_library_is_a_clear_error(self):
        with patch.dict("sys.modules", {"tiktoken": None}), self.assertRaisesRegex(ContractError, "already installed"):
            counter_for_model("gpt-5")
