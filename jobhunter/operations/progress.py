"""Read current workflow checkpoints without SQLite writes."""

from collections import Counter
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time

logger = logging.getLogger(__name__)
PHASES = {'listings': 'Raccolta listing', 'descriptions': 'Recupero descrizioni',
          'categories': 'Ricategorizzazione', 'coverage': 'Copertura descrizioni',
          'analytics': 'Statistiche e filtri', 'queue': 'Aggiornamento coda',
          'finished': 'Terminato'}


def write_checkpoint(path, report):
    """Retry Windows sharing conflicts; a temporarily locked progress file must not abort acquisition."""
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(5):
            try:
                temporary.replace(path)
                return True
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.05 * 2 ** attempt)
    except PermissionError as exc:
        logger.warning('Progress checkpoint locked; data processing continues, next checkpoint will retry: %s', exc)
        return False


def read_json(path):
    """Read and close promptly; incomplete reports are unavailable rather than successful."""
    try:
        result = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def process_alive(pid):
    """Check a local process without signalling it; unknown permissions remain unknown."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            code = wintypes.DWORD()
            return code.value == 259 if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


def timestamp(value):
    """Normalize checkpoint timestamps for comparisons and elapsed-time reporting."""
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0


def snapshot(root, cfg, run=None):
    """Select a run and distinguish process liveness, stage progress and useful recovered data."""
    root = Path(root)
    def resolve(value):
        """Resolve paths recorded by local workers against their project root."""
        path = Path(value)
        return path if path.is_absolute() else root / path

    description_cfg = read_json(root / 'config/descriptions.json')
    detail_paths = list(resolve(description_cfg.get('output_directory', 'data/descriptions')).glob('*/report.json'))
    details = [(p, read_json(p)) for p in detail_paths]
    if run:
        selected = resolve(run)
        if selected.is_dir():
            selected /= 'report.json'
        report = read_json(selected)
        if not report:
            return {'status': 'unavailable', 'report': str(selected), 'message': 'Report assente, illeggibile o in aggiornamento; riprovare.'}
    else:
        candidates = [(p, read_json(p)) for p in resolve(cfg['raw_directory']).glob('*-official/report.json')]
        # Detail runs belonging to a workflow are shown inside that workflow, not as a separate latest run.
        workflow_pids = {r.get('pid') for _, r in candidates if r.get('pid')}
        linked = {str(resolve(r['description_report'])) for _, r in candidates if r.get('description_report')}
        candidates += [(p, r) for p, r in details if str(p) not in linked and (not r.get('pid') or r['pid'] not in workflow_pids)]
        candidates = [(p, r) for p, r in candidates if timestamp(r.get('started_at'))]
        if not candidates:
            return {'status': 'not_found', 'message': 'Nessun workflow o recupero descrizioni con un report disponibile.'}
        selected, report = max(candidates, key=lambda pair: timestamp(pair[1].get('started_at')))
    started = timestamp(report.get('started_at'))
    alive = process_alive(report.get('pid'))
    terminal = bool(report.get('finished_at'))
    phase = report.get('phase') or ('finished' if terminal else 'descriptions' if 'eligible_missing' in report or report.get('description_report') else 'listings')
    detail_path = None
    detail = report if 'items' in report and 'eligible_missing' in report else {}
    if not detail:
        if report.get('description_report'):
            detail_path = resolve(report['description_report'])
            detail = read_json(detail_path)
        else:
            matching = [(p, r) for p, r in details if r.get('pid') == report.get('pid') and r.get('pid') and timestamp(r.get('started_at')) >= started]
            if matching:
                detail_path, detail = max(matching, key=lambda pair: timestamp(pair[1].get('started_at')))
    warnings = []
    error = report.get('description_followup', {}).get('error')
    if error:
        warnings.append('Errore recupero dettagli: ' + error)
    state = report.get('status', 'success') if terminal else 'running' if alive else 'not_running' if alive is False else 'unconfirmed'
    updated_path = detail_path if phase == 'descriptions' and detail_path else selected
    try:
        age = max(0, time.time() - updated_path.stat().st_mtime)
    except OSError:
        age = None
    if not terminal and age is not None and age > 120:
        warnings.append('Nessun checkpoint recente; processo vivo non significa avanzamento confermato.')
    if not terminal and alive is False:
        warnings.append('Processo non attivo e report finale assente: non considerare il workflow completato.')
    progress = None
    if detail:
        counts = Counter(item.get('status') for item in detail.get('items', []))
        done = len(detail.get('items', []))
        total = detail.get('selected_total', detail.get('eligible_missing', 0))
        attempted = detail.get('attempted', 0)
        elapsed = detail.get('elapsed_seconds', 0)
        blocked = detail.get('blocked_hosts', [])
        eta = None
        if state == 'running' and phase == 'descriptions' and not blocked and attempted >= 50 and elapsed and not counts['skipped'] and age is not None and age <= 120:
            eta = round(max(0, total - done) * elapsed / attempted)
        progress = {'processed': done, 'total': total, 'percent': round(100 * done / total, 1) if total else None,
                    'attempted': attempted, 'saved': detail.get('saved', 0), 'failed': counts['failed'], 'skipped': counts['skipped'],
                    'cached': detail.get('cached', 0), 'deferred': detail.get('deferred', 0), 'blocked_hosts': blocked,
                    'phase_eta_seconds': eta, 'eta_note': 'Stima media della sola fase dettagli, non dell’intero workflow.' if eta is not None else 'Stima non affidabile: campione insufficiente, blocchi, checkpoint vecchio o fase diversa.',
                    'report': str(detail_path or selected)}
    sources = [{'source': name, 'status': value.get('status'), 'queries_done': len(value.get('queries', [])),
                'queries_total': value.get('expected_queries'), 'accepted': value.get('accepted', 0)} for name, value in report.get('sources', {}).items()]
    end = timestamp(report.get('finished_at')) if terminal else time.time()
    return {'status': state, 'phase': phase, 'phase_label': PHASES.get(phase, phase), 'pid': report.get('pid'),
            'process_alive': alive, 'started_at': report.get('started_at'), 'elapsed_seconds': round(max(0, end-started)),
            'checkpoint_age_seconds': round(age) if age is not None else None, 'report': str(selected),
            'details': progress, 'sources': sources, 'warnings': warnings,
            'selection_note': 'Ultimo run individuato. Per un altro run usare --run PERCORSO_REPORT.'}


def duration(seconds):
    """Format elapsed times and phase estimates for terminal readers."""
    if seconds is None:
        return 'n/d'
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h {minutes:02}m {seconds:02}s' if hours else f'{minutes}m {seconds:02}s'


def render(value):
    """Show phase progress separately from saved descriptions and avoid a fictitious global percent."""
    if 'message' in value:
        return value['message']
    labels = {'running': 'IN ESECUZIONE', 'not_running': 'NON ATTIVO / FINALE ASSENTE', 'unconfirmed': 'ATTIVITÀ NON CONFERMATA',
              'success': 'TERMINATO', 'partial': 'TERMINATO CON RISULTATI PARZIALI', 'failed': 'FALLITO'}
    lines = ['JobHunter | ' + labels.get(value['status'], value['status']),
             'Fase: ' + value['phase_label'], f"PID: {value['pid'] or 'non registrato'} | Trascorso: {duration(value['elapsed_seconds'])} | Età checkpoint: {duration(value['checkpoint_age_seconds'])}"]
    for source in value['sources']:
        lines.append(f"Fonte {source['source']}: {source['status']} | query {source['queries_done']}/{source['queries_total'] or '?'} | record accettati {source['accepted']}")
    d = value['details']
    if d:
        lines += [f"Ultimo checkpoint dettagli: {d['processed']}/{d['total']} ({d['percent']}%) elaborati, inclusi i saltati",
                  f"Descrizioni salvate: {d['saved']} | tentati: {d['attempted']} | falliti: {d['failed']} | saltati: {d['skipped']}",
                  f"Testi riusati: {d['cached']} | rinviati: {d['deferred']} | host bloccati: {', '.join(d['blocked_hosts']) or 'nessuno'}",
                  'Tempo residuo fase dettagli: ' + duration(d['phase_eta_seconds']), d['eta_note']]
    lines.extend('ATTENZIONE: ' + message for message in value['warnings'])
    lines += ['Report: ' + value['report'], value['selection_note']]
    return '\n'.join(lines)


def monitor(root, cfg, run=None, watch=False, interval=5, as_json=False):
    """Read snapshots once or repeatedly; Ctrl+C stops only this observer."""
    if not 1 <= interval <= 3600:
        raise ValueError('Choose a status interval from 1 to 3600 seconds')
    import sys
    try:
        while True:
            value = snapshot(root, cfg, run)
            if watch and sys.stdout.isatty() and not as_json:
                print('\033[2J\033[H', end='')
            print(json.dumps(value, ensure_ascii=False) if as_json else render(value), flush=True)
            if not watch:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        if not as_json:
            print('\nMonitor chiuso. Il workflow non è stato interrotto.')
