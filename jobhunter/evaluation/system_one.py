"""Giudice Jev: scelta del ruolo e settore aziendale nella stessa richiesta TypeSafe.

Le due decisioni restano domande indipendenti e il codice le combina separatamente. Condividerne
lo stato e la richiesta evita di trasmettere e pagare due volte lo stesso testo. Quando un'azienda
ha già una categoria corrente, la richiesta contiene solo le domande utili per il ruolo. Quando
serve soltanto la categoria, il passaggio invia una richiesta aziendale senza inventare un ruolo.

Quello che lascia in `review` **resta indeciso**: nessun modello lo rivede, e diventa materiale per
l'utente. Al modello remoto (`company_batch`) resta solo scrivere le schede: un System One model non
genera testo, e questo non e' un limite da aggirare ma il motivo per cui le sue decisioni non possono
sbagliare formato. Si mandano uno `state` e delle domande tipizzate; tornano una probabilita' per
ogni domanda e una scelta che puo' essere solo una delle opzioni dichiarate. I due modi in cui il
batch remoto falliva — un giudizio `null` su un ruolo richiesto e una citazione con l'ID decorato —
qui sono impossibili per costruzione: un `noul` restituisce sempre un numero e una `choice`
restituisce sempre una chiave del catalogo.

Cosa NON delegare al modello (`docs/system-one.md`, dalla pagina «jaggedness» del fornitore):
aritmetica, conteggi e confronti fra date. Gli anni di esperienza obbligatori, la gestione di
persone e le lingue restano dove sono, nel regex di `selection.requirements()`.
"""

import copy
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urlsplit

from jobhunter.evaluation import remote_llm as llm
from jobhunter.evaluation.enrichment import lines
from jobhunter.evaluation.selection import judgeable
from jobhunter.operations import cancellation
from jobhunter.operations.progress import write_checkpoint
from jobhunter.workspace import ROOT, now

logger = logging.getLogger(__name__)
TASKS = ('selection', 'category')
# Le domande vivono in configurazione, ma questi nomi li legge il codice che le combina.
REQUIRED = {'selection': ('mansioni_compatibili', 'famiglia_esclusa', 'posto_per_studenti'),
            'category': ('categoria', 'descrive_il_datore', 'agenzia')}
UNCLASSIFIED = 'Da classificare'
# Richiesta rifiutata dal fornitore: niente e' stato calcolato, quindi il ritento non puo'
# duplicare una spesa. Stessa regola del batch remoto; un errore di trasporto non si ritenta.
THROTTLED = frozenset({429, 500, 502, 503, 504, 529})


def config(path=None):
    """Read the System One configuration; no credential value is ever stored in it."""
    return json.loads(Path(path or ROOT/'config/system_one.json').read_text(encoding='utf-8'))


def questions(cfg, task):
    """Load one task's typed questions, rejecting a set this module could not honour.

    Le domande non sono un prompt: sono la regola di decisione, e vivono in
    `config/system-one-questions.json` perche' l'utente le rivede senza toccare il codice.
    Un insieme senza domanda di prova verrebbe accettato dal fornitore e produrrebbe verdetti
    senza citazione, che `remote_llm.validate` rifiuta un record alla volta: meglio fermarsi qui.
    """
    spec = json.loads((ROOT/cfg['questions_path']).read_text(encoding='utf-8'))[task]
    for name, question in spec.items():
        if question.get('type') not in ('noul', 'choice'):
            raise ValueError(f'Unsupported question type for {name}')
        if question['type'] == 'choice' and not (question.get('criteria') or question.get('options_from')):
            raise ValueError(f'Choice {name} needs criteria or options_from')
    # I nomi delle domande sono il contratto fra questo file e `judgement`/`classification`, che li
    # combinano uno per uno. Rinominarne una senza toccare il codice deve fallire adesso, con un
    # messaggio che dice quale, non a meta' passata con un KeyError.
    missing = set(REQUIRED[task]) - set(spec)
    if missing:
        raise ValueError(f'Missing {task} questions: ' + ', '.join(sorted(missing)))
    if task == 'selection' and not any(q.get('options_from') == 'evidence_catalog' for q in spec.values()):
        raise ValueError('The selection question set needs one choice over evidence_catalog')
    return spec


