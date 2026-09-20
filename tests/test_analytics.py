"""Metrics count roles, preserve missing facts and deduplicate geographic membership."""
import tempfile
import unittest
from pathlib import Path
from jobhunter.workspace import Archive
from jobhunter.exploration.analytics import summary


class AnalyticsTests(unittest.TestCase):
    """Check aggregation on a disposable archive with overlapping locations."""

    def test_scope_geography_and_health(self):
        """Multiple locations cannot duplicate a role within a country or imply remote geography."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / 'test.db')
            archive.ingest([
                {'company_name': 'Example', 'title': 'Software Developer', 'source_url': 'https://example.org/1', 'locations': ['Milan, IT', 'Rome, Italy', 'Paris, FR'], 'description': 'Build software'},
                {'company_name': 'Example', 'title': 'Senior Developer', 'source_url': 'https://example.org/2', 'locations': ['Remote']},
                {'company_name': 'Other', 'title': 'Software Developer', 'source_url': 'https://example.org/3', 'locations': ['San Jose, CA']},
            ], 'test')
            result = summary(archive)
            self.assertEqual(result['total'], 3)
            self.assertEqual(result['health']['with_description'], 1)
            # Una città mappata porta con sé il suo paese: «San Jose, CA» è negli Stati Uniti.
            # «Remote» invece non è un luogo e resta non determinato.
            self.assertEqual(result['health']['country_known'], 2)
            self.assertEqual({r['label']: r['count'] for r in result['countries']}, {'Italia': 1, 'Francia': 1, 'Stati Uniti': 1, 'Non determinato': 1})
            self.assertEqual(summary(archive, 'potential')['total'], 2)
            archive.close()
