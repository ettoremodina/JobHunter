"""Cosa l'estrattore di fatti aziendali tiene e cosa butta, sulle frasi che l'hanno rotto davvero."""

import tempfile
import unittest
from pathlib import Path

from jobhunter.evaluation.company_evidence import job_facts
from jobhunter.workspace import Archive


class CompanyEvidenceTests(unittest.TestCase):
    """Una prova sul settore vale solo se il datore di lavoro sta parlando di sé."""

    def setUp(self):
        """Open an empty archive shared by every extraction case."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = Archive(Path(self.directory.name) / 'archive.db')
        self.addCleanup(lambda: self.archive.close())

    def facts(self, name, *descriptions):
        """Save one company with its roles and return the extracted sentences."""
        self.archive.ingest([{'company_name': name, 'title': f'Role {index}', 'description': text,
                              'source_url': f'https://example.org/{name}/{index}'}
                             for index, text in enumerate(descriptions)], 'test')
        cid = self.archive.db.execute('SELECT id FROM companies WHERE name=?', (name,)).fetchone()[0]
        return [fact['text'] for fact in job_facts(self.archive, cid, name)]

    def test_plural_business_noun_keeps_the_sentence(self):
        """«private markets» costava l'intera frase perché il pattern cercava «market» singolare."""
        sentence = 'Odin is building the investment infrastructure for private markets.'
        self.assertEqual(self.facts('Odin', sentence), [sentence])

    def test_identity_with_a_business_noun_is_kept(self):
        """«X è un produttore di Y» resta il caso base dell'asse azienda."""
        sentence = 'Acme is a manufacturer of industrial pumps.'
        self.assertEqual(self.facts('Acme', sentence), [sentence])

    def test_role_and_culture_sentences_are_dropped(self):
        """Il ruolo offerto e la cultura aziendale non dicono il settore del datore di lavoro."""
        self.assertEqual(self.facts('Acme', 'We are looking for a data scientist for our platform team. '
                                            'We are committed to equal opportunity. '
                                            'We are excited about the future.'), [])

    def test_the_same_sentence_in_two_roles_counts_once(self):
        """Una frase ripetuta in due annunci è una prova sola, non due."""
        sentence = 'Acme is a leading provider of grid software.'
        self.assertEqual(self.facts('Acme', sentence, sentence), [sentence])


if __name__ == '__main__':
    unittest.main()
