"""Check company batching without contacting a paid provider.

Dal 20 settembre 2026 questo passaggio **non giudica**: scrive solo le schede dei ruoli
sopravvissuti e la scheda dell'azienda. Chi decide se un ruolo si tiene e' il giudice System One,
un passaggio prima; quello che si prova qui e' che non si paghi per schede che nessuno leggera'.
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from jobhunter.workspace import Archive
from jobhunter.evaluation.company_batch import run, response_schema
from jobhunter.evaluation.remote_llm import digest, inputs, request as request_remote, validate

# «Data Scientist» ha priorita' primaria, ma diventa compatibile solo dopo un keep di Jev.
# I test salvano esplicitamente quel verdetto: e' la condizione minima perche' si paghi qualcosa.
SURVIVOR = 'Data Scientist'
EMPTY = {'summary': '', 'facts': [], 'missing_information': ['Dati insufficienti']}


def approve_survivors(archive):
    """Mark the fixture's intended survivors as Jev-compatible."""
    archive.evaluations()
    with archive.db:
        for row in archive.db.execute('SELECT id,data FROM opportunities').fetchall():
            if json.loads(row['data']).get('title') == SURVIVOR:
                payload = {'result': {'decision': 'keep', 'rationale': 'Fixture Jev',
                                      'evidence': ['Build models.'], 'missing_information': []}}
                archive.db.execute('INSERT OR REPLACE INTO enrichments VALUES(?,?,?,?,?,?,?)',
                                   ('jev:selection', row['id'], 'source', 'fixture',
                                    json.dumps(payload), 'fixture', '2026-09-22'))


def workspace(folder, titles, description='Build models.', company=''):
    """A disposable archive plus a config whose reports land in the test directory."""
    archive = Archive(Path(folder)/'db.sqlite3')
    archive.ingest([{'company_name': 'Example', 'title': title, 'description': description,
                     'source_url': f'https://example.org/{index}'} for index, title in enumerate(titles)], 'test')
    approve_survivors(archive)
    if company:
        with archive.db:
            archive.db.execute('UPDATE companies SET description=?', (company,))
    cfg = json.loads(Path('config/remote_llm.json').read_text())
    cfg['output_directory'] = folder
    return archive, cfg


def redirect(cfg):
    """Send the batch's own config read to the test copy, leaving every other read alone."""
    original = json.loads

    def config_read(value, *args, **kwargs):
        """Redirect reports to the disposable test directory."""
        result = original(value, *args, **kwargs)
        return cfg if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY' else result
    return original, config_read


def card(payload, oid):
    """One grounded role card citing the first excerpt this role was actually offered."""
    return {'summary': 'Sviluppa modelli.', 'missing_information': [],
            'facts': [{'field': 'responsibilities', 'text': 'Sviluppa modelli.',
                       'quote': next(iter(payload['jobs'][oid]['evidence_catalog']))}]}


