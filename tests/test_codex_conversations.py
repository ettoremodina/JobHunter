"""Persistent, bounded Codex conversations over undecided and selected records."""

import json
from pathlib import Path
import tempfile
import unittest

from jobhunter.evaluation import remote_llm
from jobhunter.evaluation import system_one
from jobhunter.exploration import conversations
from jobhunter.workspace import Archive


class CodexConversationTests(unittest.TestCase):
    """Exercise session scope, compact batches and explicit Markdown memory."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sessions = self.root / "sessions"
        self.memory = self.root / "selection"
        self.archive = Archive(self.root / "archive.sqlite3")

    def tearDown(self):
        self.archive.close()
        self.temporary.cleanup()

    def add_job(self, company, title, description):
        """Insert one test job and return its company and opportunity IDs."""
        self.archive.ingest([{
            "company_name": company,
            "title": title,
            "description": description,
            "source_url": "https://example.org/" + company.casefold().replace(" ", "-"),
            "locations": ["Milano, Italia"],
        }], "test")
        row = self.archive.db.execute(
            "SELECT o.id,o.company_id FROM opportunities o JOIN companies c ON c.id=o.company_id WHERE c.name=?",
            (company,),
        ).fetchone()
        return row["company_id"], row["id"]

    def save_jev(self, oid, decision):
        """Save a current Jev result under the same cache contract as the real integration."""
        cfg = system_one.config()
        state = system_one.job_state(self.archive, oid, cfg)[0]
        fingerprint = remote_llm.digest({"state": state, "settings": system_one.signature(cfg, "selection")})
        result = {"decision": decision, "rationale": "Caso di test", "evidence": ["Develop models"], "missing_information": []}
        stored = {"result": result, "detail": {}, "usage": {}}
        with self.archive.db:
            self.archive.db.execute(
                "INSERT OR REPLACE INTO enrichments VALUES(?,?,?,?,?,?,?)",
                ("jev:selection", oid, "source", fingerprint, json.dumps(stored), cfg["model"], "2026-09-22T00:00:00+00:00"),
            )

    def test_undecided_session_excludes_missing_descriptions(self):
        """Only current Jev review results with usable duties enter the conversation."""
        _, review = self.add_job("Review Co", "Systems Engineer", "Develop models and software")
        _, decided = self.add_job("Keep Co", "Data Scientist", "Develop models")
        _, missing = self.add_job("Missing Co", "Systems Engineer", "")
        self.save_jev(review, "review")
        self.save_jev(decided, "keep")
        # Missing descriptions cannot produce a current Jev cache and must stay outside the session.

        session = conversations.start(self.archive, "indecisi", batch_size=4, session_root=self.sessions)

        self.assertEqual(session["population"], 1)
        self.assertEqual([item["id"] for item in session["items"]], [review])
        self.assertNotIn("description", session["items"][0])
        row = self.archive.db.execute("SELECT data FROM opportunities WHERE id=?", (review,)).fetchone()
        changed = json.loads(row["data"])
        changed["description"] = "A later archive version"
        with self.archive.db:
            self.archive.db.execute("UPDATE opportunities SET data=? WHERE id=?", (json.dumps(changed), review))
        expanded = conversations.expand(self.archive, session["id"], review, session_root=self.sessions)
        self.assertIn("Develop models", expanded["description"])
        self.assertNotIn("later archive", expanded["description"])
        with self.assertRaises(ValueError):
            conversations.expand(self.archive, session["id"], missing, session_root=self.sessions)

    def test_selection_session_uses_tiers_filters_and_compact_cards(self):
        """Tier A/B exploration returns summaries, not complete source descriptions."""
        cid, oid = self.add_job("Energy Co", "Data Scientist", "Develop climate models in Python")
        self.save_jev(oid, "keep")
        self.archive.categorize(cid, "Energia", "Test category")

        session = conversations.start(
            self.archive,
            "selezione",
            {"tiers": ["A"], "category": "Energia", "query": "Energy"},
            batch_size=3,
            session_root=self.sessions,
        )

        self.assertEqual(session["population"], 1)
        self.assertEqual(session["items"][0]["id"], cid)
        self.assertEqual(session["items"][0]["tier"], "A")
        self.assertNotIn("description", session["items"][0])
        self.assertEqual(session["items"][0]["roles"][0]["title"], "Data Scientist")

    def test_record_is_idempotent_and_promotes_only_explicit_memories(self):
        """Session notes and confirmed memory survive retries without duplicate entries."""
        _, oid = self.add_job("Review Co", "Systems Engineer", "Develop models and software")
        self.save_jev(oid, "review")
        session = conversations.start(self.archive, "indecisi", session_root=self.sessions)
        event = {
            "event_id": "answer-001",
            "reviewed_ids": [oid],
            "notes": [{"kind": "proposal", "text": "Valutare una domanda Jev sui sistemi software", "item_ids": [oid]}],
            "memories": [{"id": "pref-001", "kind": "preference", "text": "I sistemi interessano quando il lavoro è sviluppo software."}],
        }

        first = conversations.record(session["id"], event, session_root=self.sessions, memory_root=self.memory)
        second = conversations.record(session["id"], event, session_root=self.sessions, memory_root=self.memory)

        self.assertEqual(first["remaining"], 0)
        self.assertEqual(second["remaining"], 0)
        events = (self.sessions / session["id"] / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(events), 1)
        preferences = (self.memory / "preferences.md").read_text(encoding="utf-8")
        self.assertEqual(preferences.count("pref-001"), 1)


if __name__ == "__main__":
    unittest.main()
