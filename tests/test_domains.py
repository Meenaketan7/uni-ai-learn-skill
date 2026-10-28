import json
import unittest

from uni_learn.learning import domain_resources
from uni_learn.package import read_resource


class DomainTests(unittest.TestCase):
    def test_no_irrelevant_pack_and_ordered_deduplication(self):
        self.assertEqual(domain_resources([]), [])
        self.assertEqual(domain_resources(["coding", "math-proof", "coding"]),
                         ["core/domains/coding.md", "core/domains/math-proof.md"])

    def test_unknown_pack_is_not_a_path_escape(self):
        for domain in ("../learning/workflow", "unknown", "/etc/passwd"):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                domain_resources([domain])

    def test_every_scenario_has_a_small_loadable_pack(self):
        for name in ("coding", "math-proof", "product-system", "research-projects"):
            resources = domain_resources([name])
            self.assertEqual(len(resources), 1)
            body = read_resource(resources[0])
            self.assertGreater(len(body), 100)
            self.assertLess(len(body), 8192)

    def test_canonical_command_surface(self):
        data = json.loads(read_resource("core/learning/commands.json"))
        self.assertEqual(set(data["commands"]), {
            "learn", "learn-hint", "learn-debug", "learn-review",
            "learn-design", "learn-recall", "learn-compact",
        })
        self.assertFalse(data["commands"]["learn-compact"]["teaching"])
        for item in data["commands"].values():
            self.assertTrue(read_resource(item["body"]))
