"""Product decisions distinguish mandatory constraints, preferences and missing evidence."""

import json
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from jobhunter.collection import postings
from jobhunter.selection import evaluate, requirements, saved
from jobhunter.workspace import Archive
from jobhunter.descriptions import recover
from jobhunter.maintenance import reparse


class ProductSelectionTests(unittest.TestCase):
    """Protect section meaning and explicit user constraints without inferring geography."""

    def test_structured_requirements_survive_extraction(self):
        """Qualifications and preferred qualifications must remain separate after JSON-LD parsing."""
        node = {'@type': 'JobPosting', 'description': '<h2>Qualifications</h2><p>3 years of experience</p><h2>Preferred qualifications</h2><p>5 years of experience</p>'}
        job = postings('<script type="application/ld+json">' + json.dumps(node) + '</script>', 'https://example.org/job')[0]
        self.assertEqual(requirements(job['description'])['required_years'], 3)
        self.assertEqual(requirements(job['description'])['preferred_years'], 5)

    def test_languages_are_explicit_requirements(self):
        """Unsupported mandatory languages exclude; optional languages and location do not."""
        for text in ('Fluent German required.', 'English and French are required.', 'Ottima conoscenza del tedesco richiesta.'):
            self.assertEqual(evaluate({'title': 'Data Scientist', 'description': text})['status'], 'excluded', text)
        for text in ('English or German required.', 'French is a plus. English required.', 'German is not required.', 'Based in Berlin.', 'Italian and English required.'):
            self.assertEqual(evaluate({'title': 'Data Scientist', 'description': text})['status'], 'potential', text)

    def test_career_priority_is_not_an_exclusion(self):
        """Both role families remain candidates, with mathematical/data work preferred."""
        primary = evaluate({'title': 'Machine Learning Engineer'})
        secondary = evaluate({'title': 'Software Developer'})
        self.assertEqual(primary['career_priority'], 'primary')
        self.assertEqual(secondary['career_priority'], 'secondary')
        self.assertEqual(secondary['status'], 'potential')

    def test_negated_experience_does_not_exclude(self):
        """An explicitly waived experience requirement stays unknown, even under Requirements."""
        for text in ('3 years of experience are not required.',
                     'Requirements\n3 years of experience are not necessary.',
                     'Non sono richiesti 3 anni di esperienza.'):
            with self.subTest(text=text):
                decision = evaluate({'title': 'Data Scientist', 'description': text})
                self.assertEqual(decision['status'], 'potential')
                self.assertIsNone(decision['requirements']['required_years'])
        self.assertEqual(requirements('3 years of experience are not required.\nMinimum 4 years of experience.')['required_years'], 4)

    def test_language_negations_and_comma_lists(self):
        """Waived languages stay optional; commas in language lists preserve every requirement."""
        for text in ('German is not mandatory.', 'Fluent German is not necessary.',
                     'English required, German optional.', 'German, English or Italian required.'):
            with self.subTest(text=text):
                self.assertEqual(evaluate({'title': 'Data Scientist', 'description': text})['status'], 'potential')
        for text, unsupported in (('German, English required.', ['German']),
                                  ('German, French and English required.', ['French', 'German'])):
            with self.subTest(text=text):
                decision = evaluate({'title': 'Data Scientist', 'description': text})
                self.assertEqual(decision['status'], 'excluded')
                self.assertEqual(decision['requirements']['languages']['unsupported'], unsupported)
                self.assertEqual(decision['requirements']['languages']['evidence'], [text])

    def test_company_rejection_is_recorded_beside_the_pipeline(self):
        """Una decisione manuale resta un campo a parte: nuovi annunci non la cancellano, e i verdetti non cambiano."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory) / 'test.db')) as archive:
            raw = {'company_name': 'Example', 'title': 'Data Scientist', 'source_url': 'https://example.org/1'}
            archive.ingest([raw], 'test')
            cid = archive.db.execute('SELECT id FROM companies').fetchone()[0]
            archive.feedback(cid, 'review', reason='no_current_roles')
            archive.ingest([{**raw, 'source_url': 'https://example.org/2'}], 'test')
            self.assertEqual(archive.show(cid)['status'], 'review')
            archive.feedback(cid, 'discarded', reason='company_not_interested')
            archive.ingest([{**raw, 'source_url': 'https://example.org/3'}], 'test')
            company = archive.show(cid)
            self.assertEqual(company['status'], 'discarded')
            # Il verdetto della pipeline resta quello dei filtri: la scelta manuale non lo riscrive.
            self.assertEqual({job['verdict']['verdetto'] for job in company['opportunities']}, {'tieni'})
            self.assertEqual(saved(archive)['items'], [])

    def test_recovery_remembers_failures_and_refreshes_old_text(self):
        """A stale text can refresh; a missing page is deferred instead of retried every batch."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/descriptions.json').write_text(json.dumps({'default_limit': 1, 'max_limit': 50, 'allowed_hosts': ['www.linkedin.com'], 'timeout_seconds': 1, 'request_delay_seconds': 0, 'output_directory': 'raw', 'refresh_after_days': 30}))
            with closing(Archive(root / 'test.db')) as archive:
                archive.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'source_url': 'https://www.linkedin.com/jobs/view/1', 'description': 'Old text'}], 'test', '2020-01-01T00:00:00+00:00')
                with patch('jobhunter.descriptions.ROOT', root), patch('jobhunter.descriptions.fetch', return_value='<div class="show-more-less-html__markup">New text</div>') as fetch:
                    self.assertEqual(recover(archive)['attempted'], 0)
                    self.assertEqual(recover(archive, refresh_stale=True)['saved'], 1)
                    self.assertEqual(fetch.call_count, 1)
                with archive.db:
                    archive.db.execute("UPDATE opportunities SET data=json_set(data,'$.description','')")
                with patch('jobhunter.descriptions.ROOT', root), patch('jobhunter.descriptions.fetch', side_effect=HTTPError('https://www.linkedin.com', 404, 'Missing', {}, None)) as fetch:
                    recover(archive)
                    result = recover(archive, all_missing=True)
                    self.assertEqual(fetch.call_count, 1)
                    self.assertEqual(result['deferred'], 1)
                    self.assertEqual(result['remaining_missing'], 1)
                    self.assertEqual(result['status'], 'partial')
                self.assertEqual(archive.db.execute('SELECT status FROM description_attempts').fetchone()[0], 'missing_page')
                cid = archive.db.execute('SELECT id FROM companies').fetchone()[0]
                archive.feedback(cid, 'discarded', reason='company_not_interested')
                with patch('jobhunter.descriptions.ROOT', root), patch('jobhunter.descriptions.fetch') as fetch:
                    self.assertEqual(recover(archive, force=True)['excluded_by_company'], 1)
                    fetch.assert_not_called()

    def test_reparse_is_offline_and_does_not_refresh_listing_date(self):
        """Only restore structure from the same saved content, preserving observation age."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / 'saved.html'
            page.write_text('<script type="application/ld+json">' + json.dumps({'@type': 'JobPosting', 'description': '<h2>Qualifications</h2><p>3 years of experience</p>'}) + '</script>')
            with closing(Archive(root / 'test.db')) as archive:
                archive.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'source_url': 'https://example.org/1', 'description': 'Qualifications 3 years of experience'}], 'test', '2020-01-01T00:00:00+00:00')
                row = archive.db.execute('SELECT * FROM opportunities').fetchone()
                archive.save_description(row['id'], 'Qualifications 3 years of experience', {'raw_file': str(page), 'retrieved_at': row['last_seen'], 'url': 'https://example.org/1'}, replace=True)
                with patch('jobhunter.maintenance.ROOT', root), patch('jobhunter.descriptions.fetch', side_effect=AssertionError('No network')):
                    self.assertEqual(reparse(archive)['updated'], 1)
                updated = archive.db.execute('SELECT * FROM opportunities').fetchone()
                self.assertEqual(updated['last_seen'], row['last_seen'])
                self.assertEqual(evaluate(json.loads(updated['data']))['status'], 'excluded')
