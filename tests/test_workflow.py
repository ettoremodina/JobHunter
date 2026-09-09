"""Offline regressions for company aggregation, chat decisions, parsing and HTTP safety."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from jobhunter.workspace import Archive, settings, url
from jobhunter.collection import postings, collect
from jobhunter.dashboard import create_server


class WorkflowTests(unittest.TestCase):
    """Exercise the same archive operations used by CLI and dashboard."""

    def setUp(self):
        """Use an isolated database and two distinct roles at one company."""
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.temp.name) / "test.db")
        self.rows = [{"company_name": "Example", "title": "Junior Engineer", "original_url": "https://example.org/jobs/1", "location": "Italy", "min_amount": "40000", "currency": "EUR", "interval": "yearly"},
                     {"company_name": "Example", "title": "Senior Engineer", "original_url": "https://example.org/jobs/2", "location": "Germany"}]

    def tearDown(self):
        """Close all temporary state."""
        self.archive.close()
        self.temp.cleanup()

    def seed(self):
        """Import the fixture and return its company ID."""
        self.archive.ingest(self.rows, "fixture", "2026-01-01T00:00:00+00:00")
        return self.archive.search()["items"][0]["id"]

    def test_roles_and_idempotent_import(self):
        """Aggregation preserves role attributes and repeated imports do not duplicate jobs."""
        cid = self.seed()
        result = self.archive.ingest(self.rows, "fixture")
        self.assertEqual(result["unchanged"], 2)
        self.assertEqual(self.archive.stats()["companies"], 1)
        detail = self.archive.show(cid)
        self.assertEqual(len(detail["opportunities"]), 2)
        junior = next(j for j in detail["opportunities"] if j["title"].startswith("Junior"))
        self.assertEqual(junior["salary"]["min"], 40000)
        self.assertEqual(junior["locations"], ["Italy"])
        self.assertEqual(self.archive.search(query="Junior", location="Italy")["total"], 1)
        self.assertEqual(self.archive.search(source="missing")["total"], 0)

    def test_scoped_feedback_undo_and_reopen(self):
        """Role decisions do not discard companies and all events survive restart."""
        cid = self.seed()
        oid = self.archive.show(cid)["opportunities"][0]["id"]
        self.archive.feedback(cid, "discarded", "Too senior", oid)
        self.assertEqual(self.archive.show(cid)["status"], "new")
        event = self.archive.feedback(cid, "saved", "Interesting")
        self.assertTrue(self.archive.feedback(cid, "saved", "Interesting")["duplicate"])
        self.archive.close()
        self.archive = Archive(Path(self.temp.name) / "test.db")
        self.assertEqual(self.archive.search(status="saved")["total"], 1)
        self.archive.undo(event["event_id"])
        self.assertEqual(self.archive.show(cid)["status"], "new")

    def test_older_snapshot_does_not_replace_recent_details(self):
        """Reimporting history must retain newer salary and location observations."""
        cid = self.seed()
        newer = dict(self.rows[0], location="France", min_amount=50000)
        self.archive.ingest([newer], "fixture", "2026-06-01T00:00:00+00:00")
        result = self.archive.ingest([self.rows[0]], "fixture", "2026-01-01T00:00:00+00:00")
        self.assertEqual(result["unchanged"], 1)
        job = next(j for j in self.archive.show(cid)["opportunities"] if j["title"].startswith("Junior"))
        self.assertEqual(job["locations"], ["France"])
        self.assertEqual(job["salary"]["min"], 50000)

    def test_assessment_and_research(self):
        """Explicit preference changes make old assessments stale; invalid references fail."""
        cid = self.seed()
        self.archive.assess(cid, {"reasoning": "Relevant activity", "missing_information": ["Remote eligibility"]})
        self.assertFalse(self.archive.show(cid)["assessment"]["stale"])
        self.archive.preference("Italy only")
        self.assertTrue(self.archive.show(cid)["assessment"]["stale"])
        with self.assertRaises(ValueError):
            self.archive.assess(cid, {"reasoning": "Invalid", "relevant_opportunity_ids": ["unknown"]})
        one = self.archive.add_evidence(cid, "https://example.org/about", "Builds energy systems")
        self.assertEqual(one, self.archive.add_evidence(cid, "https://example.org/about", "Builds energy systems"))

    def test_missing_identity_and_suspended_source(self):
        """Invalid rows are reported and suspended sources never contact the network."""
        result = self.archive.ingest([{"title": "No company"}], "fixture")
        self.assertEqual(len(result["rejected"]), 1)
        result = collect(self.archive, settings(), "inclimate.com")
        self.assertEqual(result["status"], "access_required")
        self.assertEqual(self.archive.stats()["opportunities"], 0)

    def test_shared_categories_preserve_chat_assignments(self):
        """Rules use company facts across sources, while chat corrections survive imports."""
        self.archive.ingest([dict(self.rows[0], company_industry="Clean Energy")], "jobspy")
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM categories").fetchone()[0], 0)
        self.archive.categorize()
        cid = self.archive.search(category="Energia")["items"][0]["id"]
        self.assertEqual(self.archive.show(cid)["category_method"], "rules")
        self.archive.categorize(cid, "Industria e materiali", "Company manufactures components")
        self.archive.ingest([dict(self.rows[1], company_vertical="Clean Energy")], "airtable")
        self.assertEqual(self.archive.show(cid)["category"], "Industria e materiali")
        self.assertEqual(self.archive.search(category="Energia")["total"], 0)
        self.archive.ingest([dict(self.rows[0], company_name="Unknown", title="Solar engineer", original_url="https://unknown.org/1")], "other")
        self.assertEqual(self.archive.search(category="Da classificare")["total"], 1)
        with self.assertRaises(ValueError):
            self.archive.categorize(cid, "Invented category", "Reason")

    def test_urls_and_jsonld(self):
        """Normalize tracking URLs and preserve JSON-LD title, location and salary."""
        self.assertEqual(url("https://example.org/job/1?utm_source=x&id=2#top"), "https://example.org/job/1?id=2")
        self.assertEqual(url("javascript:alert(1)"), "")
        node = {"@graph": [{"@type": "JobPosting", "title": "Researcher", "hiringOrganization": {"name": "Lab"}, "description": "<p>Model energy systems</p>", "baseSalary": {"currency": "EUR", "value": {"minValue": 40000, "unitText": "YEAR"}}, "jobLocation": {"address": {"addressLocality": "Milan", "addressCountry": "IT"}}}]}
        records = postings('<script type="application/ld+json">'+json.dumps(node)+'</script>', "https://example.org/job")
        self.assertEqual(records[0]["locations"], ["Milan, IT"])
        self.assertEqual(records[0]["salary"]["min"], 40000)
        self.assertEqual(records[0]["description"], "Model energy systems")

    def test_http_routes_and_csrf(self):
        """The local server exposes only assets/API and rejects unauthenticated writes."""
        cid = self.seed()
        server = create_server(self.archive.path, settings(), 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/bootstrap") as response:
                token = json.load(response)["token"]
            for route in ("/.env", "/data/1_unified_jobs_raw.json", "/../.env"):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(base + route)
                self.assertEqual(ctx.exception.code, 404)
            body = json.dumps({"company_id": cid, "status": "saved"}).encode()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(urllib.request.Request(base + "/api/feedback", body))
            self.assertEqual(ctx.exception.code, 403)
            body_action = json.dumps({'step': 'filters', 'parameters': {}}).encode()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(urllib.request.Request(base + '/api/pipeline/start', body_action))
            self.assertEqual(ctx.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(urllib.request.Request(base + '/api/pipeline/start', json.dumps({'step': 'shell', 'parameters': {}}).encode(), {'X-JobHunter-Token': token, 'Content-Type': 'application/json'}))
            self.assertEqual(ctx.exception.code, 400)
            request = urllib.request.Request(base + "/api/feedback", body, {"X-JobHunter-Token": token, "Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response:
                self.assertIn("event_id", json.load(response))
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
