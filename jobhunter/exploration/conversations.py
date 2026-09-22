"""Persistent, token-bounded Codex conversations over JobHunter decisions."""

import json
import logging
import re
from pathlib import Path

from jobhunter.evaluation import review, tier
from jobhunter.evaluation import system_one
from jobhunter.evaluation.remote_llm import digest
from jobhunter.evaluation.selection import verdicts
from jobhunter.workspace import ROOT, now

MODES = ("indecisi", "selezione", "regole")
# Modalita' che lavorano sul singolo annuncio invece che sull'azienda.
ROLE_MODES = ("indecisi", "regole")
SELECTION_TIERS = ("A", "B-attesa", "B-esperienza")
SESSION_ROOT = ROOT / "data/codex-sessions"
MEMORY_ROOT = ROOT / "user_context/selection"
SESSION_ID = re.compile(r"^[0-9TZ._-]+-[0-9a-f]{8}$")
logger = logging.getLogger(__name__)


def _compact(value, limit=700):
    """Keep chat cards bounded even when a saved summary or company description is verbose."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _write_json(path, value):
    """Atomically replace one session JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _directory(session_id, session_root=None):
    """Resolve a validated session ID below the configured session root."""
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        raise ValueError("Invalid Codex session ID")
    return Path(session_root or SESSION_ROOT) / session_id


def _load(session_id, session_root=None):
    """Read an existing session manifest without accepting arbitrary paths."""
    directory = _directory(session_id, session_root)
    path = directory / "manifest.json"
    if not path.is_file():
        raise ValueError("Codex session not found")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("id") != session_id or manifest.get("mode") not in MODES:
        raise ValueError("Invalid Codex session manifest")
    return directory, manifest


def _reviewed(archive):
    """Roles already decided above the cascade, by the user or by a current agent verdict."""
    return set(review.user_judgements(archive)) | set(review.agent_judgements(archive))


def _undecided(archive):
    """Return current Jev review IDs with usable evidence, excluding data-blocked jobs.

    Un annuncio che hai gia' deciso tu, o che l'agente ha deciso con le regole in vigore, non
    rientra in una nuova sessione: prima del 22 settembre 2026 tornava a ogni avvio.
    """
    cfg = system_one.config()
    decided = _reviewed(archive)
    result = []
    for row in archive.db.execute("SELECT id,content_hash,data FROM opportunities ORDER BY id"):
        job = json.loads(row["data"])
        if not job.get("description") or row["id"] in decided:
            continue
        saved = system_one.current_result(archive, "selection", row["id"], cfg)
        if saved and saved.get("decision") == "review":
            result.append({"id": row["id"], "hash": row["content_hash"]})
    return result


def _to_apply(archive):
    """Roles the agent may judge with the confirmed rules, not yet decided above the cascade.

    Due gruppi, scelti dall'utente il 22 settembre 2026: gli indecisi di Jev con mansioni
    leggibili, e i ruoli compatibili delle aziende di Tier A e B-esperienza. Il primo gruppo puo'
    salire, il secondo puo' scendere; il tier si ricalcola da solo in lettura.
    """
    if not review.rules():
        raise ValueError("Nessuna regola attiva in user_context/selection/regole.md: confermane "
                         "almeno una in una sessione prima di applicarle")
    decided = _reviewed(archive)
    hashes = {row["id"]: row["content_hash"] for row in archive.db.execute("SELECT id,content_hash FROM opportunities")}
    result = []
    for state in verdicts(archive).values():
        for oid, role in state["ruoli"].items():
            if oid in decided:
                continue
            jev = next((j for j in role["catena"] if j["giudice"] == "jev"), None)
            compatible = role["verdetto"] == tier.KEEP and state["tier"] in ("A", "B-esperienza")
            undecided = role["verdetto"] == tier.UNKNOWN and jev is not None
            if compatible or undecided:
                result.append({"id": oid, "hash": hashes[oid]})
    return sorted(result, key=lambda item: item["id"])


