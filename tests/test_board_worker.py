"""Verify JobSpy page checkpoints and duplicate handling without network access."""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from jobhunter.acquisition.board_worker import main


class BoardWorkerTests(unittest.TestCase):
    """Repeated URLs must not inflate saved rows or prevent a duplicate-page stop."""

    def test_duplicates_within_and_between_pages(self):
        """Keep the first row per URL and checkpoint a page containing only known URLs."""
        first = {'job_url': 'https://example.org/jobs/1', 'title': 'Engineer'}
        second = {'job_url': 'https://example.org/jobs/2', 'title': 'Scientist'}
        batches = [[first, first, second], [second, first, first]]
        scrape = Mock(side_effect=[SimpleNamespace(to_json=Mock(return_value=json.dumps(batch)))
                                   for batch in batches])
        with tempfile.TemporaryDirectory() as directory:
            spec, output = Path(directory) / 'spec.json', Path(directory) / 'results.json'
            spec.write_text(json.dumps({'max_pages': 3, 'results_wanted': 3, 'pause': 0}), encoding='utf-8')
            with patch.dict(sys.modules, {'jobspy': SimpleNamespace(scrape_jobs=scrape)}), \
                    patch.object(sys, 'argv', ['board_worker', str(spec), str(output)]):
                main()
            report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(report['rows'], [first, second])
        self.assertEqual(report['pages'], 2)
        self.assertEqual(report['stop'], 'no_new_urls')
        self.assertEqual([call.kwargs['offset'] for call in scrape.call_args_list], [0, 3])


if __name__ == '__main__':
    unittest.main()
