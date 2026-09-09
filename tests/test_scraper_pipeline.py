"""Verify acquisition and description recovery without network access."""

from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jobhunter.collection import collect
from jobhunter.sweep import sweep
from jobhunter.workspace import Archive


class ScraperPipelineTests(unittest.TestCase):
    """A bounded refresh fetches only its jobs; collect-all only collects."""

    def test_bounded_collection_recovers_and_sweep_only_collects(self):
        """DESIGN §10: lo sweep raccoglie e basta; descrizioni, analisi e coda hanno il loro passo."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/airtable.yaml').write_text('embed_url: https://example.org/embed')
            (root / 'config/sweep.json').write_text(json.dumps({}))
            (root / 'config/descriptions.json').write_text(json.dumps({
                'default_limit': 1, 'max_limit': 50, 'allowed_hosts': ['www.linkedin.com'],
                'timeout_seconds': 1, 'request_delay_seconds': 0, 'output_directory': 'details'}))
            cfg = {'sources': {'airtable': {'enabled': True, 'kind': 'airtable', 'config': 'config/airtable.yaml'}},
                   'raw_directory': 'raw', 'timeout_seconds': 1, 'max_jobs': 1, 'refresh_hours': 24}
            old = {'company_name': 'Existing', 'title': 'Senior Engineer', 'source_url': 'https://www.linkedin.com/jobs/view/1'}
            fresh = {'company_name': 'New', 'title': 'Software Engineer', 'source_url': 'https://www.linkedin.com/jobs/view/2'}
            html = '<div class="show-more-less-html__markup"><p>We manufacture solar panels.</p><p>Minimum 5 years of relevant experience required.</p></div>'
            with closing(Archive(root / 'archive.db')) as archive:
                archive.ingest([old], 'airtable', '2020-01-01T00:00:00+00:00')
                with patch('jobhunter.collection.ROOT', root), patch('jobhunter.descriptions.ROOT', root), patch('jobhunter.collection.airtable_rows', return_value=[fresh]), patch('jobhunter.descriptions.fetch', return_value=html) as fetch:
                    bounded = collect(archive, cfg, 'airtable', limit=1, force=True)
                self.assertEqual(bounded['descriptions']['saved'], 1)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(archive.search(query='New')['items'][0]['category'], 'Da classificare')
                with patch('jobhunter.sweep.ROOT', root), patch('jobhunter.descriptions.ROOT', root), patch('jobhunter.sweep.airtable_rows', return_value=[old, fresh]), patch('jobhunter.descriptions.fetch', side_effect=AssertionError('collect-all non recupera descrizioni')):
                    complete = sweep(archive, cfg)
                report = json.loads(Path(complete['report']).read_text(encoding='utf-8'))
                self.assertNotIn('description_followup', report)
                self.assertNotIn('analytics', report)
                self.assertNotIn('queue', report)
                self.assertEqual(report['archive'], {'companies': 2, 'opportunities': 2})
                self.assertEqual(report['description_coverage']['sources'][0]['missing'], 1)


if __name__ == '__main__':
    unittest.main()
