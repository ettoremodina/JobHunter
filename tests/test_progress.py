"""Progress reads must not confuse skipped jobs, stale checkpoints or dead processes with success."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from jobhunter.progress import snapshot, write_checkpoint, monitor
from jobhunter.workspace import now


class ProgressTests(unittest.TestCase):
    """Test legacy attachment, terminal precedence and sharing-conflict resilience offline."""

    def test_live_detail_eta_and_blocked_jobs(self):
        """Only a sufficiently sampled unblocked phase gets an ETA; batch totals are explicit."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'report.json'
            report = {'pid': 123, 'started_at': now(), 'status': 'running', 'eligible_missing': 500,
                      'selected_total': 100, 'attempted': 50, 'saved': 45, 'elapsed_seconds': 60,
                      'items': [{'status': 'saved'}] * 45 + [{'status': 'failed'}] * 5}
            path.write_text(json.dumps(report))
            with patch('jobhunter.progress.process_alive', return_value=True):
                value = snapshot(root, {'raw_directory': 'raw'}, str(path))
                self.assertEqual(value['details']['percent'], 50)
                self.assertEqual(value['details']['phase_eta_seconds'], 60)
                report['blocked_hosts'] = ['linkedin.com']
                path.write_text(json.dumps(report))
                self.assertIsNone(snapshot(root, {'raw_directory': 'raw'}, str(path))['details']['phase_eta_seconds'])
            with patch('jobhunter.progress.process_alive', return_value=False):
                self.assertEqual(snapshot(root, {'raw_directory': 'raw'}, str(path))['status'], 'not_running')

    def test_legacy_workflow_failure_overrides_running_detail(self):
        """Attach the pre-monitor run and retain details even when its final report wins selection."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data').mkdir()
            workflow = root/'raw/run-official/report.json'
            workflow.parent.mkdir(parents=True)
            details = root/'details.json'
            details.write_text(json.dumps({'started_at': now(), 'status': 'running', 'eligible_missing': 10, 'attempted': 2, 'saved': 1, 'items': [{'status': 'saved'}, {'status': 'failed'}]}))
            (root/'data/active-resume.json').write_text(json.dumps({'started_at': '2020-01-01T00:00:00+00:00', 'pid': 123, 'workflow_report': str(workflow), 'description_report': str(details)}))
            workflow.write_text(json.dumps({'started_at': now(), 'finished_at': now(), 'status': 'partial', 'description_followup': {'error': 'locked report'}}))
            with patch('jobhunter.progress.process_alive', return_value=False):
                value = snapshot(root, {'raw_directory':'raw'})
            self.assertEqual(value['status'], 'partial')
            self.assertEqual(value['phase'], 'finished')
            self.assertEqual(value['details']['saved'], 1)
            self.assertTrue(any('locked report' in x for x in value['warnings']))

    def test_unreadable_and_absent_reports(self):
        """Missing evidence is never shown as completed or running."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(snapshot(root, {'raw_directory':'raw'})['status'], 'not_found')
            path = root/'report.json'
            path.write_text('{')
            self.assertEqual(snapshot(root, {'raw_directory':'raw'}, str(path))['status'], 'unavailable')

    def test_checkpoint_retries_without_aborting_data_work(self):
        """A temporary Windows reader lock retries; a persistent lock leaves the old checkpoint valid."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'report.json'
            path.write_text('{"old":true}')
            real_replace = Path.replace
            calls = []
            def replace(source, target):
                """Simulate one sharing violation followed by a successful atomic replacement."""
                calls.append(target)
                if len(calls) == 1:
                    raise PermissionError('busy reader')
                return real_replace(source, target)
            with patch.object(Path, 'replace', replace), patch('jobhunter.progress.time.sleep'):
                self.assertTrue(write_checkpoint(path, {'saved': 1}))
            with patch.object(Path, 'replace', side_effect=PermissionError('busy reader')), patch('jobhunter.progress.time.sleep'):
                self.assertFalse(write_checkpoint(path, {'saved': 2}))
            self.assertEqual(json.loads(path.read_text()), {'saved': 1})

    def test_watch_interrupt_does_not_signal_workers(self):
        """Stopping the observer only exits its loop."""
        with patch('jobhunter.progress.snapshot', return_value={'status':'not_found','message':'No runs'}), patch('jobhunter.progress.time.sleep', side_effect=KeyboardInterrupt), patch('builtins.print'):
            monitor('.', {}, watch=True)

    def test_status_cli_does_not_open_sqlite(self):
        """Read-only status bypasses Archive initialization and its schema/queue side effects."""
        from jobhunter.cli import main
        with patch('sys.argv', ['main.py', 'status']), patch('jobhunter.cli.logging.basicConfig'), patch('jobhunter.cli.Archive', side_effect=AssertionError('No database writes')), patch('jobhunter.progress.monitor') as read:
            self.assertEqual(main(), 0)
            read.assert_called_once()
