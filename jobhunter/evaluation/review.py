"""Livello di revisione: i due giudici che stanno sopra la pipeline automatica.

La cascata automatica (regex -> Jev) si ferma al primo giudice che decide. Sopra di lei stanno due
giudici che la *sovrascrivono*, in quest'ordine di forza:

1. **utente** — l'ultima decisione non annullata sul singolo annuncio in `feedback`:
   `saved` vale «tieni», `discarded` vale «scarta». Gli altri stati sono informativi.
2. **agente** — un verdetto registrato in chat da Codex o Claude, in `enrichments` con task
   `agent:selection`. Deve citare almeno una regola attiva di `user_context/selection/regole.md`,
   cioe' una regola che l'utente ha confermato. Senza regola l'agente non decide.

Nessuno dei due cancella i verdetti precedenti: la catena li mostra tutti e cambia solo quale conta
alla fine (scelta dell'utente, 22 settembre 2026). Un verdetto dell'agente conta finche' il testo
dell'annuncio e le regole che cita restano identici; se una delle due cose cambia smette di contare
e il ruolo torna nella coda della sessione `regole`. Una decisione dell'utente non scade mai.
"""

import json
import logging
import re
from pathlib import Path

from jobhunter.evaluation import tier
from jobhunter.evaluation.remote_llm import digest
from jobhunter.workspace import ROOT, now

logger = logging.getLogger(__name__)
RULES_PATH = ROOT / "user_context/selection/regole.md"
AGENT_TASK = "agent:selection"
RULE_ID = re.compile(r"^R\d+$")
RULE_MARKER = re.compile(r"<!-- rule:(R\d+) -->")
USER_VERDICTS = {"saved": tier.KEEP, "discarded": tier.DROP}
AGENT_DECISIONS = {"keep": tier.KEEP, "exclude": tier.DROP}


def rules(path=None):
    """Read the confirmed rules as {id: {"text", "hash"}}; a missing file means no rule.

    Ogni regola e' un blocco che inizia con `<!-- rule:R3 -->`. Testo e impronta ignorano il
    titolo `## R3` e la riga `Origine:`, che dicono come si chiama e da dove viene la regola, non
    che cosa chiede: correggerli non deve far scadere i verdetti che la citano.
    """
    path = Path(path or RULES_PATH)
    if not path.exists():
        return {}
    parts = RULE_MARKER.split(path.read_text(encoding="utf-8"))
    result = {}
    for rule_id, block in zip(parts[1::2], parts[2::2]):
        text = "\n".join(line for line in block.strip().splitlines()
                         if not line.startswith(("Origine:", f"## {rule_id}"))).strip()
        result[rule_id] = {"text": text, "hash": digest(text)}
    return result


def add_rule(rule_id, text, origin, path=None):
    """Append one confirmed rule; an existing ID is never rewritten from chat.

    Per cambiare una regola la si modifica a mano nel file, oppure se ne aggiunge una nuova con un
    ID nuovo e si cancella la vecchia: in entrambi i casi i verdetti che citavano la vecchia scadono.
    """
    if not isinstance(rule_id, str) or not RULE_ID.fullmatch(rule_id):
        raise ValueError("Rule ID must look like R1, R2, ...")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Rule text is required")
    path = Path(path or RULES_PATH)
    if rule_id in rules(path):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else (
        "# Regole di revisione\n\nRegole confermate dall'utente. L'agente le applica agli annunci "
        "indecisi di Jev e ai ruoli compatibili di Tier A/B, e ogni suo verdetto ne cita almeno una.\n"
        "Modificare o cancellare una regola fa scadere i verdetti che la citano.\n")
    block = f"<!-- rule:{rule_id} -->\n## {rule_id}\n\n{text.strip()}\n\nOrigine: {origin}, {now()[:10]}."
    path.write_text(current.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
    return True


def _scoped(sql, oids):
    """Append a JSON-list filter so a scoped read stays one indexed query."""
    if oids is None:
        return sql, ()
    return sql + " AND {} IN (SELECT value FROM json_each(?))", (json.dumps(sorted(oids)),)


def user_judgements(archive, oids=None):
    """Latest non-undone role feedback that carries a verdict, in the common judge contract."""
    sql, args = _scoped("SELECT opportunity_id,status,note,created_at FROM feedback "
                        "WHERE opportunity_id IS NOT NULL AND undone_at IS NULL", oids)
    latest = {}
    for row in archive.db.execute(sql.format("opportunity_id") + " ORDER BY id", args):
        latest[row["opportunity_id"]] = row
    result = {}
    for oid, row in latest.items():
        if row["status"] in USER_VERDICTS:
            result[oid] = {"verdetto": USER_VERDICTS[row["status"]], "giudice": "utente",
                           "motivo": row["note"] or "Decisione manuale", "prove": []}
    return result


def agent_judgements(archive, oids=None, active=None):
    """Current agent verdicts only: same text of the role, same text of every cited rule."""
    active = rules() if active is None else active
    sql, args = _scoped("SELECT e.record_id,e.data,o.content_hash FROM enrichments e "
                        "JOIN opportunities o ON o.id=e.record_id WHERE e.task=?", oids)
    result = {}
    for row in archive.db.execute(sql.format("e.record_id"), (AGENT_TASK, *args)):
        stored = json.loads(row["data"])
        cited = stored.get("rules") or {}
        if (stored.get("content_hash") != row["content_hash"] or not cited
                or any(active.get(rule_id, {}).get("hash") != value for rule_id, value in cited.items())):
            continue
        result[row["record_id"]] = {
            "verdetto": AGENT_DECISIONS[stored["decision"]], "giudice": "agente",
            "motivo": stored["rationale"], "prove": [f"{r}: {active[r]['text']}" for r in cited]}
    return result


def save_agent_verdict(archive, oid, decision, rule_ids, rationale, origin, active=None):
    """Store one agent verdict after checking the role exists and every cited rule is active."""
    active = rules() if active is None else active
    if decision not in AGENT_DECISIONS:
        raise ValueError("Agent decision must be keep or exclude")
    if not isinstance(rule_ids, list) or not rule_ids or any(r not in active for r in rule_ids):
        raise ValueError("An agent verdict must cite at least one active rule")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("An agent verdict needs a rationale")
    row = archive.db.execute("SELECT content_hash FROM opportunities WHERE id=?", (oid,)).fetchone()
    if row is None:
        raise ValueError("Unknown opportunity")
    stored = {"decision": decision, "rationale": rationale.strip(), "origin": origin,
              "content_hash": row["content_hash"], "rules": {r: active[r]["hash"] for r in rule_ids}}
    with archive.db:
        archive.db.execute("INSERT OR REPLACE INTO enrichments VALUES(?,?,?,?,?,?,?)",
                           (AGENT_TASK, oid, row["content_hash"], digest(stored), json.dumps(stored, ensure_ascii=False),
                            "agente", now()))
    logger.info("Agent verdict %s for %s citing %s", decision, oid, ", ".join(rule_ids))
