"""Incrementally recover public job descriptions without repeating listing acquisition."""

from datetime import datetime, timedelta, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Event
import json
import logging
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from jobhunter.acquisition.collection import fetch, postings, access_block
from jobhunter.workspace import ROOT, now, url
from jobhunter.operations.progress import write_checkpoint
from jobhunter.operations import cancellation

logger = logging.getLogger(__name__)
PARSER_VERSION = "structured-3"


def has_description(text):
    """Reject ClimateTechList's observed SEO placeholder instead of counting it as job content."""
    text = (text or "").strip()
    return text.lower() not in ("", "none", "null", "nan", "n/a") and not (text.startswith("Job posting details for ") and "ClimateTechList gathers" in text)


def application_link(html, address):
    """Read the explicit employer application link from a ClimateTechList detail page."""
    if (urlsplit(address).hostname or "").removeprefix("www.") != "climatetechlist.com":
        return ""
    from bs4 import BeautifulSoup
    links = {url(a.get("href")) for a in BeautifulSoup(html, "html.parser").select("a[href]")
             if a.get_text(" ", strip=True).startswith("Apply to Job Posting")}
    links.discard("")
    return links.pop() if len(links) == 1 else ""


def coverage(archive):
    """Count unique opportunities by source and board, with missing descriptions separate."""
    groups = defaultdict(lambda: {"all": set(), "missing": set()})
    for row in archive.db.execute("SELECT o.id,o.data,s.source,s.source_url FROM opportunities o JOIN observations s ON s.opportunity_id=o.id"):
        host = urlsplit(row["source_url"]).hostname or ""
        source = "linkedin" if host.endswith("linkedin.com") else "indeed" if "indeed." in host else row["source"]
        groups[source]["all"].add(row["id"])
        if not has_description(json.loads(row["data"]).get("description")):
            groups[source]["missing"].add(row["id"])
    return {"sources": [{"source": name, "opportunities": len(value["all"]), "missing": len(value["missing"])} for name, value in sorted(groups.items())]}


def extract(html, address):
    """Use JobPosting data or LinkedIn's public description container, never whole-page boilerplate.

    Restituisce anche i campi già parsificati della pagina: `hiringOrganization.sameAs` è
    l'indirizzo del sito aziendale nel 75% delle pagine e buttarlo costerebbe un fetch in più
    per azienda (DESIGN §5).
    """
    if access_block(html):
        raise ValueError(access_block(html))
    rows = postings(html, address)
    facts = {k: v for k, v in (rows[0] if len(rows) == 1 else {}).items()
             if k in ("website_url", "locations", "posted_at", "employment_type", "remote_policy", "salary") and v}
    descriptions = [r["description"] for r in rows if has_description(r.get("description"))]
    if len(descriptions) == 1:
        return descriptions[0], "jobposting_jsonld", facts
    if (urlsplit(address).hostname or "").endswith("linkedin.com"):
        from bs4 import BeautifulSoup
        node = BeautifulSoup(html, "html.parser").select_one(".show-more-less-html__markup")
        if node:
            return node.get_text("\n", strip=True), "linkedin_public_description", facts
    if urlsplit(address).hostname == "jobs.smartrecruiters.com":
        from bs4 import BeautifulSoup
        node = BeautifulSoup(html, "html.parser").select_one('[itemprop="description"]')
        if node:
            return node.get_text("\n", strip=True), "smartrecruiters_microdata", facts
    return "", "no_description", facts


