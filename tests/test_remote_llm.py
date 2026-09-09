"""Remote integration checks without network calls, credentials or paid inference."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from jobhunter import remote_llm
from jobhunter.workspace import Archive, ROOT


class RemoteTests(unittest.TestCase):
    """Check grounding, cache invalidation, failure stops and raw-data preservation."""

    def test_alibaba_endpoint_and_json_parameters(self):
        """Require a workspace before HTTP and send Alibaba JSON mode without tools."""
        cfg = json.loads((ROOT/'config/remote_llm.json').read_text())
        cfg['endpoint'] = 'https://{WorkspaceId}.eu-central-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions'
        with patch('jobhunter.remote_llm.urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'workspace_id'):
                remote_llm.request(cfg, 'JSON', {}, 'dummy')
            opener.assert_not_called()
            cfg['workspace_id'] = 'test-workspace'
            cfg['evidence_reference_tasks'] = []
            cfg['response_schemas'] = {}
            cfg.pop('field_sections_path', None)
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}).encode()
            remote_llm.request(cfg, 'JSON', {}, 'dummy')
            req = opener.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, 'https://test-workspace.eu-central-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions')
            body = json.loads(req.data)
            self.assertFalse(body['enable_thinking'])
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            self.assertNotIn('tools', body)

    def test_validation_rejects_invented_evidence(self):
        """A valid JSON shape alone must not authorize a decision."""
        answer = {'decision': 'exclude', 'rationale': 'Experience', 'evidence': ['five years'], 'missing_information': []}
        with self.assertRaises(ValueError):
            remote_llm.validate('selection', answer, {'title': 'Scientist', 'description': 'No experience required'})
        answer.update(decision='review', evidence=[])
        remote_llm.validate('selection', answer, {'title': 'Scientist', 'description': ''})

    def test_source_references_expand_and_unknown_ids_fail(self):
        """Recover original text from IDs while preserving negations and rejecting inventions."""
        source = {'title': 'Scientist', 'description': 'No visa sponsorship.',
                  'evidence_catalog': {'S0': 'No visa sponsorship.'}}
        answer = {'decision': 'review', 'rationale': 'Check visa', 'evidence': ['S0'], 'missing_information': []}
        result = remote_llm.validate('selection', answer, source)
        self.assertEqual(result['evidence'], ['No visa sponsorship.'])
        self.assertEqual(answer['evidence'], ['S0'])
        answer['evidence'] = ['S99']
        with self.assertRaisesRegex(ValueError, 'Unknown source'):
            remote_llm.validate('selection', answer, source)
        summary = {'summary': 'Nessun visto.', 'facts': [{'section': 'conditions', 'text': 'Nessun visto', 'quote': 'S0'}], 'fields': {'visa': [0], 'languages': []}, 'missing_information': []}
        result = remote_llm.validate('job-summary', summary, source, {'visa': '', 'languages': ''})
        self.assertIsNone(result['fields']['languages'])
        self.assertEqual(result['facts'][0]['quote'], 'No visa sponsorship.')

    def test_fact_fields_build_indices_without_model_numbering(self):
        """Derive UI sections and indices deterministically from each fact's field."""
        source = {'title': 'Scientist', 'description': 'English required. Python preferred.',
                  'evidence_catalog': {'S0': 'English required.', 'S1': 'Python preferred.'},
                  'field_sections': {'languages': 'requirements', 'preferred_skills': 'requirements'}}
        answer = {'summary': 'Requisiti.', 'facts': [
            {'field': 'preferred_skills', 'text': 'Python preferenziale', 'quote': 'S1'},
            {'field': 'languages', 'text': 'Inglese obbligatorio', 'quote': 'S0'}], 'missing_information': []}
        result = remote_llm.validate('job-summary', answer, source, source['field_sections'])
        self.assertEqual(result['fields'], {'languages': [1], 'preferred_skills': [0]})
        self.assertEqual(result['facts'][1]['quote'], 'English required.')
        answer['facts'][0]['field'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'Invalid fact field'):
            remote_llm.validate('job-summary', answer, source, source['field_sections'])

    def test_company_category_and_summary_share_call_and_preserve_chat(self):
        """Save a grounded category with the summary without overwriting a user's category."""
        cfg = json.loads((ROOT/'config/remote_llm.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg.update(output_directory=str(root/'reports'), request_delay_seconds=0)
            path = root/'remote.json'
            path.write_text(json.dumps(cfg))
            archive = Archive(root/'archive.db')
            archive.ingest([{'company_name': 'Solar', 'title': 'Scientist', 'source_url': 'https://example.org/1'}], 'test')
            cid = archive.db.execute('SELECT id FROM companies').fetchone()[0]
            archive.db.execute('UPDATE companies SET description=?', ('We produce solar panels.',))
            archive.db.commit()
            payload = remote_llm.inputs(archive, 'company-summary', cid, cfg)
            ref = next(k for k,v in payload['source']['evidence_catalog'].items() if 'solar panels' in v)
            answer = {'summary': 'Produce pannelli solari.', 'category': 'Energia', 'facts': [{'section': 'business', 'text': 'Pannelli solari', 'quote': ref}], 'missing_information': []}
            with patch('jobhunter.remote_llm.api_key', return_value='dummy'), patch('jobhunter.remote_llm.request', return_value=(answer, {}, [])) as request:
                self.assertEqual(remote_llm.run(archive, 'company-summary', execute=True, config_path=path)['processed'], 1)
                request.assert_called_once()
                self.assertEqual(archive.category(cid)['category'], 'Energia')
                archive.categorize(cid, 'Industria e materiali', 'User correction')
                cfg['model'] = 'changed-model'
                path.write_text(json.dumps(cfg))
                self.assertEqual(remote_llm.run(archive, 'company-summary', execute=True, config_path=path)['processed'], 1)
                self.assertEqual(archive.category(cid)['category'], 'Industria e materiali')
            archive.close()

    def test_wire_schema_constrains_evidence_and_omits_duplicate_source(self):
        """Send strict schema and one copy of the source text in referenced mode."""
        cfg = json.loads((ROOT/'config/remote_llm.json').read_text())
        payload = {'source': {'description': 'Original text', 'evidence_catalog': {'S0': 'Original text'}},
                   'response_schema': {'type': 'object', 'properties': {}}}
        with patch('jobhunter.remote_llm.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}).encode()
            remote_llm.request(cfg, 'JSON', payload, 'dummy')
            body = json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertTrue(body['response_format']['json_schema']['strict'])
            wire = json.loads(body['messages'][1]['content'])
            self.assertNotIn('response_schema', wire)
            self.assertNotIn('description', wire['source'])
            self.assertEqual(wire['source']['evidence_catalog'], {'S0': 'Original text'})

    def test_summary_requires_source_quotes(self):
        """Preserve source constraints and reject unsupported summaries."""
        answer = {'summary': 'Visto non sponsorizzato.', 'facts': [{'section': 'conditions', 'text': 'Nessuna sponsorizzazione', 'quote': 'No visa sponsorship'}], 'missing_information': []}
        remote_llm.validate('job-summary', answer, {'title': 'Engineer', 'description': 'No visa sponsorship'})
        answer['facts'][0]['quote'] = 'Visa sponsorship available'
        with self.assertRaises(ValueError):
            remote_llm.validate('job-summary', answer, {'title': 'Engineer', 'description': 'No visa sponsorship'})

    def test_transport_redacts_http_errors_and_rejects_truncation(self):
        """A provider error must not leak response bodies or reuse incomplete output."""
        cfg = json.loads((ROOT/'config/remote_llm.json').read_text())
        cfg['workspace_id'] = 'test-workspace'
        cfg['evidence_reference_tasks'] = []
        cfg['response_schemas'] = {}
        cfg.pop('field_sections_path', None)
        with patch('jobhunter.remote_llm.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError(cfg['endpoint'], 429, 'SECRET', {}, io.BytesIO(b'SECRET'))
            with self.assertRaisesRegex(ValueError, '^Remote HTTP 429;') as error:
                remote_llm.request(cfg, 'prompt', {}, 'SECRET')
            self.assertNotIn('SECRET', str(error.exception))
            opener.return_value.open.side_effect = None
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]}).encode()
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                remote_llm.request(cfg, 'prompt', {}, 'SECRET')

    def test_preview_cache_and_failure_do_not_change_originals(self):
        """Preview is offline; completed results resume and invalidate on source/profile/model changes."""
        cfg = json.loads((ROOT/'config/remote_llm.json').read_text())
        cfg['workspace_id'] = 'test-workspace'
        cfg['evidence_reference_tasks'] = []
        cfg['response_schemas'] = {}
        cfg.pop('field_sections_path', None)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg.update(profile_path=str(root/'profile.md'), output_directory=str(root/'outputs'), request_delay_seconds=0)
            cfg['prompts'] = {k: str(ROOT/v) for k,v in cfg['prompts'].items()}
            (root/'profile.md').write_text('Data science preferred')
            config_path = root/'config.json'
            config_path.write_text(json.dumps(cfg))
            archive = Archive(root/'archive.db')
            archive.ingest([{'company_name': 'Demo', 'title': 'Data Scientist', 'source_url': 'https://example.org/job', 'description': 'Build models. No visa sponsorship.'}], 'test')
            oid = archive.db.execute('SELECT id FROM opportunities').fetchone()[0]
            original = archive.db.execute('SELECT data FROM opportunities').fetchone()[0]
            answer = {'decision': 'keep', 'rationale': 'Models', 'evidence': ['Build models.'], 'missing_information': []}
            with patch('jobhunter.remote_llm.api_key', return_value='SECRET') as key, patch('jobhunter.remote_llm.request', return_value=(answer, {'total_tokens': 10}, [])) as model:
                preview = remote_llm.run(archive, 'selection', config_path=config_path)
                self.assertEqual(preview['selected'], 1)
                model.assert_not_called()
                key.assert_not_called()
                first = remote_llm.run(archive, 'selection', execute=True, config_path=config_path)
                self.assertEqual(first['processed'], 1)
                second = remote_llm.run(archive, 'selection', execute=True, config_path=config_path)
                self.assertEqual(second['cached'], 1)
                self.assertEqual(model.call_count, 1)
                (root/'profile.md').write_text('Updated preference')
                self.assertEqual(remote_llm.run(archive, 'selection', execute=True, config_path=config_path)['processed'], 1)
                cfg['model'] = 'different-model'
                config_path.write_text(json.dumps(cfg))
                self.assertEqual(remote_llm.run(archive, 'selection', config_path=config_path)['selected'], 1)
                model.side_effect = ValueError('Remote HTTP 429; stopped')
                failed = remote_llm.run(archive, 'selection', execute=True, config_path=config_path)
                self.assertEqual(failed['status'], 'partial')
                self.assertEqual(failed['processed'], 0)
                self.assertNotIn('SECRET', json.dumps(failed))
            self.assertEqual(original, archive.db.execute('SELECT data FROM opportunities').fetchone()[0])
            self.assertEqual(archive.db.execute('SELECT count(*) FROM feedback').fetchone()[0], 0)
            archive.close()

    def test_structured_fields_and_web_provenance(self):
        """Null is valid; bad indices and quotes attributed to another URL are rejected."""
        source = {'title': 'Scientist', 'description': 'Python preferred'}
        answer = {'summary': 'Modelli', 'facts': [{'section': 'requirements', 'text': 'Python preferenziale', 'quote': 'Python preferred'}], 'fields': {'required': None, 'preferred': [0]}, 'missing_information': []}
        remote_llm.validate('job-summary', answer, source, {'required': '', 'preferred': ''})
        answer['fields']['preferred'] = [1]
        with self.assertRaises(ValueError):
            remote_llm.validate('job-summary', answer, source, {'required': '', 'preferred': ''})
        citation = {'type': 'url_citation', 'url_citation': {'url': 'https://example.org', 'content': 'We model energy.'}}
        result = {'summary': 'Modelli energetici.', 'facts': [{'section': 'business', 'text': 'Modelli energetici', 'quote': 'We model energy.', 'url': 'https://example.org'}], 'missing_information': []}
        remote_llm.validate('company-research', result, {'facts': []}, annotations=[citation])
        result['facts'][0]['url'] = 'https://wrong.org'
        with self.assertRaises(ValueError):
            remote_llm.validate('company-research', result, {'facts': []}, annotations=[citation])

    def test_openrouter_search_is_explicit_and_bounded(self):
        """Ordinary tasks have no tools; research sends only the bounded server tool."""
        cfg = json.loads((ROOT/'config/remote_llm.json').read_text())
        cfg['workspace_id'] = 'test-workspace'
        cfg['evidence_reference_tasks'] = []
        cfg['response_schemas'] = {}
        cfg.pop('field_sections_path', None)
        cfg.update(provider='openrouter', endpoint='https://openrouter.ai/api/v1/chat/completions', model='z-ai/glm-5.3-flash', parameters={})
        cfg['web_search']['enabled'] = True
        with patch('jobhunter.remote_llm.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}', 'annotations': []}}]}).encode()
            remote_llm.request(cfg, 'p', {}, 'dummy')
            sent = json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertNotIn('tools', sent)
            self.assertEqual(sent['model'], 'z-ai/glm-5.3-flash')
            remote_llm.request({**cfg, 'research_request': True}, 'p', {}, 'dummy')
            sent = json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertEqual(sent['tools'][0]['type'], 'openrouter:web_search')
            self.assertEqual(sent['tools'][0]['parameters']['max_uses'], 1)
            self.assertNotIn('thinking', sent)

    def test_research_cache_display_and_duplicate_hints(self):
        """A research result survives restart; distinct roles remain and stale output hides."""
        cfg = json.loads((ROOT/'config/remote_llm.json').read_text())
        cfg['workspace_id'] = 'test-workspace'
        cfg['evidence_reference_tasks'] = []
        cfg['response_schemas'] = {}
        cfg.pop('field_sections_path', None)
        cfg.update(provider='openrouter', endpoint='https://openrouter.ai/api/v1/chat/completions', model='z-ai/glm-5.3-flash', parameters={})
        cfg['web_search']['enabled'] = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg.update(output_directory=str(root/'outputs'), request_delay_seconds=0)
            config_path = root/'config.json'
            config_path.write_text(json.dumps(cfg))
            archive = Archive(root/'archive.db')
            text = 'Build numerical models with Python. ' * 10
            archive.ingest([{'company_name': 'Demo', 'title': title, 'source_url': 'https://example.org/'+str(i), 'description': desc} for i, title, desc in [(1, 'Data Scientist', text), (2, 'Data Scientist', text), (3, 'Data Scientist', text+' Different duties.')]], 'test')
            cid = archive.db.execute('SELECT id FROM companies').fetchone()[0]
            result = {'summary': 'Modelli energetici.', 'facts': [{'section': 'business', 'text': 'Modelli energetici', 'quote': 'We model energy.', 'url': 'https://example.org'}], 'missing_information': []}
            annotations = [{'type': 'url_citation', 'url_citation': {'url': 'https://example.org', 'content': 'We model energy.'}}]
            with patch('jobhunter.remote_llm.api_key', return_value='dummy'), patch('jobhunter.remote_llm.request', return_value=(result, {}, annotations)) as model:
                first = remote_llm.run(archive, 'company-research', execute=True, config_path=config_path)
                self.assertEqual(first['processed'], 1)
                self.assertEqual(remote_llm.run(archive, 'company-research', execute=True, config_path=config_path)['cached'], 1)
                self.assertEqual(model.call_count, 1)
            company = archive.show(cid)
            remote_llm.presentation(archive, company, config_path)
            self.assertEqual(company['remote_summary']['summary'], result['summary'])
            self.assertEqual(len(company['opportunities']), 3)
            self.assertEqual(sorted(len(j['possible_duplicates']) for j in company['opportunities']), [0, 1, 1])
            archive.db.execute('UPDATE companies SET description=? WHERE id=?', ('New description', cid))
            self.assertIsNone(archive.show(cid)['remote_summary'])
            self.assertEqual(remote_llm.run(archive, 'company-research', config_path=config_path)['selected'], 0)
            archive.close()


if __name__ == '__main__':
    unittest.main()
