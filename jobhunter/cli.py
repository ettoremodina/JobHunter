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
    status = sub.add_parser("status", help="Read workflow progress from another terminal without opening SQLite")
    status.add_argument("--watch", action="store_true", help="Refresh until Ctrl+C; does not stop the worker")
    status.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds, from 1 to 3600")
    status.add_argument("--run", help="Explicit workflow/detail report path or directory")
    status.add_argument("--json", action="store_true", help="Machine-readable snapshot; JSON lines with --watch")
    for command, help_text in [("init", "Initialize archive"), ("stats", "Counts and collection outcomes"), ("sources", "Source access and health")]:
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
    search.add_argument("--eligibility", choices=("potential", "review", "excluded"), default="", help="Only roles matching the current profile-filter outcome")
    sub.add_parser("show", help="Company with opportunities and provenance").add_argument("id")
    sub.add_parser("categories", help="List the shared category vocabulary")
    sweep = sub.add_parser("collect-all", help="Broad acquisition including descriptions, categories and selection")
    sweep.add_argument("--fresh", action="store_true", help="Back up SQLite and reset acquired data while preserving personal records")
    sweep.add_argument("--resume-after-listings", action="store_true", help="Reuse archived listings and continue details, categories and selection")
    sub.add_parser("queue", help="Resume the personal company queue")
    sub.add_parser("metrics", help="Decision coverage and reasons")
    analytics = sub.add_parser("analytics", help="Archive distributions and data completeness; no network or LLM")
    analytics.add_argument("--eligibility", choices=("potential", "review", "excluded"), default="")
    sub.add_parser("description-coverage", help="Missing descriptions by source and board")
    sub.add_parser("reparse-descriptions", help="Restore source structure from saved HTML, without network requests")
    sub.add_parser("data-quality", help="Source usefulness, fetch outcomes and possible duplicates")
    review_sample = sub.add_parser("prepare-review", help="Create a manual calibration sample without model calls")
    review_sample.add_argument("--limit", type=int, default=20)
    sub.add_parser("score-review", help="Evaluate human labels separately for development and holdout").add_argument("path")
    descriptions = sub.add_parser("fetch-descriptions", help="Recover a bounded batch of missing public descriptions")
    descriptions.add_argument("--limit", type=int)
    descriptions.add_argument("--source")
    descriptions.add_argument("--refresh-stale", action="store_true", help="Refresh cached descriptions older than configured age")
    descriptions.add_argument("--force", action="store_true", help="Retry failed jobs; active host cooldowns still apply")
    descriptions.add_argument("--workers", type=int, help="Concurrent detail workers")
    benchmark = sub.add_parser("benchmark-descriptions", help="Compare disjoint detail batches; persist texts and stop on source blocks")
    benchmark.add_argument("--batch-size", type=int, default=50)
    benchmark.add_argument("--workers", type=int, nargs="+", default=[1, 4, 8])
    descriptions.add_argument("--all", action="store_true", help="Fetch every eligible missing description after listing screening")
    interview = sub.add_parser("review-questions", help="Prepare grouped questions for uncertain roles in chat")
    interview.add_argument("--limit", type=int)
    rule = sub.add_parser("review-rule", help="Preview a user preference rule; persist only with --apply")
    rule.add_argument("path")
    rule.add_argument("--apply", action="store_true")
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
    enrich.add_argument("--missing-only", action="store_true", help="Only uncategorized companies with company-level evidence")
    remote = sub.add_parser("llm", help="Preview or explicitly execute remote selection and concise summaries")
    from jobhunter.remote_llm import TASKS
    remote.add_argument("task", choices=TASKS)
    remote.add_argument("--limit", type=int)
    remote.add_argument("--record-id", help="Opportunity ID for jobs, company ID for company summary")
    remote.add_argument("--llm-config", help="Remote model configuration JSON")
    remote.add_argument("--execute", action="store_true", help="Send selected source text to the configured paid API")
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
    if command == "llm":
        from jobhunter.remote_llm import run
        return run(archive, args.task, args.limit, args.execute, args.llm_config, args.record_id)
    if command == "analytics":
        from jobhunter.analytics import summary
        return summary(archive, args.eligibility)
    if command == "collect-all":
        from jobhunter.sweep import sweep
        if args.fresh and args.resume_after_listings:
            raise ValueError("Choose either --fresh or --resume-after-listings")
        if args.fresh:
            archive.reset_collection()
        return sweep(archive, cfg, resume_after_listings=args.resume_after_listings)
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
        return run(archive, args.task, args.limit, args.force, args.model, args.missing_only)
    if command in ("init", "stats"):
        return archive.stats()
    if command == "sources":
        return {"sources": cfg["sources"], "recent_runs": archive.stats()["runs"]}
    if command == "description-coverage":
        from jobhunter.descriptions import coverage
        return coverage(archive)
    if command in ("reparse-descriptions", "data-quality", "prepare-review", "score-review"):
        from jobhunter.maintenance import reparse, quality, prepare_review, score_review
        if command == "reparse-descriptions": return reparse(archive)
        if command == "data-quality": return quality(archive)
        if command == "prepare-review": return prepare_review(archive, args.limit)
        return score_review(args.path)
    if command == "fetch-descriptions":
        from jobhunter.descriptions import recover
        return recover(archive, args.limit, args.source, args.all, workers=args.workers, refresh_stale=args.refresh_stale, force=args.force)
    if command == "benchmark-descriptions":
        from jobhunter.benchmark import run
        return run(archive, args.batch_size, args.workers)
    if command == "review-questions":
        from jobhunter.interview import questions
        return questions(archive, args.limit)
    if command == "review-rule":
        from jobhunter.interview import review_rule
        return review_rule(archive, read_json(args.path), args.apply)
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
    if command == "search":
        return archive.search(args.query, args.status, args.source, args.location, args.limit, args.offset, args.category, args.eligibility)
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
        if args.command == "status":
            from jobhunter.progress import monitor
            monitor(ROOT, cfg, args.run, args.watch, args.interval, args.json)
            return 0
        archive = Archive(args.db or ROOT / cfg["database"])
        result = execute(args, archive, cfg)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if isinstance(result, dict) and (result.get("status") in ("failed", "partial", "access_required", "blocked") or result.get("failed")) else 0
    except (ValueError, OSError, KeyError) as exc:
        logger.error("%s", exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        if archive:
            archive.close()


if __name__ == "__main__":
    sys.exit(main())
