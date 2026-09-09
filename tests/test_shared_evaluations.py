"""All dashboard consumers must reuse durable role decisions across server restarts."""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from jobhunter.workspace import Archive, settings, ROOT, search_rules_hash
from jobhunter.selection import evaluate, queue, shortlist, filters
from jobhunter.analytics import summary
from jobhunter.interview import questions
from jobhunter.dashboard import create_server
from jobhunter.enrichment import company_input


class SharedEvaluationTests(unittest.TestCase):
    """Protect fast repeated reads, exclusive server binding and company evidence."""

    def test_dashboard_consumers_reuse_persisted_decisions(self):
        """Metrics, queue, review and details must not repeat description extraction."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'a.db'
            with closing(Archive(path)) as a:
                a.ingest([{'company_name': 'Example', 'title': 'Data Scientist',
                           'description': 'We build solar energy storage systems.',
                           'source_url': 'https://example.org/1'}], 'test')
                a.search(eligibility='potential')
            with closing(Archive(path)) as a, patch('jobhunter.selection.evaluate', wraps=evaluate) as check:
                summary(a)
                queue(a)
                questions(a)
                shortlist(a)
                cid = a.search()['items'][0]['id']
                a.show(cid)
                self.assertEqual(check.call_count, 0)

    def test_second_server_cannot_bind_same_port(self):
        """A restart must not silently leave two servers serving the same address."""
        with tempfile.TemporaryDirectory() as d:
            with create_server(Path(d) / 'a.db', settings(), 0) as first:
                with self.assertRaises(OSError):
                    with create_server(Path(d) / 'a.db', settings(), first.server_port):
                        pass

    def test_company_facts_include_saved_job_descriptions(self):
        """One informative job supplies business evidence even if other roles lack text."""
        with tempfile.TemporaryDirectory() as d, closing(Archive(Path(d) / 'a.db')) as a:
            a.ingest([{'company_name': 'Example', 'title': 'Developer',
                       'description': 'About us\nWe manufacture solar panels.',
                       'source_url': 'https://example.org/1'},
                      {'company_name': 'Example', 'title': 'Engineer', 'source_url': 'https://example.org/2'}], 'test')
            cid = a.search()['items'][0]['id']
            self.assertTrue(any('We manufacture solar panels.' in fact for fact in company_input(a, cid)['facts']))
            a.categorize()
            self.assertEqual(a.category(cid)['category'], 'Energia')

    def test_unrelated_code_does_not_invalidate_decisions(self):
        """Changing queue code leaves filters valid; changing extraction invalidates them."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'jobhunter').mkdir()
            for name in ('selection.py', 'languages.py', 'enrichment.py'):
                (root / 'jobhunter' / name).write_text((ROOT / 'jobhunter' / name).read_text(encoding='utf-8'), encoding='utf-8')
            with patch('jobhunter.workspace.ROOT', root):
                before = search_rules_hash(filters())
                file = root / 'jobhunter/selection.py'
                file.write_text(file.read_text(encoding='utf-8') + '\ndef unrelated():\n    return 2\n', encoding='utf-8')
                self.assertEqual(search_rules_hash(filters()), before)
                file.write_text(file.read_text(encoding='utf-8').replace('title = job.get("title", "")', 'title = job.get("title", "changed")'), encoding='utf-8')
                self.assertNotEqual(search_rules_hash(filters()), before)

    def test_requirements_and_conflicting_businesses_remain_unknown(self):
        """Skills and recruiting clients are not evidence of the employer's business."""
        with tempfile.TemporaryDirectory() as d, closing(Archive(Path(d) / 'a.db')) as a:
            a.ingest([{'company_name': 'Unknown', 'title': 'Software Developer',
                       'description': 'You will use Python for solar projects. We are hiring a software developer.',
                       'source_url': 'https://example.org/1'},
                      {'company_name': 'Mixed', 'title': 'Developer',
                       'description': 'We manufacture solar panels. We provide insurance.',
                       'source_url': 'https://example.org/2'},
                      {'company_name': 'Values', 'title': 'Developer',
                       'description': 'We are committed to sustainability. We are a community of explorers united by great food.',
                       'source_url': 'https://example.org/3'}], 'test')
            self.assertTrue(all(r['category'] == 'Da classificare' for r in a.search()['items']))