def _selection(archive, scope):
    """Resolve a filtered Tier A/B company population using the dashboard's search contract."""
    tiers = scope.get("tiers") or list(SELECTION_TIERS)
    if not isinstance(tiers, list) or not tiers or set(tiers) - set(SELECTION_TIERS):
        raise ValueError("Selection sessions accept only Tier A and B")
    for field in ("query", "country", "city", "category"):
        if not isinstance(scope.get(field, ""), str):
            raise ValueError(f"{field} must be text")
    ids = []
    for selected_tier in SELECTION_TIERS:
        if selected_tier not in tiers:
            continue
        offset = 0
        while True:
            page = archive.search(
                query=scope.get("query", ""), country=scope.get("country", ""),
                city=scope.get("city", ""), category=scope.get("category", ""),
                tier=selected_tier, sort="recenti", limit=500, offset=offset,
            )
            ids.extend(item["id"] for item in page["items"])
            offset += len(page["items"])
            if not page["items"] or offset >= page["total"]:
                break
    unique = list(dict.fromkeys(ids))
    return [{"id": cid, "hash": archive.basis(cid)} for cid in unique]


def start(archive, mode, scope=None, batch_size=5, session_root=None):
    """Create a reproducible conversation session and return its first compact batch.

    `indecisi` freezes current Jev review results with descriptions. `selezione` freezes a filtered
    Tier A/B company population. `regole` freezes the roles the agent may judge with the confirmed
    rules (`_to_apply`). No feedback, model call or active pipeline rule is changed.
    """
    if mode not in MODES:
        raise ValueError("Unknown Codex conversation mode")
    if type(batch_size) is not int or not 1 <= batch_size <= 20:
        raise ValueError("Batch size must be between 1 and 20")
    scope = dict(scope or {})
    records = ({"indecisi": _undecided, "regole": _to_apply}[mode](archive) if mode in ROLE_MODES
               else _selection(archive, scope))
    builder = BUILDERS[mode]
    for record in records:
        record["snapshot"] = builder(archive, record["id"])
        record["source_snapshot"] = _source_snapshot(archive, mode, record["id"], record["snapshot"])
    created = now()
    session_id = created.replace(":", "").replace("+", "_") + "-" + digest([mode, scope, created])[:8]
    directory = _directory(session_id, session_root)
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "id": session_id,
        "mode": mode,
        "status": "active",
        "created_at": created,
        "updated_at": created,
        "scope": scope,
        "criteria": _criteria(mode),
        "batch_size": batch_size,
        "population": records,
        "population_hash": digest(records),
        "reviewed_ids": [],
    }
    _write_json(directory / "manifest.json", manifest)
    (directory / "notes.md").write_text(
        f"# Sessione Codex {session_id}\n\nModalità: {mode}. Le note qui non modificano la pipeline.\n",
        encoding="utf-8",
    )
    logger.info("Created Codex %s session %s with %d records", mode, session_id, len(records))
    return view(archive, session_id, session_root)


def _undecided_item(archive, oid):
    """Build a compact undecided-role card from the current Jev result."""
    row = archive.db.execute(
        "SELECT o.id,o.company_id,o.data,c.name FROM opportunities o JOIN companies c ON c.id=o.company_id WHERE o.id=?",
        (oid,),
    ).fetchone()
    if row is None:
        return {"id": oid, "missing": True}
    job = json.loads(row["data"])
    judgement = system_one.current_result(archive, "selection", oid, system_one.config()) or {}
    return {
        "id": oid,
        "company_id": row["company_id"],
        "company": row["name"],
        "title": job.get("title", ""),
        "locations": job.get("locations", []),
        "source_url": job.get("source_url", ""),
        "jev": {key: judgement.get(key) for key in ("decision", "rationale", "evidence", "missing_information")},
    }


