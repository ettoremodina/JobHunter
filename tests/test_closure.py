"""Ads a full collection could have seen again and did not: closed, kept, slimmed, and reopened."""

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from jobhunter.acquisition import closure
from jobhunter.evaluation.selection import verdicts
from jobhunter.workspace import Archive

BEFORE, STARTED, AGAIN = "2026-09-01T10:00:00+00:00", "2026-09-20T09:00:00+00:00", "2026-09-20T10:00:00+00:00"


def ad(name, source, posted=None):
    """One listing; the URL doubles as the ad's identity."""
    return {"company_name": name, "title": "Data Scientist", "source_url": f"https://example.org/{source}/{name}",
            "description": "Build forecasting models.", "posted_at": posted}


class ClosureTests(unittest.TestCase):
    """An absent ad closes only where its source would have listed it again."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.archive = Archive(Path(self.folder.name) / "a.db")
        self.addCleanup(self.archive.close)
        self.archive.ingest([ad("Kept", "airtable"), ad("Gone", "airtable")], "airtable", BEFORE)
        self.archive.ingest([ad("Recent", "jobspy", "2026-09-18"), ad("Old", "jobspy", "2026-08-20T00:00:00.000"),
                             ad("Undated", "jobspy")], "jobspy", BEFORE)
        self.archive.ingest([ad("Kept", "airtable")], "airtable", AGAIN)
        self.ids = {r[1]: r[0] for r in self.archive.db.execute(
            "SELECT o.id,c.name FROM opportunities o JOIN companies c ON c.id=o.company_id")}

    def closed(self):
        return {name for name, oid in self.ids.items()
                if self.archive.db.execute("SELECT 1 FROM closures WHERE opportunity_id=?", (oid,)).fetchone()}

    def test_window_and_reopening(self):
        """Airtable lists everything; JobSpy only its window, so old or undated ads stay open."""
        found = closure.detect(self.archive, STARTED, {"airtable": None, "jobspy": 168}, 0.9)
        self.assertEqual(self.closed(), {"Gone", "Recent"})
        self.assertEqual(found["closed"], 2)
        self.archive.ingest([ad("Gone", "airtable")], "airtable", "2026-09-27T10:00:00+00:00")
        self.assertEqual(self.closed(), {"Recent"})

    def test_a_suspicious_share_closes_nothing(self):
        """Closing half of a source's ads looks like a broken run, not a market that vanished."""
        found = closure.detect(self.archive, STARTED, {"airtable": None}, 0.4)
        self.assertEqual(self.closed(), set())
        self.assertEqual(found["skipped_sources"], {"airtable": {"would_close": 1, "open": 2}})

    def test_closed_roles_stop_holding_a_tier(self):
        """A compatible role in a preferred sector makes Tier A only while it is still open."""
        cid = self.archive.search(query="Gone")["items"][0]["id"]
        self.archive.categorize(cid, "Energia", "Fixture: settore preferito")
        with self.archive.db:
            self.archive.db.execute("INSERT INTO enrichments VALUES(?,?,?,?,?,?,?)",
                                    ("jev:selection", self.ids["Gone"], "s", "k", json.dumps(
                                        {"result": {"decision": "keep", "rationale": "Fixture", "evidence": ["x"],
                                                    "missing_information": []}}), "fixture", BEFORE))
        self.assertEqual(verdicts(self.archive, cid)[cid]["tier"], "A")
        closure.detect(self.archive, STARTED, {"airtable": None}, 0.9)
        state = verdicts(self.archive, cid)[cid]
        self.assertEqual(state["tier"], "B-attesa")
        self.assertTrue(state["ruoli"][self.ids["Gone"]]["chiuso"])

    def test_compact_removes_only_the_pages_of_closed_ads(self):
        """Both saved pages of a closed ad go; another ad's page stays."""
        pages = Path(self.folder.name) / "descriptions" / "run"
        pages.mkdir(parents=True)
        for name in (self.ids["Gone"] + ".html", self.ids["Gone"] + "-application.html", self.ids["Kept"] + ".html"):
            (pages / name).write_text("<html>" + "x" * 100 + "</html>", encoding="utf-8")
        result = closure.compact([self.ids["Gone"]], pages.parent)
        self.assertEqual(result["files"], 2)
        self.assertEqual([p.name for p in pages.iterdir()], [self.ids["Kept"] + ".html"])


if __name__ == "__main__":
    unittest.main()
