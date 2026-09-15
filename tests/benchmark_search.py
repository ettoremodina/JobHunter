"""Repeatable search timings on disposable data; run from the repository root."""

import argparse
import cProfile
import hashlib
import json
import pstats
import statistics
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter.workspace import Archive, settings
from jobhunter.dashboard import create_server


def fixture(path, companies):
    """Build ten mixed-location roles per company and realistic sized saved verdicts."""
    archive = Archive(path)
    archive.ingest([
        {'company_name': f'Company {c:05}', 'title': 'Data Scientist' if n % 2 else 'Senior engineer',
         'location': 'Milano, IT' if n % 3 else 'London, UK',
         'description': 'Analyze data with Python. ' * 40,
         'source_url': f'https://example.org/{c}/{n}'}
        for c in range(companies) for n in range(10)], 'fixture')
    payload = json.dumps({'result': {'decision': 'keep', 'rationale': 'Saved reasoning. ' * 100,
                                    'evidence': ['Analyze data with Python.']}})
    archive.db.execute("""INSERT INTO enrichments SELECT 'remote:selection',id,content_hash,
        'fixture',?,'fixture',first_seen FROM opportunities""", (payload,))
    archive.db.commit()
    archive.db.execute("UPDATE companies SET first_seen='2026-01-01',last_seen='2026-01-01'")
    archive.db.commit()
    selected_tier = archive.search(city='Milano')['items'][0]['tier']
    archive.close()
    return selected_tier


def main():
    """Report cold-connection medians, result digests and an optional search profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--companies', type=int, default=1000)
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--browser', action='store_true', help='Also measure load() and rendering in Chromium')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'benchmark.db'
        selected_tier = fixture(path, args.companies)
        server = create_server(path, settings(), 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cases = {'page': {}, 'next': {'offset': 30}, 'text': {'query': 'Python'},
                 'city': {'city': 'Milano'}, 'eligibility': {'eligibility': 'potential'},
                 'tier': {'tier': selected_tier}}
        try:
            for name, params in cases.items():
                timings = {'search': [], 'http': []}
                for _ in range(args.repeat):
                    with closing(Archive(path)) as archive:
                        start = time.perf_counter()
                        result = archive.search(**params)
                        timings['search'].append((time.perf_counter() - start) * 1000)
                    start = time.perf_counter()
                    with urlopen(f'http://127.0.0.1:{server.server_port}/api/companies?{urlencode(params)}') as response:
                        remote = json.load(response)
                    timings['http'].append((time.perf_counter() - start) * 1000)
                    assert remote == result
                digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:12]
                print(name, {k: round(statistics.median(v), 2) for k, v in timings.items()},
                      'total', result['total'], 'digest', digest)
            if args.browser:
                browser_timings(server.server_port, args.repeat)
            if args.profile:
                archive = Archive(path)
                profiler = cProfile.Profile()
                profiler.runcall(archive.search, city='Milano')
                pstats.Stats(profiler).strip_dirs().sort_stats('cumtime').print_stats(18)
                archive.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def browser_timings(port, repeat):
    """Time the existing page loader, separating fetch from JS/DOM work, without writes."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f'http://127.0.0.1:{port}')
        page.wait_for_load_state('networkidle')
        for name, params in [('page', {}), ('next', {'offset': 30}),
                             ('text', {'query': 'Python'}), ('city', {'city': 'Milano'})]:
            samples = []
            for _ in range(repeat):
                samples.append(page.evaluate('''async (values) => {
                    document.getElementById('query').value = values.query || '';
                    document.getElementById('city').value = values.city || '';
                    offset = values.offset || 0;
                    performance.clearResourceTimings();
                    const start = performance.now();
                    await load();
                    const end = performance.now();
                    const resource = performance.getEntriesByType('resource').find(r => r.name.includes('/api/companies?'));
                    return {load_ms: end-start, fetch_ms: resource.duration,
                            after_response_ms: end-resource.responseEnd};
                }''', params))
            print('browser', name, {key: round(statistics.median(s[key] for s in samples), 2)
                                    for key in samples[0]})
        browser.close()


if __name__ == '__main__':
    main()
