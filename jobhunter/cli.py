"""JSON-first command line for the company archive and Codex skill."""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from jobhunter.workspace import Archive, ROOT, STATUSES, settings, categories

logger = logging.getLogger(__name__)


def read_json(path):
    """Read explicit UTF-8 JSON input files."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def parser():
    """Define stable non-interactive commands with machine-readable output."""
    p = argparse.ArgumentParser(description="JobHunter: archivio aziendale per Codex e dashboard locale")
    p.add_argument("--db", help="SQLite database override")
    p.add_argument("--config", help="Application JSON configuration override")
    sub = p.add_subparsers(dest="command", required=True)
    for command, help_text in [("init", "Initialize archive"), ("stats", "Counts and collection outcomes"), ("sources", "Source access and health"), ("import-legacy", "Import per-source snapshots, preserving originals")]:
        sub.add_parser(command, help=help_text)
    imp = sub.add_parser("import", help="Import unaggregated JSON or CSV")
    imp.add_argument("path")
    imp.add_argument("--source", required=True)
    search = sub.add_parser("search", help="Search one result per company")
    for field in ("query", "source", "location", "category"):
        search.add_argument("--" + field, default="")
    search.add_argument("--status", choices=STATUSES, default="")
    search.add_argument("--limit", type=int, default=30)
    search.add_argument("--offset", type=int, default=0)
    sub.add_parser("show", help="Company with opportunities and provenance").add_argument("id")
    sub.add_parser("categories", help="List the shared category vocabulary")
    sub.add_parser("collect-all", help="Broad acquisition of all configured source scopes with coverage report")
    sub.add_parser("queue", help="Resume the personal company queue")
    sub.add_parser("metrics", help="Decision coverage and reasons")
    sub.add_parser("research-brief", help="Prepare focused online research in chat").add_argument("id")
    proposal = sub.add_parser("proposals", help="List or explicitly accept/dismiss/disable learned preferences")
    proposal.add_argument("id", nargs="?")
    proposal.add_argument("--state", choices=("accepted", "dismissed", "disabled"))
    shortlist = sub.add_parser("shortlist", help="Company groups with initial role-filter explanations")
    shortlist.add_argument("--limit", type=int, default=30)
    shortlist.add_argument("--offset", type=int, default=0)
    enrich = sub.add_parser("enrich", help="Explicit bounded local Ollama pass; preserves source records")
    enrich.add_argument("task", choices=("description", "category"))
    enrich.add_argument("--limit", type=int)
    enrich.add_argument("--model")
    enrich.add_argument("--force", action="store_true")
    classify = sub.add_parser("categorize", help="Refresh automatic categories, or assign one company from chat")
    classify.add_argument("id", nargs="?")
    classify.add_argument("--category")
    classify.add_argument("--reason", default="")
    feedback = sub.add_parser("feedback", help="Persist a scoped decision")
    feedback.add_argument("id")
    feedback.add_argument("status", choices=STATUSES)
    feedback.add_argument("--note", default="")
    feedback.add_argument("--opportunity")
    feedback.add_argument("--reason", default="other")
    feedback.add_argument("--until", help="YYYY-MM-DD reminder date")
    sub.add_parser("undo", help="Undo a feedback event").add_argument("event_id", type=int)
    assessment = sub.add_parser("assess", help="Import a chat assessment JSON")
    assessment.add_argument("id")
    assessment.add_argument("path")
    evidence = sub.add_parser("evidence", help="Attach attributed online research")
    evidence.add_argument("id")
    evidence.add_argument("url")
    evidence.add_argument("--note", required=True)
    company = sub.add_parser("add-company", help="Add a research candidate without inventing a job")
    company.add_argument("name")
    company.add_argument("--website", default="")
    profile = sub.add_parser("profile", help="Read profile and explicit preference additions")
    profile.add_argument("--add", help="Append an explicit preference")
    export = sub.add_parser("export", help="Export companies as JSON or CSV")
    export.add_argument("path")
    export.add_argument("--status", choices=STATUSES, default="")
    collect = sub.add_parser("collect", help="Bounded acquisition; no LLM")
    collect.add_argument("source")
    collect.add_argument("--limit", type=int)
    collect.add_argument("--force", action="store_true")
    serve = sub.add_parser("serve", help="Dashboard on 127.0.0.1")
    serve.add_argument("--port", type=int)
    return p


def execute(args, archive, cfg):
    """Dispatch one operation without implicit scraping or preference edits."""
    command = args.command
    if command == "collect-all":
        from jobhunter.sweep import sweep
        return sweep(archive, cfg)
    if command in ("queue", "metrics", "proposals", "research-brief"):
        from jobhunter.selection import queue, metrics, proposals, research_brief
        if command == "queue": return queue(archive)
        if command == "metrics": return metrics(archive)
        if command == "proposals": return proposals(archive, args.id, args.state)
        return research_brief(archive, args.id)
    if command == "shortlist":
        from jobhunter.selection import shortlist
        return shortlist(archive, args.limit, args.offset)
    if command == "enrich":
        from jobhunter.enrichment import run
        return run(archive, args.task, args.limit, args.force, args.model)
    if command in ("init", "stats"):
        return archive.stats()
    if command == "sources":
        return {"sources": cfg["sources"], "recent_runs": archive.stats()["runs"]}
    if command == "import":
        path = Path(args.path)
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
        else:
            rows = read_json(path)
        if not isinstance(rows, list):
            raise ValueError("Import must be a list of unaggregated records")
        return archive.ingest(rows, args.source, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
    if command == "import-legacy":
        reports = []
        for path in sorted((ROOT / "data/runs/scrape").glob("*/structured_results.json")):
            stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            report = archive.ingest(read_json(path), path.parent.name, stamp)
            report["source"] = path.parent.name
            reports.append(report)
        return reports
    if command == "search":
        return archive.search(args.query, args.status, args.source, args.location, args.limit, args.offset, args.category)
    if command == "categories":
        return {"categories": [*categories(), "Da classificare"]}
    if command == "categorize":
        if not args.id and (args.category or args.reason):
            raise ValueError("A company ID is required for a chat assignment")
        return archive.categorize(args.id, args.category, args.reason)
    if command == "show":
        return archive.show(args.id)
    if command == "feedback":
        return archive.feedback(args.id, args.status, args.note, args.opportunity, args.reason, args.until)
    if command == "undo":
        return archive.undo(args.event_id)
    if command == "assess":
        return archive.assess(args.id, read_json(args.path))
    if command == "evidence":
        return archive.add_evidence(args.id, args.url, args.note)
    if command == "add-company":
        with archive.db:
            cid = archive.company(args.name, args.website)
        return {"id": cid}
    if command == "profile":
        if args.add:
            archive.preference(args.add)
        path = ROOT / cfg["profile"]
        return {"profile": path.read_text(encoding="utf-8") if path.exists() else "", "search_profile": (ROOT / "user_context/search-profile.md").read_text(encoding="utf-8"), "preferences": [dict(r) for r in archive.db.execute("SELECT * FROM preferences ORDER BY id")]}
    if command == "export":
        items, offset = [], 0
        while True:
            page = archive.search(status=args.status, limit=500, offset=offset)
            items.extend(page["items"])
            offset += len(page["items"])
            if offset >= page["total"]:
                break
        path = Path(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".csv":
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                columns = ["id", "name", "category", "category_method", "status", "website", "sectors", "locations", "opportunity_count", "last_seen"]
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                for item in items:
                    values = {k: ", ".join(item[k]) if isinstance(item[k], list) else item[k] for k in columns}
                    values = {k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for k, v in values.items()}
                    writer.writerow(values)
        else:
            path.write_text(json.dumps({"schema_version": "2", "companies": [archive.show(x["id"]) for x in items]}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"exported": len(items), "path": str(path.resolve())}
    if command == "collect":
        from jobhunter.collection import collect
        return collect(archive, cfg, args.source, args.limit, args.force)
    if command == "serve":
        from jobhunter.dashboard import serve
        serve(archive.path, cfg, args.port or cfg["port"])
        return None
    raise ValueError("Unknown command")


def main():
    """Use stderr diagnostics and nonzero exit codes for operational failures."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    archive = None
    try:
        cfg = settings(args.config)
        archive = Archive(args.db or ROOT / cfg["database"])
        result = execute(args, archive, cfg)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if isinstance(result, dict) and (result.get("status") in ("failed", "partial", "access_required") or result.get("failed")) else 0
    except (ValueError, OSError, KeyError) as exc:
        logger.error("%s", exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        if archive:
            archive.close()


if __name__ == "__main__":
    sys.exit(main())
