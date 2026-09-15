"""Check company batching without contacting a paid provider."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from jobhunter.workspace import Archive
from jobhunter.company_batch import run, response_schema
from jobhunter.remote_llm import digest, inputs, request as request_remote, validate


class BatchTests(unittest.TestCase):
    """Verify single-call accounting, resume, and atomic rejection."""

    def test_schema_is_identical_across_calls(self):
        """Un prefisso stabile e' la condizione perche' la cache del provider possa agganciare."""
        cfg = json.loads(Path('config/remote_llm.json').read_text())
        company = json.loads(Path(cfg['response_schemas']['company-summary']).read_text())
        self.assertEqual(json.dumps(response_schema(cfg, company), sort_keys=True),
                         json.dumps(response_schema(cfg, company), sort_keys=True))
        schema = response_schema(cfg, company)
        # Nessun ID di annuncio e nessun enum di citazioni: quelli li verificano apply() e validate().
        self.assertEqual(schema['properties']['jobs']['items'], {'$ref': '#/$defs/role'})
        self.assertNotIn('enum', schema['$defs']['selection']['properties']['evidence']['items'])
        self.assertEqual(schema['$defs']['role']['required'], ['id', 'selection', 'summary'])

    def test_batch_resume_and_invalid_ids(self):
        """Two roles share one paid request; invalid responses persist usage but no derivatives."""
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': 'Analyst', 'source_url': 'https://example.org/'+str(i), 'description': 'Build models.'} for i in range(2)], 'test')
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original = json.loads
                def config_read(value, *args, **kwargs):
                    """Redirect reports to the disposable test directory."""
                    result = original(value, *args, **kwargs)
                    return cfg if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY' else result
                def response(config, prompt, payload, key):
                    """Exclude each role with a valid scoped source reference."""
                    return {'company': {'summary': 'Unrequested and never stored'}, 'jobs': [{'id': oid, 'selection': {'decision': 'exclude', 'rationale': 'Test', 'evidence': ['S0'], 'missing_information': []}, 'summary': None} for oid in payload['jobs']]}, {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}, []
                values = {'all_companies': True, 'mode': 'execute'}
                with patch('json.loads', side_effect=config_read), patch('jobhunter.remote_llm.api_key', return_value='test'), patch('jobhunter.remote_llm.request', side_effect=response) as request, patch('jobhunter.company_batch.time.sleep'):
                    first = run(arc, values, lambda d: None)
                    self.assertEqual(request.call_count, 1)
                    self.assertEqual(first['counts']['evaluated_jobs'], 2)
                    self.assertEqual(first['counts']['ignored_company_summaries'], 1)
                    self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:company-summary'").fetchone()[0], 0)
                    self.assertEqual(first['counts']['submitted_jobs'], 2)
                    self.assertEqual(first['counts']['api_companies'], 1)
                    self.assertEqual(first['usage']['total_tokens'], 120)
                    run(arc, values, lambda d: None)
                    self.assertEqual(request.call_count, 1)
                    arc.db.execute('DELETE FROM enrichments'); arc.db.commit()
                    request.side_effect = None
                    request.return_value = ({'company': None, 'jobs': []}, {'total_tokens': 99}, [])
                    failed = run(arc, values, lambda d: None)
                    self.assertEqual(failed['status'], 'partial')
                    self.assertEqual(failed['usage']['total_tokens'], 99)
                    self.assertEqual(failed['counts']['submitted_jobs'], 2)
                    self.assertEqual(failed['counts']['evaluated_jobs'], 0)
                    self.assertEqual(failed['counts']['rejected_companies'], 1)
                    saved = original(Path(failed['report_path']).read_text(encoding='utf-8'))
                    self.assertEqual(saved['items'][0]['rejected_answer'], {'company': None, 'jobs': []})
                    self.assertEqual(arc.db.execute('SELECT count(*) FROM enrichments').fetchone()[0], 0)
                    def retained(config, prompt, payload, key):
                        """Return a review decision and a grounded structured role summary."""
                        empty = {'summary': '', 'facts': [], 'missing_information': ['Dati insufficienti']}
                        company = {**empty, 'category': 'Da classificare'} if payload['company_requested'] else None
                        return {'company': company, 'jobs': [{'id': oid, 'selection': {'decision': 'review', 'rationale': 'Da verificare', 'evidence': [], 'missing_information': ['Dettagli']}, 'summary': empty} for oid in payload['jobs']]}, {'total_tokens': 130}, []
                    request.side_effect = retained
                    kept = run(arc, values, lambda d: None)
                    self.assertEqual(kept['status'], 'success')
                    self.assertEqual(kept['counts']['review_jobs'], 2)
                    before = request.call_count
                    run(arc, values, lambda d: None)
                    self.assertEqual(request.call_count, before)
                    for row in arc.db.execute('SELECT id,data FROM opportunities').fetchall():
                        data = json.loads(row['data']); data['description'] = ''
                        arc.db.execute('UPDATE opportunities SET data=?,content_hash=? WHERE id=?', (json.dumps(data), 'no-description', row['id']))
                    arc.db.commit()
                    # Senza descrizione non si paga nessuna chiamata: il modello direbbe solo «review».
                    def never_called(config, prompt, payload, key):
                        raise AssertionError('Un annuncio senza descrizione non deve essere spedito')
                    request.side_effect = never_called
                    before = request.call_count
                    result = run(arc, values, lambda d: None)
                    self.assertEqual(result['status'], 'success')
                    self.assertEqual(result['counts']['evaluated_jobs'], 0)
                    self.assertEqual(request.call_count, before)


            finally:
                arc.close()
    def test_one_unusable_part_does_not_discard_the_billed_rest(self):
        """A missing company card or a bad role summary must not throw away the other grounded derivatives."""
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'description': 'Build models.',
                             'source_url': 'https://example.org/'+str(i)} for i in range(2)], 'test')
                with arc.db:
                    arc.db.execute("UPDATE companies SET description='Costruisce modelli di rete elettrica.'")
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original = json.loads
                def config_read(value, *args, **kwargs):
                    """Redirect reports to the disposable test directory."""
                    result = original(value, *args, **kwargs)
                    return cfg if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY' else result
                def partial(config, prompt, payload, key):
                    """Invent a quote in the first summary and omit the requested company card."""
                    ids = list(payload['jobs'])
                    empty = {'summary': '', 'facts': [], 'missing_information': ['Dati insufficienti']}
                    invented = {'summary': 'Inventata', 'facts': [{'field': 'responsibilities', 'text': 'x', 'quote': 'S99'}], 'missing_information': []}
                    jobs = [{'id': oid, 'selection': None, 'summary': invented if oid == ids[0] else empty} for oid in ids]
                    return {'company': None, 'jobs': jobs}, {'total_tokens': 200}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.remote_llm.api_key', return_value='k'), \
                     patch('jobhunter.remote_llm.request', side_effect=partial), patch('jobhunter.company_batch.time.sleep'):
                    result = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertEqual(result['status'], 'partial')
                # I ruoli sono già decisi dal regex: si paga solo la scheda, non un secondo giudizio.
                self.assertEqual(result['counts']['submitted_jobs'], 0)
                self.assertEqual(result['counts']['summary_requests'], 2)
                # La scheda valida sopravvive; la citazione inventata e la scheda azienda assente no.
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:selection'").fetchone()[0], 0)
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
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': 'Data Scientist',
                             'description': 'Build models.', 'source_url': 'https://example.org/'+str(i)}
                            for i in range(2)], 'test')
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original = json.loads
                def config_read(value, *args, **kwargs):
                    """Keep report writes inside the temporary directory."""
                    result = original(value, *args, **kwargs)
                    return cfg if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY' else result
                submitted = []
                def response(config, prompt, payload, key):
                    """Omit one requested summary and provide its sibling with grounded facts."""
                    ids = list(payload['jobs'])
                    submitted.append(ids)
                    return {'company': None, 'jobs': [
                        {'id': oid, 'selection': None, 'summary': None if i == 0 else {
                            'summary': 'Sviluppa modelli.', 'facts': [{'field': 'responsibilities',
                            'text': 'Sviluppa modelli.', 'quote': next(iter(payload['jobs'][oid]['evidence_catalog']))}],
                            'missing_information': []}} for i, oid in enumerate(ids)]}, {'total_tokens': 20}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.remote_llm.api_key', return_value='dummy'), \
                     patch('jobhunter.remote_llm.request', side_effect=response), patch('jobhunter.company_batch.time.sleep'):
                    report = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                    self.assertEqual(report['status'], 'partial')
                    self.assertEqual(report['counts']['rejected_jobs'], 1)
                    saved = original(Path(report['report_path']).read_text(encoding='utf-8'))
                    self.assertEqual(saved['items'][0]['rejected'][0]['error'], 'Job summary requested but not returned')
                    self.assertEqual(saved['usage']['total_tokens'], 20)
                    self.assertEqual(arc.db.execute("SELECT record_id FROM enrichments WHERE task='remote:job-summary'").fetchone()[0], submitted[0][1])
                    run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                    self.assertEqual(submitted[1], [submitted[0][0]])
            finally:
                arc.close()

    def test_each_role_gets_its_own_reference_space(self):
        """Two roles in one request must not be able to cite each other's excerpts."""
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': 'Analyst', 'description': 'Alpha only.', 'source_url': 'https://example.org/a'},
                            {'company_name': 'Example', 'title': 'Reviewer', 'description': 'Beta only.', 'source_url': 'https://example.org/b'}], 'test')
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original = json.loads
                def config_read(value, *args, **kwargs):
                    """Redirect reports to the disposable test directory."""
                    result = original(value, *args, **kwargs)
                    return cfg if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY' else result
                seen = {}
                def crossed(config, prompt, payload, key):
                    """Cite the second role's namespace while judging the first one."""
                    seen.update(payload['jobs'])
                    ids = list(payload['jobs'])
                    return {'company': None, 'jobs': [
                        {'id': ids[0], 'selection': {'decision': 'keep', 'rationale': 'x', 'evidence': ['J1-S0'], 'missing_information': []}, 'summary': None},
                        {'id': ids[1], 'selection': {'decision': 'keep', 'rationale': 'x', 'evidence': ['J1-S0'], 'missing_information': []}, 'summary': None}]}, {'total_tokens': 10}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.remote_llm.api_key', return_value='k'), \
                     patch('jobhunter.remote_llm.request', side_effect=crossed), patch('jobhunter.company_batch.time.sleep'):
                    result = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertTrue(all(ref.startswith('J') for job in seen.values() for ref in job['evidence_catalog']))
                self.assertEqual(result['counts']['evaluated_jobs'], 1)
                self.assertEqual(result['counts']['rejected_jobs'], 1)
            finally:
                arc.close()

    def test_local_exclusions_skip_the_call_except_for_a_bounded_audit(self):
        """Locally excluded roles must not be paid for, beyond the explicit audit allowance."""
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': 'Analyst', 'source_url': 'https://example.org/'+str(i), 'description': 'Build models.'} for i in range(4)], 'test')
                ids = [r[0] for r in arc.db.execute('SELECT id FROM opportunities')]
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original = json.loads
                def config_read(value, *args, **kwargs):
                    """Redirect reports to the disposable test directory and cap the audit at one role."""
                    result = original(value, *args, **kwargs)
                    if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY':
                        return cfg
                    if isinstance(result, dict) and 'remote_audit_excluded' in result:
                        return {**result, 'remote_audit_excluded': 1}
                    return result
                def response(config, prompt, payload, key):
                    """Judge exactly the roles the caller chose to submit."""
                    return {'company': None, 'jobs': [{'id': oid, 'selection': {'decision': 'exclude', 'rationale': 'Test', 'evidence': [oid[:2] + '-S0'], 'missing_information': []}, 'summary': None} for oid in payload['jobs']]}, {'total_tokens': 10}, []
                with patch('json.loads', side_effect=config_read), patch.object(arc, 'evaluations', return_value={oid: {'status': 'excluded', 'reasons': ['seniority'], 'career_priority': 'secondary'} for oid in ids}),                      patch('jobhunter.remote_llm.api_key', return_value='test'), patch('jobhunter.remote_llm.request', side_effect=response) as request,                      patch('jobhunter.company_batch.time.sleep'):
                    report = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(report['counts']['locally_decided'], 3)
                self.assertEqual(report['counts']['submitted_jobs'], 1)
            finally:
                arc.close()

    def test_company_card_does_not_depend_on_surviving_roles(self):
        """DESIGN §2: i due assi sono indipendenti. Nessun ruolo sopravvive, la scheda azienda si chiede lo stesso."""
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': 'Senior Engineer', 'description': 'Lead the team.',
                             'source_url': 'https://example.org/'+str(i)} for i in range(2)], 'test')
                with arc.db:
                    arc.db.execute("UPDATE companies SET description='Costruisce modelli di rete elettrica.'")
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original = json.loads
                def config_read(value, *args, **kwargs):
                    """Redirect reports to the disposable test directory and disable the audit sample."""
                    result = original(value, *args, **kwargs)
                    if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY':
                        return cfg
                    if isinstance(result, dict) and 'remote_audit_excluded' in result:
                        return {**result, 'remote_audit_excluded': 0}
                    return result
                def card_only(config, prompt, payload, key):
                    """Return only the company card; no role was submitted for judgement."""
                    self.assertEqual(payload['jobs'], {})
                    self.assertTrue(payload['company_requested'])
                    return {'company': {'summary': 'Costruisce modelli di rete elettrica.', 'category': 'Energia',
                                        'facts': [{'section': 'business', 'text': 'Modelli di rete', 'quote': 'S0'}],
                                        'missing_information': []}, 'jobs': []}, {'total_tokens': 40}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.remote_llm.api_key', return_value='k'),                      patch('jobhunter.remote_llm.request', side_effect=card_only) as request, patch('jobhunter.company_batch.time.sleep'):
                    result = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(result['counts']['submitted_jobs'], 0)
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:company-summary'").fetchone()[0], 1)
                self.assertEqual(arc.db.execute('SELECT category FROM categories').fetchone()[0], 'Energia')
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

        with patch('jobhunter.remote_llm.urllib.request.build_opener') as build:
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
                arc.ingest([{'company_name': 'Unknown', 'title': 'Data Scientist',
                             'source_url': 'https://example.org/empty'}], 'test')
                cid = arc.db.execute('SELECT id FROM companies').fetchone()[0]
                payload = inputs(arc, 'company-summary', cid, cfg)
                self.assertEqual(payload['source']['evidence_catalog'], {})
                quote = payload['response_schema']['properties']['facts']['items']['properties']['quote']
                self.assertNotIn('enum', quote)
                schema = response_schema(cfg, payload['response_schema'])
                self.assertIn({'type': 'null'}, schema['properties']['company']['anyOf'])
                with self.assertRaisesRegex(ValueError, 'Unknown source evidence'):
                    validate('company-summary', {'summary': 'Made up', 'category': 'Da classificare',
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
                arc.ingest([{'company_name': 'Company '+str(i), 'title': 'Analyst', 'description': 'Build models.',
                             'source_url': 'https://example.org/'+str(i)} for i in range(5)], 'test')
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original = json.loads
                def config_read(value, *args, **kwargs):
                    """Redirect reports to the disposable test directory."""
                    result = original(value, *args, **kwargs)
                    return cfg if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY' else result
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
                    empty = {'summary': '', 'facts': [], 'missing_information': ['Dati insufficienti']}
                    company = {**empty, 'category': 'Da classificare'} if payload['company_requested'] else None
                    return ({'company': company, 'jobs': [{'id': oid, 'selection': {'decision': 'review', 'rationale': 'Da verificare', 'evidence': [], 'missing_information': []}, 'summary': None} for oid in payload['jobs']]},
                            {'total_tokens': 10}, [])
                values = {'all_companies': True, 'mode': 'execute', 'workers': 2}
                with patch('json.loads', side_effect=config_read), patch('jobhunter.remote_llm.api_key', return_value='test'), \
                     patch('jobhunter.remote_llm.request', side_effect=response), patch('jobhunter.company_batch.time.sleep'):
                    report = run(arc, values, lambda d: None)
                self.assertTrue(released.is_set())
                self.assertEqual(report['status'], 'success')
                self.assertEqual(report['counts']['evaluated_jobs'], 5)
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

    def test_a_null_judgement_rejects_one_role_and_never_aborts_a_paid_run(self):
        """Visto in produzione: `selection: null` su un ruolo richiesto fermava tutta la run.

        Le risposte gia' pagate e ancora in volo venivano buttate via insieme all'eccezione. Una
        forma inattesa nella risposta del provider deve scartare *quel* pezzo e basta.
        """
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Company '+str(i), 'title': 'Analyst', 'description': 'Build models.',
                             'source_url': 'https://example.org/'+str(i)} for i in range(4)], 'test')
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original = json.loads
                def config_read(value, *args, **kwargs):
                    """Redirect reports to the disposable test directory."""
                    result = original(value, *args, **kwargs)
                    return cfg if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY' else result
                answered = []
                def response(config, prompt, payload, key):
                    """Null the judgement of the first company only; the others answer normally."""
                    ids = sorted(payload['jobs'])
                    answered.append(ids[0])
                    null = len(answered) == 1
                    empty = {'summary': '', 'facts': [], 'missing_information': ['Dati insufficienti']}
                    company = {**empty, 'category': 'Da classificare'} if payload['company_requested'] else None
                    return ({'company': company, 'jobs': [{'id': oid, 'selection': None if null else {'decision': 'review', 'rationale': 'Da verificare', 'evidence': [], 'missing_information': []}, 'summary': None} for oid in ids]},
                            {'total_tokens': 10}, [])
                with patch('json.loads', side_effect=config_read), patch('jobhunter.remote_llm.api_key', return_value='k'), \
                     patch('jobhunter.remote_llm.request', side_effect=response), patch('jobhunter.company_batch.time.sleep'):
                    report = run(arc, {'all_companies': True, 'mode': 'execute', 'workers': 1}, lambda d: None)
                self.assertEqual(report['status'], 'partial')
                self.assertEqual(report['counts']['api_calls'], 4)
                self.assertEqual(report['counts']['evaluated_jobs'], 3)
                self.assertEqual(report['counts']['rejected_jobs'], 1)
                self.assertEqual(report['counts']['rejected_companies'], 0)
                self.assertEqual([r['error'] for item in report['items'] for r in item.get('rejected', [])],
                                 ['Selection requested but not returned'])
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:selection'").fetchone()[0], 3)
            finally:
                arc.close()
