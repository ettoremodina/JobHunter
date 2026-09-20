"""Lingua dell'annuncio e lenti di esplorazione: contare senza indovinare."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobhunter import debug, remote_llm
from jobhunter.languages import detect_language
from jobhunter.workspace import Archive, ROOT

TESTI = {
    'it': 'Cerchiamo una persona con esperienza nello sviluppo di modelli per il nostro team. Il ruolo prevede anche il lavoro con i dati dei clienti e la collaborazione con le altre aree della nostra azienda.',
    'en': 'We are looking for a person with experience in the development of models for our team. The role also includes work with customer data and collaboration with the other areas of our company.',
    'de': 'Wir suchen eine Person mit Erfahrung in der Entwicklung von Modellen für unser Team. Die Stelle umfasst auch die Arbeit mit den Daten der Kunden und die Zusammenarbeit mit den anderen Bereichen unseres Unternehmens.',
    'fr': "Nous recherchons une personne avec de l'expérience dans le développement de modèles pour notre équipe. Le poste comprend aussi le travail avec les données des clients et la collaboration avec les autres services de notre entreprise.",
}


class LanguageTests(unittest.TestCase):
    """La lingua in cui è scritto l'annuncio, che è altra cosa dalla lingua richiesta."""

    def test_each_language_is_recognised(self):
        """Quattro testi equivalenti, quattro lingue diverse riconosciute."""
        for code, text in TESTI.items():
            result = detect_language(text)
            self.assertEqual(result['code'], code, text[:30])
            self.assertEqual(result['known'], code in ('it', 'en'))

    def test_too_little_text_stays_undetermined(self):
        """Su un titolo di tre parole non si indovina una lingua: si dichiara di non saperla."""
        for text in ('Data Scientist', '', None, 'Senior Machine Learning Engineer (remote)'):
            self.assertEqual(detect_language(text)['code'], '')
            self.assertTrue(detect_language(text)['known'], 'un dubbio non è una lingua sconosciuta')


class LensTests(unittest.TestCase):
    """Ogni lente risponde alla sua domanda, e le righe sanno a quale azienda appartengono."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = Archive(Path(self.directory.name) / 'archive.db')
        self.addCleanup(lambda: self.archive.close())
        self.archive.ingest([
            {'company_name': 'Con testo', 'title': 'Data Scientist', 'source_url': 'https://example.org/1',
             'company_description': 'Energia rinnovabile', 'description': TESTI['de']},
            {'company_name': 'Senza testo', 'title': 'Mechanical Engineer', 'source_url': 'https://example.org/2'},
        ], 'test')

    def counts(self):
        return {lens['id']: lens['count'] for lens in debug.lenses(self.archive)['lenses']}

    def test_missing_data_lenses_count_what_is_missing(self):
        """Un'azienda senza descrizione e un annuncio senza testo si contano una volta ciascuno."""
        counts = self.counts()
        self.assertEqual(counts['aziende-senza-descrizione'], 1)
        self.assertEqual(counts['annunci-senza-descrizione'], 1)
        self.assertEqual(counts['aziende-non-categorizzate'], 2)

    def test_each_lens_has_a_navigation_path(self):
        """Ogni controllo dichiara ambito e sezione, anche nella pagina dei risultati."""
        lenses = debug.lenses(self.archive)['lenses']
        self.assertEqual({lens['scope'] for lens in lenses}, {'aziende', 'annunci'})
        self.assertTrue(all(lens['section'] for lens in lenses))
        page = debug.rows(self.archive, 'annunci-senza-descrizione')
        self.assertEqual((page['scope'], page['section']), ('annunci', 'Dati mancanti'))

    def test_language_lens_finds_the_foreign_advert(self):
        """L'annuncio in tedesco finisce fra quelli in una lingua che non conosci, con il suo gruppo."""
        self.assertEqual(self.counts()['annunci-in-lingua-sconosciuta'], 1)
        page = debug.rows(self.archive, 'annunci-in-lingua-sconosciuta')
        self.assertEqual(page['items'][0]['facet'], 'tedesco')
        self.assertEqual(page['items'][0]['name'], 'Con testo')
        self.assertTrue(page['items'][0]['company_id'])

    def test_italian_summary_preserves_the_original_language(self):
        """A saved Italian derivative must leave the German source and its warning intact."""
        cid = self.archive.search(query='Con testo')['items'][0]['id']
        oid = self.archive.show(cid)['opportunities'][0]['id']
        cfg = json.loads((ROOT / 'config/remote_llm.json').read_text(encoding='utf-8'))
        cfg.update(output_directory=str(Path(self.directory.name) / 'remote'), request_delay_seconds=0)
        path = Path(self.directory.name) / 'remote.json'
        path.write_text(json.dumps(cfg), encoding='utf-8')
        catalog = remote_llm.inputs(self.archive, 'job-summary', oid, cfg)['source']['evidence_catalog']
        reference = next(iter(catalog))
        answer = {'summary': 'Sviluppo di modelli e collaborazione con i clienti.',
                  'facts': [{'field': 'responsibilities', 'text': 'Sviluppo di modelli', 'quote': reference}],
                  'missing_information': []}
        with patch('jobhunter.remote_llm.api_key', return_value='dummy'), patch(
                'jobhunter.remote_llm.request', return_value=(answer, {}, [])):
            report = remote_llm.run(self.archive, 'job-summary', execute=True, config_path=path, record_id=oid)
        self.assertEqual(report['processed'], 1, report)
        company = self.archive.show(cid)
        remote_llm.presentation(self.archive, company, config_path=path)
        job = company['opportunities'][0]
        self.assertEqual(job['remote_summary']['summary'], answer['summary'])
        self.assertEqual(job['description'], TESTI['de'])
        self.assertEqual(job['selection']['requirements']['written_in']['code'], 'de')
        self.assertFalse(job['selection']['requirements']['written_in']['known'])
        self.assertEqual(self.counts()['annunci-in-lingua-sconosciuta'], 1)

    def test_a_group_narrows_the_rows(self):
        """Scegliere un motivo restringe le righe, e un motivo inesistente non ne restituisce nessuna."""
        excluded = debug.rows(self.archive, 'annunci-scartati-dal-regex')
        self.assertEqual(excluded['total'], 1)
        self.assertEqual(debug.rows(self.archive, 'annunci-scartati-dal-regex', excluded['items'][0]['facet'])['total'], 1)
        self.assertEqual(debug.rows(self.archive, 'annunci-scartati-dal-regex', '', filter_value=True)['total'], 0)
        self.assertEqual(debug.rows(self.archive, 'annunci-scartati-dal-regex', 'motivo-inventato')['total'], 0)
        with self.assertRaises(ValueError):
            debug.rows(self.archive, 'lente-inventata')


if __name__ == '__main__':
    unittest.main()