def wire_questions(spec, catalog, options):
    """Fill the runtime option sets, leaving everything else exactly as configured."""
    result = {}
    for name, question in spec.items():
        question = copy.deepcopy(question)
        source = question.pop('options_from', None)
        if source == 'evidence_catalog':
            question['criteria'] = dict(catalog)
        elif source == 'categories':
            question['criteria'] = dict(options)
        result[name] = question
    return result


def signature(cfg, task):
    """Cache identity: model, endpoint, thresholds and the question set that produced the answer."""
    return {'endpoint': cfg['endpoint'], 'model': cfg['model'], 'schema_version': cfg['schema_version'],
            'thresholds': cfg['thresholds'], 'questions': questions(cfg, task)}


def job_state(archive, oid, cfg, job=None):
    """Lo stato di un annuncio e il catalogo delle frasi citabili, costruiti una volta sola.

    Il catalogo e' limitato a `max_evidence_options` perche' una `choice` ammette al massimo 255
    opzioni: lo `state` contiene solo le righe che il catalogo copre, cosi' il modello non puo'
    voler citare una frase che non gli e' stata offerta.
    """
    if job is None:
        job = json.loads(archive.db.execute('SELECT data FROM opportunities WHERE id=?', (oid,)).fetchone()['data'])
    excerpts = lines(job.get('description') or '')[:cfg['max_evidence_options']]
    catalog = {f'S{i}': text for i, text in enumerate(excerpts)}
    state = {'titolo': job.get('title') or '', 'mansioni': '\n'.join(excerpts)}
    for field in ('locations', 'employment_type', 'salary'):
        if job.get(field):
            state[field] = job[field]
    return state, catalog


def company_state(archive, cid, cfg):
    """Il testo vero degli annunci, non le frasi che un regex ha lasciato passare.

    L'asse azienda restava fermo perche' `company_evidence.job_facts` filtra le frasi con un
    regex che ne scarta la maggior parte, e il modello riceveva un catalogo vuoto. A 0,042 $ per
    milione di token il testo intero si puo' mandare: e' la leva che prima non si poteva pagare.
    """
    row = archive.db.execute('SELECT name,sectors,description FROM companies WHERE id=?', (cid,)).fetchone()
    texts = [v for v in (row['sectors'], row['description']) if v]
    budget = cfg['company']['max_chars'] - sum(len(t) for t in texts)
    for job in archive.db.execute('SELECT data FROM opportunities WHERE company_id=? ORDER BY id LIMIT ?',
                                  (cid, cfg['company']['max_jobs'])):
        text = '\n'.join(lines(json.loads(job['data']).get('description') or ''))
        if text and len(text) <= budget:
            texts.append(text)
            budget -= len(text)
    return {'azienda': row['name'], 'testi': texts}


def ask(cfg, state, wired, key):
    """One System One request: typed answers in, no generated text, never an implicit retry."""
    endpoint = urlsplit(cfg['endpoint'])
    if not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query:
        raise ValueError('System One endpoint must not carry embedded credentials or a query')
    if endpoint.scheme != 'https':
        raise ValueError('System One endpoint must use HTTPS')
    if not key:
        raise ValueError('TypeSafe API key is missing')
    body = {'model': cfg['model'], 'state': state, 'questions': wired}
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}
    request = urllib.request.Request(cfg['endpoint'], data=json.dumps(body, ensure_ascii=False).encode(),
                                     headers=headers)
    try:
        with urllib.request.build_opener(llm.NoRedirect()).open(request, timeout=cfg['timeout_seconds']) as response:
            raw = response.read(cfg['max_response_bytes']+1)
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            body = json.loads(exc.read(8192))
            error = body.get('error', body) if isinstance(body, dict) else {}
            if isinstance(error, dict):
                detail = str(error.get('message', error.get('code', '')))[:1200]
                detail = detail.replace(key, '<REDACTED>') if key else detail
        except (ValueError, OSError, AttributeError):
            pass
        rejected = ValueError(f'System One HTTP {exc.code}; request rejected by the provider' + (': '+detail if detail else ''))
        rejected.status = exc.code
        pause = (exc.headers or {}).get('Retry-After', '')
        rejected.retry_after = int(pause) if str(pause).strip().isdigit() else 0
        raise rejected from None
    except (OSError, TimeoutError):
        raise ValueError('System One transport failure; billing outcome unknown, no automatic retry') from None
    if len(raw) > cfg['max_response_bytes']:
        raise ValueError('System One response exceeds configured size')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError('Invalid System One JSON response') from None
    answers = data.get('answers')
    if not isinstance(answers, dict) or set(answers) != set(wired):
        raise ValueError('System One answered a different set of questions')
    return answers, data.get('usage') or {}, data.get('model') or cfg['model']


