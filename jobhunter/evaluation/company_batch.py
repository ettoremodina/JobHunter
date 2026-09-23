"""Combined company inference: one request per company, parallel transport, per-record validation."""
import copy
import json
import logging
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from jobhunter.operations import cancellation
from jobhunter.evaluation import remote_llm as llm, tier
from jobhunter.operations.progress import write_checkpoint
from jobhunter.evaluation.selection import judgeable, verdicts
from jobhunter.workspace import ROOT, now

logger = logging.getLogger(__name__)
# Il provider ha rifiutato la richiesta prima di generare: nessun token, nessuna riga da salvare.
# Sono i soli esiti che si possono ritentare senza rischiare di pagare due volte la stessa risposta.
THROTTLED = frozenset({429, 500, 502, 503, 504})


def signature(cfg):
    """Invalidate combined derivatives when their shared prompt or generation contract changes."""
    spec = cfg['company_batch']
    return llm.digest([spec, (ROOT/spec['prompt_path']).read_text(encoding='utf-8')])


def response_schema(cfg, company_schema):
    """Lo stesso schema per ogni chiamata: un prefisso stabile è ciò che fa agganciare la cache del provider.

    Gli ID degli annunci e le citazioni ammesse **non** stanno qui dentro. Non è una perdita di
    controllo: `apply()` rifiuta già un insieme di ID diverso da quello richiesto e `validate()`
    rifiuta già una citazione fuori catalogo. Enumerarli anche nello schema rendeva ogni richiesta
    diversa dalla precedente fin dal primo token, e la cache non poteva agganciare mai.

    Da qui è sparito il giudizio: ogni riga porta solo la sua scheda. Chi decide se un annuncio si
    tiene è il giudice System One, un passaggio prima (DESIGN §3).
    """
    summary = json.loads((ROOT/cfg['response_schemas']['job-summary']).read_text(encoding='utf-8'))
    # Source references remain strings in the shared schema. validate() checks their scoped membership.
    summary['properties']['facts']['items']['properties']['quote'] = {'type': 'string'}
    definitions = {'job-summary': summary, 'role': {
        'type': 'object', 'additionalProperties': False, 'required': ['id', 'summary'], 'properties': {
            'id': {'type': 'string'},
            # Every role in `jobs` was explicitly requested and has usable evidence. Allowing
            # null here contradicted the prompt and made the shortest schema-valid response six
            # nulls instead of six cards. Empty/insufficient cards already have a grounded object
            # form: summary="", facts=[], missing_information=[...].
            'summary': {'$ref': '#/$defs/job-summary'}}}}
    return {'type': 'object', '$defs': definitions, 'additionalProperties': False,
            'required': ['company', 'jobs'], 'properties': {
                'company': {'anyOf': [company_schema, {'type': 'null'}]},
                'jobs': {'type': 'array', 'items': {'$ref': '#/$defs/role'}}}}


def namespaced(catalog, tag):
    """Give each role its own reference space so one request cannot mix two catalogues that both start at S0."""
    return {f'{tag}-{key}': value for key, value in catalog.items()}


def first_reference(value):
    """Keep the first key of a joined list such as ``"J0-S22,J0-S23"`` or ``"J0-S22; J0-S23"``.

    Il prompt vieta di concatenare chiavi, ma `qwen3.8-flash` lo fa comunque su 155 citazioni del
    primo giro completo (22 settembre 2026), con la virgola e a volte col punto e virgola. La prima
    chiave e' testo vero della fonte: la frase resta ancorata, solo a una prova invece che a piu'.
    Una chiave inesistente la rifiuta validate().
    """
    if not isinstance(value, str):
        return value
    return value.replace(';', ',').split(',')[0].strip()


def resolve_namespace(answer, tag):
    """Strip this role's tag from returned references; anything else is left for validate() to reject."""
    def clean(value):
        value = first_reference(value)
        return value[len(tag)+1:] if isinstance(value, str) and value.startswith(tag+'-') else value
    answer = copy.deepcopy(answer)
    if isinstance(answer.get('facts'), list):
        for fact in answer['facts']:
            if isinstance(fact, dict) and 'quote' in fact:
                fact['quote'] = clean(fact['quote'])
    return answer


