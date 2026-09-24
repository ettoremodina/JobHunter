"""PhD positions: tagged from the title, findable on their own, never dropped as a student post."""

import json
import tempfile
from contextlib import closing
import unittest
from pathlib import Path

from jobhunter.evaluation import tier
from jobhunter.evaluation.selection import evaluate, filters, phd_judgement
from jobhunter.evaluation.system_one import STUDENT_MOTIVE
from jobhunter.workspace import ROOT, Archive


class PhdTests(unittest.TestCase):
    """The tag is a label on top of the usual decision, not a filter of its own."""

    def test_title_tag_excludes_postdocs(self):
        rules = json.loads((ROOT / "examples/config/role_filters.json").read_text(encoding="utf-8"))
        tagged = {t: evaluate({"title": t, "description": ""}, rules)["phd"] for t in (
            "Doctoral Student in Urban Heat Prediction", "PhD Position Control Theory for Digital Twins",
            "Promovendus landelijk gokonderzoek", "Postdoctoral Researcher", "Post-doctorant en Biomathématiques",
            "Microscopist (PhD level)")}
        self.assertEqual([t for t, v in tagged.items() if v], [
            "Doctoral Student in Urban Heat Prediction", "PhD Position Control Theory for Digital Twins",
            "Promovendus landelijk gokonderzoek"])
        self.assertEqual(evaluate({"title": "PhD Candidate in Energy Systems"}, rules)["career_priority"], "primary")

    def test_student_exclusion_becomes_review_only_on_a_phd(self):
        jev = {"verdetto": tier.DROP, "motivo": STUDENT_MOTIVE + " (compatibilita' 56%).", "prove": [], "giudice": "jev"}
        self.assertEqual(phd_judgement({"phd": True}, jev)["verdetto"], tier.UNKNOWN)
        self.assertEqual(phd_judgement({"phd": False}, jev)["verdetto"], tier.DROP)
        other = {**jev, "motivo": "Mansioni della famiglia esclusa «laboratorio sperimentale»"}
        self.assertEqual(phd_judgement({"phd": True}, other)["verdetto"], tier.DROP)

    def test_search_keeps_only_companies_with_a_phd(self):
        if "phd_title_pattern" not in filters():
            self.skipTest("personal role_filters.json predates the PhD tag")
        with tempfile.TemporaryDirectory() as folder, closing(Archive(Path(folder) / "a.db")) as archive:
            archive.ingest([{"company_name": "Uni", "title": "Doctoral Student in Transport Modelling", "source_url": "https://example.org/1"},
                            {"company_name": "Firm", "title": "Data Scientist", "source_url": "https://example.org/2"}], "test")
            self.assertEqual([i["name"] for i in archive.search(phd=True)["items"]], ["Uni"])


if __name__ == "__main__":
    unittest.main()
