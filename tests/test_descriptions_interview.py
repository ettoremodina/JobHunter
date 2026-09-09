"""Recovered facts survive imports; chat preferences affect eligibility without inventing feedback."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from jobhunter.workspace import Archive, normalize
from jobhunter.selection import evaluate, filters
from jobhunter.descriptions import extract, recover, application_link
from jobhunter.interview import review_rule


class DescriptionInterviewTests(unittest.TestCase):
    """Exercise persistence, evidence conditions and reversible rule application on disposable files."""

    def test_full_recovery_answers_company_coverage(self):
        """Recovery answers company coverage, not the role filters, and reports unvisited blocked jobs."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/descriptions.json').write_text(json.dumps({
                'default_limit': 1, 'max_limit': 50, 'allowed_hosts': ['www.linkedin.com'],
                'timeout_seconds': 1, 'request_delay_seconds': 0, 'output_directory': 'raw'}))
            archive = Archive(root / 'test.db')
            archive.ingest([{'company_name': 'Example', 'title': 'Software Engineer',
                             'source_url': f'https://www.linkedin.com/jobs/view/{i}'} for i in range(3)], 'jobspy')
            archive.ingest([{'company_name': 'Excluded', 'title': 'Senior Engineer', 'source_url': 'https://www.linkedin.com/jobs/view/excluded'}], 'jobspy')
            with patch('jobhunter.descriptions.ROOT', root), patch('jobhunter.descriptions.fetch', return_value='<div class="show-more-less-html__markup">Required experience: 5 years</div>') as fetch:
                result = recover(archive, all_missing=True)
            # DESIGN §4: l'azienda con soli ruoli esclusi dal regex resta cieca se la salta.
            self.assertEqual(result['saved'], 4)
            self.assertEqual(result['blind_companies'], 2)
            self.assertEqual(fetch.call_count, 4)
            self.assertEqual(result['remaining_missing'], 0)
            self.assertEqual([json.loads(r[0])['description'] != '' for r in archive.db.execute(
                "SELECT data FROM opportunities WHERE json_extract(data,'$.title')='Senior Engineer'")], [True])
            with archive.db:
                archive.db.execute("UPDATE opportunities SET data=json_set(data,'$.description','')")
            with patch('jobhunter.descriptions.ROOT', root), patch('jobhunter.descriptions.fetch', side_effect=HTTPError('https://www.linkedin.com', 429, 'Rate limit', {}, None)) as fetch:
                blocked = recover(archive, all_missing=True)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(blocked['status'], 'partial')
            self.assertEqual(blocked['unattempted'], 3)
            self.assertEqual(blocked['remaining_missing'], 4)
            archive.close()

    def test_airtable_mapping_preserves_observed_fields(self):
        """Map the actual embed column names without inventing numeric salary or company facts."""
        job = normalize({'Company': 'Example', 'Position Title': 'Engineer',
                         'Apply to Job': 'https://example.org/job', 'Location': 'Berlin, Germany',
                         'Country': 'Germany', 'Remote': 'Remote allowed',
                         'Commitment (Beta)': 'Full-time', '💰  Salary Range (Beta)': 'EUR 50000-60000',
                         'ℹ️ Company info': 'https://example.org/company'}, 'airtable')
        self.assertIn('Germany', job['locations'])
        self.assertEqual(job['remote_policy'], 'Remote allowed')
        self.assertEqual(job['employment_type'], 'Full-time')
        self.assertEqual(job['salary']['raw_text'], 'EUR 50000-60000')
        self.assertIsNone(job['salary']['min'])
        self.assertEqual(job['company_profile_url'], 'https://example.org/company')

    def test_placeholder_follows_employer_content(self):
        """Observed ClimateTechList SEO text is missing content; the employer contains requirements."""
        address = 'https://www.climatetechlist.com/job/example'
        node = {'@type': 'JobPosting', 'title': 'Engineer', 'description': 'Job posting details for Engineer at Example. ClimateTechList gathers 8,000+ job openings.'}
        html = '<script type="application/ld+json">' + json.dumps(node) + '</script><a href="https://jobs.smartrecruiters.com/Example/123">Apply to Job Posting →</a>'
        self.assertEqual(extract(html, address)[0], '')
        application = application_link(html, address)
        self.assertEqual(application, 'https://jobs.smartrecruiters.com/Example/123')
        body, method, _ = extract('<nav>Sign in</nav><div itemprop="description"><section>Job Description<p>Develop models.</p></section><section>Qualifications<p>Minimum 3 years of relevant experience.</p></section></div>', application)
        self.assertEqual(method, 'smartrecruiters_microdata')
        self.assertNotIn('Sign in', body)
        self.assertEqual(evaluate({'title': 'Software Engineer', 'description': body})['status'], 'excluded')

    def test_fresh_collection_preserves_personal_records_and_backup(self):
        """Reset acquired data, preserving feedback relationships and a restorable snapshot."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / 'test.db')
            archive.ingest([{'company_name': name, 'title': 'Engineer', 'source_url': f'https://example.org/{name}'} for name in ('Saved', 'Transient')], 'test')
            role = archive.db.execute("SELECT o.id,o.company_id FROM opportunities o JOIN companies c ON c.id=o.company_id WHERE c.name='Saved'").fetchone()
            archive.feedback(role['company_id'], 'saved', 'Keep this note', role['id'])
            archive.preference('Keep this preference')
            result = archive.reset_collection()
            self.assertEqual(result['retained_opportunities'], 1)
            self.assertEqual(archive.db.execute('SELECT note FROM feedback').fetchone()[0], 'Keep this note')
            self.assertEqual(archive.db.execute('SELECT count(*) FROM preferences').fetchone()[0], 1)
            self.assertEqual(list(archive.db.execute('PRAGMA foreign_key_check')), [])
            backup = Archive(result['backup'])
            self.assertEqual(backup.db.execute('SELECT count(*) FROM opportunities').fetchone()[0], 2)
            backup.close()
            archive.close()

    def test_recovered_description_survives_stub(self):
        """Same-source listing refresh must retain a recovered description and its provenance."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "test.db")
            raw = {"company_name": "Example", "title": "Systems Engineer", "source_url": "https://example.org/job"}
            archive.ingest([raw], "test")
            before = archive.db.execute("SELECT * FROM opportunities").fetchone()
            provenance = {"url": "https://example.org/job", "method": "jobposting_jsonld"}
            archive.save_description(before["id"], "Develop software models", provenance)
            self.assertEqual(archive.db.execute("SELECT last_seen FROM opportunities").fetchone()[0], before["last_seen"])
            archive.ingest([raw], "test")
            job = json.loads(archive.db.execute("SELECT data FROM opportunities").fetchone()[0])
            self.assertEqual(job["description"], "Develop software models")
            self.assertEqual(job["description_provenance"], provenance)
            archive.close()

    def test_description_only_extracts_job_content(self):
        """Boilerplate is not a description, and access blocks are errors."""
        body, method, _ = extract('<nav>Sign in</nav><div class="show-more-less-html__markup"><p>Develop models</p><p>No visa sponsorship</p></div>', 'https://www.linkedin.com/jobs/view/123')
        self.assertNotIn("Sign in", body)
        self.assertIn("No visa sponsorship", body)
        self.assertEqual(method, "linkedin_public_description")
        with self.assertRaises(ValueError):
            extract("You are being rate limited", "https://www.linkedin.com/jobs/view/123")

    def test_user_rule_conditions_and_hard_exclusions(self):
        """A conditional interest cannot bypass seniority or assume missing evidence."""
        rules = filters()
        rules["user_title_rules"] = [{"id": "systems", "pattern": "systems engineer", "action": "include", "evidence_pattern": "software", "note": "User preference"}]
        self.assertEqual(evaluate({"title": "Systems Engineer"}, rules)["status"], "review")
        self.assertEqual(evaluate({"title": "Systems Engineer", "description": "Develop software"}, rules)["status"], "potential")
        self.assertEqual(evaluate({"title": "Senior Systems Engineer", "description": "Develop software"}, rules)["status"], "excluded")
        rules["user_title_rules"][0]["enabled"] = False
        self.assertEqual(evaluate({"title": "Systems Engineer", "description": "Develop software"}, rules)["status"], "review")

    def test_rule_preview_does_not_write(self):
        """Preview is read-only; applying writes an auditable config without manual decisions."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            rules = filters()
            config = root / "config/role_filters.json"
            config.write_text(json.dumps(rules), encoding="utf-8")
            archive = Archive(root / "test.db")
            archive.ingest([{"company_name": "Example", "title": "Systems Engineer", "source_url": "https://example.org/job"}], "test")
            rule = {"id": "systems", "pattern": "systems engineer", "action": "include", "note": "User explicitly wants these roles"}
            with patch("jobhunter.interview.ROOT", root), patch("jobhunter.interview.filters", return_value=rules):
                preview = review_rule(archive, rule)
                self.assertEqual(preview["changed_opportunities"], 1)
                self.assertEqual(rules, json.loads(config.read_text(encoding="utf-8")))
                review_rule(archive, rule, apply=True)
                self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["user_title_rules"][-1]["id"], "systems")
            self.assertEqual(archive.db.execute("SELECT count(*) FROM feedback").fetchone()[0], 0)
            archive.close()
