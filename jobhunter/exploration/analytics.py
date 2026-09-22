"""Archive metrics shared by dashboard and CLI, with a recorded completion timestamp."""

from collections import Counter
import json
import logging

from jobhunter.evaluation import tier
from jobhunter.evaluation.selection import filters
from jobhunter.workspace import now

logger = logging.getLogger(__name__)


def summary(archive, eligibility=''):
    """Return processing coverage, judge outcomes and scoped archive distributions."""
    if eligibility not in ('', 'potential', 'review', 'excluded'):
        raise ValueError('Unknown eligibility scope')
    from jobhunter.exploration import places
    archive.refresh_search_eligibility()
    places.refresh(archive)
    continents = places.mapping()['continents']
    countries_of = {}
    for row in archive.db.execute("SELECT opportunity_id,country FROM places WHERE country!=''"):
        countries_of.setdefault(row['opportunity_id'], set()).add(row['country'])

    attempted = {row[0] for row in archive.db.execute('SELECT opportunity_id FROM description_attempts')}
    distributions = {key: Counter() for key in ('categories', 'countries', 'continents', 'selection')}
    health = Counter({key: 0 for key in ('with_description', 'with_usable_description', 'categorized',
                                         'country_known', 'with_salary', 'with_posted_date')})
    coverage = Counter()
    outcomes = Counter()
    scoped_companies = set()
    roles_by_company = {}
    total = 0

    rows = archive.db.execute("""SELECT o.id,o.company_id,o.data,e.status,e.decision,n.data jev_data,
            COALESCE((SELECT category FROM categories c WHERE c.company_id=o.company_id ORDER BY rank LIMIT 1),
                     'Da classificare') category
            FROM opportunities o
            JOIN search_eligibility e ON e.opportunity_id=o.id
            LEFT JOIN enrichments n ON n.task='jev:selection' AND n.record_id=o.id""")
    for row in rows:
        job = json.loads(row['data'])
        decision = json.loads(row['decision'])
        usable = bool(decision.get('verification', {}).get(
            'description_usable', (job.get('description') or '').strip()))
        coverage['archive'] += 1
        coverage['description_usable'] += usable
        if not usable:
            coverage['description_blocked' if row['id'] in attempted else 'description_pending'] += 1

        status = row['status']
        if status == 'excluded':
            outcomes['regex_discarded'] += 1
            outcomes['overall_discarded'] += 1
        else:
            outcomes['regex_forwarded'] += 1
            if not usable:
                coverage['jev_blocked'] += 1
            elif not row['jev_data']:
                coverage['jev_pending'] += 1
            else:
                result = json.loads(row['jev_data']).get('result') or {}
                jev = result.get('decision')
                if jev in ('keep', 'exclude', 'review'):
                    outcomes['jev_' + jev] += 1
                    outcomes['overall_' + {'keep': 'compatible', 'exclude': 'discarded',
                                            'review': 'review'}[jev]] += 1
                    if jev == 'keep':
                        roles_by_company.setdefault(row['company_id'], []).append({
                            'verdetto': tier.KEEP,
                            'primary': decision.get('career_priority') == 'primary',
                        })
                else:
                    coverage['jev_pending'] += 1

        if eligibility and status != eligibility:
            continue
        total += 1
        scoped_companies.add(row['company_id'])
        distributions['selection'][status] += 1
        distributions['categories'][row['category']] += 1
        countries = countries_of.get(row['id'], set())
        distributions['countries'].update(countries or {'Non determinato'})
        distributions['continents'].update({continents[country] for country in countries} or {'Non determinato'})
        salary = job.get('salary') or {}
        health.update({
            'with_description': bool((job.get('description') or '').strip()),
            'with_usable_description': usable,
            'categorized': row['category'] != 'Da classificare',
            'country_known': bool(countries),
            'with_salary': salary.get('min') is not None or salary.get('max') is not None or bool(salary.get('raw_text')),
            'with_posted_date': bool(job.get('posted_at')),
        })

    company_ids = [row[0] for row in archive.db.execute('SELECT id FROM companies')]
    assigned = {}
    for row in archive.db.execute('SELECT company_id,category FROM categories ORDER BY rank'):
        assigned.setdefault(row['company_id'], []).append(row['category'])
    preferred = filters().get('preferred_categories', [])
    tiers = Counter()
    compatible_company_tiers = Counter()
    company_axis = Counter()
    for company_id in company_ids:
        verdict = tier.company_verdict(assigned.get(company_id, []), preferred)
        company_tier = tier.tier(verdict, roles_by_company.get(company_id, []))
        company_axis[verdict] += 1
        tiers[company_tier] += 1
        if roles_by_company.get(company_id):
            compatible_company_tiers[company_tier] += 1

    jev_analyzed = outcomes['jev_keep'] + outcomes['jev_exclude'] + outcomes['jev_review']
    completed_outcomes = outcomes['regex_discarded'] + jev_analyzed
    processing = [
        {'id': 'collection', 'label': 'Raccolta', 'input': coverage['archive'],
         'completed': coverage['archive'], 'pending': 0, 'blocked': 0},
        {'id': 'descriptions', 'label': 'Recupero descrizioni', 'input': coverage['archive'],
         'completed': coverage['description_usable'], 'pending': coverage['description_pending'],
         'blocked': coverage['description_blocked']},
        {'id': 'regex', 'label': 'Regex', 'input': coverage['archive'],
         'completed': coverage['archive'], 'pending': 0, 'blocked': 0},
        {'id': 'jev', 'label': 'Jev', 'input': outcomes['regex_forwarded'],
         'completed': jev_analyzed, 'pending': coverage['jev_pending'], 'blocked': coverage['jev_blocked']},
    ]
    compatibility = {
        'regex': {'base': coverage['archive'], 'discarded': outcomes['regex_discarded'],
                  'forwarded': outcomes['regex_forwarded']},
        'jev': {'base': jev_analyzed, 'compatible': outcomes['jev_keep'],
                'discarded': outcomes['jev_exclude'], 'review': outcomes['jev_review']},
        'overall': {'base': completed_outcomes, 'compatible': outcomes['overall_compatible'],
                    'discarded': outcomes['overall_discarded'], 'review': outcomes['overall_review'],
                    'without_jev_outcome': coverage['jev_pending'] + coverage['jev_blocked'],
                    'jev_pending': coverage['jev_pending'], 'jev_blocked': coverage['jev_blocked']},
    }
    company_flow = {
        'compatible_jobs': outcomes['jev_keep'],
        'companies_with_compatible_jobs': len(roles_by_company),
        'total': len(company_ids),
        'categorized': sum(1 for company_id in company_ids if assigned.get(company_id)),
        'unclassified': sum(1 for company_id in company_ids if not assigned.get(company_id)),
        'axis': {key: company_axis[key] for key in (tier.INTERESTING, tier.NO_EVIDENCE, tier.NOT_INTERESTING)},
        'compatible_company_tiers': {key: compatible_company_tiers[key] for key in tier.TIERS},
        'tiers': {key: tiers[key] for key in tier.TIERS},
    }
    logger.info('Archive metrics: %s roles, scope=%s', total, eligibility or 'all')
    with archive.db:
        archive.db.execute("INSERT OR REPLACE INTO pipeline_updates VALUES('analytics',?)", (now(),))
    return {'generated_at': now(), 'eligibility': eligibility, 'total': total,
            'companies': len(scoped_companies), 'archive_total': coverage['archive'],
            'archive_companies': len(company_ids), 'processing': processing,
            'compatibility': compatibility, 'company_flow': company_flow,
            'health': dict(health),
            **{key: [{'label': label, 'count': count}
                     for label, count in sorted(value.items(), key=lambda pair: (-pair[1], pair[0]))]
               for key, value in distributions.items()}}
