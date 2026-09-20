"""Group uncertain roles for chat and apply explicit user preferences as reusable local rules."""

from collections import Counter
import json
import logging
import re
from jobhunter.evaluation import tier
from jobhunter.evaluation.selection import evaluate, filters, verdicts
from jobhunter.workspace import ROOT, identity, now

logger = logging.getLogger(__name__)


def questions(archive, limit=None):
    """Propose high-coverage questions on both axes, without deciding for the user (DESIGN §3, §6)."""
    cfg = json.loads((ROOT / "config/review_questions.json").read_text(encoding="utf-8"))
    assessment = verdicts(archive)
    preferred = filters().get("preferred_categories", [])
    undecided = []
    evidence_groups = {}
    excluded_companies = {row[0] for row in archive.db.execute("SELECT f.company_id FROM feedback f LEFT JOIN feedback_detail d ON d.event_id=f.id WHERE f.undone_at IS NULL AND f.opportunity_id IS NULL AND f.status IN ('discarded','contacted') AND COALESCE(d.reason,'other') NOT IN ('no_current_roles','not_now') AND f.id=(SELECT max(x.id) FROM feedback x WHERE x.company_id=f.company_id AND x.opportunity_id IS NULL AND x.undone_at IS NULL)")}
    already_answered = 0
    sectors = {}
    for row in archive.db.execute("SELECT o.id,o.company_id,o.data,c.name FROM opportunities o JOIN companies c ON c.id=o.company_id ORDER BY o.id"):
        if row["company_id"] in excluded_companies:
            continue
        job = json.loads(row["data"])
        state = assessment[row["company_id"]]
        verdict = state["ruoli"].get(row["id"], {})
        if verdict.get("verdetto") != tier.DROP:
            missing = "missing_description" if not job.get("description") else "job_evidence" if any(str(r).startswith("user_rule_needs_evidence") for r in verdict.get("prove", [])) else None
            if missing:
                group = evidence_groups.setdefault(missing, {"id": missing, "kind": "source_evidence", "opportunities": 0, "company_ids": set(), "examples": []})
                group["opportunities"] += 1
                if row["company_id"] not in group["company_ids"] and len(group["examples"]) < cfg["examples_per_group"]:
                    group["examples"].append({"id": row["id"], "company_id": row["company_id"], "company": row["name"], "title": job["title"], "source_url": job["source_url"]})
                group["company_ids"].add(row["company_id"])
        # Un «non so» conta come domanda solo se risolverlo può spostare un tier: Tier A, o un ruolo che vale da solo.
        if verdict.get("verdetto") == tier.UNKNOWN and (state["azienda"]["verdetto"] == tier.INTERESTING or verdict.get("primary")):
            if any(str(reason).startswith("user_rule") for reason in verdict.get("prove", [])):
                already_answered += 1
                continue
            undecided.append({"id": row["id"], "company_id": row["company_id"], "company": row["name"], **job})
        if verdict.get("verdetto") == tier.KEEP and state["azienda"]["verdetto"] == tier.NOT_INTERESTING:
            # Una categoria sposta centinaia di aziende, un record ne sposta uno: la domanda va fatta sul pattern.
            sector = sectors.setdefault(state["azienda"]["categoria"], {"id": "category:" + state["azienda"]["categoria"], "kind": "company_axis",
                                                                       "title": "Categoria non fra le preferite: " + state["azienda"]["categoria"],
                                                                       "question": f"«{state['azienda']['categoria']}» non è fra preferred_categories, ma qui ci sono ruoli compatibili. La categoria ti interessa?",
                                                                       "opportunities": 0, "company_ids": set(), "examples": []})
            sector["opportunities"] += 1
            if row["company_id"] not in sector["company_ids"] and len(sector["examples"]) < cfg["examples_per_group"]:
                sector["examples"].append({"id": row["id"], "company_id": row["company_id"], "company": row["name"], "title": job["title"], "source_url": job["source_url"]})
            sector["company_ids"].add(row["company_id"])
    groups = []
    covered = set()
    for group in cfg["groups"]:
        matches = [j for j in undecided if re.search(group["pattern"], j["title"], re.I)]
        if not matches:
            continue
        covered.update(j["id"] for j in matches)
        examples = []
        seen = set()
        for job in matches:
            if job["title"] not in seen:
                examples.append({k: job[k] for k in ("id", "company_id", "company", "title", "source_url")})
                seen.add(job["title"])
            if len(examples) >= cfg["examples_per_group"]:
                break
        groups.append({**group, "kind": "role_axis", "opportunities": len(matches), "companies": len({j["company_id"] for j in matches}), "missing_description": sum(not j.get("description") for j in matches), "examples": examples})
    for sector in sectors.values():
        sector["companies"] = len(sector.pop("company_ids"))
        groups.append(sector)
    groups.sort(key=lambda g: (-g["companies"], -g["opportunities"], g["id"]))
    evidence = []
    for item in evidence_groups.values():
        item["companies"] = len(item.pop("company_ids"))
        item["title"] = "Descrizioni da recuperare" if item["id"] == "missing_description" else "Preferenza già chiarita, manca la prova nell'annuncio"
        item["question"] = "Verificare le fonti dei singoli ruoli. Non generalizzare un requisito a tutti gli annunci del gruppo."
        evidence.append(item)
    counts = Counter(state["tier"] for state in assessment.values())
    blind = counts["evidenza-mancante"]
    if blind:
        evidence.append({"id": "company_evidence", "kind": "source_evidence", "title": "Aziende senza evidenza propria",
                         "question": "Coda di lavoro, non un rifiuto: senza descrizione aziendale l'asse azienda non è valutabile.",
                         "opportunities": 0, "companies": blind, "examples": []})
    return {"undecided_opportunities": len(undecided) + already_answered, "tier_counts": dict(counts),
            "preferred_categories": preferred,
            "already_answered_needing_job_review_or_evidence": already_answered,
            "grouped_opportunities": len(covered), "ungrouped_opportunities": len(undecided) - len(covered),
            "evidence_tasks": evidence, "questions": groups[:limit or cfg["default_limit"]],
            "note": "Groups can overlap. Ask for a preference, then preview a rule. No decisions have been saved."}


