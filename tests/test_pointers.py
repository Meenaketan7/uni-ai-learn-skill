import tempfile
import unittest
from pathlib import Path
from uni_learn.pointers import file_pointer, relocate, validate


class PointerTests(unittest.TestCase):
    def test_stale_source_needs_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.py"
            path.write_text("def answer():\n    return 1\n")
            pointer = file_pointer(path, symbol="answer")
            self.assertEqual(validate(pointer)["status"], "current")
            path.write_text("# shifted\ndef answer():\n    return 2\n")
            result = relocate(pointer)
            self.assertEqual(result["status"], "stale")
            self.assertEqual(result["lines"], [2])
            self.assertTrue(result["changed_conclusion_requires_review"])