def prepare(archive, cfg, prompt, cid, state, counts):
    """Read everything one company needs for a single request. All SQLite access happens here, never in a worker.

    Questo passaggio **non giudica piu'**: i verdetti li hanno gia' dati il regex e il giudice
    System One. Qui si scrivono solo le schede dei ruoli sopravvissuti e la scheda dell'azienda.
    """
    limits = cfg['company_batch']
    # Un solo passaggio sull'evidenza aziendale per tutta la richiesta: senza, ogni ruolo in attesa
    # faceva rileggere e ripulire l'HTML di tutti gli annunci dell'azienda (`enrichment.company_input`).
    shared = {}
    jobs = [r[0] for r in archive.db.execute('SELECT id FROM opportunities WHERE company_id=? ORDER BY id', (cid,))]
    name = archive.db.execute('SELECT name FROM companies WHERE id=?', (cid,)).fetchone()[0]
    carded, payloads = [], {}
    # DESIGN §4: la sintesi si fa dopo l'assegnazione del tier e solo su Tier A e B.
    wants_cards = state['tier'] in ('A', 'B-esperienza')
    for oid in jobs:
        if not wants_cards or (state['ruoli'].get(oid) or {}).get('verdetto') != tier.KEEP:
            continue
        description = json.loads(archive.db.execute('SELECT data FROM opportunities WHERE id=?', (oid,)).fetchone()[0]).get('description', '')
        if not judgeable(description):
            # Senza mansioni da leggere non c'e' niente da riassumere, e la chiamata si pagherebbe uguale.
            counts['unreadable_jobs'] += 1
        elif llm.current_result(archive, 'job-summary', oid, cfg, cache=shared):
            counts['cached_summaries'] += 1
        else:
            carded.append(oid)
    for oid in carded:
        payloads[oid] = {'job-summary': llm.inputs(archive, 'job-summary', oid, cfg, cache=shared)}
    company = llm.inputs(archive, 'company-summary', cid, cfg, cache=shared)
    # DESIGN §2 e §4: la scheda azienda e' presentazione, non categoria. La categoria la assegna il
    # giudice System One un passaggio prima; qui si paga solo per presentare un Tier A o B.
    company_needed = (bool(company['source']['facts']) and state['tier'] in ('A', 'B-attesa', 'B-esperienza')
                      and not llm.current_result(archive, 'company-summary', cid, cfg, cache=shared))
    if not carded and not company_needed:
        return None
    source = copy.deepcopy(company['source'])
    for field in ('facts', 'job_evidence', 'categories', 'website'):
        source.pop(field, None)
    tags = {oid: f'J{index}' for index, oid in enumerate(carded)}
    wire_jobs = {oid: {'evidence_catalog': namespaced(payloads[oid]['job-summary']['source']['evidence_catalog'], tags[oid])}
                 for oid in carded}
    # Le etichette sono stabili e `request()` le sposta nel messaggio di sistema. Il profilo del
    # candidato invece non appartiene a uno step descrittivo: nel pilot induceva il modello a
    # valutare il match e a scrivere persino il nome del candidato, malgrado il divieto nel prompt.
    payload = {'field_labels': json.loads((ROOT/cfg['job_fields_path']).read_text(encoding='utf-8')),
               'company': source, 'company_requested': company_needed, 'jobs': wire_jobs,
               'response_schema': response_schema(cfg, company['response_schema'])}
    oversized = len(carded) > limits['max_jobs'] or len(prompt)+len(json.dumps(payload)) > limits['max_input_chars']
    return {'id': cid, 'name': name, 'pending': carded, 'payloads': payloads, 'tags': tags,
            'carded': set(carded), 'company': company, 'company_needed': company_needed,
            'payload': payload, 'oversized': oversized}


