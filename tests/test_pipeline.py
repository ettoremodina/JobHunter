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
            self.assertEqual(steps['rewrite']['done'], 1)
            self.assertEqual(steps['categories']['done'], 0)
            self.assertIn('1 ancora senza categoria', steps['categories']['note'])
            with a.db:
                data = json.loads(job['data'])
                data['description'] = 'Changed content'
                a.db.execute('UPDATE opportunities SET data=?,content_hash=?', (json.dumps(data), 'changed'))
            steps = {s['id']: s for s in summary(a, settings(), root)['steps']}
            self.assertEqual(steps['filters']['stale'], 1)
            self.assertEqual(steps['filters']['done'], 0)
            self.assertEqual(steps['rewrite']['stale'], 1)
            self.assertEqual(steps['rewrite']['done'], 0)

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
