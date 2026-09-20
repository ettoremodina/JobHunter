"""Offline snapshot repair and evidence-based product quality checks."""

from collections import Counter, defaultdict
import json
import logging
from pathlib import Path
import random

from jobhunter.acquisition.descriptions import extract, has_description
from jobhunter.evaluation.selection import evaluate, filters
from jobhunter.workspace import ROOT, identity, now

from jobhunter.operations import cancellation

logger = logging.getLogger(__name__)


def reparse(archive):
    """Restore paragraph boundaries from saved pages only when non-whitespace content agrees."""
    report = {'updated': 0, 'no_snapshot': 0, 'different_content': 0, 'unchanged': 0, 'errors': []}
    browser_pages = {}
    for path in sorted((ROOT / 'data/collection').glob('*/climatebase.org/*.html')):
        browser_pages[path.stem] = path
    for row in archive.db.execute('SELECT id,data FROM opportunities').fetchall():
        cancellation.check()
        job = json.loads(row['data'])
        provenance = job.get('description_provenance') or {}
        path = Path(provenance['raw_file']) if provenance.get('raw_file') else browser_pages.get(identity(job['source_url']))
        if not path or not path.is_file():
            report['no_snapshot'] += 1
            continue
        try:
            description, method, _ = extract(path.read_text(encoding='utf-8'), provenance.get('url') or job['source_url'])
            if description == job.get('description'):
                report['unchanged'] += 1
            elif not has_description(description) or ''.join(description.split()) != ''.join((job.get('description') or '').split()):
                report['different_content'] += 1
            else:
                # Reformatting is not a fresh remote check; retain the original retrieval date.
                evidence = {**provenance, 'url': provenance.get('url') or job['source_url'], 'method': method,
                            'raw_file': str(path), 'reparsed_at': now()}
                archive.save_description(row['id'], description, evidence, replace=True)
                report['updated'] += 1
        except (OSError, ValueError) as exc:
            report['errors'].append({'id': row['id'], 'error': str(exc)})
    archive.run('snapshot_reparse', 'partial' if report['errors'] else 'success', report)
    logger.info('Offline snapshot repair: %s updated', report['updated'])
    return report


def quality(archive):
    """Measure source usefulness and suggest duplicates without merging or inventing closed jobs."""
    sources = defaultdict(Counter)
    duplicates = defaultdict(list)
    evaluations = archive.evaluations()
    observations = defaultdict(set)
    from urllib.parse import urlsplit
    for row in archive.db.execute('SELECT * FROM observations'):
        host = urlsplit(row['source_url']).hostname or ''
        source = 'linkedin' if host.endswith('linkedin.com') else 'indeed' if 'indeed.' in host else row['source']
        observations[row['opportunity_id']].add(source)
    for row in archive.db.execute('SELECT id,company_id,data FROM opportunities'):
        job = json.loads(row['data'])
        decision = evaluations[row['id']]
        for source in observations[row['id']]:
            sources[source]['unique_opportunities'] += 1
            sources[source]['with_description'] += has_description(job.get('description'))
            sources[source]['potential'] += decision['status'] == 'potential'
            sources[source]['potential_with_description'] += decision['status'] == 'potential' and has_description(job.get('description'))
        key = (row['company_id'], job['title'].casefold(), tuple(sorted(x.casefold() for x in job['locations'])))
        duplicates[key].append({'id': row['id'], 'url': job['application_url']})
    groups = [{'company_id': key[0], 'title': key[1], 'opportunities': values} for key, values in duplicates.items() if len(values) > 1]
    return {'generated_at': now(), 'sources': [{'source': name, **values} for name, values in sorted(sources.items())],
            'possible_duplicate_groups': len(groups), 'duplicate_examples': groups[:30],
            'description_outcomes': [dict(r) for r in archive.db.execute('SELECT status,count(*) AS opportunities FROM description_attempts GROUP BY status')],
            'note': 'Source counts can overlap. Same title/company/location suggests verification, not proof of duplicate identity. Missing pages do not prove a position is closed.'}


def prepare_review(archive, limit=20):
    """Save a balanced manual calibration sample with complete descriptions and untouched holdout rows."""
    if not 3 <= limit <= 100:
        raise ValueError('Review sample size must be from 3 to 100')
    evaluations, groups = archive.evaluations(), defaultdict(list)
    for row in archive.db.execute('SELECT id,company_id,data FROM opportunities ORDER BY id'):
        job = json.loads(row['data'])
        if not has_description(job.get('description')):
            continue
        decision = evaluations[row['id']]
        groups[decision['status']].append({'id': row['id'], 'company_id': row['company_id'], 'job': job,
                                          'suggestion': decision, 'expected_status': None, 'reviewer_note': ''})
    rng = random.Random(20260908)
    for group in groups.values():
        rng.shuffle(group)
    sample = []
    while groups and len(sample) < limit:
        for name in sorted(list(groups)):
            if len(sample) == limit:
                break
            sample.append(groups[name].pop())
            if not groups[name]:
                del groups[name]
    for i, item in enumerate(sample):
        item['split'] = 'holdout' if i % 5 == 4 else 'development'
    path = ROOT / 'data/manual-review' / (now().replace(':', '').replace('+', '_') + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'created_at': now(), 'instructions': 'Fill expected_status with potential, review or excluded based on personal review. Leave unknown cases null. Do not tune rules from holdout labels.', 'items': sample}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'path': str(path), 'items': len(sample), 'counts': dict(Counter(x['suggestion']['status'] for x in sample)), 'labelled': 0}


def score_review(path):
    """Compare current filters with explicit human labels; never treat unlabelled rows as correct."""
    items = json.loads(Path(path).read_text(encoding='utf-8'))['items']
    matrices, pending, false_exclusions = defaultdict(Counter), 0, []
    rules = filters()
    for item in items:
        expected = item.get('expected_status')
        if expected is None:
            pending += 1
            continue
        if expected not in ('potential', 'review', 'excluded'):
            raise ValueError('Invalid expected_status')
        predicted = evaluate(item['job'], rules)['status']
        matrices[item['split']][expected + ' -> ' + predicted] += 1
        if expected == 'potential' and predicted == 'excluded':
            false_exclusions.append(item['id'])
    return {'labelled': len(items)-pending, 'pending': pending, 'by_split': {k: dict(v) for k, v in matrices.items()}, 'false_exclusions': false_exclusions}
