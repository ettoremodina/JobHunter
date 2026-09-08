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

    def test_validation_rejects_invented_evidence(self):
        """A valid JSON shape alone must not authorize a decision."""
        answer = {'decision': 'exclude', 'rationale': 'Experience', 'evidence': ['five years'], 'missing_information': []}
        with self.assertRaises(ValueError):
            remote_llm.validate('selection', answer, {'title': 'Scientist', 'description': 'No experience required'})
        answer.update(decision='review', evidence=[])
        remote_llm.validate('selection', answer, {'title': 'Scientist', 'description': ''})

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
            self.assertEqual(company['remote_summary']['summary'], result['summary'])
            self.assertEqual(len(company['opportunities']), 3)
            self.assertEqual(sorted(len(j['possible_duplicates']) for j in company['opportunities']), [0, 1, 1])
            archive.db.execute('UPDATE companies SET description=? WHERE id=?', ('New description', cid))
            self.assertIsNone(archive.show(cid)['remote_summary'])
            self.assertEqual(remote_llm.run(archive, 'company-research', config_path=config_path)['selected'], 0)
            archive.close()


if __name__ == '__main__':
    unittest.main()
