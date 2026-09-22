"""Selezione a mano, requisiti letti dal testo e preferenze proposte ma mai automatiche."""

import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from jobhunter.workspace import Archive
from jobhunter.evaluation.selection import saved, requirements, proposals, metrics, evaluate


class QueueTests(unittest.TestCase):
    """Keep role decisions scoped and prevent automatic preference learning."""

    def setUp(self):
        """Create three independent companies with accessible engineering roles."""
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.temp.name)/"test.db")
        self.rows = [{"company_name": f"Company {i}", "title": "Data Scientist", "source_url": f"https://example.org/{i}", "company_industry": "Clean Energy", "description": "At least 1 year of experience required. 5 years of experience preferred."} for i in range(3)]
        self.archive.ingest(self.rows, "test")

    def tearDown(self):
        """Release temporary SQLite resources."""
        self.archive.close()
        self.temp.cleanup()

    def test_requirements_distinguish_preferred(self):
        """Preferred experience does not become a mandatory rejection."""
        facts = requirements(self.rows[0]["description"])
        self.assertEqual(facts["required_years"], 1)
        self.assertEqual(facts["preferred_years"], 5)
        self.assertEqual(evaluate(dict(self.rows[0], description="5 years of experience preferred"))["status"], "potential")
        self.assertEqual(evaluate(dict(self.rows[0], description="At least 5 years of experience required"))["status"], "excluded")

    def test_a_mandatory_range_starting_at_the_limit_is_out_of_profile(self):
        """«2-3 anni» obbligatori escludono; «0-3» e «1-4» restano aperti ai junior (scelta del 22/09/2026)."""
        role = dict(self.rows[0])
        self.assertEqual(evaluate(dict(role, description="Mindestens 2-3 Jahre Erfahrung in Data Science"))["status"], "excluded")
        for open_range in ("Junior (0-3 years of experience) required", "At least 1-4 years of experience",
                           "2-3 years of experience are a plus"):
            self.assertEqual(evaluate(dict(role, description=open_range))["status"], "potential", open_range)

    def test_calibrated_boundaries(self):
        """Reject physical specializations while preserving software and computational exceptions."""
        for title in ("Mechanical Engineer", "PCB Design Engineer", "Manufacturing Engineer"):
            self.assertEqual(evaluate({"title": title})["status"], "excluded", title)
        for title in ("Mechanical Simulation Engineer", "Manufacturing Software Engineer", "Engineer: Gas-Turbines CFD", "Software Developer"):
            self.assertEqual(evaluate({"title": title})["status"], "potential", title)
        for title in ("Systems Engineer", "cameriere ai piani", "Data Entry Specialist"):
            self.assertEqual(evaluate({"title": title})["status"], "review", title)
        self.assertEqual(evaluate({"title": "Robotics Software Engineer", "description": "A minimum of 2 years of relevant professional experience"})["status"], "potential")
        self.assertEqual(requirements("Minimum qualifications:\n5 years of experience developing models.\nPreferred qualifications:\n8 years of experience.")["required_years"], 5)
        self.assertEqual(requirements("At least 5 years of professional software-engineering experience")["required_years"], 5)
        self.assertEqual(requirements("minimum 4 lata doświadczenia")["required_years"], 4)
        self.assertEqual(requirements("au minimum 3 ans d'expérience professionnelle")["required_years"], 3)
        self.assertEqual(requirements("A 3 year degree and at least 2 years of experience")["required_years"], 2)

    def test_filtered_company_search(self):
        """A company survives with a relevant role; filtered row counts omit its unrelated roles."""
        self.archive.ingest([{**self.rows[0], "title": "Mechanical Engineer", "source_url": "https://example.org/mechanical"}], "test")
        all_jobs = self.archive.search(query="Company 0")["items"][0]
        matching = self.archive.search(query="Company 0", eligibility="potential")["items"][0]
        self.assertEqual(all_jobs["opportunity_count"], 2)
        self.assertEqual(matching["opportunity_count"], 1)

    def test_saving_a_company_or_a_role_fills_the_saved_list(self):
        """Salvare è una scelta a parte: entra nell'elenco, e annullarla lo svuota."""
        cid = self.archive.search()["items"][0]["id"]
        oid = self.archive.show(cid)["opportunities"][0]["id"]
        self.assertEqual(saved(self.archive)["items"], [])
        event = self.archive.feedback(cid, "saved", reason="interesting")
        self.archive.feedback(cid, "saved", opportunity_id=oid, reason="interesting")
        item = saved(self.archive)["items"][0]
        self.assertEqual((item["id"], item["company_saved"]), (cid, True))
        self.assertEqual([role["id"] for role in item["roles"]], [oid])
        self.assertEqual(item["tier_label"], self.archive.show(cid)["tier_label"])
        self.archive.undo(event["event_id"])
        self.assertEqual(saved(self.archive)["items"][0]["company_saved"], False)

    def test_manual_choice_leaves_the_pipeline_verdicts_alone(self):
        """Scartare a mano non cambia il giudizio della pipeline: sono due campi diversi."""
        cid = self.archive.search()["items"][0]["id"]
        before = self.archive.show(cid)
        self.archive.feedback(cid, "discarded", reason="company_not_interested")
        after = self.archive.show(cid)
        self.assertEqual(after["status"], "discarded")
        self.assertEqual(after["tier"], before["tier"])
        self.assertEqual([job["verdict"] for job in after["opportunities"]],
                         [job["verdict"] for job in before["opportunities"]])

    def test_proposals_stay_a_suggestion(self):
        """Tre scarti sullo stesso settore propongono una regola, che resta senza effetti automatici."""
        self.archive.categorize()
        for cid in [c["id"] for c in self.archive.search()["items"]]:
            event = self.archive.feedback(cid, "discarded", reason="sector")
        proposal = proposals(self.archive)["items"][0]
        self.assertEqual(proposal["state"], "proposed")
        self.assertEqual(proposals(self.archive, proposal["id"], "accepted")["state"], "accepted")
        self.archive.undo(event["event_id"])
        self.assertEqual(metrics(self.archive)["discarded"], 2)
        self.assertEqual(saved(self.archive)["items"], [])


if __name__ == "__main__":
    unittest.main()