def probability(answers, name):
    """Read one yes/no probability, refusing anything that is not a number in [0, 1].

    Un `noul` non porta un campo `confidence`: quel numero *e'* la misura dell'incertezza, e un
    valore vicino a 0,5 e' il segnale che il modello non sa. Le soglie stanno in config.
    """
    value = (answers.get(name) or {}).get('noul')
    if type(value) not in (int, float) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
        raise ValueError(f'Invalid noul answer for {name}')
    return float(value)


def selected(answers, name, allowed):
    """Read one choice and its confidence, refusing an option that was never offered."""
    answer = answers.get(name) or {}
    option, confidence = answer.get('choice'), answer.get('confidence')
    if option not in allowed:
        raise ValueError(f'Unknown option for {name}')
    if type(confidence) not in (int, float) or isinstance(confidence, bool) or not 0.0 <= confidence <= 1.0:
        raise ValueError(f'Invalid confidence for {name}')
    return option, float(confidence)


def judgement(cfg, spec, answers, catalog):
    """Dalle probabilita' al verdetto: la regola sta qui, in codice, non nel modello.

    Il modello risponde a domande indipendenti; a combinarle e' questa funzione, perche' una
    soglia e' aritmetica e l'aritmetica non si chiede a un System One model. `review` non e' un
    fallimento: e' la banda in cui nessuna soglia e' stata raggiunta, e il modello remoto la
    riceve come la riceveva dal regex.
    """
    limits = cfg['thresholds']
    evidence = next(name for name, q in spec.items() if q.get('options_from') == 'evidence_catalog')
    quote, _ = selected(answers, evidence, catalog)
    compatible = probability(answers, 'mansioni_compatibili')
    student = probability(answers, 'posto_per_studenti')
    family, family_confidence = selected(answers, 'famiglia_esclusa', set(spec['famiglia_esclusa']['criteria']))
    excluded = family != 'nessuna' and family_confidence >= limits['choice_confidence']
    if student >= limits['flag_above']:
        decision, motive = 'exclude', 'Posto riservato a studenti o di tirocinio'
    elif excluded:
        decision, motive = 'exclude', 'Mansioni della famiglia esclusa «%s»' % family.replace('_', ' ')
    elif compatible >= limits['keep_above']:
        decision, motive = 'keep', 'Mansioni quantitative o di sviluppo su dati e modelli'
    elif compatible <= limits['exclude_below']:
        decision, motive = 'exclude', 'Le mansioni non sono quantitative ne\' di sviluppo su dati e modelli'
    else:
        decision, motive = 'review', 'Le mansioni non bastano a decidere'
    unknown = [] if decision != 'review' else ['Mansioni ambigue: compatibilita' + f' {compatible:.0%}']
    result = {'decision': decision, 'evidence': [quote], 'missing_information': unknown,
              'rationale': f'{motive} (compatibilita\' {compatible:.0%}).'}
    # Stesso contratto del giudizio remoto, stesso validatore: la citazione viene risolta qui e
    # una chiave fuori catalogo fermerebbe *questo* record, non la passata.
    return llm.validate('selection', result, {'title': '', 'description': '', 'evidence_catalog': catalog}), \
        {'mansioni_compatibili': compatible, 'posto_per_studenti': student,
         'famiglia_esclusa': family, 'famiglia_confidenza': family_confidence}


