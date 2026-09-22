"""Il giudice System One senza contattare il fornitore: soglie, cancelli e posto nella cascata."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobhunter.evaluation import system_one
from jobhunter.evaluation.selection import verdicts
from jobhunter.evaluation.tier import KEEP, UNKNOWN
from jobhunter.workspace import Archive

CATALOG = {'S0': 'Costruiamo modelli di previsione della domanda.', 'S1': 'Sede a Milano.'}


def noul(value):
    """One yes/no answer in the provider's shape."""
    return {'type': 'noul', 'noul': value}


def choice(option, confidence):
    """One choice answer in the provider's shape."""
    return {'type': 'choice', 'choice': option, 'confidence': confidence}


def workspace(folder, **overrides):
    """A disposable archive plus a config whose reports land in the test directory."""
    archive = Archive(Path(folder)/'db.sqlite3')
    cfg = system_one.config()
    cfg['output_directory'] = folder
    cfg.update(overrides)
    path = Path(folder)/'system_one.json'
    path.write_text(json.dumps(cfg), encoding='utf-8')
    return archive, path


class ThresholdTests(unittest.TestCase):
    """Le probabilita' le da' il modello, il verdetto lo decide il codice."""

    def setUp(self):
        """Read the real rubric: these thresholds are the ones that ship."""
        self.cfg = system_one.config()
        self.spec = system_one.questions(self.cfg, 'selection')

    def judge(self, compatible, family=('nessuna', 0.9), student=0.02):
        """Answer every configured question, then let `judgement()` combine them."""
        answers = {'mansioni_compatibili': noul(compatible), 'posto_per_studenti': noul(student),
                   'famiglia_esclusa': choice(*family), 'prova': choice('S0', 0.8)}
        return system_one.judgement(self.cfg, self.spec, answers, CATALOG)[0]

    def test_confident_answers_decide_and_the_middle_band_does_not(self):
        """`review` non e' un errore: e' la banda in cui nessuna soglia e' stata raggiunta."""
        self.assertEqual(self.judge(0.94)['decision'], 'keep')
        self.assertEqual(self.judge(0.05)['decision'], 'exclude')
        self.assertEqual(self.judge(0.5)['decision'], 'review')
        # Una famiglia esclusa batte la compatibilita': il profilo esclude per mansione, non per punteggio.
        self.assertEqual(self.judge(0.94, family=('commerciale_marketing', 0.9))['decision'], 'exclude')
        # Una famiglia scelta senza convinzione non esclude niente.
        self.assertEqual(self.judge(0.94, family=('commerciale_marketing', 0.2))['decision'], 'keep')
        self.assertEqual(self.judge(0.94, student=0.95)['decision'], 'exclude')

    def test_evidence_is_resolved_from_the_catalog_and_cannot_be_invented(self):
        """La prova e' una chiave del catalogo: il validatore condiviso la espande e nient'altro passa."""
        self.assertEqual(self.judge(0.94)['evidence'], [CATALOG['S0']])
        answers = {'mansioni_compatibili': noul(0.94), 'posto_per_studenti': noul(0.0),
                   'famiglia_esclusa': choice('nessuna', 0.9), 'prova': choice('S9', 0.8)}
        with self.assertRaises(ValueError):
            system_one.judgement(self.cfg, self.spec, answers, CATALOG)

    def test_a_missing_or_out_of_range_probability_is_rejected(self):
        """Un numero fuori da [0, 1] o assente ferma questo record, non la passata."""
        def answers():
            """A complete, valid answer set to break one field at a time."""
            return {'mansioni_compatibili': noul(0.9), 'posto_per_studenti': noul(0.0),
                    'famiglia_esclusa': choice('nessuna', 0.9), 'prova': choice('S0', 0.8)}
        broken = [{**answers(), 'mansioni_compatibili': noul(1.4)},
                  {**answers(), 'mansioni_compatibili': noul('alta')},
                  {**answers(), 'famiglia_esclusa': choice('nessuna', None)},
                  {k: v for k, v in answers().items() if k != 'mansioni_compatibili'}]
        for case in broken:
            with self.assertRaises(ValueError):
                system_one.judgement(self.cfg, self.spec, case, CATALOG)


class CategoryTests(unittest.TestCase):
    """Due cancelli prima del settore, entrambi con una causa misurata nell'archivio."""

    def setUp(self):
        """Read the real rubric and the shared sector vocabulary."""
        self.cfg = system_one.config()
        self.spec = system_one.questions(self.cfg, 'category')
        self.options = {'Energia': 'x', system_one.UNCLASSIFIED: 'y'}

    def classify(self, category=('Energia', 0.9), employer=0.9, agency=0.02):
        """Answer the three company questions, then let `classification()` combine them."""
        answers = {'categoria': choice(*category), 'descrive_il_datore': noul(employer), 'agenzia': noul(agency)}
        return system_one.classification(self.cfg, self.spec, answers, self.options)[0]['category']

    def test_only_a_described_employer_earns_a_sector(self):
        """Un annuncio parla spesso del cliente: senza quel cancello il settore sarebbe del cliente."""
        self.assertEqual(self.classify(), 'Energia')
        self.assertEqual(self.classify(employer=0.2), system_one.UNCLASSIFIED)
        self.assertEqual(self.classify(agency=0.95), system_one.UNCLASSIFIED)
        self.assertEqual(self.classify(category=('Energia', 0.3)), system_one.UNCLASSIFIED)


