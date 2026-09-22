"""Allowlisted dashboard actions, durable execution history and input-change detection."""

from collections import Counter
import json
import logging
import os
import threading

from jobhunter.workspace import Archive, ROOT, now
from jobhunter.evaluation.remote_llm import digest
from jobhunter.operations.progress import process_alive
from jobhunter.operations import cancellation

logger = logging.getLogger(__name__)


def configuration():
    """Load UI limits and labels without exposing any credential values."""
    return json.loads((ROOT/'config/pipeline_ui.json').read_text(encoding='utf-8'))


def input_versions(archive):
    """Fingerprint input contents, excluding derived timestamps that would cause false retries."""
    jobs = [tuple(r) for r in archive.db.execute('SELECT id,company_id,content_hash FROM opportunities ORDER BY id')]
    companies = [tuple(r) for r in archive.db.execute('SELECT id,name,website,description,sectors FROM companies ORDER BY id')]
    files = {str(p.relative_to(ROOT)): p.read_text(encoding='utf-8') for folder in ('config', 'user_context')
             for p in sorted((ROOT/folder).rglob('*')) if p.suffix in ('.json', '.txt', '.md', '.yaml') and p.is_file()}
    data = digest([jobs, companies])
    rules = {k: v for k, v in files.items() if k.startswith('user_context/') or 'role_filter' in k or 'categor' in k}
    remote = {k: v for k, v in files.items() if 'remote' in k or 'job_summary_fields' in k or 'job_field_sections' in k or k == 'config/categories.json' or k.startswith('user_context/')}
    versions = {'filters': digest([data, rules])}
    # Il giudice System One dipende dalle sue domande e dalle sue soglie: cambiarle invalida i suoi
    # esiti salvati, esattamente come il prompt invalida quelli del remoto.
    system_one = {k: v for k, v in files.items() if 'system' in k}
    versions['jev'] = digest([data, system_one, rules, files.get('config/categories.json')])
    listing_inputs = [tuple(r) for r in archive.db.execute("SELECT id,json_extract(data,'$.source_url'),json_extract(data,'$.title') FROM opportunities ORDER BY id")]
    versions.update(remote=digest([data, remote]),
                    descriptions=digest([listing_inputs, files.get('config/descriptions.json'), rules]),
                    collection=digest({k: v for k, v in files.items() if k.startswith('config/') and (k.endswith('.yaml') or k == 'config/app.json')}))
    return versions


def controls(archive, cfg):
    """Describe action forms, real execution states, and changes since the last successful run."""
    ui = configuration()
    desc = json.loads((ROOT/'config/descriptions.json').read_text())
    batch = json.loads((ROOT/'config/remote_llm.json').read_text())['company_batch']
    from jobhunter.evaluation.system_one import config as system_one_config
    jev = system_one_config()
    versions = input_versions(archive)
    history = []
    for row in archive.db.execute("SELECT * FROM pipeline_jobs WHERE id IN (SELECT id FROM pipeline_jobs ORDER BY id DESC LIMIT 100) OR status='running' ORDER BY id DESC"):
        item = dict(row)
        item['parameters'] = json.loads(item['parameters'])
        item['detail'] = json.loads(item['detail'])
        item['cancel_requested'] = archive.db.execute('SELECT 1 FROM pipeline_cancellations WHERE job_id=?', (item['id'],)).fetchone() is not None
        if item['status'] == 'running' and process_alive(item['pid']) is False:
            item['status'] = 'interrupted'
        history.append(item)
    actions = {}
    for key, spec in ui['actions'].items():
        last = next((r for r in history if r['step'] == key or r['step'] == 'sequence' and r['detail'].get('phase') == key and r['status'] in ('running', 'failed', 'interrupted')), None)
        success = archive.db.execute("SELECT basis,finished_at FROM pipeline_jobs WHERE step=? AND status='success' AND json_extract(parameters,'$.mode') IS NOT 'preview' ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        actions[key] = {**spec, 'last_run': last, 'needs_update': bool(success and success['basis'] != versions[key]),
                        'last_success': success['finished_at'] if success else None}
    sources = sorted({r[0] for r in archive.db.execute("SELECT DISTINCT json_extract(data,'$.source') FROM opportunities") if r[0]})
    return {'supports_stop': True, 'actions': actions, 'history': history[:12], 'active': next((r for r in history if r['status'] == 'running'), None),
            'sequence': ui['sequence'],
            'poll_seconds': ui['poll_seconds'], 'collection_sources': [k for k, v in cfg['sources'].items() if v.get('enabled')],
            'description_sources': sources, 'limits': {'collection': cfg['max_jobs'], 'descriptions': desc['max_limit'],
            'workers': desc['max_workers'], 'remote_workers': batch['max_workers'], 'remote': ui['remote_max_companies'],
            'jev': jev['max_limit']},
            'defaults': {'workers': desc['workers'], 'remote_workers': batch['workers'], 'company_limit': ui['remote_company_limit']}}