def classification(cfg, spec, answers, options):
    """Il settore del datore di lavoro, o «Da classificare» quando il testo non lo dice.

    Due cancelli prima della categoria, ed entrambi hanno una causa misurata nell'archivio: un
    annuncio descrive spesso il cliente e non chi assume, e un'agenzia di somministrazione non
    appartiene al settore per cui sta cercando.
    """
    limits = cfg['thresholds']
    category, confidence = selected(answers, 'categoria', set(options))
    employer = probability(answers, 'descrive_il_datore')
    agency = probability(answers, 'agenzia')
    if agency >= limits['flag_above']:
        category, reason = UNCLASSIFIED, 'Agenzia per il lavoro: il settore cercato non e\' il suo'
    elif employer < limits['choice_confidence']:
        category, reason = UNCLASSIFIED, 'Il testo non distingue chi assume da chi e\' descritto'
    elif confidence < limits['choice_confidence']:
        category, reason = UNCLASSIFIED, f'Settore incerto: «{category}» solo al {confidence:.0%}'
    else:
        reason = f'Settore «{category}» al {confidence:.0%} sul testo degli annunci'
    return {'category': category, 'reason': reason}, \
        {'categoria': category, 'categoria_confidenza': confidence,
         'descrive_il_datore': employer, 'agenzia': agency}


def current_result(archive, task, record_id, cfg, fingerprint=None):
    """Return a saved judgement only while its state, questions and thresholds still match."""
    row = archive.db.execute('SELECT cache_key,data FROM enrichments WHERE task=? AND record_id=?',
                             ('jev:'+task, record_id)).fetchone()
    if not row:
        return None
    if fingerprint is None:
        state = job_state(archive, record_id, cfg)[0] if task == 'selection' else company_state(archive, record_id, cfg)
        fingerprint = llm.digest({'state': state, 'settings': signature(cfg, task)})
    return json.loads(row['data'])['result'] if row['cache_key'] == fingerprint else None


def pending(archive, cfg, limit, everything, revisit=False):
    """Build requests that combine role and company questions whenever both are pending.

    Each saved result keeps its own cache identity. Adding company questions to a role request
    therefore does not make the role stale when only company evidence changes. The request limit
    counts HTTP calls, not answers: a combined call can save two results.
    """
    from jobhunter.evaluation.selection import verdicts
    from jobhunter.evaluation.tier import UNKNOWN

    skipped = Counter()
    settings = {task: signature(cfg, task) for task in TASKS}
    assigned = {row['company_id']: dict(row) for row in archive.db.execute(
        'SELECT company_id,category,method FROM categories')}
    category_records = {}
    for row in sorted(archive.db.execute('SELECT id FROM companies').fetchall(), key=lambda item: llm.digest(item['id'])):
        cid = row['id']
        saved_category = assigned.get(cid, {})
        if not revisit and saved_category.get('category', UNCLASSIFIED) != UNCLASSIFIED:
            continue
        state = company_state(archive, cid, cfg)
        if not state['testi']:
            skipped['category_no_evidence'] += 1
            continue
        fingerprint = llm.digest({'state': state, 'settings': settings['category']})
        if current_result(archive, 'category', cid, cfg, fingerprint):
            skipped['category_cached'] += 1
            continue
        if len(json.dumps(state, ensure_ascii=False)) > cfg['max_state_chars']:
            skipped['category_oversized'] += 1
            continue
        category_records[cid] = {'id': cid, 'state': state, 'catalog': {}, 'cache_key': fingerprint}

    undecided = {oid for company in verdicts(archive).values()
                 for oid, verdict in company['ruoli'].items() if verdict['verdetto'] == UNKNOWN}
    job_rows = [row for row in archive.db.execute('SELECT id,company_id,data FROM opportunities')
                if row['id'] in undecided and judgeable(json.loads(row['data']).get('description') or '')]
    records = []
    for row in sorted(job_rows, key=lambda item: llm.digest(item['id'])):
        oid, cid = row['id'], row['company_id']
        state, catalog = job_state(archive, oid, cfg, json.loads(row['data']))
        fingerprint = llm.digest({'state': state, 'settings': settings['selection']})
        if current_result(archive, 'selection', oid, cfg, fingerprint):
            skipped['selection_cached'] += 1
            continue
        if len(json.dumps(state, ensure_ascii=False)) > cfg['max_state_chars']:
            skipped['selection_oversized'] += 1
            continue
        record = {'id': oid, 'company_id': cid, 'state': dict(state),
                  'selection': {'id': oid, 'state': state, 'catalog': catalog, 'cache_key': fingerprint}}
        category = category_records.get(cid)
        if category:
            combined_state = {**state, **category['state']}
            if len(json.dumps(combined_state, ensure_ascii=False)) <= cfg['max_state_chars']:
                record['state'] = combined_state
                record['category'] = category_records.pop(cid)
            else:
                skipped['split_oversized'] += 1
        records.append(record)

    for cid, category in category_records.items():
        records.append({'id': cid, 'company_id': cid, 'state': category['state'], 'category': category})
    if not everything:
        records = records[:limit]
    return records, skipped


