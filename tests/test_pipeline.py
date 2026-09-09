"""Pipeline monitoring must distinguish unknown, missing and stale processing evidence."""

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from jobhunter.pipeline import summary
from jobhunter.workspace import Archive, identity, settings


class PipelineTests(unittest.TestCase):
    """Verify coverage on disposable data without triggering the monitored stages."""

    def test_funnel_partitions_both_axes_and_composes_the_tier(self):
        """Ogni asse è una partizione completa; il tier ne discende e non è mai salvato."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory)/'a.db')) as a:
            a.ingest([{'company_name': company, 'title': title, 'description': 'Build models',
                       'source_url': f'https://example.org/{i}'} for i, (company, title) in enumerate([
                           ('Mixed', 'Data Scientist'), ('Mixed', 'ML Engineer'),
                           ('Mixed', 'HR Manager'), ('Excluded', 'HR Manager')])], 'test')
            a.evaluations()
            rows = a.db.execute('SELECT id,data FROM opportunities').fetchall()
            with a.db:
                for row in rows:
                    title = json.loads(row['data'])['title']
                    if title == 'ML Engineer':
                        continue
                    stored = json.dumps({'result': {'decision': 'keep'}})
                    a.db.execute('INSERT INTO enrichments VALUES(?,?,?,?,?,?,?)',
                                 ('remote:selection', row['id'], 'hash', 'key', stored, 'test', '2026-01-01'))
            before = a.db.total_changes
            f = summary(a, settings(), Path(directory))['funnel']
            self.assertEqual(a.db.total_changes, before)
            self.assertEqual(f['archive'], {'jobs': 4, 'companies': 2})
            # I due assi sono partizioni complete dei rispettivi insiemi.
            self.assertEqual(sum(f['ruolo'].values()), 4)
            self.assertEqual(sum(f['azienda'].values()), 2)
            self.assertEqual(sum(f['tier'].values()), 2)
            self.assertEqual((f['ruolo']['tieni'], f['ruolo']['scarta']), (2, 2))
            # Nessuna azienda ha una categoria: l'asse azienda non è valutabile, ma i ruoli primari valgono da soli.
            self.assertEqual(f['azienda']['evidenza_mancante'], 2)
            self.assertEqual((f['tier']['B-esperienza'], f['tier']['evidenza-mancante']), (1, 1))
            # Il regex ha già deciso ogni ruolo: nessun giudizio remoto entra nella cascata.
            self.assertEqual(f['giudici'], {'regex': 4})

    def test_description_card_counts_company_coverage(self):
        """DESIGN §4: la copertura si misura sulle aziende, non sui ruoli sopravvissuti ai filtri."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory)/'a.db')) as a:
            a.ingest([{'company_name': 'Example', 'title': title, 'description': text,
                       'source_url': f'https://example.org/{i}'} for i, (title,text) in enumerate([
                           ('Data Scientist', 'Build models'), ('Data Scientist', ''),
                           ('HR Manager', 'Manage recruitment')])], 'test')
            a.evaluations()
            result = summary(a, settings(), Path(directory))
            step = next(s for s in result['steps'] if s['id'] == 'descriptions')
            self.assertEqual((step['done'], step['total'], step['pending']), (1, 1, 0))
            self.assertEqual(result['opportunities'], 3)

    def test_empty_and_stale_evidence_is_read_only(self):
        """A changed description invalidates derived results without refreshing them on read."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory) / 'a.db')) as a:
            root = Path(directory)
            self.assertEqual(summary(a, settings(), root)['opportunities'], 0)
            a.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'location': 'Milano',
                       'description': 'Build models', 'source_url': 'https://example.org/1'}], 'test')
            a.search(eligibility='potential')
            job = a.db.execute('SELECT id,data FROM opportunities').fetchone()
            digest = identity(json.dumps('Build models', sort_keys=True, ensure_ascii=False))
            with a.db:
                a.db.execute('INSERT INTO enrichments VALUES(?,?,?,?,?,?,?)',
                             ('description', job['id'], digest, 'key', '{}', 'test', '2020-01-01T00:00:00+00:00'))
            with patch('jobhunter.selection.evaluate', side_effect=AssertionError('Monitor must not evaluate')):
                before = a.db.total_changes
                result = summary(a, settings(), root)
                self.assertEqual(a.db.total_changes, before)
            steps = {s['id']: s for s in result['steps']}
            self.assertEqual(steps['filters']['done'], 1)
            self.assertIsNotNone(steps['filters']['updated_at'])
            for retired in ('categories', 'analytics', 'reparse', 'rewrite'):
                self.assertNotIn(retired, steps)
            with a.db:
                data = json.loads(job['data'])
                data['description'] = 'Changed content'
                a.db.execute('UPDATE opportunities SET data=?,content_hash=?', (json.dumps(data), 'changed'))
            steps = {s['id']: s for s in summary(a, settings(), root)['steps']}
            self.assertEqual(steps['filters']['stale'], 1)
            self.assertEqual(steps['filters']['done'], 0)

    def test_old_filter_cache_has_no_invented_date(self):
        """Historical statuses can be valid even when their calculation date was not stored."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory) / 'a.db')) as a:
            a.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'source_url': 'https://example.org/1'}], 'test')
            a.search(eligibility='potential')
            with a.db:
                a.db.execute('DELETE FROM pipeline_updates')
            step = next(s for s in summary(a, settings(), Path(directory))['steps'] if s['id'] == 'filters')
            self.assertEqual(step['done'], 1)
            self.assertIsNone(step['updated_at'])
