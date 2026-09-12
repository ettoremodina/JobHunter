"""Le località canoniche: unificare le varianti senza inventare quelle che la mappa non conosce."""

import tempfile
import unittest
from pathlib import Path

from jobhunter import places
from jobhunter.workspace import Archive


class ResolveTests(unittest.TestCase):
    """Una stringa scritta a mano diventa paese e città, o resta dichiarata sconosciuta."""

    def test_variants_of_the_same_city_agree(self):
        """«Milano», «Milan» e «Milano, MI, IT» sono lo stesso posto."""
        for raw in ('Milano', 'Milan, Italy', 'Milano, LOM, IT', 'MILANO'):
            self.assertEqual(places.resolve(raw), ('Italia', 'Milano'), raw)

    def test_country_is_read_even_mid_sentence(self):
        """Le fonti ripetono la località: il paese va riconosciuto dove capita, non solo in fondo."""
        self.assertEqual(places.resolve('Foster City, CA Foster City California United States')[0], 'Stati Uniti')
        self.assertEqual(places.resolve('Warszawa, MZ, PL'), ('Polonia', 'Warszawa'))
        self.assertEqual(places.resolve('München, Bayern, Germany'), ('Germania', 'Munich'))

    def test_short_alias_needs_the_matching_country(self):
        """«MI» è Milano in Italia e Michigan negli Stati Uniti: nel dubbio nessuna città."""
        self.assertEqual(places.resolve('Detroit, MI, United States'), ('Stati Uniti', None))
        self.assertEqual(places.resolve('IT Manager'), (None, None))

    def test_unmapped_locations_are_reported_not_guessed(self):
        """Una città fuori mappa non diventa un'altra città: viene elencata come da mappare."""
        rows = places.of(['Kigali, Rwanda', 'Remote', 'Milano'])
        self.assertIn(('Italia', 'Milano'), [(row['country'], row['city']) for row in rows])
        self.assertEqual([row['raw'] for row in rows if row['to_map']], ['Kigali, Rwanda'])


class CacheTests(unittest.TestCase):
    """Il calcolo si salva per annuncio e si rifà solo quando cambia il contenuto o la mappa."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = Archive(Path(self.directory.name) / 'archive.db')
        self.addCleanup(lambda: self.archive.close())
        self.archive.ingest([
            {'company_name': 'Nord', 'title': 'Data Scientist', 'location': 'Milan, Italy', 'source_url': 'https://example.org/1'},
            {'company_name': 'Sud', 'title': 'Data Scientist', 'location': 'Napoli, Italia', 'source_url': 'https://example.org/2'},
            {'company_name': 'Fuori', 'title': 'Data Scientist', 'location': 'Kigali, Rwanda', 'source_url': 'https://example.org/3'},
        ], 'test')

    def test_search_by_canonical_city_ignores_the_spelling(self):
        """Chi filtra «Milano» trova l'annuncio scritto «Milan, Italy»."""
        result = self.archive.search(city='Milano')
        self.assertEqual([item['name'] for item in result['items']], ['Nord'])
        self.assertEqual(result['items'][0]['places'], ['Milano, Italia'])
        self.assertEqual(self.archive.search(country='Italia')['total'], 2)

    def test_second_read_reuses_the_saved_result(self):
        """Senza modifiche non si ricalcola nulla: il refresh dichiara zero annunci."""
        places.refresh(self.archive)
        self.assertEqual(places.refresh(self.archive), 0)
        self.archive.ingest([{'company_name': 'Nord', 'title': 'Data Scientist', 'location': 'Roma, Italia',
                              'source_url': 'https://example.org/4'}], 'test')
        self.assertEqual(places.refresh(self.archive), 1)

    def test_unmapped_report_counts_what_the_mapping_misses(self):
        """La segnalazione elenca le località da aggiungere alla mappa, con quante volte compaiono."""
        report = places.unmapped(self.archive)
        self.assertEqual([row['text'] for row in report['da_mappare']], ['Kigali, Rwanda'])
        self.assertEqual(report['annunci_con_citta'], 2)


if __name__ == '__main__':
    unittest.main()
