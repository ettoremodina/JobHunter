"""Explicit site blocks must not be mistaken for parser drift."""

import unittest
from unittest.mock import patch
from jobhunter.collection import access_block, fetch


class AccessTests(unittest.TestCase):
    """Exercise saved-page classification without contacting blocked sources."""

    def test_unicode_url_is_encoded_only_for_transport(self):
        """Observed accented listing URLs must reach HTTP without double-encoding existing escapes."""
        with patch('jobhunter.collection.urllib.request.urlopen') as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = b'<html>job</html>'
            fetch('https://example.org/job/ingénieur%20énergie?q=modélisation&lang=fr', 1)
        address = open_url.call_args.args[0].full_url
        self.assertTrue(address.isascii())
        self.assertIn('ing%C3%A9nieur%20%C3%A9nergie', address)
        self.assertIn('&lang=fr', address)
        self.assertNotIn('%2520', address)

    def test_explicit_blocks(self):
        """Recognize Cloudflare denial while allowing normal unstructured pages."""
        self.assertEqual(access_block('<html>Error 1015: You are being rate limited</html>'), 'rate_limited')
        self.assertEqual(access_block('<title>Access denied | example.com</title>'), 'access_denied')
        self.assertIsNone(access_block('<h1>Software developer</h1><p>No JSON-LD here.</p>'))