class CombinedPassTests(unittest.TestCase):
    """Role and company judgments share one provider request and remain separate results."""

    @staticmethod
    def answers():
        """Return a complete Jev response for both question groups."""
        return {'mansioni_compatibili': noul(0.95), 'posto_per_studenti': noul(0.01),
                'famiglia_esclusa': choice('nessuna', 0.95), 'prova': choice('S0', 0.9),
                'categoria': choice('Energia', 0.95), 'descrive_il_datore': noul(0.95),
                'agenzia': noul(0.01)}

    def test_a_decision_enters_the_chain_and_removes_the_role_from_the_paid_queue(self):
        """Quello che decide System One non arriva a Qwen: e' l'unico motivo per cui fa risparmiare."""
        with tempfile.TemporaryDirectory() as folder:
            archive, path = workspace(folder)
            try:
                archive.ingest([{'company_name': 'Example', 'title': 'Specialist',
                                 'source_url': 'https://example.org/1',
                                 'description': 'Costruiamo modelli di previsione della domanda.\nSede a Milano.'}], 'test')
                oid = next(iter(archive.evaluations()))
                cid = archive.db.execute('SELECT id FROM companies').fetchone()['id']
                self.assertEqual(archive.evaluations()[oid]['status'], 'review')
                answers = self.answers()
                with patch('jobhunter.evaluation.system_one.llm.api_key', return_value='test'), \
                        patch('jobhunter.evaluation.system_one.ask',
                              return_value=(answers, {'input_tokens': 900, 'output_tokens': 4}, 'jev-1.13.0')) as ask:
                    report = system_one.run(archive, {'all': True, 'mode': 'execute'}, config_path=path)
                self.assertEqual(ask.call_count, 1)
                self.assertEqual((report['status'], report['counts']['keep']), ('success', 1))
                self.assertEqual((report['counts']['combined'], report['counts']['category_decided']), (1, 1))
                sent_state, sent_questions = ask.call_args.args[1:3]
                self.assertIn('mansioni', sent_state)
                self.assertIn('testi', sent_state)
                self.assertEqual(set(sent_questions), set(answers))
                chain = {j['giudice']: j['verdetto'] for j in verdicts(archive)[cid]['ruoli'][oid]['catena']}
                self.assertEqual(chain, {'regex': UNKNOWN, 'jev': KEEP})
                self.assertEqual(verdicts(archive)[cid]['ruoli'][oid]['verdetto'], KEEP)
                self.assertEqual(archive.db.execute('SELECT category FROM categories WHERE company_id=?',
                                                    (cid,)).fetchone()['category'], 'Energia')
                # Un secondo giro non richiama il fornitore: il ruolo non e' piu' un «non so» di nessuno,
                # quindi esce dalla coda invece di essere ri-giudicato e ri-pagato.
                with patch('jobhunter.evaluation.system_one.llm.api_key', return_value='test'), \
                        patch('jobhunter.evaluation.system_one.ask') as again:
                    repeated = system_one.run(archive, {'all': True, 'mode': 'execute'}, config_path=path)
                again.assert_not_called()
                self.assertEqual(repeated['total'], 0)
            finally:
                archive.close()

    def test_preview_never_reads_a_credential_and_never_calls(self):
        """Lo stesso cancello del passaggio remoto: nessuna spesa senza averla chiesta a parole."""
        with tempfile.TemporaryDirectory() as folder:
            archive, path = workspace(folder)
            try:
                archive.ingest([{'company_name': 'Example', 'title': 'Specialist', 'source_url': 'https://example.org/1',
                                 'description': 'Costruiamo modelli di previsione della domanda.\nSede a Milano.'}], 'test')
                with patch('jobhunter.evaluation.system_one.ask') as ask, \
                        patch('jobhunter.evaluation.system_one.llm.api_key') as key:
                    report = system_one.run(archive, {'all': True}, config_path=path)
                ask.assert_not_called()
                key.assert_not_called()
                self.assertEqual((report['mode'], report['total'], report['counts']['calls']), ('preview', 1, 0))
            finally:
                archive.close()

    def test_a_chat_category_is_never_overwritten(self):
        """La scelta dell'utente non e' un risultato di pipeline: nessuna passata la sovrascrive."""
        with tempfile.TemporaryDirectory() as folder:
            archive, path = workspace(folder)
            try:
                archive.ingest([{'company_name': 'Example', 'title': 'Specialist', 'source_url': 'https://example.org/1',
                                 'description': 'Produciamo pannelli solari e sistemi di accumulo.'}], 'test')
                cid = archive.db.execute('SELECT id FROM companies').fetchone()['id']
                from jobhunter.evaluation.company_axis import assign
                assign(archive, cid, 'Aerospazio', 'Scelta in chat')
                answers = self.answers()
                with patch('jobhunter.evaluation.system_one.llm.api_key', return_value='test'), \
                        patch('jobhunter.evaluation.system_one.ask',
                              return_value=(answers, {'input_tokens': 500, 'output_tokens': 3}, 'jev-1.13.0')):
                    system_one.run(archive, {'all': True, 'revisit': True, 'mode': 'execute'}, config_path=path)
                row = archive.db.execute('SELECT category,method FROM categories WHERE company_id=?', (cid,)).fetchone()
                self.assertEqual((row['category'], row['method']), ('Aerospazio', 'chat'))
                # L'esito resta comunque salvato: si vede cosa avrebbe risposto, senza che cambi il verdetto.
                self.assertEqual(system_one.current_result(archive, 'category', cid, system_one.config(path))['category'], 'Energia')
            finally:
                archive.close()