def review_rule(archive, definition, apply=False):
    """Preview or persist a user-authored rule; explicit evidence conditions keep missing facts in review."""
    required = ("id", "pattern", "action", "note")
    if any(not isinstance(definition.get(k), str) or not definition[k].strip() for k in required):
        raise ValueError("Rule requires id, pattern, action and the user's preference as note")
    if definition["action"] not in ("include", "exclude", "review"):
        raise ValueError("Rule action must be include, exclude or review")
    if "enabled" in definition and not isinstance(definition["enabled"], bool):
        raise ValueError("enabled must be boolean")
    for key in ("pattern", "evidence_pattern"):
        if definition.get(key):
            try:
                re.compile(definition[key])
            except re.error as exc:
                raise ValueError(f"Invalid {key}: {exc}") from exc
    path = ROOT / "config/role_filters.json"
    old = filters()
    rule = {k: definition[k] for k in (*required, "evidence_pattern", "enabled") if k in definition}
    new = {**old, "user_title_rules": [r for r in old.get("user_title_rules", []) if r["id"] != rule["id"]] + [rule]}
    changes = []
    for row in archive.db.execute("SELECT id,company_id,data FROM opportunities ORDER BY id"):
        job = json.loads(row["data"])
        before, after = evaluate(job, old), evaluate(job, new)
        if before["status"] != after["status"]:
            changes.append({"id": row["id"], "company_id": row["company_id"], "title": job["title"], "before": before["status"], "after": after["status"]})
    result = {"rule": rule, "changed_opportunities": len(changes), "changed_companies": len({x["company_id"] for x in changes}), "transitions": dict(Counter(x["before"] + " -> " + x["after"] for x in changes)), "examples": changes[:10], "applied": apply, "note": "Rule changes eligibility for current and future roles; it does not mark companies interesting/discarded or bypass existing hard exclusions."}
    if apply:
        directory = ROOT / "data/profile-rules"
        directory.mkdir(parents=True, exist_ok=True)
        audit = directory / (now().replace(":", "").replace("+", "_") + "-" + identity(rule["id"]) + ".json")
        audit.write_text(json.dumps({"before": old, "after": new, "changes": changes, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        archive.run("profile_rule", "success", result)
        logger.info("Applied user rule %s: %s roles changed", rule["id"], len(changes))
    return result
