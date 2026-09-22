"""Archive metrics shared by dashboard and CLI, with a recorded completion timestamp."""

from collections import Counter
import json
import logging
from jobhunter.workspace import now

logger = logging.getLogger(__name__)


def summary(archive, eligibility=''):
    """Count unique roles per category and per geographic bucket, with explicit missing-data health."""
    if eligibility not in ('', 'potential', 'review', 'excluded'):
        raise ValueError('Unknown eligibility scope')
    from jobhunter.exploration import places
    archive.refresh_search_eligibility()
    # La geografia arriva dalla stessa mappatura della tab Aziende: due letture non possono dissentire.
    places.refresh(archive)
    continents = places.mapping()['continents']
    countries_of = {}
    for row in archive.db.execute("SELECT opportunity_id,country FROM places WHERE country!=''"):
        countries_of.setdefault(row['opportunity_id'], set()).add(row['country'])
    distributions = {k: Counter() for k in ('categories', 'countries', 'continents', 'selection')}
    health = Counter({k: 0 for k in ('with_description', 'categorized', 'country_known', 'with_salary', 'with_posted_date')})
    companies = set()
    total = 0
    for row in archive.db.execute("""SELECT o.id,o.company_id,o.data,e.status,
            COALESCE((SELECT category FROM categories c WHERE c.company_id=o.company_id ORDER BY rank LIMIT 1),
                     'Da classificare') category
            FROM opportunities o JOIN search_eligibility e ON e.opportunity_id=o.id"""):
        job = json.loads(row['data'])
        status = row['status']
        if eligibility and status != eligibility:
            continue
        total += 1
        companies.add(row['company_id'])
        distributions['selection'][status] += 1
        distributions['categories'][row['category']] += 1
        countries = countries_of.get(row['id'], set())
        distributions['countries'].update(countries or {'Non determinato'})
        distributions['continents'].update({continents[c] for c in countries} or {'Non determinato'})
        salary = job.get('salary') or {}
        health.update({
            'with_description': bool(job.get('description', '').strip()),
            'categorized': row['category'] != 'Da classificare',
            'country_known': bool(countries),
            'with_salary': salary.get('min') is not None or salary.get('max') is not None or bool(salary.get('raw_text')),
            'with_posted_date': bool(job.get('posted_at')),
        })
    logger.info('Archive metrics: %s roles, scope=%s', total, eligibility or 'all')
    with archive.db:
        archive.db.execute("INSERT OR REPLACE INTO pipeline_updates VALUES('analytics',?)", (now(),))
    return {'generated_at': now(), 'eligibility': eligibility, 'total': total, 'companies': len(companies),
            'health': dict(health), **{key: [{'label': label, 'count': count} for label, count in sorted(value.items(), key=lambda pair: (-pair[1], pair[0]))] for key, value in distributions.items()}}
