"""Print a compact, deterministic audit sample from a completed Jev report.

The script makes no model or network calls. It combines the report with the saved
Jev details in SQLite, checks decision invariants, and selects boundary plus stable
random examples for manual reading.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def arguments():
    """Parse report, sample-size, and database overrides."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Completed System One report.json; defaults to the latest")
    parser.add_argument("--db", type=Path, help="SQLite archive; defaults to config/app.json")
    parser.add_argument("--per-decision", type=int, default=5, help="Role examples for each verdict")
    parser.add_argument("--category-sample", type=int, default=8, help="Company-category examples")
    parser.add_argument("--seed", default="jev-audit-v1", help="Stable sampling seed")
    return parser.parse_args()


def latest_report():
    """Return the newest completed System One report by filename timestamp."""
    reports = sorted((ROOT / "data/system-one").glob("combined-*/report.json"))
    if not reports:
        raise SystemExit("No System One report found")
    return reports[-1]


def database_path():
    """Resolve the configured archive path from the project root."""
    cfg = json.loads((ROOT / "config/app.json").read_text(encoding="utf-8"))
    return ROOT / cfg["database"]


def compact(value, limit=230):
    """Collapse whitespace and bound untrusted source text for a readable audit."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def stable_key(identifier, seed):
    """Provide repeatable pseudo-random order without global random state."""
    return hashlib.sha256(f"{seed}:{identifier}".encode()).hexdigest()


def opportunity(connection, identifier):
    """Read only the few source fields needed to judge one sampled role."""
    row = connection.execute("SELECT data FROM opportunities WHERE id=?", (identifier,)).fetchone()
    return json.loads(row[0]) if row else {}


def saved_answers(connection, task, identifier):
    """Return persisted numeric answers for invariant and boundary checks."""
    row = connection.execute(
        "SELECT data FROM enrichments WHERE task=? AND record_id=?", (task, identifier)
    ).fetchone()
    return (json.loads(row[0]).get("answers") or {}) if row else {}


def role_sample(rows, decision, size, seed):
    """Mix boundary cases with stable random cases for one role verdict."""
    group = [row for row in rows if row["decision"] == decision]
    if not group or size <= 0:
        return []
    boundary_order = {
        "keep": lambda row: row["compatible"],
        "exclude": lambda row: -row["compatible"],
        "review": lambda row: -row["compatible"],
    }[decision]
    boundary_count = min((size + 1) // 2, len(group))
    chosen = sorted(group, key=boundary_order)[:boundary_count]
    chosen_ids = {row["id"] for row in chosen}
    remainder = sorted(
        (row for row in group if row["id"] not in chosen_ids),
        key=lambda row: stable_key(row["id"], seed),
    )
    return chosen + remainder[: size - len(chosen)]


def role_rows(connection, report):
    """Join report verdicts with titles and numeric Jev answers."""
    rows = []
    for item in report.get("items", []):
        result = (item.get("results") or {}).get("selection")
        if not result:
            continue
        answers = saved_answers(connection, "jev:selection", item["id"])
        job = opportunity(connection, item["id"])
        rows.append({
            "id": item["id"],
            "title": job.get("title") or "(senza titolo)",
            "decision": result.get("decision"),
            "compatible": float(answers.get("mansioni_compatibili", -1)),
            "seniority": float(answers.get("seniority_fuori_profilo", -1)),
            "family": answers.get("famiglia_esclusa", "?"),
            "family_confidence": float(answers.get("famiglia_confidenza", -1)),
            "rationale": result.get("rationale") or "",
            "evidence": (result.get("evidence") or [""])[0],
        })
    return rows


def category_rows(connection, report):
    """Join company-category outputs with names and their saved scores."""
    rows = []
    for item in report.get("items", []):
        result = (item.get("results") or {}).get("category")
        if not result:
            continue
        cid = item["company_id"]
        company = connection.execute("SELECT name FROM companies WHERE id=?", (cid,)).fetchone()
        answers = saved_answers(connection, "jev:category", cid)
        labels = result.get("categories") or []
        scores = answers.get("punteggi_categoria") or {}
        rows.append({
            "id": cid,
            "name": company[0] if company else "(azienda sconosciuta)",
            "labels": labels,
            "scores": [float(scores.get(label, -1)) for label in labels],
            "reason": result.get("reason") or "",
        })
    return rows


def invariant_violations(roles, categories, thresholds):
    """Find outcomes that contradict the deterministic combiner configuration."""
    violations = []
    for row in roles:
        if row["decision"] == "keep" and row["seniority"] >= thresholds["flag_above"]:
            violations.append(f"role {row['id']}: keep with seniority {row['seniority']:.0%}")
        if row["decision"] == "keep" and row["compatible"] < thresholds["keep_above"]:
            violations.append(f"role {row['id']}: keep with compatibility {row['compatible']:.0%}")
    for row in categories:
        if len(row["labels"]) > thresholds["max_categories"]:
            violations.append(f"company {row['id']}: too many categories")
        if any(score < thresholds["category_min_score"] for score in row["scores"]):
            violations.append(f"company {row['id']}: category below minimum")
        if len(row["scores"]) == 2 and sum(row["scores"]) < thresholds["category_pair_min_sum"]:
            violations.append(f"company {row['id']}: category pair below minimum sum")
    return violations


def category_sample(rows, size, seed):
    """Prefer weak second labels and unclassified companies, then fill deterministically."""
    pairs = sorted(
        (row for row in rows if len(row["labels"]) == 2),
        key=lambda row: row["scores"][1],
    )
    unclassified = sorted(
        (row for row in rows if not row["labels"]),
        key=lambda row: stable_key(row["id"], seed + ":unclassified"),
    )
    chosen = pairs[: min(size // 2, len(pairs))]
    chosen.extend(unclassified[: min(size - len(chosen), max(1, size // 3))])
    used = {row["id"] for row in chosen}
    remainder = sorted(
        (row for row in rows if row["id"] not in used),
        key=lambda row: stable_key(row["id"], seed + ":category"),
    )
    return chosen + remainder[: size - len(chosen)]


def main():
    """Print metrics, invariant checks, and a compact Markdown audit sample."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = arguments()
    report_path = (args.report or latest_report()).resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cfg = json.loads((ROOT / "config/system_one.json").read_text(encoding="utf-8"))
    connection = sqlite3.connect((args.db or database_path()).resolve())
    try:
        roles = role_rows(connection, report)
        categories = category_rows(connection, report)
        violations = invariant_violations(roles, categories, cfg["thresholds"])
        print("# Audit Jev")
        print(f"Report: {report_path}")
        print(
            f"Run: {report.get('completed', 0)}/{report.get('total', 0)} richieste; "
            f"rifiuti {report.get('counts', {}).get('rejected', 0)}; "
            f"retry throttling {report.get('counts', {}).get('throttled_retries', 0)}"
        )
        print(f"Controlli deterministici: {len(violations)} anomalie")
        for violation in violations[:20]:
            print(f"- {violation}")
        for decision in ("keep", "exclude", "review"):
            print(f"\n## Ruoli {decision}")
            for row in role_sample(roles, decision, args.per_decision, args.seed):
                print(
                    f"- `{row['id']}` {compact(row['title'], 90)} | compat {row['compatible']:.0%} | "
                    f"seniority {row['seniority']:.0%} | famiglia {row['family']} "
                    f"{row['family_confidence']:.0%} | {compact(row['evidence'])}"
                )
        print("\n## Aziende e categorie")
        for row in category_sample(categories, args.category_sample, args.seed):
            labels = ", ".join(
                f"{label} {score:.0%}" for label, score in zip(row["labels"], row["scores"])
            ) or "Da classificare"
            print(f"- `{row['id']}` {compact(row['name'], 90)} | {labels} | {compact(row['reason'])}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
