"""The example personal files let a fresh clone start, and `init` never overwrites real ones."""

import json
import tempfile
import unittest
from pathlib import Path

from jobhunter.workspace import ROOT, install_examples


class ExampleFilesTests(unittest.TestCase):
    """Check the copy rule and that each example is usable by the code that reads it."""

    def test_install_creates_missing_files_only(self):
        """Missing files are created from examples; an existing file keeps its content."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "examples/config").mkdir(parents=True)
            (root / "examples/config/a.json").write_text("{}", encoding="utf-8")
            (root / "examples/config/b.json").write_text("{}", encoding="utf-8")
            (root / "config").mkdir()
            (root / "config/b.json").write_text('{"mine": true}', encoding="utf-8")
            self.assertEqual(install_examples(root), ["config/a.json"])
            self.assertEqual((root / "config/b.json").read_text(encoding="utf-8"), '{"mine": true}')
            self.assertEqual(install_examples(root), [])

    def test_examples_match_what_the_code_reads(self):
        """Preferred sectors exist in the vocabulary and Jev's question keys are intact."""
        filters = json.loads((ROOT / "examples/config/role_filters.json").read_text(encoding="utf-8"))
        vocabulary = json.loads((ROOT / "config/categories.json").read_text(encoding="utf-8"))
        self.assertLessEqual(set(filters["preferred_categories"]), set(vocabulary))
        questions = json.loads((ROOT / "examples/config/system-one-questions.json").read_text(encoding="utf-8"))
        self.assertLessEqual({"mansioni_descritte", "mansioni_compatibili", "famiglia_esclusa",
                              "posto_per_studenti", "seniority_fuori_profilo", "prova"}, set(questions["selection"]))
        self.assertLessEqual({"nessuna", "produzione_manutenzione", "officina_ricambi"},
                             set(questions["selection"]["famiglia_esclusa"]["criteria"]))


if __name__ == "__main__":
    unittest.main()