def recover(archive, limit=None, source=None, all_missing=False, opportunity_ids=None, workers=None, refresh_stale=False, force=False, progress=None):
    """Fetch missing details driven by company coverage; worker threads never access SQLite."""
    cfg = json.loads((ROOT / "config/descriptions.json").read_text(encoding="utf-8"))
    workers = cfg.get("workers", 1) if workers is None else workers
    if not 1 <= workers <= cfg.get("max_workers", 16):
        raise ValueError("Invalid description worker count")
    limit = cfg["default_limit"] if limit is None else limit
    if not all_missing and not 1 <= limit <= cfg["max_limit"]:
        raise ValueError(f"Choose a limit from 1 to {cfg['max_limit']}")
    output = ROOT / cfg["output_directory"] / now().replace(":", "").replace("+", "_")
    output.mkdir(parents=True)
    started = time.monotonic()
    candidates, unsupported = [], 0
    cached, deferred = 0, 0
    missing_ids = set()
    # DESIGN §4: il recupero risponde alla copertura aziendale, non ai filtri sui ruoli. Un'azienda
    # senza evidenza propria ha diritto al suo primo annuncio prima che una già coperta ne prenda un secondo.
    blind = {r[0] for r in archive.db.execute("""SELECT c.id FROM companies c WHERE c.description=''
        AND NOT EXISTS(SELECT 1 FROM opportunities o WHERE o.company_id=c.id
                       AND length(trim(COALESCE(json_extract(o.data,'$.description'),'')))>0)""")}
    seen_per_company = defaultdict(int)
    excluded_companies = {r["company_id"] for r in archive.db.execute("SELECT f.company_id,f.status,d.reason FROM feedback f LEFT JOIN feedback_detail d ON d.event_id=f.id WHERE f.opportunity_id IS NULL AND f.undone_at IS NULL AND f.id=(SELECT max(x.id) FROM feedback x WHERE x.company_id=f.company_id AND x.opportunity_id IS NULL AND x.undone_at IS NULL)") if r["status"] in ("discarded", "contacted") and r["reason"] not in ("not_now", "no_current_roles")}
    excluded_by_company = 0
    checks = {r["opportunity_id"]: dict(r) for r in archive.db.execute("SELECT * FROM description_attempts")}
    current = datetime.now(timezone.utc)
    stopped_hosts = {((urlsplit(r["source_url"]).hostname or "").removeprefix("www.")) for r in checks.values()
                     if r["status"] == "blocked" and r["retry_after"] and r["retry_after"] > current.isoformat()}
    for row in archive.db.execute("SELECT id,company_id,data,first_seen FROM opportunities ORDER BY id"):
        if opportunity_ids is not None and row["id"] not in opportunity_ids:
            continue
        job = json.loads(row["data"])
        if source and job["source"] != source:
            continue
        if row["company_id"] in excluded_companies:
            excluded_by_company += 1
            continue
        check = checks.get(row["id"], {})
        fetched = check.get("last_success_at") or job.get("description_provenance", {}).get("retrieved_at") or row["first_seen"]
        try:
            stale = (current - datetime.fromisoformat(fetched)).days >= cfg.get("refresh_after_days", 30)
        except (TypeError, ValueError):
            stale = True
        if has_description(job.get("description")) and not (refresh_stale and stale):
            cached += 1
            continue
        if not has_description(job.get("description")):
            missing_ids.add(row["id"])
        if not force and ((check.get("retry_after") and check["retry_after"] > current.isoformat()) or
                          (check.get("status") == "unsupported_parser" and check.get("parser_version") == PARSER_VERSION)):
            deferred += 1
            continue
        if urlsplit(job["source_url"]).hostname not in cfg["allowed_hosts"]:
            unsupported += 1
            continue
        rank = seen_per_company[row["company_id"]]
        seen_per_company[row["company_id"]] += 1
        candidates.append((0 if row["company_id"] in blind else 1, rank, row["id"], job))
    selected = sorted(candidates) if all_missing else sorted(candidates)[:limit]
    report = {"status": "running", "scope": "all_eligible_missing" if all_missing else "eligible_batch",
              "pid": os.getpid(), "selected_total": len(selected), "phase": "descriptions",
              "cached": cached, "deferred": deferred, "refresh_stale": refresh_stale,
              "workers": workers, "request_delay_seconds": cfg["request_delay_seconds"],
              "started_at": now(), "saved": 0, "websites": 0, "attempted": 0, "eligible_missing": len(candidates),
              "blind_companies": len(blind), "unsupported_missing": unsupported,
              "excluded_by_company": excluded_by_company,
              "items": [], "raw_directory": str(output)}
    blocked, lock = set(stopped_hosts), Lock()
    stop_event = Event()
    cancelled = False

    def retrieve(candidate):
        """Download and parse one job, sharing host stops and returning evidence to the DB owner."""
        *_, oid, job = candidate
        address = job["source_url"]
        item = {"id": oid, "url": address, "requests": 0}
        request_host = (urlsplit(address).hostname or "").removeprefix("www.")
        begin = time.monotonic()
        description, provenance, facts = "", None, {}
        try:
            for step in range(2):
                if stop_event.is_set():
                    item.update(status='skipped', error='Interrotto su richiesta')
                    break
                with lock:
                    if request_host in blocked:
                        item.update(status="skipped", error="Host stopped in this batch")
                        break
                item["requests"] += 1
                html = fetch(address, cfg["timeout_seconds"])
                path = output / (oid + ("-application" if step else "") + ".html")
                path.write_text(html, encoding="utf-8")
                description, method, page_facts = extract(html, address)
                facts = page_facts or facts
                if description:
                    provenance = {"url": address, "retrieved_at": now(), "method": method, "raw_file": str(path)}
                    item.update(status="retrieved", characters=len(description), method=method)
                    break
                application = application_link(html, address) if step == 0 else ""
                if not application or application == address:
                    raise ValueError("No unambiguous job description; inspect saved page")
                address = application
                request_host = (urlsplit(address).hostname or "").removeprefix("www.")
                item["application_url"] = application
                time.sleep(cfg["request_delay_seconds"])
        except (OSError, ValueError) as exc:
            item.update(status="failed", error=str(exc))
            if isinstance(exc, HTTPError) and exc.code in (403, 429, 999) or str(exc) in ("rate_limited", "access_denied"):
                with lock:
                    blocked.add(request_host)
                item["host_stopped"] = request_host
        item["seconds"] = round(time.monotonic() - begin, 3)
        if item["requests"]:
            time.sleep(cfg["request_delay_seconds"])
        return item, description, provenance, facts

    written = [0.0]

    def checkpoint(force=True):
        """Replace the report atomically so readers cannot observe a truncated JSON document.

        Scriverlo dopo ogni annuncio costa O(n²): il report cresce con la lista `items` e verrebbe
        riserializzato per intero ogni volta, serializzando proprio il lavoro dei worker. Fra un
        annuncio e l'altro basta un aggiornamento ogni `checkpoint_seconds`; i confini della run
        restano sempre scritti.
        """
        if not force and time.monotonic() - written[0] < cfg.get("checkpoint_seconds", 2):
            return
        written[0] = time.monotonic()
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        with lock:
            report["blocked_hosts"] = sorted(blocked)
        write_checkpoint(output / "report.json", report)

    checkpoint()
    cancellation.check()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(retrieve, candidate) for candidate in selected]
        for future in as_completed(futures):
            if future.cancelled():
                continue
            item, description, provenance, facts = future.result()
            report["attempted"] += bool(item["requests"])
            website = url(facts.get("website_url") or "")
            if website:
                # hiringOrganization.sameAs: l'indirizzo del sito aziendale, gratis su una pagina già scaricata.
                with archive.db:
                    archive.db.execute("UPDATE companies SET website=? WHERE website='' AND id=(SELECT company_id FROM opportunities WHERE id=?)", (website, item["id"]))
                report["websites"] += 1
            if provenance:
                saved = archive.save_description(item["id"], description, provenance, replace=refresh_stale)
                item["status"] = "saved" if saved else "already_present"
                report["saved"] += bool(saved)
                missing_ids.discard(item["id"])
            if item["requests"]:
                status = "available" if provenance else "blocked" if item.get("host_stopped") else "missing_page" if "HTTP Error 404" in item.get("error", "") else "unsupported_parser" if "No unambiguous" in item.get("error", "") else "temporary_error"
                retry = None if status in ("available", "unsupported_parser") else (datetime.now(timezone.utc) + (timedelta(days=cfg.get("missing_page_retry_days", 7)) if status == "missing_page" else timedelta(hours=cfg.get("retry_after_hours", 24)))).isoformat()
                with archive.db:
                    archive.record_description_attempt(item["id"], status, item.get("application_url", item["url"]), item.get("error", ""), retry, parser_version=PARSER_VERSION)
            report["items"].append(item)
            checkpoint(force=False)
            if not cancelled:
                try:
                    cancellation.check()
                except cancellation.Cancelled:
                    cancelled = True
                    stop_event.set()
                    for pending in futures:
                        pending.cancel()
            # Reported after the cancellation check so a stop request never bypasses the graceful path.
            if progress and not cancelled:
                progress({"phase": "descriptions", "done": len(report["items"]), "total": len(selected),
                          "saved": report["saved"], "attempted": report["attempted"],
                          "workers": workers, "started_at": report["started_at"]})
            logger.info("Description %s: %s (%s/%s)", item["id"], item["status"], len(report["items"]), len(selected))
    report["unattempted"] = len(candidates) - report["attempted"]
    report["remaining_missing"] = len(missing_ids)
    report["remaining_to_check"] = len(candidates) - report["saved"] + unsupported + deferred
    report["failed"] = sum(item["status"] == "failed" for item in report["items"])
    report["http_requests"] = sum(item["requests"] for item in report["items"])
    report["status"] = "partial" if any(item["status"] in ("failed", "skipped") for item in report["items"]) or all_missing and report["remaining_to_check"] else "success"
    report["finished_at"] = now()
    if cancelled:
        report['status'] = 'interrupted'
    checkpoint()
    report["saved_per_minute"] = round(report["saved"] * 60 / max(report["elapsed_seconds"], .001), 2)
    checkpoint()
    archive.run("description_recovery", report["status"], report)
    if cancelled:
        raise cancellation.Cancelled()
    return report
