"""Regression checks for paginated search and filters on the same opportunity."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobhunter.evaluation.selection import evaluate, filters
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
        with patch('jobhunter.evaluation.selection.evaluate', wraps=evaluate) as check:
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
            with patch('jobhunter.evaluation.selection.filters', return_value=rules):
                self.archive.search(eligibility='potential')
            self.assertEqual(check.call_count, first + 4)


class RoleVisibilityTests(unittest.TestCase):
    """Ogni riga dichiara quali ruoli hanno risposto ai filtri, e l'ordine vale su tutto l'insieme."""

    def setUp(self):
        """Un'azienda con molti ruoli in città diverse, e una con un ruolo solo."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = Archive(Path(self.directory.name) / 'archive.db')
        self.addCleanup(lambda: self.archive.close())
        rows = [{'company_name': 'Molti', 'title': f'Data Scientist {n}', 'location': 'Roma',
                 'source_url': f'https://example.org/many/{n}'} for n in range(5)]
        rows.append({'company_name': 'Molti', 'title': 'Data Scientist Milano', 'location': 'Milano',
                     'source_url': 'https://example.org/many/milano'})
        rows.append({'company_name': 'Uno', 'title': 'Data Scientist', 'location': 'Milano',
                     'source_url': 'https://example.org/one'})
        self.archive.ingest(rows, 'test')

    def test_matching_ids_narrow_to_the_filtered_roles(self):
        """Filtrando per Milano, l'azienda con sei ruoli ne dichiara uno solo: gli altri restano in archivio."""
        items = {item['name']: item for item in self.archive.search(location='Milano')['items']}
        self.assertEqual(len(items['Molti']['matching_ids']), 1)
        self.assertEqual(items['Molti']['opportunity_count'], 1)
        self.assertEqual(items['Molti']['archive_opportunity_count'], 6)
        self.assertEqual(items['Molti']['locations'], ['Milano'])

    def test_text_narrows_roles_only_when_a_role_contains_it(self):
        """Il testo trovato in un annuncio restringe ai suoi ruoli; trovato solo nell'azienda, li lascia tutti."""
        narrowed = self.archive.search(query='Milano')['items']
        self.assertEqual({item['name']: len(item['matching_ids']) for item in narrowed}, {'Molti': 1, 'Uno': 1})
        self.assertEqual({item['name']: item['matching_roles'] for item in narrowed}, {'Molti': 1, 'Uno': 1})
        by_name = {item['name']: item for item in self.archive.search(query='Molti')['items']}
        self.assertEqual(len(by_name['Molti']['matching_ids']), 6)
        self.assertEqual(by_name['Molti']['matching_roles'], 6)
        self.assertNotIn('archive_opportunity_count', by_name['Molti'])

    def test_sorting_applies_to_the_whole_result_set(self):
        """L'ordinamento sceglie chi sta nella prima pagina, non solo come sono disposti i suoi trenta."""
        self.assertEqual([i['name'] for i in self.archive.search(sort='nome')['items']], ['Molti', 'Uno'])
        self.assertEqual(self.archive.search(sort='ruoli', limit=1)['items'][0]['name'], 'Molti')
        self.assertEqual(self.archive.search(sort='ruoli', limit=1, location='Milano')['items'][0]['matching_roles'], 1)
        self.archive.ingest([{'company_name': 'Uno', 'title': 'Data Scientist Milano',
                             'location': 'Milano', 'source_url': 'https://example.org/one/second'}], 'test')
        first = self.archive.search(query='Milano', city='Milano', sort='ruoli', limit=1)['items'][0]
        self.assertEqual((first['name'], first['matching_roles']), ('Uno', 2))
        with self.assertRaises(ValueError):
            self.archive.search(sort='; DROP TABLE companies')

    def test_publication_date_sorts_and_filters_roles(self):
        """Newest publication first; an old role drops out of the recent filter, an undated one stays."""
        from datetime import date, timedelta
        recent, old = ((date.today() - timedelta(days=d)).isoformat() for d in (3, 90))
        self.archive.ingest([
            {'company_name': 'Fresh', 'title': 'Data Scientist', 'posted_at': recent, 'source_url': 'https://example.org/f'},
            {'company_name': 'Stale', 'title': 'Data Scientist', 'posted_at': old + 'T00:00:00.000', 'source_url': 'https://example.org/s'},
        ], 'test')
        names = [i['name'] for i in self.archive.search(sort='pubblicati')['items']]
        self.assertEqual(names[:2], ['Fresh', 'Stale'])
        self.assertEqual(self.archive.search(query='Fresh', sort='pubblicati')['items'][0]['latest_posted'], recent)
        kept = {i['name'] for i in self.archive.search(posted_days=30)['items']}
        self.assertIn('Fresh', kept)
        self.assertNotIn('Stale', kept)
        # Le aziende del setUp non hanno date: restano visibili, meglio un falso positivo che un buco.
        self.assertTrue({'Molti', 'Uno'} <= kept)
