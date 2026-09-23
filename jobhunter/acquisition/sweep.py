"""Broad acquisition across all configured queries with checkpoints and coverage reporting."""

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
import subprocess
import sys
from jobhunter.workspace import ROOT, now
from jobhunter.acquisition.collection import airtable_rows, browser_rows
from jobhunter.operations.progress import write_checkpoint

logger = logging.getLogger(__name__)


def board_query(spec, directory, index, timeout):
    """Run one isolated query and recover completed pages even after timeout or failure."""
    input_path, output = directory/f"query-{index}.json", directory/f"results-{index}.json"
    input_path.write_text(json.dumps(spec), encoding="utf-8")
    error = None
    with (directory/f"query-{index}.log").open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run([sys.executable, "-B", "-m", "jobhunter.acquisition.board_worker", str(input_path), str(output)], cwd=ROOT, stdout=log, stderr=log, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                error = f"worker_exit_{result.returncode}"
        except subprocess.TimeoutExpired:
            error = "query_timeout"
    saved = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"rows": [], "pages": 0, "stop": "no_output"}
    return {"query": spec["search_term"], "location": spec["location"], "board": spec["site_name"], "error": error, **saved}


def sweep(archive, cfg):
    """Acquire all accessible configured source scopes; report caps and access limits explicitly."""
    import yaml
    limits = json.loads((ROOT/"config/sweep.json").read_text(encoding="utf-8"))
    output = ROOT/cfg["raw_directory"]/ (now().replace(":", "").replace("+", "_") + "-official")
    output.mkdir(parents=True)
    report = {"started_at": now(), "scope": "All configured source URLs, countries and search queries; site-wide completeness is not asserted", "sources": {}}
    report.update(pid=os.getpid(), status="running")

    def checkpoint(phase):
        """Persist the current stage without making a temporary reader lock abort collection."""
        report.update(phase=phase, updated_at=now())
        write_checkpoint(output / "report.json", report)

    checkpoint("listings")
    ordered = sorted(cfg["sources"].items(), key=lambda item: {"airtable": 0, "browser": 1, "jobspy": 2}[item[1]["kind"]])
    for name, source in ordered:
        directory = output/name
        directory.mkdir()
        details = {"status": "running", "received": 0, "accepted": 0, "queries": []}
        report["sources"][name] = details
        checkpoint("listings")
        if not source["enabled"]:
            details.update(status="access_required", reason=source.get("reason"))
            archive.run(name, details["status"], details)
            continue
        conf = yaml.safe_load((ROOT/source["config"]).read_text(encoding="utf-8"))
        try:
            if source["kind"] == "jobspy":
                specs = [{"site_name": board, "search_term": query, "location": location, "country_indeed": "uk" if location == "UK" else location.lower(), "hours_old": conf["hours_old"], "results_wanted": limits["jobspy_page_size"], "linkedin_fetch_description": False, "max_pages": limits["jobspy_max_pages"], "pause": cfg["request_delay_seconds"]} for query in conf["search_queries"] for location in conf["locations"] for board in conf["site_names"]]
                details["description_policy"] = "Discover listings first; retrieve missing descriptions once per unique opportunity after import, before selection"
                details["expected_queries"] = len(specs)
                with ThreadPoolExecutor(max_workers=limits["jobspy_workers"]) as pool:
                    futures = [pool.submit(board_query, spec, directory, i, limits["jobspy_query_timeout_seconds"]) for i, spec in enumerate(specs)]
                    for future in as_completed(futures):
                        result = future.result()
                        rows = result.pop("rows")
                        imported = archive.ingest(rows, name)
                        result["import"] = imported
                        details["queries"].append(result)
                        details["received"] += len(rows)
                        details["accepted"] += len(rows)-len(imported["rejected"])
                        checkpoint("listings")
                        logger.info("Official %s: %s/%s queries, %s records", name, len(details["queries"]), len(specs), details["received"])
                details["status"] = "partial" if any(q["error"] or q["stop"] in ("page_cap", "empty_or_blocked", "no_output") or q["import"]["rejected"] for q in details["queries"]) else "success"
            else:
                if source["kind"] == "airtable":
                    rows, errors = airtable_rows(conf, cfg["timeout_seconds"]), []
                    details["scope"] = "Entire public embed response; server-side completeness unverified"
                else:
                    broad_cfg = {**cfg, "max_pages": limits["browser_max_pages"]}
                    rows, errors = asyncio.run(browser_rows(conf, source, broad_cfg, limits["browser_max_jobs"], directory))
                    details["scope"] = "Configured listing, until navigation stalls or configured safety caps; see discovery.json"
                (directory/"raw.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                imported = archive.ingest(rows, name)
                details.update(received=len(rows), accepted=len(rows)-len(imported["rejected"]), errors=errors, import_report=imported)
                details["status"] = "partial" if errors or imported["rejected"] else "success" if rows else "empty"
                if source["kind"] == "browser":
                    details["discovery"] = json.loads((directory/"discovery.json").read_text(encoding="utf-8"))
                    details["status"] = "partial"
        except Exception as exc:
            logger.exception("Official source %s failed", name)
            details.update(status="failed", error=str(exc))
        archive.run(name, details["status"], details)
        checkpoint("listings")
    # DESIGN §10: collect-all fa solo raccolta. Descrizioni, categorie, analisi e coda hanno il loro
    # posto nei dieci passi e non vanno ripetute con un ordine diverso alla fine di uno sweep.
    from jobhunter.acquisition.descriptions import coverage
    checkpoint("coverage")
    report["description_coverage"] = coverage(archive)
    report["finished_at"] = now()
    report["status"] = "success" if all(v["status"] == "success" for v in report["sources"].values()) else "partial"
    report["archive"] = {k: archive.stats()[k] for k in ("companies", "opportunities")}
    checkpoint("finished")
    return {"status": report["status"], "report": str(output/"report.json"), "archive": report["archive"], "sources": {k: v["status"] for k, v in report["sources"].items()}}