def _rule_item(archive, oid):
    """Undecided-role card plus the tier and current verdict the rules would change."""
    card = _undecided_item(archive, oid)
    if card.get("missing"):
        return card
    state = verdicts(archive, card["company_id"])[card["company_id"]]
    card["tier"] = state["tier"]
    card["verdetto"] = state["ruoli"][oid]["verdetto"]
    return card


def _criteria(mode):
    """Fingerprint the decision inputs needed to explain a frozen session later."""
    values = {
        "tier_code": digest(Path(tier.__file__).read_text(encoding="utf-8")),
        "role_filters": digest((ROOT / "config/role_filters.json").read_text(encoding="utf-8")),
    }
    if mode in ROLE_MODES:
        values["jev_selection"] = system_one.signature(system_one.config(), "selection")
    if mode == "regole":
        values["rules"] = {rule_id: rule["hash"] for rule_id, rule in review.rules().items()}
    return values


def _source_snapshot(archive, mode, item_id, card):
    """Freeze original text locally without exposing it in the compact chat batch."""
    if mode in ROLE_MODES:
        row = archive.db.execute("SELECT company_id,data FROM opportunities WHERE id=?", (item_id,)).fetchone()
        job = json.loads(row["data"])
        return {"id": item_id, "company_id": row["company_id"], "title": job.get("title", ""),
                "source_url": job.get("source_url", ""), "description": job.get("description", "")}
    company = archive.db.execute(
        "SELECT id,name,website,description FROM companies WHERE id=?", (item_id,),
    ).fetchone()
    sources = {"company": dict(company), "opportunities": {}}
    for role in card.get("roles", []):
        row = archive.db.execute("SELECT data FROM opportunities WHERE id=? AND company_id=?", (role["id"], item_id)).fetchone()
        if row is None:
            continue
        job = json.loads(row["data"])
        sources["opportunities"][role["id"]] = {
            "id": role["id"], "company_id": item_id, "title": job.get("title", ""),
            "source_url": job.get("source_url", ""), "description": job.get("description", ""),
        }
    return sources


def _selection_item(archive, cid):
    """Build one compact Tier A/B company card, preferring validated Qwen summaries."""
    company = archive.show(cid)
    state = verdicts(archive, cid)[cid]
    roles = []
    for job in company["opportunities"]:
        role = state["ruoli"].get(job["id"], {})
        if role.get("verdetto") != tier.KEEP:
            continue
        summary = _compact((job.get("remote_summary") or {}).get("summary"))
        roles.append({
            "id": job["id"], "title": job.get("title", ""), "locations": job.get("locations", []),
            "application_url": job.get("application_url", ""), "summary": summary,
        })
    company_summary = _compact((company.get("remote_summary") or {}).get("summary") or company.get("description", ""))
    return {
        "id": cid,
        "name": company["name"],
        "website": company.get("website", ""),
        "tier": state["tier"],
        "categories": state["azienda"].get("categorie", []),
        "summary": company_summary,
        "roles": roles,
    }


BUILDERS = {"indecisi": _undecided_item, "regole": _rule_item, "selezione": _selection_item}


def view(archive, session_id, session_root=None):
    """Return the current compact batch without advancing or rereading reviewed records."""
    _, manifest = _load(session_id, session_root)
    reviewed = set(manifest.get("reviewed_ids", []))
    remaining = [item["id"] for item in manifest["population"] if item["id"] not in reviewed]
    ids = remaining[:manifest["batch_size"]]
    records = {item["id"]: item for item in manifest["population"]}
    builder = BUILDERS[manifest["mode"]]
    return {
        "id": session_id,
        "mode": manifest["mode"],
        "status": "complete" if not remaining else manifest.get("status", "active"),
        "scope": manifest["scope"],
        "population": len(manifest["population"]),
        "reviewed": len(reviewed),
        "remaining": len(remaining),
        "batch_size": manifest["batch_size"],
        "items": [records[item_id].get("snapshot") or builder(archive, item_id) for item_id in ids],
        "note": "Use expand only for an item that needs its original source text.",
    }