def save(archive, task, record, result, answers, usage, model, settings):
    """Persist one decision and, for the company axis, the category it implies."""
    stored = {'result': result, 'answers': answers, 'usage': usage, 'settings': settings}
    with archive.db:
        archive.db.execute('INSERT OR REPLACE INTO enrichments VALUES(?,?,?,?,?,?,?)',
                           ('jev:'+task, record['id'], llm.digest(record['state']), record['cache_key'],
                            json.dumps(stored, ensure_ascii=False), model, now()))
        if task == 'category':
            # Una classificazione fatta in chat resta dell'utente: la stessa guardia del batch remoto.
            archive.db.execute("INSERT INTO categories VALUES(?,?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET "
                               "category=excluded.category,method=excluded.method,reason=excluded.reason,"
                               "updated_at=excluded.updated_at WHERE categories.method!='chat'",
                               (record['id'], result['category'], 'jev', result['reason'], now()))


def run(archive, values, progress=None, config_path=None):
    """Run the combined Jev pass with an explicit preview gate and bounded concurrency."""
    cfg = config(config_path)
    specs = {task: questions(cfg, task) for task in TASKS}
    options = {
        **{name: ', '.join(terms) for name, terms in json.loads((ROOT/'config/categories.json').read_text(encoding='utf-8')).items()},
        UNCLASSIFIED: 'Il testo non dice in che settore opera chi assume.'}
    limit = min(max(int(values.get('limit') or cfg['default_limit']), 1), cfg['max_limit'])
    workers = min(max(int(values.get('workers') or cfg['workers']), 1), cfg['max_workers'])
    settings = {task: signature(cfg, task) for task in TASKS}
    records, skipped = pending(archive, cfg, limit, values.get('all', False), values.get('revisit', False))
    counts = Counter({key: 0 for key in ('calls', 'decided', 'selection_decided', 'category_decided',
                                         'rejected', 'throttled_retries', 'combined', 'selection_only', 'category_only',
                                         'keep', 'exclude', 'review', 'classified', 'unclassified')})
    counts.update(skipped)
    for record in records:
        kinds = tuple(task for task in TASKS if task in record)
        counts['combined' if len(kinds) == 2 else kinds[0] + '_only'] += 1
    usage = Counter()
    execute = values.get('mode') == 'execute'
    report = {'task': 'jev', 'status': 'success', 'mode': 'execute' if execute else 'preview',
              'model': cfg['model'], 'started_at': now(),
              'total': len(records), 'completed': 0, 'counts': counts, 'usage': usage, 'items': []}
    output = ROOT/cfg['output_directory']/('combined-'+now().replace(':', '').replace('+', '_'))/'report.json'
    written = [0.0]

    def checkpoint(force=True):
        """Persist run totals at record boundaries, not once per answer at full concurrency."""
        if not force and time.monotonic() - written[0] < cfg['checkpoint_seconds']:
            return
        written[0] = time.monotonic()
        output.parent.mkdir(parents=True, exist_ok=True)
        write_checkpoint(output, report)
        if progress:
            progress({k: v for k, v in report.items() if k != 'items'})

    if not records or not execute:
        report['report_path'] = str(output)
        report['items'] = [{'id': record['id'], 'company_id': record['company_id'],
                            'tasks': [task for task in TASKS if task in record],
                            'state_chars': len(json.dumps(record['state'], ensure_ascii=False)),
                            'evidence_options': len(record.get('selection', {}).get('catalog', {}))}
                           for record in records]
        logger.info('Jev preview: %s requests, no API calls', len(records))
        checkpoint()
        return report
    gate, ready = threading.Lock(), [0.0]
    interval = 60.0 / max(cfg['max_requests_per_minute'], 1)
    key = llm.api_key(cfg)

    def pace():
        """Spread departures so `workers` concurrent calls still respect the per-minute limit."""
        with gate:
            start = max(time.monotonic(), ready[0])
            ready[0] = start + interval
        time.sleep(max(0.0, start - time.monotonic()))

    def call(record):
        """One billed request in a worker thread; no SQLite handle is touched here."""
        wired = {}
        if 'selection' in record:
            wired.update(wire_questions(specs['selection'], record['selection']['catalog'], {}))
        if 'category' in record:
            wired.update(wire_questions(specs['category'], {}, options))
        attempts = max(cfg['max_attempts'], 1)
        for attempt in range(attempts):
            pace()
            try:
                return ask(cfg, record['state'], wired, key)
            except ValueError as exc:
                if getattr(exc, 'status', None) not in THROTTLED or attempt == attempts - 1:
                    raise
                with gate:
                    counts['throttled_retries'] += 1
                time.sleep(min(getattr(exc, 'retry_after', 0) or 2 ** attempt, 60))

    queue, inflight, stopped = iter(records), {}, False
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        while True:
            while not stopped and len(inflight) < workers:
                record = next(queue, None)
                if record is None:
                    break
                cancellation.check()
                counts['calls'] += 1
                inflight[pool.submit(call, record)] = record
            if not inflight:
                break
            checkpoint(force=False)
            for future in wait(list(inflight), return_when=FIRST_COMPLETED).done:
                record = inflight.pop(future)
                item = {'id': record['id'], 'company_id': record['company_id'],
                        'tasks': [task for task in TASKS if task in record], 'results': {}}
                report['items'].append(item)
                received = False
                try:
                    answers, consumed, model = future.result()
                    received = True
                    item['usage'] = consumed
                    for field in ('input_tokens', 'output_tokens'):
                        usage[field] += consumed.get(field, 0)
                    for task in item['tasks']:
                        try:
                            if task == 'selection':
                                result, detail = judgement(cfg, specs[task], answers, record[task]['catalog'])
                                counts[result['decision']] += 1
                            else:
                                result, detail = classification(cfg, specs[task], answers, options)
                                counts['unclassified' if result['category'] == UNCLASSIFIED else 'classified'] += 1
                            save(archive, task, record[task], result, detail, consumed, model, settings[task])
                            counts['decided'] += 1
                            counts[task + '_decided'] += 1
                            item['results'][task] = result
                        except (ValueError, TypeError, KeyError, OSError) as exc:
                            counts['rejected'] += 1
                            item.setdefault('errors', {})[task] = str(exc)
                            report['status'] = 'partial'
                            logger.warning('Jev %s answer rejected for %s: %s', task, record[task]['id'], exc)
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    counts['rejected'] += 1
                    item['error'] = str(exc)
                    if received:
                        item['rejected_answer'] = answers
                    report['status'] = 'partial'
                    logger.warning('Jev request rejected for %s: %s', record['id'], exc)
                    # Un errore di trasporto lascia la fatturazione ignota: non si spedisce altro.
                    stopped = stopped or not received
                report['completed'] += 1
                checkpoint(force=False)
    finally:
        pool.shutdown(wait=True)
    report['report_path'] = str(output)
    checkpoint()
    logger.info('Jev: %s results saved from %s requests, %s rejected',
                counts['decided'], counts['calls'], counts['rejected'])
    archive.run('system_one', report['status'], {k: v for k, v in report.items() if k != 'items'})
    return report
