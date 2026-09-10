"""I filtri della ricerca aziende devono incrociarsi sullo stesso annuncio, non sull'azienda."""

from pathlib import Path
import tempfile
import unittest

from jobhunter.workspace import Archive


class SearchFilterTests(unittest.TestCase):
    """Tier e località insieme non devono descrivere due annunci diversi."""

    def build(self, directory):
        """Un'azienda di un settore preferito, compatibile a Londra e scartata a Milano."""
        archive = Archive(Path(directory) / 'test.db')
        archive.ingest([
            {'company_name': 'Acme', 'title': 'Data Scientist', 'source_url': 'https://example.org/1',
             'location': 'London, UK'},
            {'company_name': 'Acme', 'title': 'Marketing Specialist', 'source_url': 'https://example.org/2',
             'location': 'Milano, IT'}], 'test')
        cid = archive.search(query='Acme')['items'][0]['id']
        archive.categorize(cid, 'Energia', 'Fixture: settore preferito')
        return archive, cid

    def test_tier_and_location_must_meet_on_one_advert(self):
        """Tier A per un ruolo a Londra non rende l'azienda un risultato per «Milano»."""
        with tempfile.TemporaryDirectory() as directory:
            archive, cid = self.build(directory)
            try:
                self.assertEqual(archive.search(tier='A')['total'], 1)
                self.assertEqual([i['id'] for i in archive.search(tier='A', location='London')['items']], [cid])
                # L'annuncio di Milano c'è, ma è scartato: non è lui a produrre il Tier A.
                self.assertEqual(archive.search(tier='A', location='Milano')['total'], 0)
                # Senza tier, il filtro sulla località continua a guardare tutti gli annunci.
                self.assertEqual(archive.search(location='Milano')['total'], 1)
            finally:
                archive.close()

    def test_a_company_qualifying_in_the_filtered_place_is_still_found(self):
        """Se è proprio l'annuncio compatibile a stare a Milano, l'azienda esce."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / 'test.db')
            try:
                archive.ingest([{'company_name': 'Beta', 'title': 'Data Scientist',
                                 'source_url': 'https://example.org/3', 'location': 'Milano, IT'}], 'test')
                cid = archive.search(query='Beta')['items'][0]['id']
                archive.categorize(cid, 'Energia', 'Fixture: settore preferito')
                self.assertEqual([i['id'] for i in archive.search(tier='A', location='Milano')['items']], [cid])
            finally:
                archive.close()


if __name__ == '__main__':
    unittest.main()