def apply(archive, cfg, prompt, job, answer, counts, report_path=''):
    """Validate and save each card on its own evidence; one unusable part never discards the others."""
    pending, payloads = job['pending'], job['payloads']
    if not isinstance(answer, dict) or set(answer) != {'company', 'jobs'} or not isinstance(answer['jobs'], list):
        raise ValueError('Missing, duplicate or unexpected job IDs')
    # Lo schema non elenca gli ID: l'insieme esatto si verifica qui, dove si verificava gia'.
    # Un ID ripetuto non e' un ID inventato: sulle chiamate a ruolo singolo il modello rimanda la
    # stessa scheda 2-3 volte (21 aziende perse per intero il 22 settembre 2026). Le copie restano
    # candidate e vince la prima che passa la validazione.
    returned = {}
    for entry in answer['jobs']:
        if not isinstance(entry, dict) or set(entry) != {'id', 'summary'}:
            raise ValueError('Missing, duplicate or unexpected job IDs')
        returned.setdefault(entry['id'], []).append(entry)
    if set(returned) != set(pending):
        raise ValueError('Missing, duplicate or unexpected job IDs')
    results, rejected = [], []
    for oid in pending:
        jp = payloads[oid]['job-summary']
        error = None
        for entry in returned[oid]:
            try:
                if entry['summary'] is None:
                    # Una scheda chiesta e non data e' inutilizzabile per *questo* ruolo, non un motivo
                    # per buttare via gli altri della stessa chiamata.
                    raise ValueError('Job summary requested but not returned')
                results.append(('job-summary', oid, jp, llm.validate(
                    'job-summary', resolve_namespace(entry['summary'], job['tags'][oid]), jp['source'], jp['field_labels'])))
                break
            except (AttributeError, ValueError, TypeError, KeyError) as exc:
                error = exc
        else:
            rejected.append({'id': oid, 'error': str(error)})
    if job['company_needed']:
        try:
            if answer['company'] is None:
                raise ValueError('Company summary requested but not returned')
            # La scheda azienda cita spesso frasi degli annunci (`J0-S3`): 213 citazioni rifiutate
            # nel primo giro completo, benche' il prompt lo vieti. Le chiavi `Jn-Sm` non collidono
            # con le `Sm` dell'azienda e puntano a testo che il modello ha davvero ricevuto nella
            # stessa richiesta, quindi si risolvono invece di buttare la scheda. Il prompt resta
            # com'e': cambiarlo invaliderebbe tutte le schede gia' salvate (`signature`).
            source = copy.deepcopy(job['company']['source'])
            source['evidence_catalog'] = {**source.get('evidence_catalog', {}),
                                          **{k: v for wire in job['payload']['jobs'].values()
                                             for k, v in wire['evidence_catalog'].items()}}
            card = copy.deepcopy(answer['company'])
            for fact in card.get('facts') or []:
                if isinstance(fact, dict) and 'quote' in fact:
                    fact['quote'] = first_reference(fact['quote'])
            results.append(('company-summary', job['id'], job['company'], llm.validate('company-summary', card, source)))
        except (AttributeError, ValueError, TypeError, KeyError) as exc:
            # The company card is retried next run; the role cards above are grounded independently.
            rejected.append({'id': job['id'], 'task': 'company-summary', 'error': str(exc)})
    elif answer['company'] is not None:
        counts['ignored_company_summaries'] += 1
    with archive.db:
        for task, oid, original, result in results:
            settings = llm.signature(cfg, task)
            original_prompt = (ROOT/cfg['prompts'][task]).read_text(encoding='utf-8')
            fingerprint = llm.digest({'source': original, 'prompt': original_prompt, 'settings': settings})
            stored = {'result': result, 'usage': {}, 'batch_signature': signature(cfg), 'batch_report': report_path, 'settings': settings, 'prompt_hash': llm.digest(prompt)}
            archive.db.execute('INSERT OR REPLACE INTO enrichments VALUES(?,?,?,?,?,?,?)', ('remote:'+task, oid, llm.digest(original), fingerprint, json.dumps(stored, ensure_ascii=False), cfg['model'], now()))
    # La categoria non si scrive piu' da qui: la assegna il giudice System One (DESIGN §2).
    counts['saved_summaries'] += sum(1 for r in results if r[0] == 'job-summary')
    counts['saved_company_cards'] += sum(1 for r in results if r[0] == 'company-summary')
    counts['rejected_jobs'] += len(rejected)
    return rejected


