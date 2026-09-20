"""Bounded source acquisition with immutable run files and explicit outcomes."""

from __future__ import annotations
import asyncio
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.error import HTTPError
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit
from jobhunter.workspace import ROOT, clean, identity, url

from jobhunter.operations import cancellation

logger = logging.getLogger(__name__)


def access_block(html):
    """Identify explicit access denial without confusing missing structured data with blocking."""
    text = html.lower()
    if "you are being rate limited" in text or "error 1015" in text:
        return "rate_limited"
    if "<title>access denied" in text:
        return "access_denied"
    return None


_SESSIONS = threading.local()


def session():
    """Keep one connection pool per thread: senza, ogni annuncio ripaga l'handshake TLS da zero."""
    existing = getattr(_SESSIONS, "value", None)
    if existing is None:
        import requests
        existing = _SESSIONS.value = requests.Session()
        existing.headers.update({"User-Agent": "Mozilla/5.0 JobHunter/2.0"})
    return existing


def fetch(address, timeout):
    """Fetch an HTTP resource with a finite timeout and explicit status errors."""
    parts = urlsplit(address)
    address = parts._replace(netloc=parts.netloc.encode("idna").decode("ascii"),
                             path=quote(parts.path, safe="/%:@!$&'()*+,;="),
                             query=quote(parts.query, safe="=&%/:?@!$'()*+,;")).geturl()
    # HTTPError e il suo messaggio restano la forma dell'errore: descriptions.py classifica i 404
    # e i blocchi 403/429/999 su quelli, e una libreria diversa non deve cambiarne il significato.
    with session().get(address, timeout=timeout, stream=True) as response:
        if response.status_code >= 400:
            raise urllib.error.HTTPError(address, response.status_code,
                                         f"HTTP Error {response.status_code}: {response.reason}", response.headers, None)
        return response.raw.read(12_000_000, decode_content=True).decode("utf-8", errors="replace")


class JobDataParser(HTMLParser):
    """Extract JSON-LD and readable text without executing page content."""

    def __init__(self):
        """Initialize document accumulators."""
        super().__init__()
        self.blocks, self.text = [], []
        self.script, self.hidden = None, 0

    def handle_starttag(self, tag, attrs):
        """Track structured scripts and suppress other scripts/styles."""
        if tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section") and not self.hidden:
            self.text.append("\n")
        if tag in ("script", "style"):
            self.hidden += 1
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.script = ""

    def handle_endtag(self, tag):
        """Finish captured scripts."""
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section") and not self.hidden:
            self.text.append("\n")
        if tag == "script" and self.script is not None:
            self.blocks.append(self.script)
            self.script = None
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        """Collect JSON-LD or visible text."""
        if self.script is not None:
            self.script += data
        elif not self.hidden:
            self.text.append(data)


