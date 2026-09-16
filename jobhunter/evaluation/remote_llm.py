"""Explicit remote LLM pilots: grounded selection and summaries, with resumable caches."""

import hashlib
import copy
import json
import logging
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from jobhunter.evaluation.enrichment import company_input, lines
from jobhunter.workspace import ROOT, now

logger = logging.getLogger(__name__)
TASKS = ('selection', 'job-summary', 'company-summary', 'company-research')


def digest(value):
    """Fingerprint structured inputs without including credentials."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def api_key(config):
    """Read one key from the environment or a simple ignored KEY=value secrets file."""
    name = config['api_key_env']
    if os.environ.get(name, '').strip():
        return os.environ[name].strip()
    path = ROOT / config['secrets_file']
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip() == name:
                value = value.strip().strip('"\'')
                if value:
                    return value
    raise ValueError(f'Missing credential: set {name} in the environment or configured secrets file')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Avoid forwarding bearer credentials to a redirected endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject redirects; endpoint changes must be explicit in configuration."""
        return None


def request(config, prompt, payload, key):
    """Call chat completions once; never retry a potentially billed request implicitly."""
    url = config['endpoint']
    if '{WorkspaceId}' in url:
        workspace = config.get('workspace_id', '').strip()
        if not workspace or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for c in workspace):
            raise ValueError('Set workspace_id in config/remote_llm.json to your Alibaba Frankfurt workspace ID')
        url = url.replace('{WorkspaceId}', workspace)
    endpoint = urlsplit(url)
    if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query:
        raise ValueError('Remote endpoint must be HTTPS without embedded credentials or query')
    parameters = config.get('parameters', {})
    if set(parameters) - {'thinking', 'enable_thinking', 'reasoning_effort', 'reasoning', 'max_tokens', 'temperature', 'top_p', 'response_format'}:
        raise ValueError('Unsupported generation parameter')
    wire_payload = copy.deepcopy(payload)
    schema = wire_payload.pop('response_schema', None)
    if schema:
        parameters = {**parameters, 'response_format': {'type': 'json_schema', 'json_schema': {'name': 'jobhunter', 'strict': True, 'schema': schema}}}
    if 'evidence_catalog' in wire_payload.get('source', {}):
        for name in ('description', 'facts', 'field_sections'):
            wire_payload['source'].pop(name, None)
    # Il profilo e le etichette non cambiano mai fra una chiamata e l'altra: stanno nel messaggio di
    # sistema, non nel payload, così il prefisso è identico e supera i 1.024 token che il provider
    # chiede per poter mettere qualcosa in cache. Sono testi dell'utente, non fonti raccolte.
    system = [prompt]
    # Le etichette di categoria sono identiche per ogni azienda: stanno nella parte stabile.
    # Lo schema della risposta le enumera comunque, ed e' quello a vincolare l'output.
    for holder in ('source', 'company'):
        options = wire_payload.get(holder, {}).pop('category_options', None) if isinstance(wire_payload.get(holder), dict) else None
        if options:
            wire_payload.setdefault('category_options', options)
    for name in ('candidate_profile', 'field_labels', 'category_options'):
        value = wire_payload.pop(name, None)
        if value:
            system.append(f'## {name}\n\n' + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)))
    content = '\n\n'.join(system)
    cache = config.get('cache', {})
    if cache.get('explicit'):
        # La cache implicita è dichiarata «not guaranteed» e non ha mai agganciato: questa è quella
        # deterministica, e va chiesta marcando il blocco da riusare.
        content = [{'type': 'text', 'text': content, 'cache_control': {'type': cache.get('type', 'ephemeral')}}]
    body = {**parameters, 'model': config['model'], 'stream': False,
            'messages': [{'role': 'system', 'content': content},
                         {'role': 'user', 'content': json.dumps(wire_payload, ensure_ascii=False, separators=(',', ':'))}]}
    if config.get('research_request'):
        search = dict(config['web_search'])
        if config['provider'] != 'openrouter' or not search.pop('enabled', False):
            raise ValueError('Company research requires enabled OpenRouter web search')
        if search.get('engine') != 'exa' or any(type(search.get(k)) is not int or search[k] < 1 for k in ('max_uses', 'max_results', 'max_total_results', 'max_characters')):
            raise ValueError('Web search requires Exa and positive explicit limits')
        body['tools'] = [{'type': 'openrouter:web_search', 'parameters': search}]
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=config['timeout_seconds']) as response:
            raw = response.read(config['max_response_bytes']+1)
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            error = json.loads(exc.read(8192)).get('error', {})
            if isinstance(error, dict):
                detail = str(error.get('message', error.get('code', ''))).replace(key, '<REDACTED>')[:1200]
        except (ValueError, OSError, AttributeError):
            pass
        # Lo stato viaggia sull'eccezione: una richiesta rifiutata dal provider non ha generato
        # niente, ed e' l'unico caso in cui chi chiama puo' riprovare senza rischiare di pagare due
        # volte la stessa risposta. La decisione resta al chiamante: qui non si riprova mai.
        rejected = ValueError(f'Remote HTTP {exc.code}; request rejected by the provider' + (': '+detail if detail else ''))
        rejected.status = exc.code
        pause = (exc.headers or {}).get('Retry-After', '')
        rejected.retry_after = int(pause) if str(pause).strip().isdigit() else 0
        raise rejected from None
    except (OSError, TimeoutError):
        raise ValueError('Remote transport failure; billing outcome unknown, no automatic retry') from None
    if len(raw) > config['max_response_bytes']:
        raise ValueError('Remote response exceeds configured size')
    try:
        data = json.loads(raw)
        choice = data['choices'][0]
        if choice.get('finish_reason') != 'stop' or choice['message'].get('tool_calls'):
            raise ValueError('Incomplete or tool response rejected')
        answer = json.loads(choice['message']['content'])
        return answer, data.get('usage', {}), choice['message'].get('annotations', [])
    except (AttributeError, KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError('Invalid remote JSON response') from None


def validate(task, answer, source, field_labels=None, annotations=None):
    """Reject malformed answers and invented quotes; semantic accuracy still needs QA."""
    if not isinstance(answer, dict):
        raise ValueError('Expected a JSON object')
    answer = copy.deepcopy(answer)
    if task == 'job-summary' and source.get('field_sections') and isinstance(answer.get('facts'), list):
        fields = {key: None for key in field_labels}
        for index, fact in enumerate(answer['facts']):
            if not isinstance(fact, dict) or set(fact) != {'field', 'text', 'quote'} or fact['field'] not in fields:
                raise ValueError('Invalid fact field')
            field = fact.pop('field')
            fact['section'] = source['field_sections'][field]
            fields[field] = (fields[field] or []) + [index]
        answer['fields'] = fields
    catalog = source.get('evidence_catalog')
    if catalog is not None:
        def resolve(reference):
            """Expand only known source references; never infer or repair their meaning."""
            if not isinstance(reference, str) or reference not in catalog:
                raise ValueError('Unknown source evidence reference')
            return catalog[reference]
        if task == 'selection' and isinstance(answer.get('evidence'), list):
            answer['evidence'] = [resolve(r) for r in answer['evidence']]
        elif isinstance(answer.get('facts'), list):
            for fact in answer['facts']:
                if isinstance(fact, dict) and 'quote' in fact:
                    fact['quote'] = resolve(fact['quote'])
    if isinstance(answer.get('fields'), dict):
        answer['fields'] = {k: None if v == [] else v for k, v in answer['fields'].items()}
    missing = answer.get('missing_information')
    if not isinstance(missing, list) or any(not isinstance(x, str) for x in missing):
        raise ValueError('Invalid missing_information')
    company_task = task.startswith('company-')
    texts = source['facts'] if company_task else [source['title'], source['description']]
    if catalog is not None:
        texts = list(catalog.values())
    citations = []
    if task == 'company-research':
        for annotation in annotations or []:
            citation = annotation.get('url_citation', {}) if isinstance(annotation, dict) and annotation.get('type') == 'url_citation' else {}
            url, content = citation.get('url'), citation.get('content')
            if isinstance(url, str) and urlsplit(url).scheme in ('http', 'https') and urlsplit(url).hostname and isinstance(content, str) and content.strip():
                citations.append(citation)
        texts = [c['content'] for c in citations]
    if task == 'selection':
        if set(answer) != {'decision', 'rationale', 'evidence', 'missing_information'}:
            raise ValueError('Unexpected selection fields')
        if answer['decision'] not in ('keep', 'exclude', 'review') or not isinstance(answer['rationale'], str) or not answer['rationale'].strip():
            raise ValueError('Invalid selection decision')
        quotes = answer['evidence']
        if not isinstance(quotes, list) or (answer['decision'] != 'review' and not quotes):
            raise ValueError('Decision requires evidence')
    else:
        expected = {'summary', 'facts', 'missing_information'} | ({'fields'} if field_labels is not None else set())
        if source.get('category_options'):
            expected.add('category')
            if answer.get('category') not in source['category_options']:
                raise ValueError('Unknown company category')
            if answer['category'] != 'Da classificare' and not answer.get('facts'):
                raise ValueError('Company category requires evidence')
        if set(answer) != expected or not isinstance(answer['summary'], str):
            raise ValueError('Unexpected summary fields')
        facts = answer['facts']
        if not isinstance(facts, list) or len(facts) > (8 if company_task else 20):
            raise ValueError('Invalid summary facts')
        allowed = {'business'} if company_task else {'responsibilities', 'requirements', 'conditions'}
        for fact in facts:
            keys = {'section', 'text', 'quote'} | ({'url'} if task == 'company-research' else set())
            if not isinstance(fact, dict) or set(fact) != keys or fact['section'] not in allowed or not isinstance(fact['text'], str) or not fact['text'].strip():
                raise ValueError('Invalid summary fact')
            if task == 'company-research' and not any(fact['url'] == c['url'] and isinstance(fact['quote'], str) and fact['quote'] in c['content'] for c in citations):
                raise ValueError('Web evidence not found in provider citation content')
        if field_labels is not None:
            fields = answer.get('fields')
            if not isinstance(fields, dict) or set(fields) != set(field_labels):
                raise ValueError('Structured fields must match configured schema')
            for indices in fields.values():
                if indices is not None and (not isinstance(indices, list) or not indices or any(type(i) is not int or not 0 <= i < len(facts) for i in indices)):
                    raise ValueError('Structured fields must be null or valid fact indices')
        if not facts and (not missing or answer['summary'].strip()):
            raise ValueError('Unsupported summary: empty facts require empty summary and missing information')
        quotes = [fact['quote'] for fact in facts]
    if any(not isinstance(q, str) or not q.strip() or not any(q in text for text in texts) for q in quotes):
        raise ValueError('Evidence quote not found in supplied source')
    return answer


def inputs(archive, task, record_id, config, job=None, cache=None):
    """Build identical source payloads for inference and display cache validation.

    `cache` viaggia fino a `company_input`: e' il dizionario di una sola preparazione, non una
    cache di processo. Senza, nulla cambia rispetto a prima.
    """
    if task.startswith('company-'):
        source = company_input(archive, record_id, cache)
        row = archive.db.execute('SELECT website FROM companies WHERE id=?', (record_id,)).fetchone()
        source['website'] = row['website']
    else:
        if job is None:
            job = json.loads(archive.db.execute('SELECT data FROM opportunities WHERE id=?', (record_id,)).fetchone()['data'])
        source = {k: job.get(k) for k in ('title', 'locations', 'salary', 'employment_type', 'source_url')}
        source['description'] = '\n'.join(lines(job.get('description', '')))
    payload = {'source': source}
    if task == 'company-summary' and config.get('company_categories_path'):
        source['category_options'] = [*json.loads((ROOT/config['company_categories_path']).read_text(encoding='utf-8')), 'Da classificare']
    if task in config.get('evidence_reference_tasks', []):
        texts = source['facts'] if task.startswith('company-') else [source['title'], source['description']]
        excerpts = [line.strip() for text in texts if text for line in text.splitlines() if line.strip()]
        if not task.startswith('company-'):
            excerpts += [f'{k}: {json.dumps(source[k], ensure_ascii=False)}' for k in ('locations', 'salary', 'employment_type') if source.get(k)]
        source['evidence_catalog'] = {f'S{i}': text for i, text in enumerate(excerpts)}
    if task == 'selection':
        payload['candidate_profile'] = (ROOT/config['profile_path']).read_text(encoding='utf-8')
        cid = archive.db.execute('SELECT company_id FROM opportunities WHERE id=?', (record_id,)).fetchone()[0]
        payload['company_context'] = company_input(archive, cid, cache)
        research = current_result(archive, 'company-research', cid, config, cache=cache)
        if research:
            payload['company_context']['verified_web_facts'] = research['facts']
    if task == 'job-summary':
        payload['field_labels'] = json.loads((ROOT/config['job_fields_path']).read_text(encoding='utf-8'))
        if config.get('field_sections_path'):
            source['field_sections'] = json.loads((ROOT/config['field_sections_path']).read_text(encoding='utf-8'))
    if task in config.get('response_schemas', {}):
        schema = json.loads((ROOT/config['response_schemas'][task]).read_text(encoding='utf-8'))
        # Nessun enum degli ID di citazione: cambierebbe lo schema a ogni record e romperebbe il
        # prefisso che la cache del provider riusa. validate() li rifiuta già uno per uno.
        reference = {'type': 'string'}
        if task == 'selection':
            schema['properties']['evidence']['items'] = reference
        else:
            schema['properties']['facts']['items']['properties']['quote'] = reference
            if source.get('category_options'):
                schema['properties']['category'] = {'type': 'string', 'enum': source['category_options']}
        payload['response_schema'] = schema
    return payload


def signature(config, task):
    """Include generation and web-search settings in the cache identity."""
    settings = {k: config[k] for k in ('endpoint', 'model', 'parameters', 'schema_version')}
    if task == 'company-research':
        settings['web_search'] = config['web_search']
    return settings


def current_result(archive, task, record_id, config, job=None, cache=None):
    """Return a derivative only when its actual source, prompt and configuration still match."""
    row = archive.db.execute('SELECT cache_key,data FROM enrichments WHERE task=? AND record_id=?', ('remote:'+task, record_id)).fetchone()
    if not row:
        return None
    prompt = (ROOT/config['prompts'][task]).read_text(encoding='utf-8')
    payload = inputs(archive, task, record_id, config, job, cache)
    fingerprint = digest({'source': payload, 'prompt': prompt, 'settings': signature(config, task)})
    stored = json.loads(row['data'])
    if stored.get('batch_signature'):
        from jobhunter.evaluation.company_batch import signature as batch_signature
        if stored['batch_signature'] != batch_signature(config):
            return None
    return stored['result'] if row['cache_key'] == fingerprint else None


def presentation(archive, company, config_path=None):
    """Attach current summaries and conservative duplicate hints without changing records."""
    config = json.loads(Path(config_path or ROOT/'config/remote_llm.json').read_text(encoding='utf-8'))

    company['remote_summary'] = current_result(archive, 'company-summary', company['id'], config)
    research = current_result(archive, 'company-research', company['id'], config)
    if not company['remote_summary'] or not company['remote_summary']['summary']:
        company['remote_summary'] = research or company['remote_summary']
    company['job_field_labels'] = json.loads((ROOT/config['job_fields_path']).read_text(encoding='utf-8'))
    groups = {}
    for job in company['opportunities']:
        job['remote_summary'] = current_result(archive, 'job-summary', job['id'], config, job)
        job['possible_duplicates'] = []
        normalized = ' '.join(lines(job.get('description', ''))).casefold()
        if config['duplicates']['enabled'] and len(normalized) >= config['duplicates']['minimum_description_chars']:
            # ponytail: exact text only; semantic near-duplicates require a separate reviewed comparison.
            key = digest([job['title'].strip().casefold(), job['locations'], normalized])
            groups.setdefault(key, []).append(job)
    for jobs in groups.values():
        if len(jobs) > 1:
            for job in jobs:
                job['possible_duplicates'] = [other['id'] for other in jobs if other['id'] != job['id']]


def run(archive, task, limit=None, execute=False, config_path=None, record_id=None, include_excluded=False):
    """Preview a bounded batch or save validated derivatives without changing eligibility."""
    if task not in TASKS:
        raise ValueError('Unknown remote task')
    config = json.loads(Path(config_path or ROOT/'config/remote_llm.json').read_text(encoding='utf-8'))
    limit = config['default_limit'] if limit is None else limit
    if not 1 <= limit <= config['max_batch']:
        raise ValueError('Invalid remote batch size')
    prompt = (ROOT/config['prompts'][task]).read_text(encoding='utf-8')
    settings = signature(config, task)
    if task == 'company-research' and (config['provider'] != 'openrouter' or not config['web_search']['enabled']):
        raise ValueError('Company research requires enabled OpenRouter web search')
    task_key = 'remote:'+task
    report = {'task': task, 'mode': 'execute' if execute else 'preview', 'model': config['model'],
              'selected': 0, 'processed': 0, 'cached': 0, 'skipped': 0, 'failed': [], 'items': [], 'status': 'success'}
    if task.startswith('company-'):
        records = archive.db.execute('SELECT id FROM companies' + (' WHERE id=?' if record_id else '') + ' ORDER BY id', (record_id,) if record_id else ()).fetchall()
        decisions = {}
    else:
        records = archive.db.execute('SELECT id,data FROM opportunities' + (' WHERE id=?' if record_id else '') + ' ORDER BY id', (record_id,) if record_id else ()).fetchall()
        decisions = {} if include_excluded else archive.evaluations()
    selected = []
    for row in records:
        if record_id and row['id'] != record_id:
            continue
        payload = inputs(archive, task, row['id'], config)
        source = payload['source']
        if task.startswith('company-'):
            # Research only fills a gap. Sectors alone do not describe a business.
            description = archive.db.execute('SELECT description FROM companies WHERE id=?', (row['id'],)).fetchone()['description']
            if (task == 'company-summary' and not source['facts']) or (task == 'company-research' and (description or source['job_evidence'])):
                report['skipped'] += 1
                continue
        else:
            if not include_excluded and decisions[row['id']]['status'] == 'excluded':
                report['skipped'] += 1
                continue
            if task == 'job-summary' and not source['description']:
                report['skipped'] += 1
                continue
        fingerprint = digest({'source': payload, 'prompt': prompt, 'settings': settings})
        cached = archive.db.execute('SELECT cache_key FROM enrichments WHERE task=? AND record_id=?', (task_key, row['id'])).fetchone()
        if cached and cached['cache_key'] == fingerprint:
            report['cached'] += 1
            continue
        if len(prompt)+len(json.dumps(payload, ensure_ascii=False)) > config['max_input_chars']:
            report['skipped'] += 1
            continue
        selected.append((row['id'], source, payload, fingerprint))
        if len(selected) >= limit:
            break
    report['selected'] = len(selected)
    if not execute:
        report['items'] = [{'id': oid, 'input_chars': len(prompt)+len(json.dumps(payload, ensure_ascii=False))} for oid, _, payload, _ in selected]
        logger.info('Remote preview: %s records; no API calls', len(selected))
        return report
    key = api_key(config) if selected else None
    output = ROOT/config['output_directory']/now().replace(':', '').replace('+', '_')
    output.mkdir(parents=True)
    for oid, source, payload, fingerprint in selected:
        received = False
        try:
            logger.info('Remote %s: processing %s', task, oid)
            answer, usage, annotations = request({**config, 'research_request': task == 'company-research'}, prompt, payload, key)
            received = True
            report['items'].append({'id': oid, 'usage': usage})
            result = validate(task, answer, source, payload.get('field_labels'), annotations)
            report['items'][-1]['result'] = result
            stored = {'result': result, 'usage': usage, 'annotations': annotations, 'settings': settings, 'prompt_hash': digest(prompt)}
            with archive.db:
                archive.db.execute('INSERT OR REPLACE INTO enrichments VALUES(?,?,?,?,?,?,?)',
                                   (task_key, oid, digest(payload), fingerprint, json.dumps(stored, ensure_ascii=False), config['model'], now()))
                if task == 'company-summary' and 'category' in result:
                    archive.db.execute("INSERT INTO categories VALUES(?,?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET category=excluded.category,method=excluded.method,reason=excluded.reason,updated_at=excluded.updated_at WHERE categories.method!='chat'",
                                       (oid, result['category'], 'remote', result['summary'] or 'Dati aziendali insufficienti', now()))
            report['processed'] += 1
        except (ValueError, TypeError, OSError) as exc:
            validation_error = received and isinstance(exc, (ValueError, TypeError))
            report['failed'].append({'id': oid, 'error': str(exc), 'validation_error': validation_error})
            if validation_error:
                report['items'][-1]['rejected_answer'] = answer
            report['status'] = 'partial'
            logger.warning('Remote task stopped for %s: %s', oid, exc)
            break
        finally:
            (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        time.sleep(config['request_delay_seconds'])
    report['report_path'] = str(output/'report.json')
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    archive.run('remote_llm', report['status'], report)
    return report
