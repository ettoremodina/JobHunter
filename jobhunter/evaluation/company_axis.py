"""Asse azienda: il giudice a regole che assegna la categoria, con la sua provenienza.

DESIGN §2: «l'azienda è interessante?» equivale a «la sua categoria è fra quelle preferite?».
Non è un giudizio a sé: categorizzare l'azienda *è* valutare l'asse. Qui vive solo chi assegna
la categoria; chi decide se quella categoria interessa è `config/role_filters.json`, letto da
`tier.company_verdict()`.

Il vocabolario dei settori sta in `config/categories.json`: nessun nome di settore è scritto qui.
"""

import json
import logging
import re

from jobhunter.normalization import identity
from jobhunter.workspace import ROOT, now

logger = logging.getLogger(__name__)
UNCLASSIFIED = "Da classificare"
# Valori larghi e benefit per i dipendenti non stabiliscono il settore di un datore di lavoro.
WEAK_TERMS = {"sustainability", "environmental", "training", "efficiency & reduction"}


def vocabulary():
    """Read shared company-sector rules, independent of acquisition source."""
    return json.loads((ROOT / "config/categories.json").read_text(encoding="utf-8"))


def category(archive, cid):
    """Expose an assignment together with its provenance and explanation."""
    row = archive.db.execute("SELECT category,method AS category_method,reason AS category_reason FROM categories WHERE company_id=?", (cid,)).fetchone()
    return dict(row) if row else {"category": UNCLASSIFIED, "category_method": "unknown", "category_reason": "Dati aziendali insufficienti"}


def assign(archive, cid, label, reason):
    """Save a chat classification; imports and model passes never overwrite it."""
    if not archive.db.execute("SELECT 1 FROM companies WHERE id=?", (cid,)).fetchone():
        raise ValueError("Company not found")
    if label not in [*vocabulary(), UNCLASSIFIED] or not isinstance(reason, str) or not reason.strip():
        raise ValueError("Choose a known category and supply a reason")
    with archive.db:
        archive.db.execute("INSERT OR REPLACE INTO categories VALUES(?,?,?,?,?)", (cid, label, "chat", reason, now()))
    logger.info("Classified company %s from chat", cid)
    return category(archive, cid)


def categorize(archive, cid=None, label=None, reason="", company_ids=None):
    """Refresh rule suggestions, or save one chat classification when a company is named."""
    rules = vocabulary()
    if cid is not None:
        return assign(archive, cid, label, reason)
    count = 0
    with archive.db:
        scope = " AND id IN (SELECT value FROM json_each(?))" if company_ids is not None else ""
        for company in archive.db.execute("SELECT * FROM companies WHERE id NOT IN (SELECT company_id FROM categories WHERE method='chat')" + scope,
                                          (json.dumps(sorted(company_ids)),) if company_ids is not None else ()).fetchall():
            from jobhunter.evaluation.enrichment import company_input
            enriched = archive.db.execute("SELECT source_hash FROM enrichments WHERE task='category' AND record_id=?", (company["id"],)).fetchone()
            if enriched and enriched[0] == identity(json.dumps(company_input(archive, company["id"]), sort_keys=True, ensure_ascii=False)):
                continue
            matches = {}
            # Sector labels take precedence; job titles cannot identify a company's industry.
            for field in ("sectors", "description"):
                value = company[field].casefold()
                matches = {name: term for name, terms in rules.items() for term in terms if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", value)}
                if matches:
                    break
            evidence_matches = {}
            if not matches:
                from jobhunter.evaluation.company_evidence import job_facts
                for fact in job_facts(archive, company["id"], company["name"]):
                    for candidate, terms in rules.items():
                        if any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", fact["text"], re.I)
                               for term in terms if term not in WEAK_TERMS):
                            evidence_matches.setdefault(candidate, fact)
                matches = {candidate: fact["text"] for candidate, fact in evidence_matches.items()}
                field = "Descrizione annuncio"
            name = next(iter(matches)) if len(matches) == 1 else UNCLASSIFIED
            reason = f"{field}: {matches[name]}" if len(matches) == 1 else "Più settori possibili: " + ", ".join(matches) if matches else "Dati aziendali insufficienti"
            if len(matches) == 1 and name in evidence_matches:
                reason += " | " + evidence_matches[name]["source_url"]
            method = "rules" if len(matches) == 1 else "unknown"
            # Il giudizio di un modello batte una parola chiave: le regole riscrivono solo quello
            # che hanno scritto loro, o una casella ancora vuota. Senza questa condizione una
            # passata a regole cancellava una categoria pagata al modello remoto (docs/asse-azienda-piano.md).
            archive.db.execute("INSERT INTO categories VALUES(?,?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET category=excluded.category,method=excluded.method,reason=excluded.reason,updated_at=excluded.updated_at WHERE categories.method IN ('rules','unknown') AND (category!=excluded.category OR method!=excluded.method OR reason!=excluded.reason)",
                               (company["id"], name, method, reason, now()))
            count += name != UNCLASSIFIED
    logger.info("Category rules matched %s companies", count)
    return {"matched_by_rules": count,
            "coverage": [dict(r) for r in archive.db.execute("SELECT category,method,count(*) AS companies FROM categories GROUP BY category,method")]}
