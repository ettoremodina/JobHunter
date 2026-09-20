"""Explicit site blocks must not be mistaken for parser drift."""

import unittest
from unittest.mock import patch
from jobhunter.acquisition.collection import access_block, fetch


class AccessTests(unittest.TestCase):
    """Exercise saved-page classification without contacting blocked sources."""

    def test_unicode_url_is_encoded_only_for_transport(self):
        """Observed accented listing URLs must reach HTTP without double-encoding existing escapes."""
        with patch('jobhunter.acquisition.collection.session') as pool:
            page = pool.return_value.get.return_value.__enter__.return_value
            page.status_code = 200
            page.raw.read.return_value = b'<html>job</html>'
            fetch('https://example.org/job/ingénieur%20énergie?q=modélisation&lang=fr', 1)
        address = pool.return_value.get.call_args.args[0]
        self.assertTrue(address.isascii())
        self.assertIn('ing%C3%A9nieur%20%C3%A9nergie', address)
        self.assertIn('&lang=fr', address)
        self.assertNotIn('%2520', address)

    def test_http_status_keeps_the_shape_descriptions_classifies_on(self):
        """descriptions.py distingue 404 e blocchi leggendo tipo e messaggio: non devono cambiare."""
        from urllib.error import HTTPError
        with patch('jobhunter.acquisition.collection.session') as pool:
            page = pool.return_value.get.return_value.__enter__.return_value
            page.status_code, page.reason, page.headers = 404, 'Not Found', {}
            with self.assertRaises(HTTPError) as caught:
                fetch('https://example.org/gone', 1)
        self.assertEqual(caught.exception.code, 404)
        self.assertIn('HTTP Error 404', str(caught.exception))

    def test_explicit_blocks(self):
        """Recognize Cloudflare denial while allowing normal unstructured pages."""
        self.assertEqual(access_block('<html>Error 1015: You are being rate limited</html>'), 'rate_limited')
        self.assertEqual(access_block('<title>Access denied | example.com</title>'), 'access_denied')
        self.assertIsNone(access_block('<h1>Software developer</h1><p>No JSON-LD here.</p>'))
