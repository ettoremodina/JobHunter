"""Livello di revisione: utente e agente sopra la cascata regex -> Jev, senza cancellarla."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobhunter.evaluation import remote_llm, review, system_one, tier
from jobhunter.evaluation.selection import verdicts
from jobhunter.exploration import conversations
from jobhunter.workspace import Archive


class ReviewLayerTests(unittest.TestCase):
    """Priority, staleness and session contract of the user and agent judges."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.memory = self.root / "selection"
        self.sessions = self.root / "sessions"
        self.rules_path = self.memory / "regole.md"
        self.patch = patch.object(review, "RULES_PATH", self.rules_path)
        self.patch.start()
        self.archive = Archive(self.root / "archive.sqlite3")

    def tearDown(self):
        self.archive.close()
        self.patch.stop()
        self.temporary.cleanup()

    def add_job(self, company, title, description, decision):
        """Insert one role with a current Jev result and return (company_id, opportunity_id)."""
        self.archive.ingest([{"company_name": company, "title": title, "description": description,
                              "source_url": "https://example.org/" + company.casefold().replace(" ", "-")}], "test")
        row = self.archive.db.execute("SELECT o.id,o.company_id FROM opportunities o JOIN companies c "
                                      "ON c.id=o.company_id WHERE c.name=?", (company,)).fetchone()
        cfg = system_one.config()
        state = system_one.job_state(self.archive, row["id"], cfg)[0]
        fingerprint = remote_llm.digest({"state": state, "settings": system_one.signature(cfg, "selection")})
        result = {"decision": decision, "rationale": "Caso di test", "evidence": ["Develop models"],
                  "missing_information": []}
        with self.archive.db:
            self.archive.db.execute("INSERT OR REPLACE INTO enrichments VALUES(?,?,?,?,?,?,?)",
                                    ("jev:selection", row["id"], "source", fingerprint,
                                     json.dumps({"result": result}), cfg["model"], "2026-09-22"))
        self.archive.categorize(row["company_id"], "Energia", "Test category")
        return row["company_id"], row["id"]

    def role(self, cid, oid):
        """Final role verdict as every reader of the archive sees it."""
        return verdicts(self.archive, cid)[cid]["ruoli"][oid]

    def test_user_beats_agent_beats_jev_and_the_chain_keeps_all_of_them(self):
        """Il verdetto finale cambia; la catena mostra ancora cosa aveva detto ognuno."""
        cid, oid = self.add_job("Energy Co", "Systems Engineer", "Develop grid models", "review")
        self.assertEqual((self.role(cid, oid)["verdetto"], verdicts(self.archive, cid)[cid]["tier"]),
                         (tier.UNKNOWN, "B-attesa"))
        review.add_rule("R1", "I modelli di rete elettrica sono compatibili.", "test", self.rules_path)
        review.save_agent_verdict(self.archive, oid, "keep", ["R1"], "Modelli di rete: R1", "test")
        self.assertEqual(self.role(cid, oid)["giudice"], "agente")
        self.assertEqual(verdicts(self.archive, cid)[cid]["tier"], "A")
        event = self.archive.feedback(cid, "discarded", "Non mi convince", opportunity_id=oid)
        role = self.role(cid, oid)
        self.assertEqual((role["verdetto"], role["giudice"]), (tier.DROP, "utente"))
        self.assertEqual([j["giudice"] for j in role["catena"]], ["regex", "jev", "agente", "utente"])
        # Annullare la tua decisione restituisce la parola all'agente, non la cancella.
        self.archive.undo(event["event_id"])
        self.assertEqual(self.role(cid, oid)["giudice"], "agente")
        # Uno stato informativo non e' un verdetto.
        self.archive.feedback(cid, "contacted", opportunity_id=oid)
        self.assertEqual(self.role(cid, oid)["giudice"], "agente")

    def test_an_agent_verdict_expires_with_its_rule_or_its_text(self):
        """Cambiare la regola citata o il testo dell'annuncio rimette il ruolo in coda."""
        cid, oid = self.add_job("Energy Co", "Systems Engineer", "Develop grid models", "review")
        review.add_rule("R1", "I modelli di rete elettrica sono compatibili.", "test", self.rules_path)
        review.save_agent_verdict(self.archive, oid, "keep", ["R1"], "R1", "test")
        # Correggere titolo o origine non cambia che cosa chiede la regola.
        self.rules_path.write_text(self.rules_path.read_text(encoding="utf-8").replace("Origine: test", "Origine: altro"),
                                   encoding="utf-8")
        self.assertEqual(self.role(cid, oid)["giudice"], "agente")
        self.rules_path.write_text(self.rules_path.read_text(encoding="utf-8").replace("compatibili", "ammessi"),
                                   encoding="utf-8")
        self.assertEqual(self.role(cid, oid)["verdetto"], tier.UNKNOWN)
        review.add_rule("R2", "Nuova regola.", "test", self.rules_path)
        review.save_agent_verdict(self.archive, oid, "exclude", ["R2"], "R2", "test")
        with self.archive.db:
            self.archive.db.execute("UPDATE opportunities SET content_hash='changed' WHERE id=?", (oid,))
        self.assertIsNone(review.agent_judgements(self.archive).get(oid))
        with self.assertRaises(ValueError):
            review.save_agent_verdict(self.archive, oid, "keep", ["R9"], "Regola inesistente", "test")

    def test_rules_session_judges_undecided_and_compatible_roles_only(self):
        """La sessione `regole` scrive regole e verdetti in un evento, e non tocca le tue decisioni."""
        with self.assertRaises(ValueError):
            conversations.start(self.archive, "regole", session_root=self.sessions)
        _, undecided = self.add_job("Energy Co", "Systems Engineer", "Develop grid models", "review")
        _, compatible = self.add_job("Grid Co", "Data Scientist", "Develop forecasts", "keep")
        mine_cid, mine = self.add_job("Mine Co", "Systems Engineer", "Develop software", "review")
        self.add_job("Drop Co", "Sales Manager", "Sell products", "exclude")
        self.archive.feedback(mine_cid, "saved", opportunity_id=mine)
        review.add_rule("R1", "Regola di partenza.", "test", self.rules_path)

        session = conversations.start(self.archive, "regole", batch_size=5, session_root=self.sessions)
        self.assertEqual({item["id"] for item in session["items"]}, {undecided, compatible})
        self.assertEqual({item["verdetto"] for item in session["items"]}, {tier.UNKNOWN, tier.KEEP})
        event = {"event_id": "turno-1",
                 "rules": [{"id": "R2", "text": "Le previsioni di carico sono compatibili."}],
                 "verdicts": [{"opportunity_id": undecided, "decision": "keep", "rule_ids": ["R2"],
                               "rationale": "Previsioni di carico"}]}
        first = conversations.record(session["id"], event, self.sessions, self.memory, self.archive)
        conversations.record(session["id"], event, self.sessions, self.memory, self.archive)
        self.assertEqual((first["reviewed"], first["remaining"]), (1, 1))
        self.assertEqual(self.rules_path.read_text(encoding="utf-8").count("<!-- rule:R2 -->"), 1)
        self.assertEqual(review.agent_judgements(self.archive)[undecided]["verdetto"], tier.KEEP)
        # Una regola esistente non si riscrive dalla chat, e un verdetto deve citarne una attiva.
        for bad in ({"event_id": "x", "rules": [{"id": "R2", "text": "Testo diverso"}]},
                    {"event_id": "y", "verdicts": [{"opportunity_id": compatible, "decision": "exclude",
                                                    "rule_ids": ["R7"], "rationale": "?"}]}):
            with self.assertRaises(ValueError):
                conversations.record(session["id"], bad, self.sessions, self.memory, self.archive)
        # Gli indecisi non ripropongono cio' che l'agente ha gia' deciso.
        indecisi = conversations.start(self.archive, "indecisi", session_root=self.sessions)
        self.assertEqual(indecisi["population"], 0)
        with self.assertRaises(ValueError):
            conversations.record(indecisi["id"], {"event_id": "z", "verdicts": [
                {"opportunity_id": undecided, "decision": "keep", "rule_ids": ["R1"], "rationale": "x"}]},
                self.sessions, self.memory, self.archive)


if __name__ == "__main__":
    unittest.main()
