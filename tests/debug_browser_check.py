"""Verify the Debug hierarchy in Chromium with a disposable archive."""

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import expect, sync_playwright

from jobhunter import debug
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
        original_rows = debug.rows
        original_show = Archive.show

        def delayed_rows(archive, key, *args, **kwargs):
            """Make the first company control finish after the next one."""
            time.sleep(.35 if key == "aziende-senza-descrizione" else .01)
            return original_rows(archive, key, *args, **kwargs)

        def delayed_show(archive, company_id):
            """Make the first alphabetical company detail finish last."""
            result = original_show(archive, company_id)
            time.sleep(.35 if result["name"].endswith("00") else .01)
            return result

        try:
            with patch.object(debug, "rows", side_effect=delayed_rows), patch.object(
                Archive, "show", new=delayed_show
            ), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.wait_for_load_state("networkidle")
                page.get_by_role("button", name="Debug", exact=True).click()

                companies = page.locator("#debug-scope .debug-scope-button", has_text="Aziende")
                adverts = page.locator("#debug-scope .debug-scope-button", has_text="Annunci")
                expect(companies).to_have_attribute("aria-pressed", "true")
                expect(page.locator("#debug-path")).to_contain_text("Aziende senza descrizione")

                # A slow response from the previous control must not replace the newer result.
                page.locator(".debug-lens-open", has_text="Aziende senza descrizione").click()
                page.locator(".debug-lens-open", has_text="Aziende senza categoria").click()
                expect(page.locator("#debug-rows h3")).to_have_text("Aziende senza categoria")
                page.wait_for_timeout(500)
                expect(page.locator("#debug-rows h3")).to_have_text("Aziende senza categoria")

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
                second_result = page.locator("#debug-rows .debug-result-open").nth(1)
                first_result.click()
                second_result.click()
                expect(second_result).to_have_attribute("aria-current", "true")
                expect(page.locator("#debug-detail h3")).to_have_text("Azienda test 01")
                page.wait_for_timeout(500)
                expect(page.locator("#debug-detail h3")).to_have_text("Azienda test 01")
                expect(page.locator("#debug-detail")).to_contain_text("Evidenze del controllo")
                expect(page.locator("#debug-view")).to_be_visible()
                expect(page.locator("#debug-path")).to_contain_text("Annunci senza descrizione")
                page.screenshot(path=args.output / "debug-desktop.png", full_page=True)

                # Changing the result list also invalidates a pending detail request.
                first_result.click()
                page.locator(".debug-lens-open", has_text="Annunci per lingua").click()
                expect(page.locator("#debug-rows h3")).to_have_text("Annunci per lingua dell’originale")
                page.wait_for_timeout(500)
                expect(page.locator("#debug-detail h3")).to_have_text("Scegli un risultato")

                page.locator(".debug-lens-open", has_text="Annunci senza descrizione").click()
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
                page.locator("#debug-rows .debug-result-open").nth(2).click()
                expect(page.locator("#debug-detail")).to_be_focused()
                assert page.locator("#debug-detail").bounding_box()["top"] < 844
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
