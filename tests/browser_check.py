"""Exercise the dashboard in Chromium against disposable data, saving desktop/mobile screenshots."""

import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from jobhunter.workspace import Archive, ROOT, settings
from jobhunter.exploration.dashboard import create_server
from jobhunter.evaluation import remote_llm
from jobhunter.evaluation.company_categories import replace as replace_categories


def main():
    """Verify navigation, search, persistence, undo, profile and responsive layout."""
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "qa.sqlite3"
        archive = Archive(database)
        archive.ingest([{"company_name": "Energy Lab", "title": "Junior optimization engineer", "original_url": "https://example.org/job/1", "website_url": "https://example.org", "location": "Milan, Italy", "min_amount": 40000, "currency": "EUR", "interval": "year", "description": "Energy optimization using Python."},
                        {"company_name": "Energy Lab", "title": "Senior engineer", "original_url": "https://example.org/job/2", "location": "Berlin, Germany"},
                        {"company_name": "<img src=x onerror=alert(1)>", "title": "Research", "original_url": "https://other.org/job/1", "location": "Paris"}], "qa")
        cid = archive.search(query="Energy Lab")["items"][0]["id"]
        long_location = ", ".join(f"City {n}, Region, DE" for n in range(70))
        archive.ingest([{"company_name": "Energy Lab", "title": "Junior optimization engineer", "original_url": "https://example.org/job/1", "locations": [long_location], "company_industry": "Clean Energy", "description": r"**DESCRIPTION** -------- Build energy systems \- safely. * Analyze data * Improve models"}], "qa")
        archive.assess(cid, {"reasoning": "Energy work is relevant. Check experience requirements.", "missing_information": ["Remote eligibility"]})
        remote_cfg = json.loads((ROOT/'config/remote_llm.json').read_text())
        remote_cfg.update(output_directory=str(Path(directory)/'remote'), request_delay_seconds=0)
        remote_path = Path(directory)/'remote.json'
        remote_path.write_text(json.dumps(remote_cfg))
        field_labels = json.loads((ROOT/remote_cfg['job_fields_path']).read_text(encoding='utf-8'))
        oid = archive.db.execute("SELECT id FROM opportunities WHERE json_extract(data,'$.title')='Junior optimization engineer'").fetchone()[0]
        catalog = remote_llm.inputs(archive, 'job-summary', oid, remote_cfg)['source']['evidence_catalog']
        reference = next(key for key, text in catalog.items() if 'Analyze data' in text)
        summary = {'summary': 'Analisi dati e miglioramento dei modelli.', 'facts': [{'field': 'responsibilities', 'text': 'Analisi dati', 'quote': reference}], 'missing_information': []}
        with patch('jobhunter.evaluation.remote_llm.api_key', return_value='dummy'), patch('jobhunter.evaluation.remote_llm.request', return_value=(summary, {}, [])):
            assert remote_llm.run(archive, 'job-summary', execute=True, config_path=remote_path)['processed'] == 1
        with archive.db:
            replace_categories(archive, cid, ["Energia", "Software e tecnologia"], "jev",
                               "Fixture multi-categoria", {"Energia": 0.55, "Software e tecnologia": 0.40},
                               preserve_chat=False)
        archive.close()
        cfg = settings()
        server = create_server(database, cfg, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        destination = ROOT / "data/qa"
        destination.mkdir(parents=True, exist_ok=True)
        errors = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.wait_for_load_state("networkidle")
                expect(page.locator("#rows .company-link")).to_have_count(2)
                # I filtri espongono un solo asse di selezione, il Tier, con un pulsante per valore.
                expect(page.locator('#filters select')).to_have_count(4)
                expect(page.locator('#tier-scope button')).to_have_count(6)
                page.locator('#tier-scope button[data-tier="scarto"]').click()
                expect(page.locator('#results-table th.col-tier')).to_be_hidden()
                page.get_by_role("button", name="Azzera", exact=True).click()
                expect(page.locator('#tier-scope button[aria-pressed="true"]')).to_have_attribute('data-tier', '')
                expect(page.locator("#rows .company-link")).to_have_count(2)
                # Da tastiera si scorre l'elenco: la riga evidenziata e la scheda aperta si spostano insieme.
                page.locator('body').press('ArrowDown')
                first = page.locator('#rows tr').nth(0)
                expect(first).to_have_class('selected')
                expect(page.locator('#detail h2')).to_have_text(first.locator('.company-link').inner_text())
                page.keyboard.press('j')
                expect(page.locator('#rows tr').nth(1)).to_have_class('selected')
                expect(first).not_to_have_class('selected')
                page.keyboard.press('k')
                expect(first).to_have_class('selected')
                expect(page.locator('#filters')).not_to_contain_text('Asse ruolo')
                expect(page.locator('#filters')).not_to_contain_text('Stato')
                expect(page.locator('body')).not_to_contain_text('Località dichiarata nell’annuncio')
                page.locator('#columns-box summary').click()
                page.locator('#columns').get_by_label('Categoria', exact=True).uncheck()
                expect(page.locator('#results-table th.col-categoria')).to_be_hidden()
                page.locator('#columns').get_by_label('Categoria', exact=True).check()
                page.locator('#columns-box summary').click()
                before = page.locator('#detail').bounding_box()['width']
                page.locator('#detail-resize').focus()
                page.keyboard.press('ArrowLeft')
                assert page.locator('#detail').bounding_box()['width'] > before
                page.get_by_role("button", name="Cerca", exact=True).click()
                page.get_by_role("button", name="Energy Lab", exact=True).click()
                page.get_by_role("heading", name="Energy Lab", exact=True).wait_for()
                expect(page.locator("#detail")).to_contain_text("Energia · Software e tecnologia")
                expect(page.get_by_role("button", name="Azienda non interessante", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="Nessun ruolo adatto adesso", exact=True)).to_be_visible()
                # Il collegamento porta il nome del dominio, non un'etichetta generica.
                assert page.get_by_role("link", name="example.org", exact=True).count() == 1
                # Ogni ruolo è un blocco richiudibile: il titolo sta nella sua riga di apertura.
                assert page.locator("#detail .job-title", has_text="Senior engineer").count() == 1
                # Con più ruoli i blocchi restano chiusi: si aprono per leggere sintesi e campi.
                for summary in page.locator("#detail details.opportunity > summary").all():
                    summary.click()
                expect(page.get_by_text('Analisi dati e miglioramento dei modelli.', exact=True)).to_be_visible()
                expect(page.locator('#detail dt').filter(has_text='Competenze obbligatorie')).to_have_count(1)
                assert page.locator('#detail dd').filter(has_text='Non indicato').count() == len(field_labels)-1
                assert page.locator("img").count() == 0
                expect(page.get_by_role("columnheader", name="Categoria", exact=True)).to_be_visible()
                assert len(page.locator("#rows tr").filter(has=page.get_by_role("button", name="Energy Lab", exact=True)).locator("td").nth(2).inner_text()) <= 90
                page.locator("#detail .locations summary").click()
                expect(page.locator("#detail .locations p")).to_have_text(long_location)
                page.get_by_text("Leggi descrizione", exact=True).first.click()
                expect(page.locator(".description h4")).to_have_text("DESCRIPTION")
                expect(page.locator(".description li")).to_have_count(2)
                assert "\\-" not in page.locator(".description").first.inner_text()
                page.locator("#detail .locations summary").click()
                # Il modulo completo sta dietro un blocco richiudibile: le scelte rapide restano a vista.
                page.get_by_text("Registra una decisione con motivo e nota", exact=True).click()
                page.locator("#detail-status").select_option("saved")
                page.locator("#detail-note").fill("Interesting company, keep both opportunities")
                page.get_by_role("button", name="Salva decisione", exact=True).click()
                page.get_by_text("Decisione salvata.", exact=True).wait_for()
                page.reload()
                page.get_by_role("button", name="Energy Lab", exact=True).click()
                expect(page.locator("#detail-status")).to_have_value("saved")
                page.screenshot(path=str(destination / "desktop.png"), full_page=True)
                page.get_by_text("Storico decisioni", exact=True).click()
                page.get_by_role("button", name="Annulla", exact=True).click()
                page.get_by_text("Decisione annullata.", exact=True).wait_for()
                # La scelta manuale vive in una tab sua: qui si salva, e la scheda si apre lì dentro.
                page.get_by_role("button", name="Salva azienda", exact=True).click()
                page.get_by_text("Salvato l'azienda: lo trovi nella tab Salvate.", exact=True).wait_for()
                page.get_by_role("button", name="Salvate", exact=True).click()
                expect(page.locator("#saved-summary")).to_contain_text("1 aziende salvate")
                page.screenshot(path=str(destination / "saved-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(destination / "saved-mobile.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1050})
                page.get_by_role("button", name="Prepara testo per la chat", exact=True).click()
                expect(page.get_by_role("textbox", name="Testo da copiare nella chat di Codex").first).to_have_value(__import__("re").compile("Usa la skill jobhunter"))
                page.get_by_role("button", name="Apri Energy Lab", exact=True).click()
                expect(page.locator("#saved-detail")).to_contain_text("Energy Lab")
                page.get_by_role('button', name='Prepara il controllo di tutte le salvate', exact=True).click()
                expect(page.locator('#saved-brief-box textarea')).to_have_value(__import__('re').compile('Energy Lab'))
                page.get_by_role("button", name="Rimuovi dalle salvate").first.click()
                page.get_by_text("Nessuna azienda salvata.", exact=False).wait_for()
                page.get_by_role("button", name="Aziende", exact=True).click()
                page.locator("#query").fill("no-such-company")
                page.get_by_role("button", name="Cerca", exact=True).click()
                page.get_by_text("Nessun risultato.", exact=False).wait_for()
                page.get_by_role("button", name="Azzera", exact=True).click()
                expect(page.locator("#query")).to_have_value("")
                page.get_by_role("button", name="Cerca", exact=True).click()
                expect(page.locator("#rows .company-link")).to_have_count(2)
                page.locator("#category").select_option("Energia")
                page.get_by_role("button", name="Cerca", exact=True).click()
                expect(page.locator("#rows .company-link")).to_have_count(1)
                page.get_by_role("button", name="Azzera", exact=True).click()
                expect(page.locator("#rows .company-link")).to_have_count(1)
                page.get_by_role("button", name="Metriche", exact=True).click()
                page.get_by_role("heading", name="Stato di elaborazione", exact=True).wait_for()
                expect(page.locator("#analytics-status")).to_contain_text("3 annunci")
                expect(page.locator('.processing-row')).to_have_count(4)
                expect(page.locator('.decision-tree')).to_be_visible()
                expect(page.locator('.decision-tree')).to_contain_text('Passano a Jev')
                expect(page.locator('.decision-block')).to_contain_text('Esito finale')
                expect(page.locator('.decision-block')).to_contain_text('senza esito Jev')
                expect(page.locator('.company-bridge')).to_be_visible()
                expect(page.locator("#analytics-content")).to_contain_text("Dagli annunci compatibili alle aziende")
                expect(page.locator('.compatible-company-map')).to_be_visible()
                expect(page.locator('.all-company-map')).to_be_visible()
                expect(page.locator('.all-company-map')).to_contain_text('riparte')
                expect(page.locator('.all-company-map')).to_contain_text('La Regex giudica gli annunci e non determina la categoria')
                expect(page.locator('.all-company-map')).to_contain_text('Senza categoria')
                expect(page.locator("#analytics-content")).to_contain_text("Completezza dei dati")
                assert page.locator('#analytics-content svg.donut').count() >= 2
                page.screenshot(path=str(destination / "metrics-desktop.png"), full_page=True)
                page.locator("#analytics-scope").select_option("potential")
                expect(page.locator("#analytics-status")).to_contain_text("3 annunci")
                expect(page.locator("#analytics-content")).to_contain_text("1 annunci in 1 aziende")
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(destination / "metrics-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="Fonti e test di raccolta", exact=True).click()
                page.get_by_role("heading", name="Fonti e raccolta").wait_for()
                expect(page.get_by_role("button", name="Avvia raccolta limitata", exact=True)).to_have_count(4)
                assert page.get_by_role("button", name="Avvia raccolta limitata", exact=True).last.is_disabled()
                page.get_by_role("button", name="Aziende", exact=True).click()
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(destination / "mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                # Check pagination and city filtering together through real HTTP requests.
                archive = Archive(database)
                archive.ingest([
                    {"company_name": f"Search QA {n:02}", "title": "Data Scientist", "location": "Milano", "source_url": f"https://example.org/search/{n}"}
                    for n in range(31)
                ] + [
                    {"company_name": "Search QA Mixed", "title": "Senior engineer", "location": "Milano", "source_url": "https://example.org/search/senior"},
                    {"company_name": "Search QA Mixed", "title": "Data Scientist", "location": "Roma", "source_url": "https://example.org/search/rome"},
                ], "qa")
                archive.close()
                # I menu geografici si costruiscono dai dati: dopo un'importazione vanno riletti.
                page.reload()
                page.locator("#city").get_by_text("Milano", exact=False).first.wait_for(state="attached")
                page.locator("#query").fill("Search QA")
                page.locator("#city").select_option("Milano")
                page.get_by_role("button", name="Cerca", exact=True).click()
                expect(page.locator("#count")).to_have_text("32 aziende")
                expect(page.locator("#rows .company-link")).to_have_count(30)
                page.locator("#next").click()
                expect(page.locator("#rows .company-link")).to_have_count(2)
                expect(page.locator("#page")).to_have_text("31–32 di 32")
                # L'azienda con un ruolo a Milano e uno a Roma entra dichiarando quanti ruoli rispondono.
                mixed = page.locator("#rows tr").filter(has=page.get_by_role("button", name="Search QA Mixed", exact=True))
                expect(mixed.locator("td.col-ruoli")).to_have_text("1 di 2")
                mixed.get_by_role('button', name='Search QA Mixed', exact=True).click()
                expect(page.locator('#detail > details.opportunity')).to_have_count(1)
                expect(page.locator('#detail details.other-roles')).not_to_have_attribute('open', '')
                page.locator('#detail details.other-roles > summary').click()
                expect(page.locator('#detail details.other-roles .job-title')).to_contain_text('Data Scientist')
                # Salvate apre tutti i ruoli anche con il filtro Milano ancora attivo.
                page.get_by_role('button', name='Salva azienda', exact=True).click()
                page.get_by_text("Salvato l'azienda: lo trovi nella tab Salvate.", exact=True).wait_for()
                page.get_by_role('button', name='Salvate', exact=True).click()
                expect(page.locator('#saved-view')).not_to_contain_text('Da chiarire insieme')
                expect(page.locator('#saved-view')).not_to_contain_text('Preferenze proposte')
                page.get_by_role('button', name='Apri Search QA Mixed', exact=True).click()
                expect(page.locator('#saved-detail > details.opportunity')).to_have_count(2)
                expect(page.locator('#saved-detail .other-roles')).to_have_count(0)
                page.get_by_role('button', name='Aziende', exact=True).click()
                expect(page.locator("#next")).to_be_disabled()
                # La colonna mostra la forma canonica, non la stringa della fonte.
                expect(page.locator("#rows td.col-localita").first).to_have_text("Milano, Italia")
                page.locator("#previous").click()
                expect(page.locator("#rows .company-link")).to_have_count(30)
                assert not errors, errors
                page.get_by_role("button", name="Pipeline", exact=True).click()
                # Sei passaggi automatici: le conversazioni Codex restano strumenti separati.
                expect(page.locator("#pipeline-steps > li")).to_have_count(6)
                expect(page.locator('#pipeline-steps details.pipeline-about')).to_have_count(6)
                expect(page.locator("#pipeline-status")).to_contain_text("annunci")
                expect(page.locator("#pipeline-steps")).not_to_contain_text("Categorie locali")
                expect(page.locator("#pipeline-steps")).not_to_contain_text("Statistiche dell")
                for retired in ("HTML salvato", "Impaginazione Ollama"):
                    expect(page.locator("#pipeline-steps")).not_to_contain_text(retired)
                page.get_by_role('button', name='Prepara revisione degli indecisi', exact=True).click()
                assert 'codex-session start --mode indecisi' in page.locator('#codex-prompt-box textarea').input_value()
                page.get_by_role('button', name='Prepara esplorazione della selezione', exact=True).click()
                assert 'codex-session start --mode selezione' in page.locator('#codex-prompt-box textarea').input_value()
                page.screenshot(path=str(destination / "pipeline-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.set_viewport_size({"width": 1440, "height": 1050})
                page.screenshot(path=str(destination / "pipeline-desktop.png"), full_page=True)
                # Jev sceglie ruolo e settore nello stesso passaggio, con il cancello dell'anteprima.
                page.get_by_role('button', name='Apri Giudice 2 · Jev sceglie il ruolo e il settore', exact=True).click()
                expect(page.get_by_label('Modalità Jev', exact=True)).to_have_value('preview')
                expect(page.get_by_role('button', name='Prepara anteprima', exact=True)).to_be_visible()
                page.get_by_role('button', name='Chiudi parametri', exact=True).click()
                page.get_by_role('button', name='Apri Schede · Qwen per annunci e aziende', exact=True).click()
                expect(page.locator('#pipeline-dialog')).to_be_visible()
                expect(page.get_by_label('Numero di aziende Tier A/B del campione', exact=True)).to_have_value('100')
                expect(page.get_by_label('Tutte le aziende Tier A e B', exact=True)).not_to_be_checked()
                expect(page.get_by_label('Modalità LLM remoto', exact=True)).to_have_value('preview')
                page.screenshot(path=str(destination/'pipeline-dialog.png'), full_page=True)
                page.get_by_label('Numero di aziende Tier A/B del campione', exact=True).fill('2')
                page.get_by_role('button', name='Prepara anteprima', exact=True).click()
                expect(page.locator('#pipeline-last-result')).to_contain_text('chiamate aziendali previste', timeout=45000)
                expect(page.locator('#pipeline-last-result')).to_contain_text('1 / 1 aziende Tier A/B')
                page.get_by_role('button', name='Chiudi parametri', exact=True).click()
                page.get_by_role('button', name='Apri Filtro regex su titolo e descrizione', exact=True).click()
                page.get_by_label('Continua da qui con i passaggi successivi', exact=True).check()
                expect(page.get_by_role('button', name='Avvia sequenza', exact=True)).to_be_visible()
                page.get_by_label('Continua da qui con i passaggi successivi', exact=True).uncheck()
                page.get_by_role('button', name='Avvia passaggio', exact=True).click()
                expect(page.locator('#pipeline-dialog-state')).to_contain_text('Terminato', timeout=45000)
                page.get_by_role('button', name='Chiudi parametri', exact=True).click()
                page.get_by_role("button", name="Aggiorna stato", exact=True).click()
                expect(page.locator("#refresh-pipeline")).to_be_enabled()
                # La tab di controllo: una domanda per lente, e le righe aprono l'azienda.
                page.get_by_role("button", name="Debug", exact=True).click()
                page.locator(".debug-lens-open").first.wait_for()
                page.locator(".debug-lens-open", has_text="Aziende senza descrizione").click()
                expect(page.locator("#debug-rows h3")).to_have_text("Aziende senza descrizione")
                assert page.locator("#debug-rows .debug-list li").count() >= 1
                page.get_by_role("button", name="Pipeline", exact=True).click()
                page.locator("#pipeline-steps > li").first.wait_for()

                def slow_stage(archive, cfg, step, values, progress):
                    """Expose a cancellable offline operation to exercise the real stop button."""
                    import time
                    for index in range(200):
                        progress({'saved': index})
                        time.sleep(.05)
                    return {'status': 'success'}
                with patch('jobhunter.operations.pipeline_actions.execute', side_effect=slow_stage):
                    page.get_by_role('button', name='Apri Filtro regex su titolo e descrizione', exact=True).click()
                    page.get_by_role('button', name='Avvia passaggio', exact=True).click()
                    expect(page.locator('#pipeline-stop')).to_be_enabled()
                    page.locator('#pipeline-stop').click()
                    expect(page.locator('#pipeline-dialog-state')).to_contain_text('Interrotto', timeout=15000)
                    expect(page.locator('#pipeline-stop')).to_be_disabled()
                page.get_by_role('button', name='Chiudi parametri', exact=True).click()
                assert not errors, errors
                browser.close()
            print(json.dumps({"status": "passed", "page_errors": errors, "screenshots": str(destination)}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
