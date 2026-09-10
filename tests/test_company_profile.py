"""Il recupero dei dati aziendali riempie i vuoti e lascia traccia di ogni strada provata."""

import json
from pathlib import Path
import tempfile
import unittest

from jobhunter.company_profile import ad_text, demo, kind, one, profile_page, save
from jobhunter.workspace import Archive

CARD = '''<html><body><table><tbody>
  <tr><td>Vertical</td><td>Clean Energy</td></tr>
  <tr><td>Website</td><td><a href="https://acme.example?ref=climatetechlist.com&amp;utm_source=x">acme.example</a></td></tr>
</tbody></table>
<h2> Company Info </h2><p>Acme manufactures solar inverters and battery storage systems for utilities.</p>
</body></html>'''


class CompanyProfileTests(unittest.TestCase):
    """Estrattori, regole di scrittura e traccia dei tentativi."""

    def archive(self, directory, rows):
        """Un archivio temporaneo con le righe date, e l'id della prima azienda."""
        archive = Archive(Path(directory) / 'test.db')
        archive.ingest(rows, 'test')
        return archive, archive.search(query=rows[0]['company_name'])['items'][0]['id']

    def test_extractors_and_address_kinds(self):
        """I controlli eseguibili del modulo valgono anche qui, senza rete."""
        demo()

    def test_profile_page_strips_tracking_from_the_outbound_link(self):
        """Il sito vero non deve portarsi dietro i parametri dell'aggregatore."""
        found = profile_page(CARD)
        self.assertEqual(found['website'], 'https://acme.example/')
        self.assertEqual(found['sectors'], 'Clean Energy')
        self.assertTrue(found['description'].startswith('Acme manufactures solar inverters'))

    def test_an_aggregator_card_replaces_the_saved_address_but_spares_the_rest(self):
        """La scheda non era un sito: quell'indirizzo va sostituito. Un settore già noto no."""
        with tempfile.TemporaryDirectory() as directory:
            archive, cid = self.archive(directory, [
                {'company_name': 'Acme', 'title': 'Engineer', 'source_url': 'https://example.org/1',
                 'website_url': 'https://www.climatetechlist.com/company/acme',
                 'sectors': 'Settore gia noto'}])
            try:
                found = dict(profile_page(CARD), id=cid, name='Acme', kind='scheda_aggregatore',
                             strategy='scheda_aggregatore', attempts=[],
                             url='https://www.climatetechlist.com/company/acme')
                written = save(archive, found)
                row = archive.db.execute(
                    'SELECT website,sectors,description,description_provenance FROM companies WHERE id=?',
                    (cid,)).fetchone()
                self.assertEqual(sorted(written), ['description', 'website'])
                self.assertEqual(row['website'], 'https://acme.example/')
                self.assertEqual(row['sectors'], 'Settore gia noto')
                self.assertTrue(row['description'].startswith('Acme manufactures'))
                provenance = json.loads(row['description_provenance'])
                self.assertEqual(provenance['source'], 'scheda_aggregatore')
                self.assertIsNone(provenance['rewritten_by'])
            finally:
                archive.close()

    def test_a_recruiting_host_leaves_a_trace_and_falls_back_to_the_ad(self):
        """Un gestionale di recruiting non genera richieste, ma resta scritto che è stato valutato."""
        with tempfile.TemporaryDirectory() as directory:
            archive, cid = self.archive(directory, [
                {'company_name': 'Acme', 'title': 'Engineer', 'source_url': 'https://example.org/1',
                 'website_url': 'https://acme.breezy.hr/',
                 'description': 'Acme costruisce inverter solari per impianti industriali.'}])
            try:
                company = dict(archive.db.execute(
                    'SELECT id,name,website FROM companies WHERE id=?', (cid,)).fetchone())
                self.assertEqual(kind(company['website']), 'non_aziendale')
                result = one(archive, company, timeout=1)
                self.assertEqual(result['requests'], 0)
                self.assertEqual(result['status'], 'da_sintetizzare')
                self.assertEqual(result['strategy'], 'annuncio')
                save(archive, result)
                trace = {a['strategy']: a['status'] for a in archive.profile_attempts(cid)}
                self.assertEqual(trace['sito_aziendale'], 'non_applicabile')
                self.assertEqual(trace['annuncio'], 'trovata')
                # Il testo grezzo dell'annuncio non finisce nella descrizione: la sintesi è un altro passo.
                self.assertEqual(archive.db.execute(
                    'SELECT description FROM companies WHERE id=?', (cid,)).fetchone()[0], '')
            finally:
                archive.close()

    def test_a_company_without_any_described_ad_says_so(self):
        """Senza annunci descritti non c'è niente da sintetizzare, e va scritto."""
        with tempfile.TemporaryDirectory() as directory:
            archive, cid = self.archive(directory, [
                {'company_name': 'Vuota', 'title': 'Engineer', 'source_url': 'https://example.org/2'}])
            try:
                self.assertIsNone(ad_text(archive, cid))
                company = dict(archive.db.execute(
                    'SELECT id,name,website FROM companies WHERE id=?', (cid,)).fetchone())
                result = one(archive, company, timeout=1)
                save(archive, result)
                trace = {a['strategy']: a['status'] for a in archive.profile_attempts(cid)}
                self.assertEqual(trace['annuncio'], 'non_applicabile')
                self.assertEqual(result['status'], 'vuota')
            finally:
                archive.close()


if __name__ == '__main__':
    unittest.main()