def run(archive, values, progress):
    """Write one request per company of cards, keeping `workers` requests in flight.

    E' l'ultimo passaggio della pipeline e l'unico che **scrive**: schede dei ruoli sopravvissuti e
    scheda dell'azienda. Non giudica e non categorizza — lo fanno il regex e il giudice System One,
    prima di qui (DESIGN §3).

    Il calcolo non e' su questa macchina: il tempo totale lo decide quante risposte si aspettano
    insieme, non quanto costa prepararle. Le richieste non partono piu' a ondate di `workers` che
    aspettano la piu' lenta prima di far partire la successiva: appena una risposta rientra,
    un'altra azienda parte. Preparazione e salvataggio restano su questo thread, l'unico che puo'
    toccare SQLite: sono 11 ms per azienda misurati su 400 aziende dell'archivio, quindi il
    preparatore regge ~90 aziende al secondo e non affama il pool nemmeno a cento chiamate in
    volo. Erano 915 ms prima che `prepare()` smettesse di rileggere l'evidenza aziendale una
    volta per ruolo in attesa: con molte richieste insieme un preparatore lento diventa subito
    il vero limite, non il provider.
    """
    cfg = json.loads((ROOT/'config/remote_llm.json').read_text(encoding='utf-8'))
    limits = cfg['company_batch']
    workers = min(max(int(values.get('workers') or limits.get('workers', 1)), 1), limits.get('max_workers', 8))
    prompt = (ROOT/limits['prompt_path']).read_text(encoding='utf-8')
    assessment = verdicts(archive)
    ids = sorted((cid for cid, state in assessment.items()
                  if state['tier'] in ('A', 'B-attesa', 'B-esperienza')), key=llm.digest)
    if not values['all_companies']:
        ids = ids[:values['company_limit']]
    counts = Counter({key: 0 for key in ('api_calls', 'api_companies', 'summary_requests', 'company_requests',
                     'saved_summaries', 'saved_company_cards', 'cached_summaries', 'unreadable_jobs',
                     'rejected_companies', 'rejected_jobs', 'throttled_retries')})
    usage = Counter()
    execute = values['mode'] == 'execute'
    report = {'status': 'success', 'mode': values['mode'], 'total_companies': len(ids), 'started_at': now(),
              'queue_scope': 'tier-a-b',
              'workers': workers if execute else 1,
              'completed_companies': 0, 'counts': counts, 'usage': usage, 'items': [], 'model': cfg['model'], 'task': 'company-batch'}
    output = ROOT/cfg['output_directory']/('company-batch-'+now().replace(':', '').replace('+', '_'))/'report.json'
    written = [0.0]

    def checkpoint(force=True):
        """Persist run totals once, including rejected response usage, before exposing progress.

        Con molte chiamate in volo le risposte rientrano a raffica, e il report intero verrebbe
        riserializzato piu' volte al secondo proprio sul thread che le salva: fra una risposta e
        l'altra basta un aggiornamento ogni `checkpoint_seconds`. I confini della run si scrivono
        sempre.
        """
        if not force and time.monotonic() - written[0] < limits.get('checkpoint_seconds', 2):
            return
        written[0] = time.monotonic()
        if execute:
            output.parent.mkdir(parents=True, exist_ok=True)
            write_checkpoint(output, report)
        progress({k: v for k, v in report.items() if k != 'items'})

    gate, ready = threading.Lock(), [0.0]
    interval = cfg['request_delay_seconds'] / max(workers, 1)

    def pace():
        """Distanzia le partenze invece di far scattare tutti i worker insieme al primo giro."""
        with gate:
            start = max(time.monotonic(), ready[0])
            ready[0] = start + interval
        time.sleep(max(0.0, start - time.monotonic()))

    def call(job):
        """Perform one billed request in a worker thread; no SQLite handle is touched here.

        Si ritenta solo un rifiuto del provider (`THROTTLED`), con attesa crescente: quella
        richiesta non ha generato niente, quindi il ritento non puo' duplicare una spesa. Un
        fallimento di trasporto lascia l'esito di fatturazione ignoto e continua a fermare la run.
        """
        attempts = max(int(limits.get('max_attempts', 4)), 1)
        for attempt in range(attempts):
            pace()
            try:
                # `company_batch.parameters` sovrascrive i parametri di generazione solo per il batch
                # (es. enable_thinking), senza toccare le chiamate brevi di selezione e descrizione.
                return llm.request({**cfg, 'parameters': {**cfg['parameters'], 'max_tokens': limits['max_tokens'], **limits.get('parameters', {})}}, prompt, job['payload'], job['key'])
            except ValueError as exc:
                if getattr(exc, 'status', None) not in THROTTLED or attempt == attempts - 1:
                    raise
                with gate:
                    counts['throttled_retries'] += 1
                logger.warning('Company %s: HTTP %s, retry %s/%s', job['id'], exc.status, attempt+1, attempts-1)
                time.sleep(min(getattr(exc, 'retry_after', 0) or 2 ** attempt, 60))

    key = llm.api_key(cfg) if execute else None
    pool = ThreadPoolExecutor(max_workers=workers) if execute else None
    queue, inflight, stopped = iter(ids), {}, False

    def next_job():
        """Prepare companies until one needs a paid request, accounting for the ones passed over.

        In anteprima nessuna azienda viene spedita, quindi una sola chiamata le conta tutte.
        """
        for cid in queue:
            cancellation.check()
            job = prepare(archive, cfg, prompt, cid, assessment[cid], counts)
            if job is not None and job['oversized']:
                counts['deferred_companies'] += 1
                report['items'].append({'id': job['id'], 'error': 'Azienda oltre i limiti configurati: nessuna chiamata, nessun annuncio troncato'})
                report['status'] = 'partial'
            elif job is not None and not execute:
                counts['planned_api_calls'] += 1
                counts['planned_summaries'] += len(job['carded'])
                counts['planned_company_cards'] += int(job['company_needed'])
            elif job is not None:
                return job
            report['completed_companies'] += 1
            checkpoint(force=False)
        return None

    try:
        if not execute:
            next_job()
        while execute:
            while not stopped and len(inflight) < workers:
                job = next_job()
                if job is None:
                    break
                counts['api_calls'] += 1
                counts['api_companies'] += 1
                counts['summary_requests'] += len(job['carded'])
                counts['company_requests'] += int(job['company_needed'])
                inflight[pool.submit(call, {**job, 'key': key})] = job
            if not inflight:
                break
            checkpoint(force=False)
            for future in wait(list(inflight), return_when=FIRST_COMPLETED).done:
                job = inflight.pop(future)
                item = {'id': job['id'], 'job_ids': job['pending']}
                report['items'].append(item)
                report['company_name'] = job['name']
                received = False
                try:
                    answer, consumed, _ = future.result()
                    received = True
                    item['usage'] = consumed
                    for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                        usage[field] += consumed.get(field, 0)
                    rejected = apply(archive, cfg, prompt, job, answer, counts, str(output))
                    item['status'] = 'partial' if rejected else 'success'
                    if rejected:
                        item['rejected'] = rejected
                        # La risposta grezza va nel report accanto all'errore: e' l'unico modo per
                        # capire *perche'* il modello sbaglia, e i derivati salvati non la conservano
                        # (una risposta rifiutata non diventa una riga in `enrichments`). Come fa gia'
                        # `remote_llm.run` con `rejected_answer`. E' limitata da `max_tokens`.
                        item['rejected_answer'] = answer
                        report['status'] = 'partial'
                except (AttributeError, ValueError, TypeError, KeyError, OSError) as exc:
                    counts['rejected_companies'] += 1
                    logger.warning('Company batch failed: %s', exc)
                    item['error'] = str(exc)
                    if received:
                        item['rejected_answer'] = answer
                    report['status'] = 'partial'
                    # A transport failure leaves the billing outcome unknown: stop submitting, never retry implicitly.
                    stopped = stopped or not received
                report['completed_companies'] += 1
                checkpoint(force=False)
    finally:
        if pool:
            pool.shutdown(wait=True)
    report['report_path'] = str(output) if execute else None
    checkpoint()
    return report
