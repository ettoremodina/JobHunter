"""Asse azienda e asse ruolo: le regole che restano dopo la rimozione del modello locale."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from jobhunter.evaluation.selection import evaluate
from jobhunter.evaluation.company_categories import replace
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
        """Una passata a regole non cancella una categoria assegnata da Jev."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "test.db")
            try:
                archive.ingest([{"company_name": "Paid", "title": "Engineer", "source_url": "https://example.org/1",
                                 "company_description": "Solar power installer"}], "test")
                cid = archive.search(query="Paid")["items"][0]["id"]
                with archive.db:
                    archive.db.execute("""INSERT OR REPLACE INTO categories
                        (company_id,category,rank,confidence,method,reason,updated_at)
                        VALUES(?,?,?,?,?,?,?)""",
                                       (cid, "Software e tecnologia", 1, 0.9, "jev", "Giudizio Jev", now()))
                archive.categorize()
                saved = archive.category(cid)
                self.assertEqual(saved["category"], "Software e tecnologia")
                self.assertEqual(saved["category_method"], "jev")
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

    def test_two_categories_are_visible_and_each_search_filter_matches(self):
        """A company belongs to both accepted sectors without duplicating the company row."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "test.db")
            try:
                archive.ingest([{"company_name": "Hybrid", "title": "Data Scientist",
                                 "source_url": "https://example.org/hybrid"}], "test")
                cid = archive.search(query="Hybrid")["items"][0]["id"]
                with archive.db:
                    replace(archive, cid, ["Energia", "Software e tecnologia"], "jev", "Due settori",
                            {"Energia": 0.45, "Software e tecnologia": 0.40})
                self.assertEqual(archive.category(cid)["categories"],
                                 ["Energia", "Software e tecnologia"])
                for label in ("Energia", "Software e tecnologia"):
                    result = archive.search(category=label)
                    self.assertEqual((result["total"], result["items"][0]["id"]), (1, cid))
            finally:
                archive.close()

    def test_single_category_schema_is_migrated_without_losing_the_assignment(self):
        """Opening an existing archive turns its scalar category into rank one."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            archive = Archive(path)
            archive.ingest([{"company_name": "Legacy", "title": "Engineer",
                             "source_url": "https://example.org/legacy"}], "test")
            cid = archive.search(query="Legacy")["items"][0]["id"]
            archive.close()
            with closing(sqlite3.connect(path)) as db:
                db.execute("ALTER TABLE categories RENAME TO categories_multi")
                db.execute("""CREATE TABLE categories(company_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL, method TEXT NOT NULL, reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL)""")
                db.execute("INSERT INTO categories VALUES(?,?,?,?,?)",
                           (cid, "Energia", "rules", "Legacy", "2026-01-01"))
                db.execute("DROP TABLE categories_multi")
                db.commit()
            migrated = Archive(path)
            try:
                row = migrated.db.execute(
                    "SELECT category,rank,confidence,method FROM categories WHERE company_id=?", (cid,)).fetchone()
                self.assertEqual(tuple(row), ("Energia", 1, None, "rules"))
            finally:
                migrated.close()


if __name__ == "__main__":
    unittest.main()
