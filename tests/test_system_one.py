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

    def judge(self, compatible, family=('nessuna', 0.9), student=0.02, described=0.98,
              too_senior=0.02):
        """Answer every configured question, then let `judgement()` combine them."""
        answers = {'mansioni_descritte': noul(described), 'mansioni_compatibili': noul(compatible),
                   'posto_per_studenti': noul(student),
                   'seniority_fuori_profilo': noul(too_senior),
                   'famiglia_esclusa': choice(*family), 'prova': choice('S0', 0.8)}
        return system_one.judgement(self.cfg, self.spec, answers, CATALOG)[0]

    def test_confident_answers_decide_and_the_middle_band_does_not(self):
        """`review` non e' un errore: e' la banda in cui nessuna soglia e' stata raggiunta."""
        self.assertEqual(self.judge(0.94)['decision'], 'keep')
        self.assertEqual(self.judge(0.05)['decision'], 'exclude')
        self.assertEqual(self.judge(0.5)['decision'], 'review')
        # Una famiglia esclusa batte la compatibilita': il profilo esclude per mansione, non per punteggio.
        self.assertEqual(self.judge(0.94, family=('commerciale_marketing', 0.9))['decision'], 'review')
        self.assertEqual(self.judge(0.5, family=('commerciale_marketing', 0.9))['decision'], 'exclude')
        # Una famiglia scelta senza convinzione non esclude niente.
        self.assertEqual(self.judge(0.94, family=('commerciale_marketing', 0.2))['decision'], 'keep')
        self.assertEqual(self.judge(0.94, student=0.95)['decision'], 'exclude')
        self.assertEqual(self.judge(0.94, too_senior=0.95)['decision'], 'exclude')
        # Il motivo nomina per primo il numero che ha deciso, non la compatibilita' che ha perso.
        self.assertIn("(seniority fuori profilo 95%; compatibilita' 94%)", self.judge(0.94, too_senior=0.95)['rationale'])
        self.assertEqual(self.judge(0.05, described=0.05)['decision'], 'review')

    def test_uncertain_seniority_sends_a_compatible_role_to_review(self):
        """La fascia e' una manopola: spenta si tiene il ruolo, accesa lo si rivede a mano."""
        # Spenta di default dal 22/09/2026: l'utente ha scelto di tenere i ruoli con seniority incerta.
        self.assertEqual(self.judge(0.94, too_senior=0.6)['decision'], 'keep')
        self.cfg['thresholds']['seniority_review_above'] = 0.5
        self.assertEqual(self.judge(0.94, too_senior=0.49)['decision'], 'keep')
        uncertain = self.judge(0.94, too_senior=0.6)
        self.assertEqual(uncertain['decision'], 'review')
        self.assertEqual(uncertain['missing_information'], ['Seniority incerta: 60%'])
        self.assertEqual(self.judge(0.94, too_senior=0.8)['decision'], 'exclude')
        # Un ruolo non compatibile resta escluso: il dubbio sulla seniority non lo riapre.
        self.assertEqual(self.judge(0.05, too_senior=0.6)['decision'], 'exclude')

    def test_conflicting_positive_and_excluded_signals_go_to_review(self):
        """Quantitative work in an excluded domain must not become a false negative."""
        result = self.judge(0.92, family=('produzione_manutenzione', 0.88))
        self.assertEqual(result['decision'], 'review')
        self.assertEqual(result['missing_information'], ["Conflitto tra compatibilita' e famiglia esclusa"])
        self.assertEqual(self.judge(0.67, family=('commerciale_marketing', 0.85))['decision'], 'review')

    def test_an_obvious_manual_role_does_not_require_detailed_duties(self):
        """A strong excluded family and very low compatibility can decide a short manual advert."""
        result = self.judge(0.02, family=('produzione_manutenzione', 1.0), described=0.34)
        self.assertEqual(result['decision'], 'exclude')
        self.assertEqual(result['missing_information'], [])
        self.assertEqual(self.judge(0.02, described=0.34)['decision'], 'review')
        self.assertEqual(self.judge(
            0.02, family=('amministrazione_hr_legale', 0.95), described=0.03
        )['decision'], 'review')

    def test_evidence_is_resolved_from_the_catalog_and_cannot_be_invented(self):
        """La prova e' una chiave del catalogo: il validatore condiviso la espande e nient'altro passa."""
        self.assertEqual(self.judge(0.94)['evidence'], [CATALOG['S0']])
        answers = {'mansioni_descritte': noul(0.98), 'mansioni_compatibili': noul(0.94),
                   'posto_per_studenti': noul(0.0), 'seniority_fuori_profilo': noul(0.02),
                   'famiglia_esclusa': choice('nessuna', 0.9), 'prova': choice('S9', 0.8)}
        with self.assertRaises(ValueError):
            system_one.judgement(self.cfg, self.spec, answers, CATALOG)

    def test_a_missing_or_out_of_range_probability_is_rejected(self):
        """Un numero fuori da [0, 1] o assente ferma questo record, non la passata."""
        def answers():
            """A complete, valid answer set to break one field at a time."""
            return {'mansioni_descritte': noul(0.98), 'mansioni_compatibili': noul(0.9),
                    'posto_per_studenti': noul(0.0), 'seniority_fuori_profilo': noul(0.02),
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
        self.options = {'Energia': 'x', 'Consulenza e servizi': 'y', 'Software e tecnologia': 'z'}

    def answers(self, scores=None, employer=0.9, agency=0.02):
        """Build one independent Noul response for every configured category."""
        scores = {'Energia': 0.9} if scores is None else scores
        return {**{f'settore_{index:02d}': noul(scores.get(category, 0.02))
                   for index, category in enumerate(self.options)},
                'descrive_il_datore': noul(employer), 'agenzia': noul(agency)}

    def classify(self, scores=None, employer=0.9, agency=0.02):
        """Answer the three company questions, then let `classification()` combine them."""
        return system_one.classification(
            self.cfg, self.spec, self.answers(scores, employer, agency), self.options)[0]

    def test_only_a_described_employer_earns_a_sector(self):
        """A client sector is not assigned to the employer when the text does not distinguish them."""
        self.assertEqual(self.classify()['categories'], ['Energia'])
        self.assertEqual(self.classify(employer=0.2)['categories'], [])
        self.assertEqual(self.classify({'Energia': 0.3})['categories'], [])

    def test_two_moderate_sectors_are_kept_and_lower_scores_are_dropped(self):
        """The approved 0.40 / 0.80 rule keeps at most the two strongest sectors."""
        result = self.classify({'Energia': 0.45, 'Software e tecnologia': 0.40,
                                'Consulenza e servizi': 0.34})
        self.assertEqual(result['categories'], ['Energia', 'Software e tecnologia'])

    def test_a_staffing_agency_is_classified_by_its_own_activity(self):
        """Recruiting is a company sector, not a reason to erase the company axis."""
        answers = self.answers({}, employer=0.2, agency=0.95)
        result, detail = system_one.classification(self.cfg, self.spec, answers, self.options)
        self.assertEqual(result['category'], 'Consulenza e servizi')
        self.assertEqual(result['categories'], ['Consulenza e servizi'])
        self.assertEqual(detail['punteggi_categoria']['Consulenza e servizi'], 0.95)

    def test_category_cache_identity_contains_the_live_vocabulary(self):
        """Adding a category must invalidate answers produced without that option."""
        signature = system_one.signature(self.cfg, 'category')
        self.assertIn('Media, cultura e intrattenimento', signature['category_options'])


class CombinedPassTests(unittest.TestCase):
    """Role and company judgments share one provider request and remain separate results."""

    @staticmethod
    def answers():
        """Return a complete Jev response for both question groups."""
        category_names = list(json.loads((system_one.ROOT/'config/categories.json').read_text(encoding='utf-8')))
        return {'mansioni_descritte': noul(0.98), 'mansioni_compatibili': noul(0.95),
                'posto_per_studenti': noul(0.01), 'seniority_fuori_profilo': noul(0.02),
                'famiglia_esclusa': choice('nessuna', 0.95), 'prova': choice('S0', 0.9),
                **{f'settore_{index:02d}': noul(0.95 if category == 'Energia' else 0.01)
                   for index, category in enumerate(category_names)},
                'descrive_il_datore': noul(0.95),
                'agenzia': noul(0.01)}

    def test_a_decision_enters_the_chain_and_removes_the_role_from_the_paid_queue(self):
        """Quello che decide Jev esce dalla coda invece di essere pagato una seconda volta."""
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
                category_key = archive.db.execute(
                    "SELECT cache_key FROM enrichments WHERE task='jev:category' AND record_id=?", (cid,)
                ).fetchone()['cache_key']
                archive.db.execute("UPDATE enrichments SET cache_key='stale' WHERE task='jev:category' AND record_id=?",
                                   (cid,))
                archive.db.commit()
                stale_category = system_one.run(archive, {'all': True}, config_path=path)
                self.assertEqual(stale_category['total'], 1)
                self.assertEqual(stale_category['items'][0]['tasks'], ['category'])
                archive.db.execute("UPDATE enrichments SET cache_key=? WHERE task='jev:category' AND record_id=?",
                                   (category_key, cid))
                archive.db.commit()
                archive.db.execute("UPDATE enrichments SET cache_key='stale' WHERE task='jev:selection' AND record_id=?",
                                   (oid,))
                archive.db.commit()
                with patch('jobhunter.evaluation.system_one.llm.api_key', return_value='test'), \
                        patch('jobhunter.evaluation.system_one.ask',
                              return_value=(answers, {'input_tokens': 900}, 'jev-1.13.0')) as refreshed:
                    rerun = system_one.run(archive, {'all': True, 'mode': 'execute'}, config_path=path)
                self.assertEqual((refreshed.call_count, rerun['counts']['selection_decided']), (1, 1))
            finally:
                archive.close()

    def test_a_rule_only_change_reuses_saved_answers_without_calling(self):
        """Soglie e nome del modello cambiano l'impronta, non le probabilita': niente da ripagare."""
        with tempfile.TemporaryDirectory() as folder:
            old = system_one.config()
            old.update(model='jev-latest')
            old['thresholds'].pop('seniority_review_above', None)
            old['decision_versions']['selection'] = '4'
            archive, path = workspace(folder, **{k: old[k] for k in ('model', 'thresholds', 'decision_versions')})
            try:
                archive.ingest([{'company_name': 'Example', 'title': 'Specialist', 'source_url': 'https://example.org/1',
                                 'description': 'Costruiamo modelli di previsione della domanda.\nSede a Milano.'}], 'test')
                answers = {**self.answers(), 'seniority_fuori_profilo': noul(0.6)}
                with patch('jobhunter.evaluation.system_one.llm.api_key', return_value='test'), \
                        patch('jobhunter.evaluation.system_one.ask',
                              return_value=(answers, {'input_tokens': 900}, 'jev-1.13.0')):
                    first = system_one.run(archive, {'all': True, 'mode': 'execute'}, config_path=path)
                self.assertEqual(first['counts']['keep'], 1)
                current = system_one.config()
                current['output_directory'] = folder
                # Accendere la fascia sulla seniority e' un cambio di sola regola: nessuna chiamata.
                current['thresholds']['seniority_review_above'] = 0.5
                path.write_text(json.dumps(current), encoding='utf-8')
                with patch('jobhunter.evaluation.system_one.ask') as again:
                    rerun = system_one.run(archive, {'all': True}, config_path=path)
                again.assert_not_called()
                self.assertEqual(rerun['total'], 0)
                self.assertEqual(rerun['counts']['selection_keep_to_review'], 1)
                self.assertEqual((rerun['counts']['selection_rekeyed'], rerun['counts']['category_rekeyed']), (1, 1))
                oid = next(iter(archive.evaluations()))
                self.assertEqual(system_one.current_result(archive, 'selection', oid, current)['decision'], 'review')
                # Una domanda cambiata cambia il significato della risposta: quella si ripaga.
                spec = json.loads((system_one.ROOT/current['questions_path']).read_text(encoding='utf-8'))
                spec['selection']['mansioni_compatibili']['instructions'] += ' Nuova regola.'
                questions = Path(folder)/'questions.json'
                questions.write_text(json.dumps(spec), encoding='utf-8')
                current['questions_path'] = str(questions)
                path.write_text(json.dumps(current), encoding='utf-8')
                changed = system_one.run(archive, {'all': True}, config_path=path)
                self.assertEqual((changed['total'], changed['counts']['selection_stale']), (1, 1))
            finally:
                archive.close()

    def test_preview_never_reads_a_credential_and_never_calls(self):
        """Anche un titolo preferito arriva a Jev, ma l'anteprima non spende né legge segreti."""
        with tempfile.TemporaryDirectory() as folder:
            archive, path = workspace(folder)
            try:
                archive.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'source_url': 'https://example.org/1',
                                  'description': 'Costruiamo modelli di previsione della domanda.\nSede a Milano.'}], 'test')
                self.assertEqual(next(iter(archive.evaluations().values()))['status'], 'potential')
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

    def test_zero_explicitly_disables_local_rate_pacing(self):
        """Unlimited means no local sleep, while a negative or boolean value is a config error."""
        cfg = system_one.config()
        self.assertEqual(system_one.request_interval({**cfg, 'max_requests_per_minute': 0}), 0.0)
        self.assertEqual(system_one.request_interval({**cfg, 'max_requests_per_minute': 500}), 0.12)
        for value in (-1, True, '500'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                system_one.request_interval({**cfg, 'max_requests_per_minute': value})


if __name__ == '__main__':
    unittest.main()