class TransportTests(unittest.TestCase):
    """Il corpo che parte e la risposta che torna, senza contattare nessuno."""

    def opener(self, payload, status=None):
        """Stand in for the HTTPS opener, capturing the request this module builds."""
        sent = {}

        class Response:
            """A minimal context manager with the one method `ask()` calls."""

            def __enter__(inner):
                """Enter the with-block the way urlopen's result does."""
                return inner

            def __exit__(inner, *exception):
                """Leave without swallowing anything."""
                return False

            def read(inner, size):
                """Return the canned body, bounded like the real read."""
                return json.dumps(payload).encode()[:size]

        class Opener:
            """Record the request and answer it, or raise the configured HTTP error."""

            def open(inner, request, timeout=None):
                """Capture URL, headers and body before answering."""
                sent['url'] = request.full_url
                sent['headers'] = request.headers
                sent['body'] = json.loads(request.data.decode())
                if status:
                    import urllib.error
                    raise urllib.error.HTTPError(request.full_url, status, 'busy', {'Retry-After': '7'}, None)
                return Response()

        return sent, Opener()

    def test_the_request_carries_state_and_questions_and_the_answers_come_back_typed(self):
        """Un System One query e' `{model, state, questions}`: nessun prompt, nessun messaggio."""
        cfg = system_one.config()
        wired = {'is_urgent': {'type': 'noul', 'instructions': 'Urgent?'}}
        answers = {'is_urgent': noul(0.92)}
        sent, opener = self.opener({'model': 'jev-1.13.0', 'answers': answers, 'usage': {'input_tokens': 312}})
        with patch('jobhunter.evaluation.system_one.urllib.request.build_opener', return_value=opener):
            got, usage, model = system_one.ask(cfg, {'titolo': 'x'}, wired, 'secret-key')
        self.assertEqual(sent['url'], cfg['endpoint'])
        self.assertEqual(sent['headers']['Authorization'], 'Bearer secret-key')
        self.assertEqual(set(sent['body']), {'model', 'state', 'questions'})
        self.assertEqual(sent['body']['questions'], wired)
        self.assertEqual((got, usage['input_tokens'], model), (answers, 312, 'jev-1.13.0'))

    def test_a_different_set_of_answers_is_refused(self):
        """Le risposte si leggono per nome: un insieme diverso non si allinea alle domande poste."""
        cfg = system_one.config()
        wired = {'is_urgent': {'type': 'noul', 'instructions': 'Urgent?'}}
        _, opener = self.opener({'answers': {'altra_domanda': noul(0.5)}})
        with patch('jobhunter.evaluation.system_one.urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ValueError):
                system_one.ask(cfg, {'titolo': 'x'}, wired, 'k')

    def test_a_rejected_request_carries_its_status_so_the_caller_can_retry(self):
        """Solo un rifiuto si ritenta: non ha generato niente, quindi non puo' duplicare una spesa."""
        cfg = system_one.config()
        _, opener = self.opener({}, status=429)
        with patch('jobhunter.evaluation.system_one.urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ValueError) as caught:
                system_one.ask(cfg, {'titolo': 'x'}, {'q': {'type': 'noul', 'instructions': 'y'}}, 'k')
        self.assertEqual((caught.exception.status, caught.exception.retry_after), (429, 7))
        self.assertIn(429, system_one.THROTTLED)

    def test_a_plain_http_endpoint_is_refused_before_the_key_travels(self):
        """La credenziale non parte su un canale in chiaro, nemmeno se la configurazione lo chiede."""
        cfg = {**system_one.config(), 'endpoint': 'http://api.example.org/v1/systemone'}
        with self.assertRaises(ValueError):
            system_one.ask(cfg, {'titolo': 'x'}, {'q': {'type': 'noul', 'instructions': 'y'}}, 'k')


if __name__ == '__main__':
    unittest.main()
