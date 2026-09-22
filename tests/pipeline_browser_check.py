"""Check the Pipeline and Metrics summaries in Chromium against a disposable archive."""

import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import expect, sync_playwright

from jobhunter.evaluation import remote_llm
from jobhunter.exploration.dashboard import create_server
from jobhunter.workspace import Archive, settings


def save_remote_result(archive, opportunity_id, result, current=True):
    """Store a fixture result with either a current or deliberately stale cache key."""
    cfg = json.loads((remote_llm.ROOT / 'config/remote_llm.json').read_text(encoding='utf-8'))
    job = json.loads(archive.db.execute(
        'SELECT data FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()[0])
    payload = remote_llm.inputs(archive, 'selection', opportunity_id, cfg, job=job, cache={})
    prompt = (remote_llm.ROOT / cfg['prompts']['selection']).read_text(encoding='utf-8')
    key = remote_llm.digest({'source': payload, 'prompt': prompt,
                             'settings': remote_llm.signature(cfg, 'selection')}) if current else 'stale'
    archive.db.execute('INSERT INTO enrichments VALUES(?,?,?,?,?,?,?)',
                       ('remote:selection', opportunity_id, 'source', key,
                        json.dumps({'result': result}), 'browser-fixture', '2026-01-01'))


def main():
    """Render both views at desktop and phone widths without touching the real archive."""
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / 'pipeline.sqlite3'
        archive = Archive(database)
        archive.ingest([
            {'company_name': 'Signal Works', 'title': 'Data Scientist',
             'description': 'Build forecasting models.', 'source_url': 'https://example.org/keep'},
            {'company_name': 'Signal Works', 'title': 'Growth Hacker Review',
             'description': 'Analyse experiments and product metrics.', 'source_url': 'https://example.org/review'},
            {'company_name': 'Signal Works', 'title': 'Growth Hacker Ready',
             'description': 'Analyse experiments and product metrics.', 'source_url': 'https://example.org/ready'},
            {'company_name': 'Signal Works', 'title': 'Growth Hacker Blocked',
             'description': '', 'source_url': 'https://example.org/blocked'},
            {'company_name': 'Signal Works', 'title': 'Growth Hacker Stale',
             'description': 'Analyse experiments and product metrics.', 'source_url': 'https://example.org/stale'},
        ], 'browser-fixture')
        archive.evaluations()
        ids = {json.loads(row['data'])['title']: row['id']
               for row in archive.db.execute('SELECT id,data FROM opportunities')}
        save_remote_result(archive, ids['Growth Hacker Review'], {'decision': 'review'})
        save_remote_result(archive, ids['Growth Hacker Stale'], {'decision': 'keep'}, current=False)
        archive.close()

        server = create_server(database, settings(), 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.wait_for_load_state('networkidle')

                page.get_by_role('button', name='Pipeline', exact=True).click()
                expect(page.locator('#pipeline-status')).to_contain_text('Archivio: 5 annunci · 1 aziende')
                expect(page.locator('#pipeline-active')).to_contain_text('Attività della web app')
                expect(page.locator('#pipeline-active')).to_contain_text('Nessun comando attivo')
                expect(page.locator('#pipeline-handoff')).to_contain_text('Indecisi anche per System One')
                expect(page.locator('#pipeline-handoff')).to_contain_text('Già valutati, ancora indecisi')
                expect(page.locator('#pipeline-handoff')).to_contain_text('Da aggiornare o verificare')
                expect(page.locator('#pipeline-handoff')).to_contain_text('Candidati al giudice System One')
                expect(page.locator('#pipeline-steps > li')).to_have_count(6)
                assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')

                page.get_by_role('button', name='Metriche', exact=True).click()
                expect(page.locator('.journey-handoff')).to_contain_text('Partizione corrente · annunci')
                expect(page.locator('.journey-handoff')).to_contain_text('Valutati dal remoto, ancora indecisi')
                expect(page.locator('.journey-chart rect.track')).not_to_have_count(0)
                expect(page.locator('.journey-chart rect.seg')).not_to_have_count(0)

                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_role('button', name='Pipeline', exact=True).click()
                expect(page.locator('#pipeline-handoff')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
                page.get_by_role('button', name='Metriche', exact=True).click()
                expect(page.locator('.journey-handoff')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
        if errors:
            raise AssertionError(errors)


if __name__ == '__main__':
    main()