class BatchTests(unittest.TestCase):
    """Verify single-call accounting, resume, and atomic rejection."""

    def test_all_companies_counts_only_tier_a_and_b_queue(self):
        """The progress denominator is the payable A/B queue, not every archived company."""
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder) / 'db.sqlite3')
            try:
                arc.ingest([
                    {'company_name': 'Wanted', 'title': 'Senior Engineer', 'description': 'Lead.',
                     'source_url': 'https://example.org/wanted'},
                    {'company_name': 'Outside', 'title': 'Senior Engineer', 'description': 'Lead.',
                     'source_url': 'https://example.org/outside'},
                ], 'test')
                ids = {row['name']: row['id'] for row in arc.db.execute('SELECT id,name FROM companies')}
                with arc.db:
                    for name, category in [('Wanted', 'Energia'), ('Outside', 'Industria e materiali')]:
                        arc.db.execute('''INSERT INTO categories
                            (company_id,category,rank,confidence,method,reason,updated_at)
                            VALUES(?,?,?,?,?,?,?)''',
                                       (ids[name], category, 1, 1, 'chat', 'Fixture', '2026-09-22'))
                report = run(arc, {'all_companies': True, 'mode': 'preview'}, lambda detail: None)
                self.assertEqual(report['total_companies'], 1)
                self.assertEqual(report['completed_companies'], 1)
            finally:
                arc.close()

    def test_schema_is_identical_across_calls(self):
        """Un prefisso stabile e' la condizione perche' la cache del provider possa agganciare."""
        cfg = json.loads(Path('config/remote_llm.json').read_text())
        company = json.loads(Path(cfg['response_schemas']['company-summary']).read_text())
        self.assertEqual(json.dumps(response_schema(cfg, company), sort_keys=True),
                         json.dumps(response_schema(cfg, company), sort_keys=True))
        schema = response_schema(cfg, company)
        # Nessun ID di annuncio e nessun enum di citazioni: quelli li verificano apply() e validate().
        self.assertEqual(schema['properties']['jobs']['items'], {'$ref': '#/$defs/role'})
        self.assertNotIn('enum', schema['$defs']['job-summary']['properties']['facts']['items']['properties']['quote'])
        # Nessun giudizio: una riga porta il suo riassunto e basta.
        self.assertEqual(schema['$defs']['role']['required'], ['id', 'summary'])
        self.assertEqual(schema['$defs']['role']['properties']['summary'], {'$ref': '#/$defs/job-summary'})
        self.assertNotIn('selection', schema['$defs'])
        self.assertNotIn('category', company['properties'])

    def test_summary_payload_never_contains_candidate_profile(self):
        """A descriptive card must not receive personal data that can turn it into a match review."""
        with tempfile.TemporaryDirectory() as folder:
            arc, cfg = workspace(folder, [SURVIVOR])
            original, config_read = redirect(cfg)
            try:
                captured = []

                def response(config, prompt, payload, key):
                    captured.append(payload)
                    oid = next(iter(payload['jobs']))
                    return {'company': None, 'jobs': [{'id': oid, 'summary': card(payload, oid)}]}, {'total_tokens': 1}, []

                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='dummy'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=response), \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertNotIn('candidate_profile', captured[0])
                self.assertIn('field_labels', captured[0])
            finally:
                arc.close()

    def test_batch_resume_and_invalid_ids(self):
        """Two cards share one paid request; invalid responses persist usage but no derivatives."""
        with tempfile.TemporaryDirectory() as folder:
            arc, cfg = workspace(folder, [SURVIVOR, SURVIVOR])
            original, config_read = redirect(cfg)
            try:
                def response(config, prompt, payload, key):
                    """Return a grounded card for every role the caller submitted."""
                    return {'company': {'summary': 'Non richiesta e mai salvata', 'facts': [], 'missing_information': []},
                            'jobs': [{'id': oid, 'summary': card(payload, oid)} for oid in payload['jobs']]}, \
                        {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}, []
                values = {'all_companies': True, 'mode': 'execute'}
                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='test'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=response) as request, \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    first = run(arc, values, lambda d: None)
                    self.assertEqual(request.call_count, 1)
                    self.assertEqual(first['counts']['saved_summaries'], 2)
                    self.assertEqual(first['counts']['summary_requests'], 2)
                    self.assertEqual(first['counts']['api_companies'], 1)
                    self.assertEqual(first['usage']['total_tokens'], 120)
                    # L'azienda non era richiesta: la sua scheda arriva lo stesso e non viene salvata.
                    self.assertEqual(first['counts']['ignored_company_summaries'], 1)
                    self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:company-summary'").fetchone()[0], 0)
                    run(arc, values, lambda d: None)
                    self.assertEqual(request.call_count, 1, 'una scheda gia\' valida non si ripaga')
                    arc.db.execute('DELETE FROM enrichments'); arc.db.commit()
                    approve_survivors(arc)
                    request.side_effect = None
                    request.return_value = ({'company': None, 'jobs': []}, {'total_tokens': 99}, [])
                    failed = run(arc, values, lambda d: None)
                    self.assertEqual(failed['status'], 'partial')
                    self.assertEqual(failed['usage']['total_tokens'], 99)
                    self.assertEqual(failed['counts']['summary_requests'], 2)
                    self.assertEqual(failed['counts']['saved_summaries'], 0)
                    self.assertEqual(failed['counts']['rejected_companies'], 1)
                    saved = original(Path(failed['report_path']).read_text(encoding='utf-8'))
                    self.assertEqual(saved['items'][0]['rejected_answer'], {'company': None, 'jobs': []})
                    self.assertEqual(arc.db.execute(
                        "SELECT count(*) FROM enrichments WHERE task LIKE 'remote:%'").fetchone()[0], 0)
                    # Senza mansioni da leggere non c'e' niente da riassumere: nessuna chiamata.
                    for row in arc.db.execute('SELECT id,data FROM opportunities').fetchall():
                        data = json.loads(row['data']); data['description'] = ''
                        arc.db.execute('UPDATE opportunities SET data=?,content_hash=? WHERE id=?',
                                       (json.dumps(data), 'no-description', row['id']))
                    arc.db.commit()

                    def never_called(config, prompt, payload, key):
                        """Fail loudly if an unreadable role is ever submitted."""
                        raise AssertionError('Un annuncio senza descrizione non deve essere spedito')
                    request.side_effect = never_called
                    before = request.call_count
                    result = run(arc, values, lambda d: None)
                    self.assertEqual((result['status'], request.call_count), ('success', before))
                    self.assertEqual(result['counts']['unreadable_jobs'], 2)
            finally:
                arc.close()

    def test_only_surviving_roles_of_a_presented_company_are_paid_for(self):
        """Il passaggio non giudica: paga solo per chi ha gia' superato i giudici, e mai per gli altri."""
        with tempfile.TemporaryDirectory() as folder:
            arc, cfg = workspace(folder, [SURVIVOR, 'HR Manager', 'Analyst'])
            original, config_read = redirect(cfg)
            try:
                submitted = []

                def response(config, prompt, payload, key):
                    """Record exactly which roles the caller chose to pay for."""
                    submitted.append(set(payload['jobs']))
                    return {'company': None, 'jobs': [{'id': oid, 'summary': card(payload, oid)} for oid in payload['jobs']]}, \
                        {'total_tokens': 10}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='k'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=response), \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    report = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                titles = {r['id']: json.loads(r['data'])['title'] for r in arc.db.execute('SELECT id,data FROM opportunities')}
                # «HR Manager» lo scarta il regex, «Analyst» resta un «non so»: nessuno dei due si paga.
                self.assertEqual({titles[oid] for oid in submitted[0]}, {SURVIVOR})
                self.assertEqual(report['counts']['summary_requests'], 1)
                self.assertEqual(report['counts']['saved_summaries'], 1)
            finally:
                arc.close()

    def test_one_unusable_part_does_not_discard_the_billed_rest(self):
        """A bad role card must not throw away the other grounded derivatives of the same paid call."""
        with tempfile.TemporaryDirectory() as folder:
            arc, cfg = workspace(folder, [SURVIVOR, SURVIVOR], company='Costruisce modelli di rete elettrica.')
            original, config_read = redirect(cfg)
            try:
                def partial(config, prompt, payload, key):
                    """Invent a quote in the first card and omit the requested company card."""
                    ids = list(payload['jobs'])
                    invented = {'summary': 'Inventata', 'missing_information': [],
                                'facts': [{'field': 'responsibilities', 'text': 'x', 'quote': 'S99'}]}
                    return {'company': None, 'jobs': [{'id': oid, 'summary': invented if oid == ids[0] else card(payload, oid)}
                                                      for oid in ids]}, {'total_tokens': 200}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='k'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=partial), \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    result = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['counts']['summary_requests'], 2)
                # La scheda valida sopravvive; la citazione inventata e la scheda azienda assente no.
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:job-summary'").fetchone()[0], 1)
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:company-summary'").fetchone()[0], 0)
                errors = [r['error'] for item in result['items'] for r in item.get('rejected', [])]
                self.assertEqual(len(errors), 2)
                # La risposta grezza rifiutata resta nel report: senza, non si puo' diagnosticare perche'.
                self.assertTrue(any('rejected_answer' in item for item in result['items'] if item.get('rejected')))
            finally:
                arc.close()

    def test_requested_null_summary_is_partial_and_valid_sibling_persists(self):
        """Omitted requested summaries must be reported and remain eligible on the next run."""
        with tempfile.TemporaryDirectory() as folder:
            arc, cfg = workspace(folder, [SURVIVOR, SURVIVOR])
            original, config_read = redirect(cfg)
            try:
                submitted = []

                def response(config, prompt, payload, key):
                    """Omit one requested summary and provide its sibling with grounded facts."""
                    ids = list(payload['jobs'])
                    submitted.append(ids)
                    return {'company': None, 'jobs': [{'id': oid, 'summary': None if i == 0 else card(payload, oid)}
                                                      for i, oid in enumerate(ids)]}, {'total_tokens': 20}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='dummy'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=response), \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    report = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                    self.assertEqual(report['status'], 'partial')
                    self.assertEqual(report['counts']['rejected_jobs'], 1)
                    saved = original(Path(report['report_path']).read_text(encoding='utf-8'))
                    self.assertEqual(saved['items'][0]['rejected'][0]['error'], 'Job summary requested but not returned')
                    self.assertEqual(saved['usage']['total_tokens'], 20)
                    self.assertEqual(arc.db.execute("SELECT record_id FROM enrichments WHERE task='remote:job-summary'").fetchone()[0], submitted[0][1])
                    run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                    self.assertEqual(submitted[1], [submitted[0][0]], 'solo il ruolo rimasto senza scheda riparte')
            finally:
                arc.close()

    def test_each_role_gets_its_own_reference_space(self):
        """Two roles in one request must not be able to cite each other's excerpts."""
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': SURVIVOR, 'description': 'Alpha only.', 'source_url': 'https://example.org/a'},
                            {'company_name': 'Example', 'title': SURVIVOR, 'description': 'Beta only.', 'source_url': 'https://example.org/b'}], 'test')
                approve_survivors(arc)
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original, config_read = redirect(cfg)
                seen = {}

                def crossed(config, prompt, payload, key):
                    """Cite the second role's namespace while describing both."""
                    seen.update(payload['jobs'])
                    ids = list(payload['jobs'])
                    stolen = {'summary': 'Rubata', 'missing_information': [],
                              'facts': [{'field': 'responsibilities', 'text': 'x', 'quote': 'J1-S0'}]}
                    return {'company': None, 'jobs': [{'id': ids[0], 'summary': stolen},
                                                      {'id': ids[1], 'summary': stolen}]}, {'total_tokens': 10}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='k'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=crossed), \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    result = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertTrue(all(ref.startswith('J') for job in seen.values() for ref in job['evidence_catalog']))
                # Solo il proprietario di quel namespace puo' citarlo: l'altro viene rifiutato.
                self.assertEqual(result['counts']['saved_summaries'], 1)
                self.assertEqual(result['counts']['rejected_jobs'], 1)
            finally:
                arc.close()

    def test_known_format_slips_are_repaired_not_thrown_away(self):
        """Tre slittamenti di formato visti nel primo giro completo (22/09/2026) salvano la scheda.

        Stesso annuncio ripetuto, chiavi unite da virgola, scheda azienda che cita l'annuncio: in
        tutti e tre la citazione punta a testo davvero inviato, quindi si risolve. Una chiave
        inesistente resta rifiutata.
        """
        with tempfile.TemporaryDirectory() as folder:
            arc, cfg = workspace(folder, [SURVIVOR], description='Costruisce modelli di rete.\nUsa Python.',
                                 company='Opera nella rete elettrica.')
            cid = arc.db.execute('SELECT id FROM companies').fetchone()[0]
            with arc.db:
                arc.db.execute('''INSERT INTO categories
                    (company_id,category,rank,confidence,method,reason,updated_at) VALUES(?,?,?,?,?,?,?)''',
                               (cid, 'Energia', 1, 0.9, 'jev', 'Fixture', '2026-09-22'))
            original, config_read = redirect(cfg)

            def sloppy(config, prompt, payload, key):
                """Repeat the role, join two keys, and cite the role from the company card."""
                oid = next(iter(payload['jobs']))
                joined = {'summary': 'Modelli di rete.', 'missing_information': [],
                          'facts': [{'field': 'responsibilities', 'text': 'Modelli e Python.', 'quote': 'J0-S0,J0-S1'}]}
                broken = {**joined, 'facts': [{'field': 'responsibilities', 'text': 'x', 'quote': 'J0-S9'}]}
                company = {'summary': 'Rete elettrica.', 'missing_information': [],
                           'facts': [{'section': 'business', 'text': 'Rete', 'quote': 'S0'},
                                     {'section': 'business', 'text': 'Modelli', 'quote': 'J0-S1'}]}
                return {'company': company, 'jobs': [{'id': oid, 'summary': broken},
                                                     {'id': oid, 'summary': joined}]}, {'total_tokens': 10}, []
            try:
                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='k'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=sloppy), \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    result = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertEqual((result['counts']['saved_summaries'], result['counts']['saved_company_cards']), (1, 1))
                self.assertEqual(result['counts']['rejected_jobs'], 0)
                stored = json.loads(arc.db.execute(
                    "SELECT data FROM enrichments WHERE task='remote:company-summary'").fetchone()[0])
                self.assertEqual([f['quote'] for f in stored['result']['facts']],
                                 ['Opera nella rete elettrica.', 'Costruisce modelli di rete.'])
            finally:
                arc.close()

    def test_company_card_does_not_depend_on_surviving_roles(self):
        """DESIGN §2: i due assi sono indipendenti. Nessun ruolo sopravvive, la scheda azienda si chiede lo stesso."""
        with tempfile.TemporaryDirectory() as folder:
            arc, cfg = workspace(folder, ['Senior Engineer', 'Senior Engineer'],
                                 description='Lead the team.', company='Costruisce modelli di rete elettrica.')
            original, config_read = redirect(cfg)
            try:
                # Azienda interessante e nessun ruolo compatibile: Tier B-attesa, la presentazione si paga.
                cid = arc.db.execute('SELECT id FROM companies').fetchone()[0]
                with arc.db:
                    arc.db.execute('''INSERT INTO categories
                        (company_id,category,rank,confidence,method,reason,updated_at)
                        VALUES(?,?,?,?,?,?,?)''',
                                   (cid, 'Energia', 1, 0.9, 'jev', 'Settore «Energia» al 90%', '2026-09-20'))

                def card_only(config, prompt, payload, key):
                    """Return only the company card; no role survived to be summarised."""
                    self.assertEqual(payload['jobs'], {})
                    self.assertTrue(payload['company_requested'])
                    return {'company': {'summary': 'Costruisce modelli di rete elettrica.', 'missing_information': [],
                                        'facts': [{'section': 'business', 'text': 'Modelli di rete', 'quote': 'S0'}]},
                            'jobs': []}, {'total_tokens': 40}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='k'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=card_only) as request, \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    result = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertEqual(request.call_count, 1)
                self.assertEqual((result['counts']['summary_requests'], result['counts']['saved_company_cards']), (0, 1))
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:company-summary'").fetchone()[0], 1)
                # La categoria resta quella del giudice System One: questo passaggio non la tocca.
                self.assertEqual(tuple(arc.db.execute('SELECT category,method FROM categories').fetchone()), ('Energia', 'jev'))
            finally:
                arc.close()

    def test_stable_prefix_is_marked_for_the_context_cache(self):
        """La cache implicita non e' garantita: il prefisso va marcato, e deve restare identico."""
        cfg = json.loads(Path('config/remote_llm.json').read_text())
        company = json.loads(Path(cfg['response_schemas']['company-summary']).read_text())
        payload = {'candidate_profile': 'PROFILO', 'field_labels': {'a': 'A'},
                   'company': {'company': 'Example'}, 'jobs': {},
                   'response_schema': response_schema(cfg, company)}
        sent = {}

        class Answer:
            """Stand in for the HTTP response without contacting the provider."""

            def read(self, size):
                """Return one valid completion."""
                return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}],
                                   'usage': {'prompt_tokens': 1, 'prompt_tokens_details': {'cached_tokens': 0}}}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exception):
                return False

        def opener(request, timeout=None):
            """Capture the request body the provider would receive."""
            sent.update(json.loads(request.data))
            return Answer()

        with patch('jobhunter.evaluation.remote_llm.urllib.request.build_opener') as build:
            build.return_value.open = opener
            request_remote(cfg, 'ISTRUZIONI', payload, 'key')
        block = sent['messages'][0]['content'][0]
        self.assertEqual(block['cache_control'], {'type': 'ephemeral'})
        # Tutto cio' che non cambia sta nel prefisso; il messaggio utente porta solo il lavoro.
        self.assertIn('ISTRUZIONI', block['text'])
        self.assertIn('PROFILO', block['text'])
        self.assertIn('field_labels', block['text'])
        user = sent['messages'][1]['content']
        self.assertNotIn('PROFILO', user)
        self.assertNotIn('field_labels', user)
        # Lo schema viaggia fuori dai messaggi, come contratto di risposta.
        self.assertEqual(sent['response_format']['json_schema']['schema'], payload['response_schema'])

    def test_missing_company_evidence_never_generates_empty_enum(self):
        """A company with role listings but no business facts must still have a valid request schema."""
        cfg = json.loads(Path('config/remote_llm.json').read_text())
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'test.db')
            try:
                arc.ingest([{'company_name': 'Unknown', 'title': SURVIVOR,
                             'source_url': 'https://example.org/empty'}], 'test')
                cid = arc.db.execute('SELECT id FROM companies').fetchone()[0]
                payload = inputs(arc, 'company-summary', cid, cfg)
                self.assertEqual(payload['source']['evidence_catalog'], {})
                quote = payload['response_schema']['properties']['facts']['items']['properties']['quote']
                self.assertNotIn('enum', quote)
                schema = response_schema(cfg, payload['response_schema'])
                self.assertIn({'type': 'null'}, schema['properties']['company']['anyOf'])
                with self.assertRaisesRegex(ValueError, 'Unknown source evidence'):
                    validate('company-summary', {'summary': 'Made up',
                        'facts': [{'section': 'business', 'text': 'Made up', 'quote': 'S0'}],
                        'missing_information': []}, payload['source'])
            finally:
                arc.close()

    def test_a_slow_company_never_holds_the_pool_and_a_rejected_call_is_retried(self):
        """Le chiamate in volo si rimpiazzano una a una, e un 429 non ha generato niente: si ritenta.

        La prima chiamata non rientra finche' le altre quattro non sono passate: con le ondate di
        `workers` questa attesa non finirebbe mai, perche' nessuna partiva prima che la piu' lenta
        del gruppo fosse rientrata.
        """
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Company '+str(i), 'title': SURVIVOR, 'description': 'Build models.',
                             'source_url': 'https://example.org/'+str(i)} for i in range(5)], 'test')
                approve_survivors(arc)
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original, config_read = redirect(cfg)
                lock, released, slow, done, throttled = threading.Lock(), threading.Event(), [], set(), []

                def response(config, prompt, payload, key):
                    """Hold the first request open; reject exactly one other with a provider 429."""
                    oid = sorted(payload['jobs'])[0]
                    with lock:
                        if not slow:
                            slow.append(oid)
                        first = slow[0] == oid
                    if first:
                        self.assertTrue(released.wait(20), 'una chiamata lenta ha bloccato le altre')
                    else:
                        with lock:
                            reject = not throttled
                            throttled.append(oid) if reject else None
                        if reject:
                            error = ValueError('Remote HTTP 429; request rejected by the provider')
                            error.status = 429
                            raise error
                        with lock:
                            done.add(oid)
                            if len(done) == 4:
                                released.set()
                    return ({'company': None, 'jobs': [{'id': oid, 'summary': card(payload, oid)} for oid in payload['jobs']]},
                            {'total_tokens': 10}, [])
                values = {'all_companies': True, 'mode': 'execute', 'workers': 2}
                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='test'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=response), \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    report = run(arc, values, lambda d: None)
                self.assertTrue(released.is_set())
                self.assertEqual(report['status'], 'success')
                self.assertEqual(report['counts']['saved_summaries'], 5)
                self.assertEqual(report['counts']['api_calls'], 5)
                self.assertEqual(report['counts']['throttled_retries'], 1)
                self.assertEqual(report['completed_companies'], 5)
            finally:
                arc.close()

    def test_shared_company_evidence_does_not_change_what_is_sent(self):
        """La cache di preparazione risparmia una rilettura, non deve cambiare di un byte la richiesta.

        `company_input` viene restituito a piu' ruoli della stessa azienda: se fosse lo stesso
        oggetto, l'arricchimento di un ruolo comparirebbe nella richiesta dell'altro e la firma
        della cache dei risultati cambierebbe senza che nulla sia cambiato davvero.
        """
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': 'Analyst '+str(i), 'description': 'We build models. Looking for an analyst.',
                             'source_url': 'https://example.org/'+str(i)} for i in range(3)], 'test')
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cid = arc.db.execute('SELECT id FROM companies').fetchone()[0]
                ids = [r[0] for r in arc.db.execute('SELECT id FROM opportunities ORDER BY id')]
                shared = {}
                for oid in ids:
                    for task in ('selection', 'job-summary'):
                        self.assertEqual(digest(inputs(arc, task, oid, cfg, cache=shared)),
                                         digest(inputs(arc, task, oid, cfg)))
                self.assertEqual(digest(inputs(arc, 'company-summary', cid, cfg, cache=shared)),
                                 digest(inputs(arc, 'company-summary', cid, cfg)))
                self.assertEqual(list(shared), [cid])
                first = inputs(arc, 'selection', ids[0], cfg, cache=shared)['company_context']
                first['verified_web_facts'] = ['inventato']
                self.assertNotIn('verified_web_facts', inputs(arc, 'selection', ids[1], cfg, cache=shared)['company_context'])
            finally:
                arc.close()

    def test_a_null_card_rejects_one_company_and_never_aborts_a_paid_run(self):
        """Visto in produzione: una parte `null` su una richiesta fermava tutta la run.

        Le risposte gia' pagate e ancora in volo venivano buttate via insieme all'eccezione. Una
        forma inattesa nella risposta del provider deve scartare *quel* pezzo e basta.
        """
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Company '+str(i), 'title': SURVIVOR, 'description': 'Build models.',
                             'source_url': 'https://example.org/'+str(i)} for i in range(4)], 'test')
                approve_survivors(arc)
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original, config_read = redirect(cfg)
                answered = []

                def response(config, prompt, payload, key):
                    """Null the card of the first company only; the others answer normally."""
                    ids = sorted(payload['jobs'])
                    answered.append(ids[0])
                    null = len(answered) == 1
                    return ({'company': None, 'jobs': [{'id': oid, 'summary': None if null else card(payload, oid)} for oid in ids]},
                            {'total_tokens': 10}, [])
                with patch('json.loads', side_effect=config_read), patch('jobhunter.evaluation.remote_llm.api_key', return_value='k'), \
                        patch('jobhunter.evaluation.remote_llm.request', side_effect=response), \
                        patch('jobhunter.evaluation.company_batch.time.sleep'):
                    report = run(arc, {'all_companies': True, 'mode': 'execute', 'workers': 1}, lambda d: None)
                self.assertEqual(report['status'], 'partial')
                self.assertEqual(report['counts']['api_calls'], 4)
                self.assertEqual(report['counts']['saved_summaries'], 3)
                self.assertEqual(report['counts']['rejected_jobs'], 1)
                self.assertEqual(report['counts']['rejected_companies'], 0)
                self.assertEqual([r['error'] for item in report['items'] for r in item.get('rejected', [])],
                                 ['Job summary requested but not returned'])
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:job-summary'").fetchone()[0], 3)
            finally:
                arc.close()


if __name__ == '__main__':
    unittest.main()
