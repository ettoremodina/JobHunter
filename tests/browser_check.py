"""Exercise the dashboard in Chromium against disposable data, saving desktop/mobile screenshots."""

import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from jobhunter.workspace import Archive, ROOT, settings
from jobhunter.dashboard import create_server


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
        archive.close()
        cfg = settings()
        server = create_server(database, cfg, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        destination = ROOT / "data/qa"
        destination.mkdir(exist_ok=True)
        errors = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.wait_for_load_state("networkidle")
                expect(page.locator("#eligibility")).to_have_value("potential")
                expect(page.locator("#rows .company-link")).to_have_count(1)
                page.locator("#eligibility").select_option("")
                page.get_by_role("button", name="Cerca", exact=True).click()
                page.get_by_role("button", name="Energy Lab", exact=True).click()
                page.get_by_role("heading", name="Energy Lab", exact=True).wait_for()
                expect(page.get_by_role("button", name="Azienda non interessante", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="Nessun ruolo adatto adesso", exact=True)).to_be_visible()
                assert page.get_by_role("link", name="Sito aziendale").count() == 1
                assert page.get_by_role("heading", name="Senior engineer", exact=True).count() == 1
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
                page.get_by_role("button", name="La mia coda", exact=True).click()
                expect(page.get_by_role("heading", name="Da chiarire insieme", exact=True)).to_be_visible()
                page.screenshot(path=str(destination / "queue-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(destination / "queue-mobile.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1050})
                expect(page.get_by_role("button", name="Apri e valuta Energy Lab", exact=True)).to_have_count(1)
                expect(page.get_by_text("Questa pagina non chiama ChatGPT né Ollama.", exact=False)).to_be_visible()
                page.get_by_role("button", name="Prepara testo per la chat", exact=True).click()
                expect(page.get_by_role("textbox", name="Brief da copiare nella chat")).to_be_visible()
                expect(page.get_by_role("textbox", name="Brief da copiare nella chat")).to_have_value(__import__("re").compile("Usa la skill jobhunter"))
                page.get_by_role("button", name="Apri e valuta Energy Lab", exact=True).click()
                expect(page.locator("#detail-status")).to_have_value("new")
                page.get_by_role("button", name="Aziende", exact=True).click()
                page.locator("#query").fill("no-such-company")
                page.get_by_role("button", name="Cerca", exact=True).click()
                page.get_by_text("Nessun risultato.", exact=False).wait_for()
                page.get_by_role("button", name="Azzera", exact=True).click()
                expect(page.locator("#query")).to_have_value("")
                page.locator("#eligibility").select_option("")
                page.get_by_role("button", name="Cerca", exact=True).click()
                expect(page.locator("#rows .company-link")).to_have_count(2)
                page.locator("#category").select_option("Energia")
                page.get_by_role("button", name="Cerca", exact=True).click()
                expect(page.locator("#rows .company-link")).to_have_count(1)
                page.get_by_role("button", name="Azzera", exact=True).click()
                expect(page.locator("#rows .company-link")).to_have_count(1)
                page.get_by_role("button", name="Metriche", exact=True).click()
                page.get_by_role("heading", name="Completezza dei dati", exact=True).wait_for()
                expect(page.locator("#analytics-status")).to_contain_text("3 annunci")
                page.screenshot(path=str(destination / "metrics-desktop.png"), full_page=True)
                page.locator("#analytics-scope").select_option("potential")
                expect(page.locator("#analytics-status")).to_contain_text("1 annunci")
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(destination / "metrics-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="Fonti e test di raccolta", exact=True).click()
                page.get_by_role("heading", name="Fonti e raccolta").wait_for()
                expect(page.get_by_role("button", name="Avvia raccolta limitata", exact=True)).to_have_count(4)
                assert page.get_by_role("button", name="Avvia raccolta limitata", exact=True).last.is_disabled()
                page.get_by_role("button", name="Profilo", exact=True).click()
                page.locator("#preference-note").fill("QA preference")
                page.get_by_role("button", name="Salva preferenza").click()
                page.get_by_text("QA preference", exact=True).wait_for()
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
                page.locator("#query").fill("Search QA")
                page.locator("#location").fill("Milano")
                page.locator("#eligibility").select_option("potential")
                page.get_by_role("button", name="Cerca", exact=True).click()
                expect(page.locator("#count")).to_have_text("31 aziende")
                expect(page.locator("#rows .company-link")).to_have_count(30)
                expect(page.get_by_role("button", name="Search QA Mixed", exact=True)).to_have_count(0)
                page.locator("#next").click()
                expect(page.locator("#rows .company-link")).to_have_count(1)
                expect(page.locator("#page")).to_have_text("31–31 di 31")
                expect(page.locator("#next")).to_be_disabled()
                expect(page.locator("#rows tr td").nth(2)).to_have_text("Milano")
                page.locator("#previous").click()
                expect(page.locator("#rows .company-link")).to_have_count(30)
                assert not errors, errors
                page.get_by_role("button", name="Pipeline", exact=True).click()
                expect(page.locator("#pipeline-steps > li")).to_have_count(9)
                expect(page.locator("#pipeline-status")).to_contain_text("annunci")
                expect(page.locator("#pipeline-steps")).to_contain_text("Classificazione aziende")
                page.screenshot(path=str(destination / "pipeline-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.set_viewport_size({"width": 1440, "height": 1050})
                page.screenshot(path=str(destination / "pipeline-desktop.png"), full_page=True)
                page.get_by_role("button", name="Aggiorna stato", exact=True).click()
                expect(page.locator("#refresh-pipeline")).to_be_enabled()
                assert not errors, errors
                browser.close()
            print(json.dumps({"status": "passed", "page_errors": errors, "screenshots": str(destination)}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
