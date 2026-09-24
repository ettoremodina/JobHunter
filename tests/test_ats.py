"""ATS boards: where they are recognised, whose they are, what they return and what they may close."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobhunter.acquisition import ats, closure
from jobhunter.workspace import Archive

BEFORE, STARTED, AGAIN = "2026-09-01T10:00:00+00:00", "2026-09-20T09:00:00+00:00", "2026-09-20T10:00:00+00:00"


class Response:
    """The few parts of a `requests` response the adapters read."""

    def __init__(self, payload, status=200):
        self.status_code, self.ok, self.payload = status, status < 400, payload
        self.content = payload.encode() if isinstance(payload, str) else b""
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return self.payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class DetectionTests(unittest.TestCase):
    """Board references in URLs and pages, and the name check that keeps namesakes out."""

    def test_urls_and_embeds(self):
        text = ("https://job-boards.eu.greenhouse.io/marvelfusion/jobs/1 https://jobs.eu.lever.co/acme/2 "
                "<script src='https://boards.greenhouse.io/embed/job_board/js?for=vectra'></script> "
                "https://abb.wd3.myworkdayjobs.com/en-US/External/job/x https://apply.workable.com/j/ABC "
                "https://www.recruitee.com https://intouch.recruitee.com/o/role")
        found = {(b["ats"], b["slug"]) for b in ats.detect(text)}
        self.assertEqual(found, {("greenhouse", "marvelfusion"), ("greenhouse", "vectra"), ("lever_eu", "acme"),
                                 ("workday", "abb/wd3/External"), ("recruitee", "intouch")})

    def test_same_company(self):
        self.assertTrue(ats.same_company("STARK", "Jobs at STARK"))
        self.assertTrue(ats.same_company("Marvel Fusion GmbH", "Marvel Fusion"))
        self.assertTrue(ats.same_company("CarCutter", "Car Cutter"))
        self.assertFalse(ats.same_company("Nova", "Novartis"))
        self.assertFalse(ats.same_company("SMA Solar Technology", "SMA America"))


class AdapterTests(unittest.TestCase):
    """Payloads mapped to the common row, checked offline for the ATSs hardest to reach live."""

    def test_recruitee_and_personio(self):
        offers = {"offers": [{"title": "Data Scientist", "careers_url": "https://acme.recruitee.com/o/data-scientist",
                              "description": "<p>Build models.</p>", "requirements": "<ul><li>Python</li></ul>",
                              "published_at": "2026-09-01 10:00:00 UTC", "location": "Milano, Italy", "remote": False,
                              "hybrid": True, "employment_type_code": "fulltime"}]}
        row = ats.jobs({"ats": "recruitee", "slug": "acme"}, lambda *a, **k: Response(offers))[0][0]
        self.assertEqual((row["posted_at"], row["remote_policy"], row["description"]), ("2026-09-01", "hybrid", "Build models.\n\nPython"))
        xml = ("<workzag-jobs><position><id>7</id><office>Berlin</office><additionalOffices><office>Munich</office>"
               "</additionalOffices><name>Engineer</name><jobDescriptions><jobDescription><name>Role</name>"
               "<value><![CDATA[<p>Simulate grids.</p>]]></value></jobDescription></jobDescriptions>"
               "<createdAt>2026-08-30T09:00:00+00:00</createdAt><schedule>full-time</schedule></position></workzag-jobs>")
        row = ats.jobs({"ats": "personio", "slug": "acme"}, lambda *a, **k: Response(xml))[0][0]
        self.assertEqual((row["source_url"], row["locations"], row["description"]),
                         ("https://acme.jobs.personio.de/job/7", ["Berlin", "Munich"], "Role\nSimulate grids."))

    def test_known_ads_skip_the_detail_call(self):
        """SmartRecruiters needs a second call for the text; an ad already read does not pay it again."""
        listing = {"totalFound": 2, "content": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]}
        calls = []

        def get(method, address, **kwargs):
            calls.append(address)
            return Response(listing if "offset" in address else {"jobAd": {"sections": {"x": {"title": "Job", "text": "<p>Text</p>"}}}})
        rows, complete = ats.jobs({"ats": "smartrecruiters", "slug": "Acme"}, get, known={"https://jobs.smartrecruiters.com/Acme/1"})
        self.assertTrue(complete)
        self.assertEqual([r.get("description") for r in rows], [None, "Job\nText"])
        self.assertEqual(sum("postings/" in c for c in calls), 1)


class CollectionTests(unittest.TestCase):
    """Rows land on the archive's company, and only boards read in full may close ads."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.archive = Archive(self.root / "a.db")
        self.addCleanup(self.archive.close)

    def ad(self, company, title):
        return {"company_name": company, "title": title, "source_url": f"https://example.org/{company}/{title}",
                "description": "Text", "posted_at": "2026-09-01"}

    def test_rows_use_the_archive_company_and_report_complete_boards(self):
        with self.archive.db:
            cid = self.archive.company("Marvel Fusion GmbH", "https://marvelfusion.com")
        (self.root / "config").mkdir()
        (self.root / "config/w.json").write_text(json.dumps({"boards": [
            {"ats": "greenhouse", "slug": "marvelfusion", "company_id": cid, "company": "Marvel Fusion"},
            {"ats": "lever", "slug": "broken", "company": "Broken"}]}), encoding="utf-8")

        def jobs(board, get, known, cap):
            if board["slug"] == "broken":
                raise RuntimeError("HTTP 500")
            return [{"title": "Engineer", "source_url": "https://job-boards.greenhouse.io/marvelfusion/jobs/1", "locations": [None, "Munich"]}], True
        with patch.object(ats, "ROOT", self.root), patch.object(ats, "jobs", jobs):
            rows, errors, answered = ats.rows(self.archive, {"config": "config/w.json"}, {"timeout_seconds": 1})
        self.assertEqual((rows[0]["company_name"], rows[0]["website_url"], rows[0]["locations"]),
                         ("Marvel Fusion GmbH", "https://marvelfusion.com/", ["Munich"]))
        self.assertEqual([e["board"] for e in errors], ["lever/broken"])
        self.assertEqual(answered, {"Marvel Fusion GmbH"})
        self.archive.ingest(rows, "ats")
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM companies").fetchone()[0], 1)

    def test_closure_is_limited_to_boards_that_answered(self):
        self.archive.ingest([self.ad("Read", "Kept"), self.ad("Read", "Gone"), self.ad("Failed", "Missing")], "ats", BEFORE)
        self.archive.ingest([self.ad("Read", "Kept")], "ats", AGAIN)
        read = self.archive.db.execute("SELECT id FROM companies WHERE name='Read'").fetchone()[0]
        found = closure.detect(self.archive, STARTED, {"ats": None}, 0.9, {"ats": {read}})
        titles = {r[0] for r in self.archive.db.execute(
            "SELECT json_extract(o.data,'$.title') FROM closures c JOIN opportunities o ON o.id=c.opportunity_id")}
        self.assertEqual((found["closed"], titles), (1, {"Gone"}))


if __name__ == "__main__":
    unittest.main()
