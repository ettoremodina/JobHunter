"""Print saved cards from one company-batch report without network or model calls."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def arguments():
    """Accept an optional report and archive override."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="company-batch report.json; defaults to latest")
    parser.add_argument("--db", type=Path, help="SQLite archive; defaults to config/app.json")
    return parser.parse_args()


def latest_report():
    """Return the newest company-batch report by directory timestamp."""
    reports = sorted((ROOT / "data/remote-llm").glob("company-batch-*/report.json"))
    if not reports:
        raise SystemExit("No company-batch report found")
    return reports[-1]


def database_path():
    """Resolve the configured archive path."""
    cfg = json.loads((ROOT / "config/app.json").read_text(encoding="utf-8"))
    return ROOT / cfg["database"]


def compact(value, limit=300):
    """Make generated prose easy to scan in a terminal."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def saved_result(connection, task, identifier):
    """Read one validated derivative, if the report produced it."""
    row = connection.execute(
        "SELECT data FROM enrichments WHERE task=? AND record_id=?", ("remote:" + task, identifier)
    ).fetchone()
    return (json.loads(row[0]).get("result") or {}) if row else {}


def main():
    """Print run totals followed by company and role cards touched by the run."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = arguments()
    report_path = (args.report or latest_report()).resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    connection = sqlite3.connect((args.db or database_path()).resolve())
    try:
        print("# Audit schede Qwen")
        print(f"Report: {report_path}")
        print(f"Stato: {report['status']} | chiamate: {report['counts'].get('api_calls', 0)} | rifiuti: {report['counts'].get('rejected_jobs', 0)}")
        for item in report.get("items", []):
            company = connection.execute("SELECT name FROM companies WHERE id=?", (item["id"],)).fetchone()
            card = saved_result(connection, "company-summary", item["id"])
            print(f"\n## {company[0] if company else item['id']}")
            company_text = compact(card.get("summary"))
            if card and not company_text:
                company_text = "(vuota: " + compact("; ".join(card.get("missing_information") or []), 180) + ")"
            print(f"Azienda: {company_text or '(non richiesta o non salvata)'}")
            for oid in item.get("job_ids", []):
                row = connection.execute("SELECT data FROM opportunities WHERE id=?", (oid,)).fetchone()
                title = json.loads(row[0]).get("title") if row else oid
                result = saved_result(connection, "job-summary", oid)
                summary = compact(result.get("summary"))
                if result and not summary:
                    summary = "(vuota: " + compact("; ".join(result.get("missing_information") or []), 180) + ")"
                print(f"- {title}: {summary or '(non salvata)'}")
                for fact in (result.get("facts") or [])[:4]:
                    print(f"  - [{fact.get('section', '?')}] {compact(fact.get('text'), 180)}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
