"""Parallel HTTP work must preserve database writes and stop queued work on source blocks."""

from contextlib import closing
import json
from pathlib import Path
import tempfile
import time
from threading import Barrier, Lock
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from jobhunter.acquisition.descriptions import recover
from jobhunter.workspace import Archive
from jobhunter.operations import cancellation


class DescriptionConcurrencyTests(unittest.TestCase):
    """Exercise real worker threads and a real temporary SQLite archive."""

    def test_cancel_drains_inflight_results_and_skips_queued_requests(self):
        """Stop queued work while saving descriptions already fetched by active workers."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config').mkdir()
            (root/'config/descriptions.json').write_text(json.dumps({
                'default_limit': 12, 'max_limit': 50, 'workers': 2, 'max_workers': 8,
                'allowed_hosts': ['www.linkedin.com'], 'timeout_seconds': 1,
                'request_delay_seconds': 0, 'output_directory': 'details'}))
            with closing(Archive(root/'archive.db')) as archive:
                archive.ingest([{'company_name': 'Example', 'title': 'Data Scientist',
                                 'source_url': f'https://www.linkedin.com/jobs/view/{i}'} for i in range(12)], 'jobspy')
                checks = 0
                def requested():
                    """Request a stop after the first result has been saved."""
                    nonlocal checks
                    checks += 1
                    return checks > 1
                def fetched(*args):
                    """Keep workers briefly occupied so queued requests remain cancellable."""
                    time.sleep(.03)
                    return '<div class="show-more-less-html__markup">Build data science models.</div>'
                cancellation.bind(requested)
                try:
                    with patch('jobhunter.acquisition.descriptions.ROOT', root), patch('jobhunter.acquisition.descriptions.fetch', side_effect=fetched) as fetch:
                        with self.assertRaises(cancellation.Cancelled):
                            recover(archive, all_missing=True)
                    self.assertLess(fetch.call_count, 12)
                    saved = archive.db.execute("SELECT count(*) FROM opportunities WHERE json_extract(data,'$.description') != ''").fetchone()[0]
                    self.assertEqual(saved, fetch.call_count)
                    report = json.loads(next((root/'details').glob('*/report.json')).read_text())
                    self.assertEqual(report['status'], 'interrupted')
                finally:
                    cancellation.bind()

    def test_parallel_saves_and_blocked_queue(self):
        """Four requests overlap, all writes persist, and a 429 prevents queued requests."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/descriptions.json').write_text(json.dumps({
                'default_limit': 8, 'max_limit': 50, 'workers': 1, 'max_workers': 8,
                'allowed_hosts': ['www.linkedin.com'], 'timeout_seconds': 1,
                'request_delay_seconds': 0, 'output_directory': 'details'}))
            with closing(Archive(root / 'archive.db')) as archive:
                archive.ingest([{'company_name': 'Example', 'title': 'Software Engineer',
                                 'source_url': f'https://www.linkedin.com/jobs/view/{i}'} for i in range(8)], 'jobspy')
                barrier, lock = Barrier(4, timeout=5), Lock()
                active, peak = 0, 0

                def fetch_parallel(address, timeout):
                    """Require four concurrent requests; a serial implementation cannot pass."""
                    nonlocal active, peak
                    with lock:
                        active += 1
                        peak = max(peak, active)
                    barrier.wait()
                    with lock:
                        active -= 1
                    return '<div class="show-more-less-html__markup">Minimum 5 years of experience required.</div>'

                with patch('jobhunter.acquisition.descriptions.ROOT', root), patch('jobhunter.acquisition.descriptions.fetch', side_effect=fetch_parallel):
                    result = recover(archive, limit=8, workers=4)
                self.assertEqual(result['saved'], 8)
                self.assertEqual(peak, 4)
                self.assertEqual(archive.db.execute("SELECT count(*) FROM opportunities WHERE json_extract(data,'$.description') != ''").fetchone()[0], 8)
                with archive.db:
                    archive.db.execute("UPDATE opportunities SET data=json_set(data,'$.description','')")
                with patch('jobhunter.acquisition.descriptions.ROOT', root), patch('jobhunter.acquisition.descriptions.fetch', side_effect=HTTPError('https://www.linkedin.com', 429, 'Rate limited', {}, None)) as fetch:
                    result = recover(archive, limit=8, workers=4)
                self.assertLessEqual(fetch.call_count, 4)
                self.assertEqual(result['blocked_hosts'], ['linkedin.com'])
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['saved'], 0)
                self.assertEqual(result['remaining_missing'], 8)


if __name__ == '__main__':
    unittest.main()
