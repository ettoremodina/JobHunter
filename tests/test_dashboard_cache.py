"""The dashboard reuses heavy reads only while archive and rules are unchanged."""

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from jobhunter.exploration.dashboard import ReadCache
from jobhunter.workspace import Archive


class ReadCacheTests(unittest.TestCase):
    """A stale Pipeline or Metrics page is worse than a slow one."""

    def test_recomputes_after_any_write_or_rule_change(self):
        """Another connection's commit and a touched config file both invalidate the saved result."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            rules = root / "config" / "rules.json"
            rules.write_text("{}")
            database = root / "a.db"
            with closing(Archive(database)):
                pass
            with closing(ReadCache(database, root)) as cache:
                calls = []
                compute = lambda: calls.append(1) or len(calls)
                self.assertEqual(cache.get("k", compute), 1)
                self.assertEqual(cache.get("k", compute), 1)
                with closing(sqlite3.connect(database)) as other, other:
                    other.execute("INSERT INTO preferences(note,created_at) VALUES('x','now')")
                self.assertEqual(cache.get("k", compute), 2)
                stamp = rules.stat().st_mtime_ns + 1_000_000_000
                os.utime(rules, ns=(stamp, stamp))
                self.assertEqual(cache.get("k", compute), 3)
                self.assertEqual(cache.get("k", compute), 3)

    def test_run_lists_count_items_instead_of_sending_them(self):
        """Per-item outcomes can weigh megabytes: lists carry only how many there were."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory) / "a.db")) as archive:
            archive.run("description_recovery", "partial", {"items": [{"id": 1}, {"id": 2}], "saved": 1})
            run = archive.stats()["runs"][0]
            self.assertEqual(run["items_omitted"], 2)
            self.assertEqual(run["detail"], '{"saved":1}')


if __name__ == "__main__":
    unittest.main()
