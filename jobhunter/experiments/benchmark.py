"""Measure detail concurrency on disjoint, host-balanced batches of real missing jobs."""

from collections import defaultdict
import json
import logging
import random
import time
from urllib.parse import urlsplit

from jobhunter.acquisition.descriptions import has_description, recover
from jobhunter.evaluation.selection import evaluate, filters
from jobhunter.workspace import ROOT, now

logger = logging.getLogger(__name__)


def run(archive, batch_size=50, worker_counts=(1, 4, 8), seed=20260908):
    """Save recovered texts and compare useful throughput; stop escalation on any access block."""
    cfg = json.loads((ROOT / 'config/descriptions.json').read_text())
    if not 1 <= batch_size <= cfg['max_limit'] or not worker_counts or worker_counts[0] != 1:
        raise ValueError('Use a supported batch size and a serial first batch')
    if any(not 1 <= w <= cfg.get('max_workers', 16) for w in worker_counts):
        raise ValueError('Invalid worker count')
    buckets = defaultdict(list)
    rules = filters()
    for row in archive.db.execute('SELECT id,data FROM opportunities ORDER BY id'):
        job = json.loads(row['data'])
        host = urlsplit(job['source_url']).hostname
        if not has_description(job.get('description')) and host in cfg['allowed_hosts']:
            decision = evaluate({**job, 'description': ''}, rules)
            if decision['status'] != 'excluded':
                buckets[host.removeprefix('www.')].append(row['id'])
    rng = random.Random(seed)
    for values in buckets.values():
        rng.shuffle(values)
    # Round-robin hosts gives each batch comparable source exposure without refetching URLs.
    ids = []
    while buckets and len(ids) < batch_size * len(worker_counts):
        for host in sorted(list(buckets)):
            ids.append(buckets[host].pop())
            if not buckets[host]:
                del buckets[host]
    if len(ids) < batch_size * len(worker_counts):
        raise ValueError('Not enough eligible missing jobs for disjoint benchmark batches')
    output = ROOT / 'data/benchmarks' / now().replace(':', '').replace('+', '_')
    output.mkdir(parents=True)
    report = {'started_at': now(), 'batch_size': batch_size, 'seed': seed, 'batches': [],
              'status': 'running', 'comparison': 'Disjoint host-balanced samples; not identical pages or a sustained-load guarantee'}
    manifest = [{'workers': w, 'ids': ids[i*batch_size:(i+1)*batch_size]} for i, w in enumerate(worker_counts)]
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for batch in manifest:
        logger.info('Benchmark: %s workers, %s jobs', batch['workers'], batch_size)
        cpu = time.process_time()
        result = recover(archive, limit=batch_size, opportunity_ids=set(batch['ids']), workers=batch['workers'])
        seconds = result['elapsed_seconds']
        sample = {k: result[k] for k in ('workers', 'saved', 'attempted', 'failed', 'http_requests', 'blocked_hosts', 'raw_directory', 'saved_per_minute')}
        sample.update(seconds=seconds, cpu_seconds=round(time.process_time()-cpu, 3))
        durations = sorted(item['seconds'] for item in result['items'])
        sample.update(p50_job_seconds=durations[len(durations)//2], p95_job_seconds=durations[min(len(durations)-1, int(len(durations)*.95))])
        sample['sources'] = dict((host, sum(urlsplit(item['url']).hostname == host for item in result['items'])) for host in sorted({urlsplit(item['url']).hostname for item in result['items']}))
        baseline = report['batches'][0]['saved_per_minute'] if report['batches'] else result['saved_per_minute']
        sample['useful_speedup'] = round(result['saved_per_minute']/baseline, 2) if baseline else None
        report['batches'].append(sample)
        report['status'] = 'blocked' if result['blocked_hosts'] else 'running'
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        logger.info('Benchmark result: %s', sample)
        if result['blocked_hosts']:
            break
    if report['status'] != 'blocked':
        report['status'] = 'complete'
    report['finished_at'] = now()
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return {'status': report['status'], 'report': str(output / 'report.json'), 'batches': report['batches']}
