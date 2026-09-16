"""Exercise GUI dispatch, paid previews, resume evidence and serialization on disposable archives."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from jobhunter.workspace import Archive, settings
from jobhunter.operations.pipeline_actions import controls, parameters, start, stop, execute, configuration


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

    def test_validation_rejects_unknown_steps_and_out_of_range_values(self):
        """Reject command injection, unknown steps and values outside the configured limits."""
        for step, values in [('shell', {}), ('rewrite', {'limit': 1}), ('filters', {'command': 'anything'}),
                             ('remote', {'company_limit': True}), ('remote', {'mode': 'anything'})]:
            with self.assertRaises(ValueError):
                parameters(step, values, self.archive, self.cfg)

    def test_pipeline_collection_defers_details_to_their_own_step(self):
        """The operational order must defer network details and classification, not just relabel cards."""
        self.assertEqual(configuration()['sequence'], ['collection', 'filters', 'descriptions', 'remote'])
        with patch('jobhunter.acquisition.collection.collect', return_value={}) as collect:
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

        with patch('jobhunter.operations.pipeline_actions.execute', side_effect=blocked):
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

    def test_partial_continues_and_only_failed_stops_the_sequence(self):
        """data-model.md: `partial` è lo stato normale e prosegue; solo `failed` ferma la sequenza."""
        lock = threading.Lock()
        executed = []

        def partial(archive, cfg, step, values, progress):
            """Return a partial stage result without making network requests."""
            executed.append(step)
            return {'status': 'partial' if step == 'descriptions' else 'success'}

        with patch('jobhunter.operations.pipeline_actions.execute', side_effect=partial):
            start(self.path, self.cfg, 'filters', {}, lock, continue_after=True)
            self.assertTrue(lock.acquire(timeout=5))
            lock.release()
        self.assertEqual(executed, ['filters', 'descriptions', 'remote'])
        result = controls(self.archive, self.cfg)
        self.assertEqual(result['actions']['descriptions']['last_run']['status'], 'partial')

        executed.clear()

        def failing(archive, cfg, step, values, progress):
            """Break the second stage: credentials, transport or database."""
            executed.append(step)
            return {'status': 'failed' if step == 'descriptions' else 'success'}

        with patch('jobhunter.operations.pipeline_actions.execute', side_effect=failing):
            start(self.path, self.cfg, 'filters', {}, lock, continue_after=True)
            self.assertTrue(lock.acquire(timeout=5))
            lock.release()
        self.assertEqual(executed, ['filters', 'descriptions'])

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
        with patch('jobhunter.operations.pipeline_actions.execute', side_effect=work):
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
        with patch('jobhunter.operations.pipeline_actions.process_alive', return_value=False):
            result = controls(self.archive, self.cfg)
        self.assertIsNone(result['active'])
        self.assertEqual(result['actions']['remote']['last_run']['status'], 'interrupted')
        self.archive.db.execute("UPDATE pipeline_jobs SET status='success',parameters=?", (json.dumps({'mode': 'preview'}),))
        self.archive.db.commit()
        self.assertIsNone(controls(self.archive, self.cfg)['actions']['remote']['last_success'])


if __name__ == '__main__':
    unittest.main()
