"""Persistent, token-bounded Codex conversations over JobHunter decisions."""

import json
import logging
import re
from pathlib import Path

from jobhunter.evaluation import tier
from jobhunter.evaluation import system_one
from jobhunter.evaluation.remote_llm import digest
from jobhunter.evaluation.selection import verdicts
from jobhunter.workspace import ROOT, now

MODES = ("indecisi", "selezione")
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


def _undecided(archive):
    """Return current Jev review IDs with usable evidence, excluding data-blocked jobs."""
    cfg = system_one.config()
    result = []
    for row in archive.db.execute("SELECT id,content_hash,data FROM opportunities ORDER BY id"):
        job = json.loads(row["data"])
        if not job.get("description"):
            continue
        saved = system_one.current_result(archive, "selection", row["id"], cfg)
        if saved and saved.get("decision") == "review":
            result.append({"id": row["id"], "hash": row["content_hash"]})
    return result


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
    Tier A/B company population. No feedback, model call or active pipeline rule is changed.
    """
    if mode not in MODES:
        raise ValueError("Unknown Codex conversation mode")
    if type(batch_size) is not int or not 1 <= batch_size <= 20:
        raise ValueError("Batch size must be between 1 and 20")
    scope = dict(scope or {})
    records = _undecided(archive) if mode == "indecisi" else _selection(archive, scope)
    builder = _undecided_item if mode == "indecisi" else _selection_item
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


def _criteria(mode):
    """Fingerprint the decision inputs needed to explain a frozen session later."""
    values = {
        "tier_code": digest(Path(tier.__file__).read_text(encoding="utf-8")),
        "role_filters": digest((ROOT / "config/role_filters.json").read_text(encoding="utf-8")),
    }
    if mode == "indecisi":
        values["jev_selection"] = system_one.signature(system_one.config(), "selection")
    return values


def _source_snapshot(archive, mode, item_id, card):
    """Freeze original text locally without exposing it in the compact chat batch."""
    if mode == "indecisi":
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


def view(archive, session_id, session_root=None):
    """Return the current compact batch without advancing or rereading reviewed records."""
    _, manifest = _load(session_id, session_root)
    reviewed = set(manifest.get("reviewed_ids", []))
    remaining = [item["id"] for item in manifest["population"] if item["id"] not in reviewed]
    ids = remaining[:manifest["batch_size"]]
    records = {item["id"]: item for item in manifest["population"]}
    builder = _undecided_item if manifest["mode"] == "indecisi" else _selection_item
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
    if manifest["mode"] == "indecisi":
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


def record(session_id, event, session_root=None, memory_root=None):
    """Record progress and explicitly confirmed Markdown memories without changing pipeline rules."""
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

    manifest["reviewed_ids"] = sorted(set(manifest.get("reviewed_ids", [])) | set(reviewed))
    manifest["updated_at"] = now()
    if len(manifest["reviewed_ids"]) == len(population):
        manifest["status"] = "complete"
    _write_json(directory / "manifest.json", manifest)
    remaining = len(population) - len(manifest["reviewed_ids"])
    logger.info("Recorded Codex session %s: %d reviewed, %d remaining", session_id, len(manifest["reviewed_ids"]), remaining)
    return {"id": session_id, "status": manifest["status"], "reviewed": len(manifest["reviewed_ids"]), "remaining": remaining}
