"""Exercise GUI dispatch, paid previews, resume evidence and serialization on disposable archives."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from jobhunter.workspace import Archive, settings
from jobhunter.pipeline_actions import controls, parameters, legacy_remote_companies as remote_companies, start, stop, execute, configuration


class ActionTests(unittest.TestCase):
    """Check the UI execution boundary without network or paid inference."""

    def setUp(self):
        """Create a small archive with both desirable and locally excluded roles."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'qa.db'
        self.archive = Archive(self.path)
        self.archive.ingest([{'company_name': name, 'title': title, 'source_url': f'https://example.org/{i}', 'description': 'Build models.'}
                             for i, (name, title) in enumerate([('A', 'Data Scientist'), ('A', 'HR Manager'), ('B', 'Data Scientist')])], 'test')
        self.cfg = settings()

    def tearDown(self):
        """Release SQLite before removing the temporary Windows directory."""
        self.archive.close()
        self.tmp.cleanup()

    def test_validation_and_cohort(self):
        """Reject command injection and count companies rather than requests or jobs."""
        for step, values in [('shell', {}), ('filters', {'command': 'anything'}), ('remote', {'company_limit': True}), ('remote', {'mode': 'anything'})]:
            with self.assertRaises(ValueError):
                parameters(step, values, self.archive, self.cfg)
        calls = []

        def mocked_run(archive, task, **kwargs):
            """Record task scope while returning a valid offline preview report."""
            calls.append((task, kwargs))
            return {'status': 'success', 'selected': 1, 'processed': 0, 'cached': 0, 'skipped': 0, 'items': [], 'failed': []}

        with patch('jobhunter.remote_llm.run', side_effect=mocked_run):
            result = remote_companies(self.archive, {'all_companies': True, 'company_limit': 1, 'mode': 'preview'}, lambda d: None)
        self.assertEqual(result['total_companies'], 2)
        self.assertEqual(len(calls), 8)  # two companies, three jobs with two tasks each
        self.assertTrue(all(c[1]['include_excluded'] for c in calls))
        self.assertTrue(all(not c[1]['execute'] for c in calls))

    def test_pipeline_collection_defers_details_and_local_categories(self):
        """The operational order must defer network details and classification, not just relabel cards."""
        self.assertEqual(configuration()['sequence'], ['collection', 'filters', 'descriptions', 'remote', 'queue'])
        with patch('jobhunter.collection.collect', return_value={}) as collect:
            execute(self.archive, self.cfg, 'collection', {'source': 'test', 'limit': 3}, lambda d: None)
        self.assertFalse(collect.call_args.kwargs['recover_descriptions'])
        self.assertEqual(self.archive.db.execute('SELECT count(*) FROM categories').fetchone()[0], 0)

    def test_run_serialization_history_and_staleness(self):
        """Closing a tab cannot lose a run; changed inputs make its saved outcome stale."""
        entered, release = threading.Event(), threading.Event()
        lock = threading.Lock()

        def blocked(*args):
            """Keep the worker busy long enough to exercise the concurrent-start guard."""
            entered.set()
            release.wait(5)
            return {'status': 'success'}

        with patch('jobhunter.pipeline_actions.execute', side_effect=blocked):
            start(self.path, self.cfg, 'filters', {}, lock)
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(ValueError):
                    start(self.path, self.cfg, 'analytics', {}, lock)
            finally:
                release.set()
            self.assertTrue(lock.acquire(timeout=5))
            lock.release()
        result = controls(self.archive, self.cfg)
        self.assertIsNone(result['active'])
        self.assertEqual(result['actions']['filters']['last_run']['status'], 'success')
        self.assertFalse(result['actions']['filters']['needs_update'])
        self.archive.db.execute("UPDATE opportunities SET content_hash='changed'")
        self.archive.db.commit()
        self.assertTrue(controls(self.archive, self.cfg)['actions']['filters']['needs_update'])

    def test_selection_precedes_enrichment_and_exclusions_skip_summaries(self):
        """Only surviving jobs incur summary work; company summary runs once after selection."""
        calls = []
        report = {'status': 'success', 'selected': 1, 'processed': 1, 'cached': 0, 'skipped': 0, 'items': [], 'failed': []}
        ids = [r[0] for r in self.archive.db.execute('SELECT id FROM opportunities ORDER BY id')]
        survivor = ids[0]
        def mocked(archive, task, **kwargs):
            """Record ordered calls without performing inference."""
            calls.append((task, kwargs['record_id']))
            return report
        def decision(archive, task, oid, config):
            """Keep one role and exclude the rest of the fixed fixture."""
            return {'decision': 'keep' if oid == survivor else 'exclude'}
        with patch('jobhunter.remote_llm.run', side_effect=mocked), patch('jobhunter.remote_llm.current_result', side_effect=decision):
            result = remote_companies(self.archive, {'all_companies': True, 'company_limit': 2, 'mode': 'execute'}, lambda d: None)
        self.assertEqual([oid for task, oid in calls if task == 'job-summary'], [survivor])
        self.assertEqual(sum(task == 'company-summary' for task, oid in calls), 1)
        self.assertLess(calls.index(('selection', survivor)), next(i for i,c in enumerate(calls) if c[0] == 'company-summary'))
        self.assertEqual(result['counts']['remote_excluded'], 2)

    def test_local_exclusions_have_only_a_bounded_audit(self):
        """Exclude locally before API work and spend only the explicit audit allowance."""
        ids = [r[0] for r in self.archive.db.execute('SELECT id FROM opportunities')]
        report = {'status': 'success', 'selected': 1, 'processed': 1, 'cached': 0, 'skipped': 0, 'items': [], 'failed': []}
        with patch.object(self.archive, 'evaluations', return_value={oid: {'status': 'excluded'} for oid in ids}), \
             patch('jobhunter.pipeline_actions.configuration', return_value={'remote_audit_excluded': 1}), \
             patch('jobhunter.remote_llm.run', return_value=report) as run, \
             patch('jobhunter.remote_llm.current_result', return_value={'decision': 'exclude'}):
            result = remote_companies(self.archive, {'all_companies': True, 'company_limit': 2, 'mode': 'execute'}, lambda d: None)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[1], 'selection')
        self.assertEqual(result['counts']['local_excluded'], 2)
        self.assertEqual(result['counts']['audit_jobs'], 1)

    def test_remote_rejections_continue_but_transport_stops(self):
        """Count billed rejected outputs across a cohort, but stop on provider failures."""
        for validation_error in (True, False):
            report = {'status': 'partial', 'selected': 1, 'processed': 0, 'cached': 0,
                      'skipped': 0, 'items': [{'usage': {'total_tokens': 10}}],
                      'failed': [{'error': 'test', 'validation_error': validation_error}]}
            with patch('jobhunter.remote_llm.run', return_value=report) as run:
                result = remote_companies(self.archive, {'all_companies': True, 'company_limit': 2, 'mode': 'execute'}, lambda d: None)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(run.call_count, 3 if validation_error else 1)
            self.assertEqual(result['completed_companies'], 2 if validation_error else 0)
            self.assertEqual(result['usage']['total_tokens'], 30 if validation_error else 10)

    def test_sequence_stops_at_partial_and_keeps_stage_result(self):
        """A partial upstream result must prevent later API work."""
        lock = threading.Lock()
        executed = []

        def partial(archive, cfg, step, values, progress):
            """Fail the second stage without making network requests."""
            executed.append(step)
            return {'status': 'partial' if step == 'descriptions' else 'success'}

        with patch('jobhunter.pipeline_actions.execute', side_effect=partial):
            start(self.path, self.cfg, 'filters', {}, lock, continue_after=True)
            self.assertTrue(lock.acquire(timeout=5))
            lock.release()
        self.assertEqual(executed, ['filters', 'descriptions'])
        result = controls(self.archive, self.cfg)
        self.assertEqual(result['actions']['descriptions']['last_run']['status'], 'partial')
        self.assertFalse(any(r['step'] == 'remote' for r in result['history']))

    def test_stop_preserves_completed_work_and_prevents_next_stage(self):
        """A persisted stop survives progress updates and interrupts the sequence at a safe boundary."""
        entered, release = threading.Event(), threading.Event()
        lock = threading.Lock()
        calls = []
        def work(archive, cfg, step, values, progress):
            """Simulate one in-flight operation that finishes after the stop request."""
            calls.append(step)
            entered.set()
            release.wait(5)
            return {'status': 'success', 'saved': 1}
        with patch('jobhunter.pipeline_actions.execute', side_effect=work):
            job = start(self.path, self.cfg, 'filters', {}, lock, continue_after=True)
            self.assertTrue(entered.wait(5))
            try:
                self.assertTrue(stop(self.archive, job['job_id'])['stop_requested'])
                self.assertTrue(stop(self.archive, job['job_id'])['stop_requested'])
            finally:
                release.set()
            self.assertTrue(lock.acquire(timeout=5))
            lock.release()
        self.assertEqual(calls, ['filters'])
        row = self.archive.db.execute('SELECT status,detail FROM pipeline_jobs WHERE id=?', (job['job_id'],)).fetchone()
        self.assertEqual(row['status'], 'interrupted')
        self.assertEqual(json.loads(row['detail'])['saved'], 1)
        self.assertFalse(stop(self.archive, job['job_id'])['stop_requested'])

    def test_interrupted_process_is_retryable_and_preview_is_not_freshness(self):
        """A dead process is not shown as active; previews don't claim completed processing."""
        self.archive.db.execute("INSERT INTO pipeline_jobs(step,status,parameters,basis,pid,started_at,detail) VALUES('remote','running','{}','old',12345,'2020-01-01','{}')")
        self.archive.db.commit()
        with patch('jobhunter.pipeline_actions.process_alive', return_value=False):
            result = controls(self.archive, self.cfg)
        self.assertIsNone(result['active'])
        self.assertEqual(result['actions']['remote']['last_run']['status'], 'interrupted')
        self.archive.db.execute("UPDATE pipeline_jobs SET status='success',parameters=?", (json.dumps({'mode': 'preview'}),))
        self.archive.db.commit()
        self.assertIsNone(controls(self.archive, self.cfg)['actions']['remote']['last_success'])


if __name__ == '__main__':
    unittest.main()
