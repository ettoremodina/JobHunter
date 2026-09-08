"""Reproducible GPT classification sample with exact inputs, outputs and token events."""

import argparse
import csv
from collections import Counter
import json
import logging
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import tempfile
from jobhunter.workspace import ROOT, now, settings
from jobhunter.selection import evaluate, filters

logger = logging.getLogger(__name__)


def prepare(config, directory):
    """Freeze a seeded sample of currently eligible jobs and the full profile evidence."""
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "sample.json").exists():
        raise ValueError("Sample already exists; reuse it rather than silently replacing evidence")
    with sqlite3.connect(ROOT / settings()["database"]) as db:
        jobs = [{**json.loads(data), "id": oid, "company_id": cid} for oid, cid, data in db.execute("SELECT id,company_id,data FROM opportunities ORDER BY id")]
    rules = filters()
    baseline = [{"id": j["id"], "company_id": j["company_id"], **evaluate(j, rules)} for j in jobs]
    allowed = {j["id"] for j in baseline if j["status"] == "potential"}
    pool = [j for j in jobs if j["id"] in allowed]
    random.Random(config["seed"]).shuffle(pool)
    sample = pool[:config["sample_size"]]
    files = {"sample.json": sample, "baseline.json": baseline, "rules-before.json": rules,
             "manifest.json": {**config, "created_at": now(), "population": len(jobs), "baseline_counts": dict(Counter(j["status"] for j in baseline)), "sampling": "Seeded shuffle of eligible opportunities sorted by ID; first sample_size selected", "sample_missing_description": sum(not j.get("description") for j in sample)}}
    for name, content in files.items():
        (directory / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(ROOT / rules["profile_evidence"], directory / "portfolio.md")
    shutil.copyfile(ROOT / "config/prompts/calibration.txt", directory / "prompt.txt")
    logger.info("Prepared %s jobs from %s eligible", len(sample), len(pool))


def validate(result, jobs):
    """Reject malformed batches; downgrade unsupported citations to review for safe calibration."""
    labels = [dict(label) for label in result["labels"]]
    expected = {j["id"]: j for j in jobs}
    if len(labels) != len(jobs) or {x["id"] for x in labels} != set(expected):
        raise ValueError("Classification IDs do not match the batch")
    for label in labels:
        job = expected[label["id"]]
        if label["decision"] not in ("keep", "exclude", "review") or label["confidence"] not in ("high", "medium", "low"):
            raise ValueError("Invalid decision or confidence")
        label["model_decision"] = label["decision"]
        label["evidence_valid"] = bool(label["evidence"]) and label["evidence"] in (job.get("title", "") + "\n" + (job.get("description") or ""))
        if not label["evidence_valid"]:
            label["decision"] = "review"
            label["confidence"] = "low"
            label["validation_note"] = "Citazione non letterale: decisione non utilizzabile per un hard filter."
    return labels


def label_batches(config, directory, only_batch=None):
    """Classify via authenticated Codex CLI; persist usage and never silently switch models."""
    jobs = json.loads((directory / "sample.json").read_text(encoding="utf-8"))
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI is required")
    schema = {"type": "object", "additionalProperties": False, "required": ["labels"], "properties": {"labels": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "decision", "reason_code", "evidence", "rationale", "confidence"], "properties": {key: {"type": "string"} for key in ["id", "decision", "reason_code", "evidence", "rationale", "confidence"]}}}}}
    schema_path = directory / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    prompt = (directory / "prompt.txt").read_text(encoding="utf-8") + "\nPORTFOLIO EVIDENCE:\n" + (directory / "portfolio.md").read_text(encoding="utf-8")
    for start in range(0, len(jobs), config["batch_size"]):
        index = start // config["batch_size"]
        if only_batch is not None and index != only_batch:
            continue
        batch = jobs[start:start + config["batch_size"]]
        output = directory / f"labels-{index:02}.json"
        if output.exists():
            labels = validate(json.loads(output.read_text(encoding="utf-8")), batch)
            (directory / f"validated-{index:02}.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        payload = [{k: j.get(k) for k in ("id", "title", "description", "seniority", "employment_type")} for j in batch]
        text = prompt + "\nJOBS:\n" + json.dumps(payload, ensure_ascii=False)
        (directory / f"input-{index:02}.txt").write_text(text, encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="jobhunter-classify-") as cwd, (directory / f"events-{index:02}.jsonl").open("w", encoding="utf-8") as log, (directory / f"stderr-{index:02}.log").open("w", encoding="utf-8") as err:
            command = [executable, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--model", config["model"], "-c", f'model_reasoning_effort="{config["reasoning_effort"]}"', "--json", "--output-schema", str(schema_path), "--output-last-message", str(output), "-"]
            result = subprocess.run(command, input=text, text=True, encoding="utf-8", cwd=cwd, stdout=log, stderr=err, timeout=config["timeout_seconds"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not output.exists():
            raise ValueError(f"Batch {index} failed; inspect saved events and stderr")
        labels = validate(json.loads(output.read_text(encoding="utf-8")), batch)
        (directory / f"validated-{index:02}.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Batch %s: %s", index, dict(Counter(x["decision"] for x in labels)))


def summarize(config, directory):
    """Reevaluate the entire archive and compare frozen baseline, model labels and held-out rows."""
    sample = json.loads((directory / "sample.json").read_text(encoding="utf-8"))
    sample_ids = {j["id"] for j in sample}
    training_ids = {j["id"] for j in sample[:config["training_size"]]}
    baseline = json.loads((directory / "baseline.json").read_text(encoding="utf-8"))
    before = {j["id"]: j for j in baseline}
    labels = [x for i in range((len(sample) + config["batch_size"] - 1) // config["batch_size"]) for x in json.loads((directory / f"validated-{i:02}.json").read_text(encoding="utf-8"))]
    if len(labels) != len(sample) or {x["id"] for x in labels} != sample_ids:
        raise ValueError("Cannot report an incomplete calibration")
    rules = filters()
    rows = []
    with sqlite3.connect(ROOT / settings()["database"]) as db:
        for oid, cid, name, data in db.execute("SELECT o.id,o.company_id,c.name,o.data FROM opportunities o JOIN companies c ON c.id=o.company_id ORDER BY o.id"):
            job = json.loads(data)
            decision = evaluate(job, rules)
            rows.append({"id": oid, "company_id": cid, "company": name, "title": job["title"], "before": before[oid]["status"], "after": decision["status"], "reasons": decision["reasons"], "has_description": bool(job.get("description")), "split": "training" if oid in training_ids else "validation" if oid in sample_ids else "remaining"})
    after = {r["id"]: r for r in rows}
    usage = Counter()
    calls = 0
    tool_calls = 0
    for path in directory.rglob("events-*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("type") == "turn.completed":
                calls += 1
                usage.update(event.get("usage", {}))
            if event.get("type") == "item.completed" and event.get("item", {}).get("type") in ("command_execution", "mcp_tool_call", "web_search"):
                tool_calls += 1
    comparisons = {}
    for split, ids in (("training", training_ids), ("validation", sample_ids - training_ids)):
        selected = [x for x in labels if x["id"] in ids]
        comparisons[split] = {"jobs": len(selected), "matrix": dict(Counter(x["decision"] + " -> " + after[x["id"]]["after"] for x in selected)), "hard_excluded_model_keep": sum(x["decision"] == "keep" and after[x["id"]]["after"] == "excluded" for x in selected)}
    groups = {}
    for split, selected in (("all", rows), ("sample", [r for r in rows if r["id"] in sample_ids]), ("remaining", [r for r in rows if r["id"] not in sample_ids])):
        groups[split] = {"jobs": len(selected), "before": dict(Counter(r["before"] for r in selected)), "after": dict(Counter(r["after"] for r in selected)), "companies_before_potential": len({r["company_id"] for r in selected if r["before"] == "potential"}), "companies_after_potential": len({r["company_id"] for r in selected if r["after"] == "potential"})}
    metrics = {"model": config["model"], "effort": config["reasoning_effort"], "created_at": now(), "groups": groups, "transitions": dict(Counter(r["before"] + " -> " + r["after"] for r in rows)), "new_exclusion_reasons": dict(Counter(reason for r in rows if r["before"] != "excluded" and r["after"] == "excluded" for reason in r["reasons"])), "sample": {"model_decisions": dict(Counter(x["model_decision"] for x in labels)), "validated_decisions": dict(Counter(x["decision"] for x in labels)), "invalid_citations": sum(not x["evidence_valid"] for x in labels), "missing_descriptions": sum(not j.get("description") for j in sample)}, "comparison_with_model_not_ground_truth": comparisons, "usage": {**dict(usage), "total_tokens": usage["input_tokens"] + usage["output_tokens"], "completed_model_calls": calls, "model_tool_calls": tool_calls}, "remaining_potential_missing_description": sum(r["after"] == "potential" and not r["has_description"] for r in rows)}
    (directory / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    with (directory / "archive-evaluation.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            values = {k: " | ".join(v) if isinstance(v, list) else v for k, v in row.items()}
            writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for k, v in values.items()})
    (directory / "sample-decisions.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Calibration metrics: %s", directory / "metrics.json")


def main():
    """Run an explicit calibration phase without changing job records or preferences."""
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "label", "report"))
    parser.add_argument("--batch", type=int)
    args = parser.parse_args()
    config = json.loads((ROOT / "config/calibration.json").read_text())
    directory = ROOT / config["output_directory"]
    if args.phase == "prepare":
        prepare(config, directory)
    elif args.phase == "label":
        label_batches(config, directory, args.batch)
    else:
        summarize(config, directory)


if __name__ == "__main__":
    main()