def parameters(step, supplied, archive, cfg):
    """Reject unknown actions, extra fields, invalid enums and out-of-range values before dispatch."""
    ui = configuration()
    if step not in ui['actions'] or not isinstance(supplied, dict):
        raise ValueError('Passaggio o parametri non validi')
    if set(supplied) - set(ui['actions'][step]['fields']):
        raise ValueError('Parametro non previsto per questo passaggio')
    values = dict(supplied)
    desc = json.loads((ROOT/'config/descriptions.json').read_text())
    for key in ('all', 'refresh_stale', 'force', 'all_companies', 'revisit'):
        if key in ui['actions'][step]['fields']:
            values.setdefault(key, False)
            if type(values[key]) is not bool:
                raise ValueError('Valore booleano richiesto')
    batch = json.loads((ROOT/'config/remote_llm.json').read_text())['company_batch']
    from jobhunter.evaluation.system_one import config as system_one_config
    jev = system_one_config()
    for key, default, maximum in [('limit', jev['default_limit'] if step == 'jev' else min(10, cfg['max_jobs']),
                                   cfg['max_jobs'] if step == 'collection' else
                                   jev['max_limit'] if step == 'jev' else desc['max_limit']),
                                  ('workers', desc['workers'] if step == 'descriptions' else batch['workers'],
                                   desc['max_workers'] if step == 'descriptions' else batch['max_workers']),
                                  ('company_limit', ui['remote_company_limit'], ui['remote_max_companies'])]:
        if key in ui['actions'][step]['fields']:
            values.setdefault(key, default)
            if type(values[key]) is not int or not 1 <= values[key] <= maximum:
                raise ValueError(f'{key}: scegliere un intero da 1 a {maximum}')
    if step == 'collection':
        if values.get('source') not in cfg['sources'] or not cfg['sources'][values['source']].get('enabled'):
            raise ValueError('Scegli una fonte abilitata')
    if step == 'descriptions':
        values.setdefault('source', '')
        sources = {r[0] for r in archive.db.execute("SELECT DISTINCT json_extract(data,'$.source') FROM opportunities")}
        if values['source'] and values['source'] not in sources:
            raise ValueError('Fonte non presente in archivio')
    if 'mode' in ui['actions'][step]['fields']:
        # Ogni passaggio che può spendere ha lo stesso cancello: `preview` è il valore di riposo.
        values.setdefault('mode', 'preview')
        if values['mode'] not in ('preview', 'execute'):
            raise ValueError('Modalità remota non valida')
    return values


def execute(archive, cfg, step, values, progress):
    """Call existing Python operations directly, never shell commands supplied by the browser."""
    if step == 'collection':
        from jobhunter.acquisition.collection import collect
        return collect(archive, cfg, values['source'], values['limit'], recover_descriptions=False)
    if step == 'descriptions':
        from jobhunter.acquisition.descriptions import recover
        return recover(archive, values['limit'], values['source'] or None, values['all'], workers=values['workers'], refresh_stale=values['refresh_stale'], force=values['force'], progress=progress)
    if step == 'filters':
        result = Counter(x['status'] for x in archive.evaluations().values())
        return {'counts': dict(result)}
    if step == 'jev':
        from jobhunter.evaluation.system_one import run
        return run(archive, values, progress)
    if step == 'remote':
        from jobhunter.evaluation.company_batch import run
        return run(archive, values, progress)
    raise ValueError('Passaggio non eseguibile')


