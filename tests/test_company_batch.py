"""Check company batching without contacting a paid provider."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from jobhunter.workspace import Archive
from jobhunter.company_batch import run, response_schema
from jobhunter.remote_llm import inputs, validate


class BatchTests(unittest.TestCase):
    """Verify single-call accounting, resume, and atomic rejection."""

    def test_shared_schema_does_not_repeat_role_contract(self):
        """Adding roles must add only IDs/references, while strict object shapes remain enforced."""
        cfg = json.loads(Path('config/remote_llm.json').read_text())
        company = json.loads(Path(cfg['response_schemas']['company-summary']).read_text())
        one = response_schema(cfg, ['a'], company)
        many = response_schema(cfg, [str(i) for i in range(20)], company)
        self.assertLess(len(json.dumps(many)), len(json.dumps(one))*2)
        self.assertEqual(many['properties']['jobs']['required'], [str(i) for i in range(20)])
        self.assertFalse(many['properties']['jobs']['additionalProperties'])
        self.assertEqual(many['properties']['jobs']['properties']['0'], {'$ref': '#/$defs/role'})

    def test_batch_resume_and_invalid_ids(self):
        """Two roles share one paid request; invalid responses persist usage but no derivatives."""
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'source_url': 'https://example.org/'+str(i), 'description': 'Build models.'} for i in range(2)], 'test')
                cfg = json.loads(Path('config/remote_llm.json').read_text())
                cfg['output_directory'] = folder
                original = json.loads
                def config_read(value, *args, **kwargs):
                    """Redirect reports to the disposable test directory."""
                    result = original(value, *args, **kwargs)
                    return cfg if isinstance(result, dict) and result.get('api_key_env') == 'JOBHUNTER_API_KEY' else result
                def response(config, prompt, payload, key):
                    """Exclude each role with a valid scoped source reference."""
                    return {'company': {'summary': 'Unrequested and never stored'}, 'jobs': {oid: {'selection': {'decision': 'exclude', 'rationale': 'Test', 'evidence': ['S0'], 'missing_information': []}, 'summary': None} for oid in payload['jobs']}}, {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}, []
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
                    request.return_value = ({'company': None, 'jobs': {}}, {'total_tokens': 99}, [])
                    failed = run(arc, values, lambda d: None)
                    self.assertEqual(failed['status'], 'partial')
                    self.assertEqual(failed['usage']['total_tokens'], 99)
                    self.assertEqual(failed['counts']['submitted_jobs'], 2)
                    self.assertEqual(failed['counts']['evaluated_jobs'], 0)
                    self.assertEqual(failed['counts']['rejected_companies'], 1)
                    self.assertEqual(arc.db.execute('SELECT count(*) FROM enrichments').fetchone()[0], 0)
                    def retained(config, prompt, payload, key):
                        """Return a review decision and a grounded structured role summary."""
                        empty = {'summary': '', 'facts': [], 'missing_information': ['Dati insufficienti']}
                        company = {**empty, 'category': 'Da classificare'} if payload['company_requested'] else None
                        return {'company': company, 'jobs': {oid: {'selection': {'decision': 'review', 'rationale': 'Da verificare', 'evidence': [], 'missing_information': ['Dettagli']}, 'summary': empty} for oid in payload['jobs']}}, {'total_tokens': 130}, []
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
                    def selection_only(config, prompt, payload, key):
                        """Absent descriptions permit judgments but never request an invented summary."""
                        self.assertTrue(all(not job['summary_requested'] for job in payload['jobs'].values()))
                        return {'company': None, 'jobs': {oid: {'selection': {'decision': 'review', 'rationale': 'Manca descrizione', 'evidence': [], 'missing_information': ['Descrizione']}, 'summary': None} for oid in payload['jobs']}}, {'total_tokens': 50}, []
                    request.side_effect = selection_only
                    result = run(arc, values, lambda d: None)
                    self.assertEqual(result['status'], 'success')
                    self.assertEqual(result['counts']['evaluated_jobs'], 2)
                    before = request.call_count
                    run(arc, values, lambda d: None)
                    self.assertEqual(request.call_count, before)


            finally:
                arc.close()
    def test_one_unusable_part_does_not_discard_the_billed_rest(self):
        """A missing company card or a bad role summary must not throw away grounded role decisions."""
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
                    """Keep both roles, invent a quote in the first summary and omit the requested company card."""
                    ids = list(payload['jobs'])
                    empty = {'summary': '', 'facts': [], 'missing_information': ['Dati insufficienti']}
                    invented = {'summary': 'Inventata', 'facts': [{'field': 'responsibilities', 'text': 'x', 'quote': 'S99'}], 'missing_information': []}
                    jobs = {oid: {'selection': {'decision': 'keep', 'rationale': 'Pertinente', 'evidence': ['S0'], 'missing_information': []},
                                  'summary': invented if oid == ids[0] else empty} for oid in ids}
                    return {'company': None, 'jobs': jobs}, {'total_tokens': 200}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.remote_llm.api_key', return_value='k'), \
                     patch('jobhunter.remote_llm.request', side_effect=partial), patch('jobhunter.company_batch.time.sleep'):
                    result = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['counts']['evaluated_jobs'], 2)
                self.assertEqual(result['counts']['kept_jobs'], 2)
                # Both selections and the one valid summary survive; the invented quote and the absent card do not.
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:selection'").fetchone()[0], 2)
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:job-summary'").fetchone()[0], 1)
                self.assertEqual(arc.db.execute("SELECT count(*) FROM enrichments WHERE task='remote:company-summary'").fetchone()[0], 0)
                errors = [r['error'] for item in result['items'] for r in item.get('rejected', [])]
                self.assertEqual(len(errors), 2)
            finally:
                arc.close()

    def test_each_role_gets_its_own_reference_space(self):
        """Two roles in one request must not be able to cite each other's excerpts."""
        with tempfile.TemporaryDirectory() as folder:
            arc = Archive(Path(folder)/'db.sqlite3')
            try:
                arc.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'description': 'Alpha only.', 'source_url': 'https://example.org/a'},
                            {'company_name': 'Example', 'title': 'ML Engineer', 'description': 'Beta only.', 'source_url': 'https://example.org/b'}], 'test')
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
                    return {'company': None, 'jobs': {
                        ids[0]: {'selection': {'decision': 'keep', 'rationale': 'x', 'evidence': ['J1-S0'], 'missing_information': []}, 'summary': None},
                        ids[1]: {'selection': {'decision': 'keep', 'rationale': 'x', 'evidence': ['J1-S0'], 'missing_information': []}, 'summary': None}}}, {'total_tokens': 10}, []
                with patch('json.loads', side_effect=config_read), patch('jobhunter.remote_llm.api_key', return_value='k'), \
                     patch('jobhunter.remote_llm.request', side_effect=crossed), patch('jobhunter.company_batch.time.sleep'):
                    result = run(arc, {'all_companies': True, 'mode': 'execute'}, lambda d: None)
                self.assertTrue(all(ref.startswith('J') for job in seen.values() for ref in job['evidence_catalog']))
                self.assertEqual(result['counts']['evaluated_jobs'], 1)
                self.assertEqual(result['counts']['rejected_jobs'], 1)
            finally:
                arc.close()

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
                schema = response_schema(cfg, ['job'], payload['response_schema'], company_requested=False)
                self.assertEqual(schema['properties']['company'], {'type': 'null'})
                with self.assertRaisesRegex(ValueError, 'Unknown source evidence'):
                    validate('company-summary', {'summary': 'Made up', 'category': 'Da classificare',
                        'facts': [{'section': 'business', 'text': 'Made up', 'quote': 'S0'}],
                        'missing_information': []}, payload['source'])
            finally:
                arc.close()
