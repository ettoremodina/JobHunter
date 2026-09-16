"""Verify the Debug hierarchy in Chromium with a disposable archive."""

import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import expect, sync_playwright

from jobhunter.dashboard import create_server
from jobhunter.workspace import Archive, settings


def fixture(database):
    """Create enough missing-description cases to exercise the second page."""
    archive = Archive(database)
    archive.ingest([
        {
            "company_name": f"Azienda test {index:02d}",
            "title": "Mechanical Engineer",
            "source_url": f"https://example.org/jobs/{index}",
        }
        for index in range(55)
    ], "debug-qa")
    archive.close()


def main():
    """Check hierarchy, subgroup, pagination, in-place detail and responsive layout."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "debug.sqlite3"
        fixture(database)
        server = create_server(database, settings(), 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.wait_for_load_state("networkidle")
                page.get_by_role("button", name="Debug", exact=True).click()

                companies = page.locator("#debug-scope .debug-scope-button", has_text="Aziende")
                adverts = page.locator("#debug-scope .debug-scope-button", has_text="Annunci")
                expect(companies).to_have_attribute("aria-pressed", "true")
                adverts.click()
                expect(adverts).to_have_attribute("aria-pressed", "true")
                expect(companies).to_have_attribute("aria-pressed", "false")

                page.locator(".debug-lens-open", has_text="Annunci senza descrizione").click()
                expect(page.locator("#debug-path")).to_contain_text(
                    "Debug / Annunci / Dati mancanti / Annunci senza descrizione"
                )
                expect(page.locator("#debug-rows .debug-list li")).to_have_count(50)
                expect(page.locator("#debug-rows .pager")).to_contain_text("Pagina 1 di 2")

                first_result = page.locator("#debug-rows .debug-result-open").first
                first_result.click()
                expect(first_result).to_have_attribute("aria-current", "true")
                expect(page.locator("#debug-detail")).to_contain_text("Azienda test")
                expect(page.locator("#debug-detail")).to_contain_text("Evidenze del controllo")
                expect(page.locator("#debug-view")).to_be_visible()
                expect(page.locator("#debug-path")).to_contain_text("Annunci senza descrizione")
                page.screenshot(path=args.output / "debug-desktop.png", full_page=True)

                page.get_by_role("button", name="Successive", exact=True).click()
                expect(page.locator("#debug-rows .debug-list li")).to_have_count(5)
                expect(page.locator("#debug-rows .pager")).to_contain_text("Pagina 2 di 2")

                page.locator(".debug-lens-open", has_text="Annunci per lingua").click()
                subgroup = page.locator("#debug-rows .debug-filter select")
                expect(subgroup).to_be_visible()
                assert subgroup.locator("option").count() >= 2
                subgroup.select_option(index=1)
                expect(page.locator("#debug-path")).to_contain_text("Annunci per lingua")

                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.locator("#debug-lenses")).to_be_visible()
                expect(page.locator("#debug-rows")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path=args.output / "debug-mobile.png", full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(json.dumps({"status": "passed", "screenshots": str(args.output)}))


if __name__ == "__main__":
    main()