def start(database, cfg, step, supplied, lock, continue_after=False, remote_parameters=None):
    """Validate and claim a durable job before starting a worker with its own SQLite connection."""
    if not lock.acquire(blocking=False):
        raise ValueError('Un passaggio è già in esecuzione. Attendi il risultato.')
    archive = None
    try:
        archive = Archive(database)
        values = parameters(step, supplied, archive, cfg)
        if type(continue_after) is not bool:
            raise ValueError('Scelta di sequenza non valida')
        sequence = configuration()['sequence']
        if continue_after and step not in sequence:
            raise ValueError('La sequenza parte dai passaggi principali')
        steps = sequence[sequence.index(step):] if continue_after else [step]
        remote_values = parameters('remote', remote_parameters or {}, archive, cfg) if 'remote' in steps and step != 'remote' else values
        # In sequenza i passaggi che spendono condividono la modalità scelta per la parte pagata: uno
        # che restasse in anteprima dentro una sequenza «esegui» non farebbe nulla, e in silenzio.
        paid_mode = (remote_values if 'remote' in steps else values).get('mode') or 'preview'
        if paid_mode == 'execute':
            from jobhunter.evaluation.remote_llm import api_key
            if 'remote' in steps:
                api_key(json.loads((ROOT/'config/remote_llm.json').read_text()))
            if 'jev' in steps:
                from jobhunter.evaluation.system_one import config as system_one_config
                api_key(system_one_config())
        if archive.path.resolve() == (ROOT/cfg['database']).resolve():
            from jobhunter.operations.progress import snapshot
            if snapshot(ROOT, cfg).get('status') == 'running':
                raise ValueError('Un workflow esterno risulta in esecuzione. Attendi prima di avviare altri passaggi.')
        basis = input_versions(archive)[step]
        with archive.db:
            archive.db.execute('BEGIN IMMEDIATE')
            for row in archive.db.execute("SELECT id,pid FROM pipeline_jobs WHERE status='running'").fetchall():
                if process_alive(row['pid']) is not False:
                    raise ValueError('Una precedente esecuzione risulta ancora attiva. Non avvio un duplicato.')
                archive.db.execute("UPDATE pipeline_jobs SET status='interrupted',finished_at=? WHERE id=?", (now(), row['id']))
            cursor = archive.db.execute("INSERT INTO pipeline_jobs(step,status,parameters,basis,pid,started_at,detail) VALUES(?,'running',?,?,?,?,?)",
                                        ('sequence' if continue_after else step, json.dumps(values), basis, os.getpid(), now(), json.dumps({'phase': step, 'steps': steps})))
            job_id = cursor.lastrowid
    except Exception:
        lock.release()
        raise
    finally:
        if archive:
            archive.close()

    def work():
        """Persist results and progress even when the initiating browser tab has closed."""
        worker = None
        try:
            worker = Archive(database)
            cancellation.bind(lambda: worker.db.execute('SELECT 1 FROM pipeline_cancellations WHERE job_id=?', (job_id,)).fetchone() is not None)

            def progress(detail):
                """Save bounded progress at record boundaries, without credentials or model input."""
                with worker.db:
                    worker.db.execute('UPDATE pipeline_jobs SET detail=? WHERE id=?', (json.dumps(detail), job_id))
                cancellation.check()

            results = []
            for current in steps:
                chained = ({'all': True, 'mode': paid_mode} if current == 'jev'
                           else {'all': True} if current == 'descriptions' else {})
                current_values = values if current == step else remote_values if current == 'remote' else parameters(current, chained, worker, cfg)
                current_basis = input_versions(worker)[current]
                started = now()
                progress({'phase': current, 'steps': steps, 'completed_steps': [r['step'] for r in results]})
                result = execute(worker, cfg, current, current_values, lambda detail: progress({**detail, 'phase': current, 'steps': steps}))
                progress({**result, 'phase': current, 'steps': steps})
                status = result.get('status', 'partial' if result.get('failed') or result.get('errors') else 'success')
                status = status if status in ('success', 'partial', 'failed') else 'partial'
                if continue_after:
                    with worker.db:
                        worker.db.execute('INSERT INTO pipeline_jobs(step,status,parameters,basis,pid,started_at,finished_at,detail) VALUES(?,?,?,?,?,?,?,?)',
                                          (current, status, json.dumps(current_values), current_basis, os.getpid(), started, now(), json.dumps(result)))
                results.append({'step': current, 'status': status})
                # `partial` è lo stato normale e permanente: solo `failed` ferma la sequenza (data-model.md).
                if status == 'failed':
                    break
            if continue_after:
                result = {'steps': results, 'last_result': result, 'message': 'Sequenza completata.' if status != 'failed' else 'Sequenza fermata: un passaggio non ha potuto lavorare. I risultati già salvati restano disponibili.'}
            with worker.db:
                worker.db.execute('UPDATE pipeline_jobs SET status=?,finished_at=?,detail=?,basis=? WHERE id=?', (status, now(), json.dumps(result, ensure_ascii=False), current_basis if not continue_after else basis, job_id))
            logger.info('Pipeline %s ended: %s', step, status)
        except cancellation.Cancelled:
            if worker:
                with worker.db:
                    worker.db.execute("UPDATE pipeline_jobs SET status='interrupted',finished_at=?,detail=json_set(detail,'$.message',?) WHERE id=?",
                                      (now(), 'Interrotto su richiesta. I risultati salvati restano disponibili; nessun passaggio successivo avviato.', job_id))
        except Exception:
            logger.exception('Pipeline %s failed', step)
            if worker:
                with worker.db:
                    worker.db.execute("UPDATE pipeline_jobs SET status='failed',finished_at=?,detail=json_set(detail,'$.message',?) WHERE id=?", (now(), 'Passaggio non completato. I risultati già salvati restano disponibili; consulta il log del server.', job_id))
        finally:
            cancellation.bind()
            if worker:
                worker.close()
            lock.release()
    thread = threading.Thread(target=work, daemon=True)
    try:
        thread.start()
    except Exception:
        lock.release()
        failed = Archive(database)
        try:
            with failed.db:
                failed.db.execute("UPDATE pipeline_jobs SET status='failed',finished_at=? WHERE id=?", (now(), job_id))
        finally:
            failed.close()
        raise
    return {'started': step, 'job_id': job_id}


def stop(archive, job_id):
    """Persist an idempotent stop request for one active job, including external pipeline workers."""
    if type(job_id) is not int:
        raise ValueError('Identificativo esecuzione non valido')
    row = archive.db.execute('SELECT status FROM pipeline_jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise ValueError('Esecuzione non trovata')
    if row['status'] != 'running':
        return {'job_id': job_id, 'status': row['status'], 'stop_requested': False}
    with archive.db:
        archive.db.execute('INSERT OR IGNORE INTO pipeline_cancellations VALUES(?,?)', (job_id, now()))
    logger.info('Stop requested for pipeline job %s', job_id)
    return {'job_id': job_id, 'stop_requested': True}
