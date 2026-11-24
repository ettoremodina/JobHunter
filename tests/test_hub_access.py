"""The dashboard answers loopback and, when HUB_ORIGIN names it, the hub's private address; everything else is refused."""

import json
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from jobhunter.exploration.dashboard import create_server, hub_origin
from jobhunter.workspace import Archive, settings

HUB = "https://prova.example:8898"


class HubAccessTests(unittest.TestCase):
    """Host and Origin rules, health, shared kit and manifest, on an ephemeral port."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        database = Path(self.directory.name) / "access.sqlite3"
        Archive(database).close()
        with patch.dict(os.environ, {"HUB_ORIGIN": HUB}):
            self.server = create_server(database, settings(), 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def call(self, method, path, headers=None, body=None):
        """One request with explicit headers; returns status and body."""
        connection = HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_pages_health_kit_and_manifest_are_served(self):
        """Page, health, kit and manifest answer 200 for the hub's host and for loopback."""
        for host in (f"127.0.0.1:{self.port}", "prova.example:8898"):
            for path in ("/", "/api/health", "/kit/kit.css", "/kit/kit.js", "/manifest.webmanifest", "/icon-192.png"):
                self.assertEqual(self.call("GET", path, {"Host": host})[0], 200, (host, path))

    def test_foreign_host_is_refused(self):
        """A Host that is neither loopback nor the hub is a 403."""
        self.assertEqual(self.call("GET", "/", {"Host": "estraneo.example"})[0], 403)

    def test_post_origin_rules_keep_the_token(self):
        """The hub's origin passes with a valid token; a foreign origin or a missing token does not."""
        status, boot = self.call("GET", "/api/bootstrap", {"Host": "prova.example:8898"})
        token = json.loads(boot)["token"]
        body = json.dumps({"event_id": 0})

        def post(origin, sent_token):
            headers = {"Host": "prova.example:8898", "Origin": origin, "Content-Type": "application/json", "X-JobHunter-Token": sent_token}
            return self.call("POST", "/api/undo", headers, body)[0]
        self.assertNotEqual(post(HUB, token), 403)
        self.assertEqual(post("https://estraneo.example", token), 403)
        self.assertEqual(post(HUB, "sbagliato"), 403)

    def test_hub_origin_must_be_a_bare_https_origin(self):
        """A malformed HUB_ORIGIN stops the start instead of widening access."""
        for value in ("http://prova.example:8898", "https://prova.example:8898/percorso", "https://utente@prova.example"):
            with patch.dict(os.environ, {"HUB_ORIGIN": value}), self.assertRaises(ValueError):
                hub_origin()
        with patch.dict(os.environ, {"HUB_ORIGIN": ""}):
            self.assertIsNone(hub_origin())


if __name__ == "__main__":
    unittest.main()
