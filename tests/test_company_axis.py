"""Asse azienda e asse ruolo: le regole che restano dopo la rimozione del modello locale."""

from pathlib import Path
import tempfile
import unittest

from jobhunter.selection import evaluate
from jobhunter.workspace import Archive, now


class CompanyAxisTests(unittest.TestCase):
    """Il giudice a regex sui titoli, e chi può riscrivere una categoria già assegnata."""

    def test_role_boundaries(self):
        """Il regex tiene sviluppo e ML, esclude i ruoli senior, e non guarda l'azienda."""
        for title in ("Software Developer", "Machine Learning Engineer", "Mechanical Simulation Engineer"):
            self.assertEqual(evaluate({"title": title})["status"], "potential")
        for title in ("Senior Data Scientist", "Marketing Specialist", "Engineering Manager", "Mechanical Engineer"):
            self.assertEqual(evaluate({"title": title})["status"], "excluded")
        self.assertEqual(evaluate({"title": "Data Scientist", "description": "Work with senior managers"})["status"], "potential")

    def test_rules_never_overwrite_a_model_category(self):
        """Difetto 20: una passata a regole cancellava una categoria pagata al modello remoto."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "test.db")
            try:
                archive.ingest([{"company_name": "Paid", "title": "Engineer", "source_url": "https://example.org/1",
                                 "company_description": "Solar power installer"}], "test")
                cid = archive.search(query="Paid")["items"][0]["id"]
                with archive.db:
                    archive.db.execute("INSERT OR REPLACE INTO categories VALUES(?,?,?,?,?)",
                                       (cid, "Software e tecnologia", "remote", "Scheda pagata", now()))
                archive.categorize()
                saved = archive.category(cid)
                self.assertEqual(saved["category"], "Software e tecnologia")
                self.assertEqual(saved["category_method"], "remote")
            finally:
                archive.close()

    def test_a_chat_category_survives_every_automatic_pass(self):
        """Una tua decisione da chat non viene toccata da nessun passaggio automatico."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "test.db")
            try:
                archive.ingest([{"company_name": "Mine", "title": "Engineer", "source_url": "https://example.org/2",
                                 "company_description": "Solar power installer"}], "test")
                cid = archive.search(query="Mine")["items"][0]["id"]
                archive.categorize(cid, "Industria e materiali", "Correzione mia")
                archive.categorize()
                self.assertEqual(archive.category(cid)["category"], "Industria e materiali")
                self.assertEqual(archive.category(cid)["category_method"], "chat")
            finally:
                archive.close()


if __name__ == "__main__":
    unittest.main()
