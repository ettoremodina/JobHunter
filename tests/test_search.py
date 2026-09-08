"""Regression checks for paginated search and filters on the same opportunity."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobhunter.selection import evaluate, filters
from jobhunter.workspace import Archive


class SearchTests(unittest.TestCase):
    """Keep location, source and eligibility consistent across pages and updates."""

    def setUp(self):
        """Create two companies whose roles have conflicting locations and eligibility."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'archive.db'
        self.archive = Archive(self.path)
        self.addCleanup(lambda: self.archive.close())
        self.archive.ingest([
            {'company_name': 'Mixed', 'title': 'Senior engineer', 'location': 'Milano', 'source_url': 'https://example.org/1'},
            {'company_name': 'Mixed', 'title': 'Data Scientist', 'location': 'Roma', 'source_url': 'https://example.org/2'},
            {'company_name': 'Local', 'title': 'Data Scientist', 'location': 'Milano', 'source_url': 'https://example.org/3'},
        ], 'test')

    def test_filters_match_the_same_role(self):
        """A rejected Milan role cannot qualify a compatible Rome role for Milan."""
        result = self.archive.search(location='Milano', eligibility='potential')
        self.assertEqual([r['name'] for r in result['items']], ['Local'])
        self.assertEqual(result['items'][0]['locations'], ['Milano'])
        self.assertEqual(self.archive.search(location='Milano', eligibility='excluded')['total'], 1)
        self.archive.ingest([{'company_name': 'Mixed', 'title': 'Data Scientist', 'location': 'Torino', 'source_url': 'https://example.org/4'}], 'other')
        self.assertEqual(self.archive.search(location='Milano', source='other')['total'], 0)

    def test_pages_reuse_evaluations_and_invalidate_changes(self):
        """Reuse statuses across HTTP connections, but refresh changed roles and rules."""
        with patch('jobhunter.selection.evaluate', wraps=evaluate) as check:
            self.archive.search(eligibility='potential', limit=1)
            first = check.call_count
            self.assertEqual(first, 3)
            self.archive.close()
            self.archive = Archive(self.path)
            self.archive.search(eligibility='potential', limit=1, offset=1)
            self.assertEqual(check.call_count, first, 'Pagination must not reanalyse descriptions')
            self.archive.ingest([{'company_name': 'Local', 'title': 'Senior engineer', 'location': 'Milano', 'source_url': 'https://example.org/3'}], 'test')
            self.assertEqual(self.archive.search(location='Milano', eligibility='potential')['total'], 0)
            self.assertEqual(check.call_count, first + 1)
            rules = filters()
            rules['exclude_title_patterns'] = {}
            with patch('jobhunter.selection.filters', return_value=rules):
                self.archive.search(eligibility='potential')
            self.assertEqual(check.call_count, first + 4)