def expand(archive, session_id, item_id, opportunity_id=None, session_root=None):
    """Return original text for one item already frozen into a session."""
    _, manifest = _load(session_id, session_root)
    records = {item["id"]: item for item in manifest["population"]}
    if item_id not in records:
        raise ValueError("Item is outside this Codex session")
    frozen = records[item_id].get("source_snapshot")
    if manifest["mode"] in ROLE_MODES:
        if frozen:
            return frozen
        row = archive.db.execute("SELECT company_id,data FROM opportunities WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise ValueError("Opportunity no longer exists")
        job = json.loads(row["data"])
        return {"id": item_id, "company_id": row["company_id"], "title": job.get("title", ""),
                "source_url": job.get("source_url", ""), "description": job.get("description", "")}
    if frozen:
        if opportunity_id:
            if opportunity_id not in frozen["opportunities"]:
                raise ValueError("Opportunity is outside this frozen company selection")
            return frozen["opportunities"][opportunity_id]
        return frozen["company"]
    company = archive.db.execute("SELECT id,name,website,description FROM companies WHERE id=?", (item_id,)).fetchone()
    if company is None:
        raise ValueError("Company no longer exists")
    if opportunity_id:
        row = archive.db.execute("SELECT data FROM opportunities WHERE id=? AND company_id=?", (opportunity_id, item_id)).fetchone()
        if row is None:
            raise ValueError("Opportunity is outside this company")
        job = json.loads(row["data"])
        return {"id": opportunity_id, "company_id": item_id, "title": job.get("title", ""),
                "source_url": job.get("source_url", ""), "description": job.get("description", "")}
    return dict(company)


def _append_once(path, marker, text):
    """Append one Markdown memory entry once, identified by a stable marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else "# Memoria di selezione\n"
    if marker in current:
        return
    path.write_text(current.rstrip() + "\n\n" + text.rstrip() + "\n", encoding="utf-8")


def record(session_id, event, session_root=None, memory_root=None, archive=None):
    """Record progress, confirmed memories, confirmed rules and agent verdicts.

    Tutto viene validato prima di scrivere qualsiasi cosa, cosi' un evento sbagliato non lascia
    meta' delle sue righe. `rules` sono regole che l'utente ha confermato a parole in chat: da
    quel momento l'agente puo' citarle. `verdicts` sono i giudizi dell'agente e si accettano solo
    in una sessione `regole`, solo su annunci della sessione che l'utente non ha gia' deciso, e
    solo citando regole attive o confermate nello stesso evento.
    """
    directory, manifest = _load(session_id, session_root)
    if not isinstance(event, dict) or not isinstance(event.get("event_id"), str) or not event["event_id"].strip():
        raise ValueError("Session event requires event_id")
    population = {item["id"] for item in manifest["population"]}
    reviewed = event.get("reviewed_ids", [])
    if not isinstance(reviewed, list) or any(not isinstance(value, str) for value in reviewed) or set(reviewed) - population:
        raise ValueError("reviewed_ids must belong to the session")
    notes = event.get("notes", [])
    memories = event.get("memories", [])
    if not isinstance(notes, list) or not isinstance(memories, list):
        raise ValueError("notes and memories must be arrays")
    for note in notes:
        if not isinstance(note, dict) or note.get("kind") not in ("proposal", "consideration") or not isinstance(note.get("text"), str) or not note["text"].strip():
            raise ValueError("Invalid session note")
        if not isinstance(note.get("item_ids", []), list) or any(not isinstance(value, str) for value in note.get("item_ids", [])) or set(note.get("item_ids", [])) - population:
            raise ValueError("Session note references an item outside the session")
    for memory in memories:
        if not isinstance(memory, dict) or memory.get("kind") not in ("preference", "consideration") or not isinstance(memory.get("id"), str) or not isinstance(memory.get("text"), str) or not memory["id"].strip() or not memory["text"].strip():
            raise ValueError("Invalid confirmed memory")
    rules_path = Path(memory_root or MEMORY_ROOT) / "regole.md"
    new_rules = event.get("rules", [])
    judged = event.get("verdicts", [])
    if not isinstance(new_rules, list) or not isinstance(judged, list):
        raise ValueError("rules and verdicts must be arrays")
    active = review.rules(rules_path)
    for rule in new_rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not review.RULE_ID.fullmatch(rule["id"])
                or not isinstance(rule.get("text"), str) or not rule["text"].strip()):
            raise ValueError("Invalid confirmed rule: id like R1 and non-empty text")
        if rule["id"] in active and active[rule["id"]]["text"] != rule["text"].strip():
            raise ValueError(f"Rule {rule['id']} already exists with a different text: use a new ID")
    if judged:
        if manifest["mode"] != "regole":
            raise ValueError("Agent verdicts are recorded only in a regole session")
        if archive is None:
            raise ValueError("Recording agent verdicts needs the archive")
        citable = set(active) | {rule["id"] for rule in new_rules}
        decided = review.user_judgements(archive, [v.get("opportunity_id") for v in judged if isinstance(v, dict)])
        for verdict in judged:
            if (not isinstance(verdict, dict) or verdict.get("opportunity_id") not in population
                    or verdict.get("decision") not in review.AGENT_DECISIONS
                    or not isinstance(verdict.get("rule_ids"), list) or not verdict["rule_ids"]
                    or set(verdict["rule_ids"]) - citable
                    or not isinstance(verdict.get("rationale"), str) or not verdict["rationale"].strip()):
                raise ValueError("Invalid agent verdict: session role, keep/exclude, active rule_ids, rationale")
            if verdict["opportunity_id"] in decided:
                raise ValueError(f"{verdict['opportunity_id']} was decided by the user; the agent cannot override it")

    events_path = directory / "events.jsonl"
    previous = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()] if events_path.exists() else []
    if event["event_id"] not in {item["event_id"] for item in previous}:
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({**event, "recorded_at": now()}, ensure_ascii=False) + "\n")
        if notes:
            with (directory / "notes.md").open("a", encoding="utf-8") as stream:
                for note in notes:
                    stream.write(f"\n## {note['kind']}\n\n{note['text'].strip()}\n")
        target = Path(memory_root or MEMORY_ROOT)
        for memory in memories:
            marker = f"<!-- memory:{memory['id']} -->"
            title = "Preferenza confermata" if memory["kind"] == "preference" else "Considerazione confermata"
            block = f"{marker}\n## {title}\n\n{memory['text'].strip()}\n\nOrigine: sessione `{session_id}`."
            path = target / "preferences.md" if memory["kind"] == "preference" else target / "notes" / f"{session_id}.md"
            _append_once(path, marker, block)
        for rule in new_rules:
            review.add_rule(rule["id"], rule["text"], f"sessione `{session_id}`", rules_path)
        active = review.rules(rules_path)
        for verdict in judged:
            review.save_agent_verdict(archive, verdict["opportunity_id"], verdict["decision"], verdict["rule_ids"],
                                      verdict["rationale"], session_id, active)
        reviewed = sorted(set(reviewed) | {verdict["opportunity_id"] for verdict in judged})

    manifest["reviewed_ids"] = sorted(set(manifest.get("reviewed_ids", [])) | set(reviewed))
    manifest["updated_at"] = now()
    if len(manifest["reviewed_ids"]) == len(population):
        manifest["status"] = "complete"
    _write_json(directory / "manifest.json", manifest)
    remaining = len(population) - len(manifest["reviewed_ids"])
    logger.info("Recorded Codex session %s: %d reviewed, %d remaining", session_id, len(manifest["reviewed_ids"]), remaining)
    return {"id": session_id, "status": manifest["status"], "reviewed": len(manifest["reviewed_ids"]), "remaining": remaining}
