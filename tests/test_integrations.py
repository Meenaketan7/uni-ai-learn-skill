import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uni_learn.contracts import ContractError
from uni_learn.integrations import configured_references, detect, documentation_pointer, graph_pointer, run_rtk
from uni_learn.package import read_json


class DetectionTests(unittest.TestCase):
    def test_missing_optional_tools_do_not_install(self):
        run = Mock()
        result = detect(data=read_json("registry/integrations.json"), which=lambda _: None, run=run, configured={})
        self.assertTrue(all(item["status"] == "missing" for item in result.values()))
        self.assertTrue(all(item["network_health"] == "unverified" for item in result.values()))
        run.assert_not_called()

    def test_versions_and_connector_reuse_are_distinct_evidence(self):
        run = Mock(return_value=SimpleNamespace(returncode=0, stdout="fixture 1\n", stderr=""))
        result = detect(which=lambda name: "/bin/" + name, run=run, configured={"context7": ["existing"]})
        self.assertEqual(result["rtk"]["status"], "found")
        self.assertEqual(result["context7"]["status"], "reused")
        self.assertEqual(result["context7"]["network_health"], "unverified")


class RtkTests(unittest.TestCase):
    def test_argv_exit_and_raw_diagnostics_preserved_without_rerun(self):
        run = Mock(return_value=SimpleNamespace(returncode=7, stdout="raw", stderr="failure"))
        argv = ["git", "show", "name with spaces"]
        result = run_rtk(argv, rtk_path="/existing/rtk", run=run)
        self.assertEqual(run.call_args.args[0], ["/existing/rtk", "proxy", *argv])
        self.assertEqual(result["returncode"], 7)
        self.assertEqual(result["error"], "failure")
        self.assertFalse(result["filtered"])
        self.assertEqual(result["raw_recovery"], "saved output only")
        self.assertEqual(run.call_count, 1)


class GraphifyTests(unittest.TestCase):
    def test_stale_or_absent_graph_uses_file_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(graph_pointer(root, "a.py", "A")["status"], "fallback_file")
            report = root / "graphify-out/GRAPH_REPORT.md"
            report.parent.mkdir()
            report.write_text("fixture")
            self.assertEqual(graph_pointer(root, "a.py", "A", graph_revision="B")["status"], "fallback_file")
            self.assertEqual(graph_pointer(root, "a.py", "A", graph_revision="A")["status"], "graph_candidate")


class Context7Tests(unittest.TestCase):
    def test_configuration_is_normalized_without_printing_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".mcp.json"
            config.write_text(json.dumps({"mcpServers": {"Library docs": {
                "command": "npx", "args": ["@upstash/context7-mcp"], "env": {"TOKEN": "do-not-expose"}}}}))
            report = detect(project=root, which=lambda _: None)
            self.assertEqual(report["context7"]["status"], "reused")
            self.assertNotIn("do-not-expose", json.dumps(report))

    def test_provenance_and_unparseable_config(self):
        reference = documentation_pointer("fixture", "1", "question", "https://example.test/docs", "2026-10-22T00:00:00Z")
        self.assertEqual(reference["connectivity"], "unverified")
        with self.assertRaises(ContractError):
            documentation_pointer("fixture", "1", "question", "https://example.test", "yesterday")
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.json"
            config.write_text("{ // JSONC requires host UI\n")
            found, diagnostics = configured_references(paths=[config])
            self.assertEqual(found["context7"], [])
            self.assertEqual(diagnostics[0]["status"], "manual_inspection_required")


class ContextModeTests(unittest.TestCase):
    def test_optional_connector_is_neither_bundled_nor_installed(self):
        record = read_json("registry/integrations.json")["integrations"]["context-mode"]
        self.assertFalse(record["bundled"])
        self.assertFalse(record["automatic_install"])
        self.assertEqual(detect(which=lambda _: None, configured={})["context-mode"]["status"], "missing")
