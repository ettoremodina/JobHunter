"""Bound expensive saved-result decoding to the companies actually requested."""

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from jobhunter.selection import verdicts
from jobhunter.workspace import Archive


class SearchPerformanceTests(unittest.TestCase):
    """Check work performed rather than unstable wall-clock thresholds."""

    def test_page_decodes_only_its_saved_remote_results(self):
        """Pagination must not deserialize remote reasoning for off-page companies."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory) / 'a.db')) as archive:
            archive.ingest([{'company_name': f'Company {n}', 'title': 'Research fellow',
                             'source_url': f'https://example.org/{n}'} for n in range(40)], 'test')
            payload = json.dumps({'result': {'decision': 'keep', 'rationale': 'REMOTE_SENTINEL'}})
            archive.db.execute("""INSERT INTO enrichments SELECT 'remote:selection',id,content_hash,
                'fixture',?,'fixture',first_seen FROM opportunities""", (payload,))
            archive.db.commit()
            archive.refresh_search_eligibility()
            with patch('jobhunter.selection.json.loads', wraps=json.loads) as decode:
                result = archive.search(limit=1)
            decoded = [call for call in decode.call_args_list if 'REMOTE_SENTINEL' in str(call.args[0])]
            self.assertEqual(len(decoded), 1, 'Off-page remote verdicts must stay in SQLite')
            cid = result['items'][0]['id']
            complete = verdicts(archive)
            self.assertEqual(verdicts(archive, cid), {cid: complete[cid]})
            self.assertEqual(verdicts(archive, [cid]), {cid: complete[cid]})
            self.assertEqual(verdicts(archive, []), {})
            with patch('jobhunter.selection.verdicts', wraps=verdicts) as assess:
                archive.search(tier=result['items'][0]['tier'], limit=1)
            self.assertEqual(assess.call_count, 1, 'Tier pagination must reuse its full assessment')
            archive.db.execute("UPDATE enrichments SET data=?", (payload.replace('keep', 'exclude'),))
            archive.db.commit()
            self.assertNotEqual(verdicts(archive, cid), {cid: complete[cid]})


if __name__ == '__main__':
    unittest.main()