def postings(html, address):
    """Map JSON-LD JobPosting nodes, including graphs, to the common input schema."""
    parser = JobDataParser()
    parser.feed(html)
    nodes = []

    def walk(value):
        """Visit structured objects recursively."""
        if isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                nodes.append(value)
            else:
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
    for block in parser.blocks:
        try:
            walk(json.loads(block))
        except json.JSONDecodeError:
            logger.debug("Invalid JSON-LD at %s", address)
    rows = []
    for node in nodes:
        org = node.get("hiringOrganization") or {}
        if isinstance(org, list):
            org = org[0] if org else {}
        if isinstance(org, str):
            org = {"name": org}
        locations = node.get("jobLocation") or []
        if isinstance(locations, dict):
            locations = [locations]
        labels = []
        for loc in locations:
            addr = loc.get("address", {}) if isinstance(loc, dict) else loc
            labels.append(", ".join(filter(None, [clean(addr.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")])) if isinstance(addr, dict) else clean(addr))
        description = JobDataParser()
        description.feed(str(node.get("description") or ""))
        salary = node.get("baseSalary") or {}
        if not isinstance(salary, dict):
            salary = {}
        value = salary.get("value") or {}
        if not isinstance(value, dict):
            value = {"value": value}
        rows.append({"company_name": org.get("name"), "website_url": org.get("sameAs"), "title": node.get("title"),
                     "source_url": urljoin(address, node.get("url") or address), "description": "\n".join(clean(line) for line in "".join(description.text).splitlines() if clean(line)),
                     "locations": labels, "posted_at": node.get("datePosted"), "employment_type": node.get("employmentType"),
                     "remote_policy": "remote" if node.get("jobLocationType") == "TELECOMMUTE" else None,
                     "salary": {"min": value.get("minValue", value.get("value")), "max": value.get("maxValue", value.get("value")), "period": value.get("unitText"), "currency": salary.get("currency")}})
    return rows


def airtable_rows(config, timeout):
    """Read the public embed protocol and report drift rather than invent empty success."""
    import requests
    with requests.Session() as client:
        page = client.get(config["embed_url"], timeout=timeout)
        page.raise_for_status()
        address_match = re.search(r'urlWithParams:\s*("(?:\\.|[^"\\])*")', page.text)
        header_match = re.search(r'var headers\s*=\s*(\{.*?\});', page.text, re.S)
        if not address_match or not header_match:
            raise ValueError("Airtable embed changed or requires access; inspect the adapter")
        address = urljoin("https://airtable.com", json.loads(address_match.group(1)))
        if urlsplit(address).hostname != "airtable.com":
            raise ValueError("Unexpected Airtable endpoint")
        headers = json.loads(header_match.group(1))
        headers = {k: v for k, v in headers.items() if isinstance(v, str) and k.lower() != "x-airtable-accept-msgpack"}
        headers.update({"X-Requested-With": "XMLHttpRequest", "x-time-zone": "UTC", "x-user-locale": "en"})
        response = client.get(address, headers=headers, timeout=timeout)
        if response.status_code != 200:
            raise ValueError(f"Airtable data request returned HTTP {response.status_code}; inspect public embed access")
        table = response.json().get("data", {}).get("table")
        if not isinstance(table, dict) or "rows" not in table:
            raise ValueError("Airtable table payload missing")
        columns = table.get("columns", [])
        names = {c["id"]: c["name"] for c in columns}
        choices = {k: v.get("name", k) for c in columns for k, v in c.get("typeOptions", {}).get("choices", {}).items()}

        def flatten(value):
            """Resolve embed choices and linked row values."""
            if isinstance(value, str):
                return choices.get(value, value)
            if isinstance(value, list):
                return ", ".join(filter(None, (clean(flatten(v)) for v in value)))
            if isinstance(value, dict):
                return value["url"] if "url" in value else flatten(list(value.get("valuesByForeignRowId", {}).values()))
            return value
        return [{names[k]: flatten(v) for k, v in row.get("cellValuesByColumnId", {}).items() if k in names} for row in table["rows"]]


async def browser_rows(config, source, cfg, limit, output):
    """Reuse one browser; prefer direct structured data then render missing details."""
    from playwright.async_api import async_playwright
    rows, errors = [], []
    timeout = cfg["timeout_seconds"] * 1000
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(config["entry_point"], wait_until="domcontentloaded", timeout=timeout)
            listing = await page.content()
            (output / "listing.html").write_text(listing, encoding="utf-8")
            if access_block(listing):
                raise ValueError(access_block(listing))
            links, stagnant = set(), 0
            stop_reason = "page_cap"
            for page_number in range(cfg["max_pages"]):
                cancellation.check()
                await page.wait_for_timeout(1000)
                current = await page.locator("a[href]").evaluate_all("els => els.map(e => e.href)")
                previous = len(links)
                links.update(url(x) for x in current if source["link_contains"] in x and url(x) and urlsplit(x).hostname == urlsplit(config["entry_point"]).hostname)
                stagnant = stagnant + 1 if len(links) == previous else 0
                if len(links) >= limit or stagnant >= 3:
                    stop_reason = "record_cap" if len(links) >= limit else "navigation_stalled"
                    break
                more = page.get_by_role("button", name=re.compile("load more|show more", re.I))
                if await more.count() and await more.first.is_visible():
                    await more.first.click(timeout=timeout)
                else:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            (output / "discovery.json").write_text(json.dumps({"links": len(links), "pages_or_scrolls": page_number + 1, "stop": stop_reason, "coverage": "No proof of source exhaustion"}), encoding="utf-8")
            (output / "listing.html").write_text(await page.content(), encoding="utf-8")
            if not links:
                await page.screenshot(path=str(output / "listing.png"))
                raise ValueError("No job links found; inspect saved listing for access restrictions or changed navigation")
            addresses = sorted(links)[:limit]
            for index, address in enumerate(addresses):
                cancellation.check()
                try:
                    try:
                        html = await asyncio.to_thread(fetch, address, cfg["timeout_seconds"])
                    except HTTPError as exc:
                        if exc.code in (429, 403):
                            errors.append({"url": address, "error": "rate_limited" if exc.code == 429 else "access_denied", "remaining_unfetched": len(addresses) - index})
                            break
                        html = ""
                    except Exception:
                        html = ""
                    if access_block(html):
                        (output / (identity(address) + ".html")).write_text(html, encoding="utf-8")
                        errors.append({"url": address, "error": access_block(html), "remaining_unfetched": len(addresses) - index})
                        break
                    extracted = postings(html, address)
                    if not extracted:
                        await page.goto(address, wait_until="domcontentloaded", timeout=timeout)
                        await page.wait_for_timeout(1000)
                        html = await page.content()
                        extracted = postings(html, address)
                    (output / (identity(address) + ".html")).write_text(html, encoding="utf-8")
                    if access_block(html):
                        errors.append({"url": address, "error": access_block(html), "remaining_unfetched": len(addresses) - index})
                        break
                    if not extracted:
                        errors.append({"url": address, "error": "No JobPosting data; adapter needs a site-specific parser"})
                    rows.extend(extracted)
                    (output / "raw.partial.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                except Exception as exc:
                    errors.append({"url": address, "error": str(exc)})
                await asyncio.sleep(config.get("request_delay_seconds", cfg["request_delay_seconds"]))
        finally:
            await browser.close()
    return rows[:limit], errors


def collect(archive, cfg, name, limit=None, force=False, recover_descriptions=True):
    """Acquire a source; pipeline callers defer optional description recovery until after filters."""
    if name not in cfg["sources"]:
        raise ValueError("Unknown source")
    source = cfg["sources"][name]
    if not source["enabled"]:
        result = {"source": name, "status": "access_required", "reason": source.get("reason", "Disabled")}
        archive.run(name, result["status"], result)
        return result
    count = cfg["max_jobs"] if limit is None else limit
    if count < 1:
        raise ValueError("limit must be positive")
    config_text = (ROOT / source["config"]).read_text(encoding="utf-8")
    fingerprint = identity("adapter-2.1-details" + config_text + json.dumps(source, sort_keys=True))
    recent = archive.db.execute("SELECT created_at,detail FROM runs WHERE source=? AND status='success' ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    previous = json.loads(recent[1]) if recent else {}
    if recent and not force and previous.get("requested_limit", 0) >= count and previous.get("config_hash") == fingerprint and (datetime.now(timezone.utc) - datetime.fromisoformat(recent[0])).total_seconds() < cfg["refresh_hours"] * 3600:
        return {"source": name, "status": "cached", "last_success": recent[0]}
    output = ROOT / cfg["raw_directory"] / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "-" + name)
    output.mkdir(parents=True)
    started = time.monotonic()
    try:
        import yaml
        config = yaml.safe_load(config_text)
        errors = []
        if source["kind"] == "airtable":
            rows = airtable_rows(config, cfg["timeout_seconds"])[:count]
        elif source["kind"] == "jobspy":
            from jobspy import scrape_jobs
            rows = []
            for query in config["search_queries"]:
                for location in config["locations"]:
                    cancellation.check()
                    if len(rows) >= count:
                        break
                    try:
                        frame = scrape_jobs(site_name=config["site_names"], search_term=query, location=location,
                                            country_indeed=location.lower(), hours_old=config["hours_old"],
                                            results_wanted=min(count - len(rows), config["results_wanted"]), linkedin_fetch_description=recover_descriptions)
                        rows.extend(json.loads(frame.to_json(orient="records", date_format="iso")))
                    except Exception as exc:
                        errors.append({"query": query, "location": location, "error": str(exc)})
                    time.sleep(cfg["request_delay_seconds"])
                if len(rows) >= count:
                    break
        else:
            rows, errors = asyncio.run(browser_rows(config, source, cfg, count, output))
        rows = rows[:count]
        (output / "raw.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        observed = datetime.now(timezone.utc).isoformat()
        report = archive.ingest(rows, name, observed)
        accepted = len(rows) - len(report["rejected"])
        status = "partial" if errors or report["rejected"] else "success" if rows else "empty"
        if not accepted and (errors or report["rejected"]):
            status = "failed"
        result = {"source": name, "status": status, "scope": "bounded sample; completeness not asserted", "import": report, "errors": errors}
        if recover_descriptions and accepted and source["kind"] in ("airtable", "jobspy"):
            from jobhunter.acquisition.descriptions import recover
            ids = {r[0] for r in archive.db.execute("SELECT opportunity_id FROM observations WHERE source=? AND observed_at=?", (name, observed))}
            result["descriptions"] = recover(archive, all_missing=True, opportunity_ids=ids)
            if result["descriptions"]["status"] != "success":
                result["status"] = "partial"
    except Exception as exc:
        logger.exception("Collection failed for %s", name)
        result = {"source": name, "status": "failed", "error": str(exc)}
    result.update(raw_directory=str(output), requested_limit=count, config_hash=fingerprint, adapter_version="2.1", seconds=round(time.monotonic() - started, 2))
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    archive.run(name, result["status"], result)
    return result
