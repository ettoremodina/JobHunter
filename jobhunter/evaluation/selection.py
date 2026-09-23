"""Explainable role suggestions derived from explicit preferences, without deleting data."""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone, date
from jobhunter.evaluation import tier
from jobhunter.workspace import ROOT, identity, now

logger = logging.getLogger(__name__)


def filters():
    """Load reviewable initial rules from the candidate's configuration."""
    return json.loads((ROOT / "config/role_filters.json").read_text(encoding="utf-8"))


def judgeable(description):
    """Un giudizio semantico ha bisogno di mansioni da leggere: senza descrizione non si chiama nessun modello.

    Stesso freno per il giudice locale e per quello remoto. Il titolo l'ha già visto il regex: dare
    a un modello solo il titolo produce sempre `review`, cioè il costo di una chiamata per nessuna
    informazione nuova. Misurato il 9 settembre 2026: 2.693 dei 6.799 annunci indecisi.
    """
    from jobhunter.evaluation.enrichment import lines
    return bool(lines(description or ""))


def evaluate(job, rules=None):
    """Separate explicit role constraints, career priorities and missing evidence."""
    rules = rules or filters()
    title = job.get("title", "")
    exceptions = rules.get("exclude_title_exceptions", {})
    reasons = [key for key, pattern in rules["exclude_title_patterns"].items() if re.search(pattern, title, re.I)
               and (key not in exceptions or not re.search(exceptions[key], title, re.I))]
    facts = requirements(job.get("description", ""))
    limit = rules.get("max_required_years", 2)
    # Una forbice obbligatoria che parte gia' dal limite e lo supera («2-3 anni») e' fuori profilo;
    # una che parte sotto («0-3», «1-4») resta aperta ai junior. Scelta dell'utente, 22/09/2026.
    if (facts["required_years"] is not None and facts["required_years"] > limit
            or any(low >= limit and high > limit for low, high in facts["required_ranges"])):
        reasons.append("required_experience")
    if facts["people_management"]:
        reasons.append("people_management")
    from jobhunter.evaluation.languages import detect_language, language_requirements
    facts["languages"] = language_requirements(job.get("description", ""), rules)
    # In che lingua è scritto l'annuncio: si legge qui, sull'originale, perché la sintesi remota
    # arriva già tradotta e quell'informazione andrebbe persa. Non è un requisito: è un'etichetta.
    facts["written_in"] = detect_language(job.get("description") or title)
    if facts["languages"]["unsupported"]:
        reasons.append("required_language")

    def decision(status, reasons):
        """Expose career priority and missing evidence independently of compatibility."""
        return {"status": status, "reasons": reasons, "requirements": facts,
                "career_priority": "primary" if re.search(rules.get("primary_title_pattern", r"(?!)"), title, re.I) else "secondary",
                "verification": {"description": bool(job.get("description")),
                                 "description_usable": facts["description_usable"],
                                 "experience_determined": facts["required_years"] is not None,
                                 "language_requirement": facts["languages"]["status"], "opening": "not_verified"}}
    if reasons:
        return decision("excluded", reasons)
    for rule in reversed(rules.get("user_title_rules", [])):
        if rule.get("enabled", True) and re.search(rule["pattern"], title, re.I):
            if rule.get("evidence_pattern") and not re.search(rule["evidence_pattern"], title + "\n" + (job.get("description") or ""), re.I):
                return decision("review", ["user_rule_needs_evidence:" + rule["id"]])
            return decision({"include": "potential", "exclude": "excluded", "review": "review"}[rule["action"]], ["user_rule:" + rule["id"]])
    relevant = bool(re.search(rules["preferred_title_pattern"], title, re.I))
    review = [key for key, pattern in rules.get("review_title_patterns", {}).items() if re.search(pattern, title, re.I)]
    return decision("potential" if relevant else "review", ["role_family_allowed"] if relevant else review or ["insufficient_role_evidence"])


def regex_judgement(decision):
    """Use deterministic rules only to reject; every surviving role still needs Jev."""
    verdetto = tier.DROP if decision["status"] == "excluded" else tier.UNKNOWN
    motivo = ("Escluso dalle regole locali" if verdetto == tier.DROP else
              "Non escluso dalle regole locali: passa a Jev")
    return {"verdetto": verdetto, "motivo": motivo, "prove": list(decision["reasons"]), "giudice": "regex",
            "primary": decision["career_priority"] == "primary"}


def llm_judgement(result, judge):
    """Esito di un giudice LLM nel contratto comune; 'review' resta un «non so» per il giudice dopo."""
    if not result:
        return None
    verdetto = {"keep": tier.KEEP, "exclude": tier.DROP, "review": tier.UNKNOWN}.get(result.get("decision"), tier.UNKNOWN)
    return {"verdetto": verdetto, "motivo": result.get("rationale") or "", "prove": list(result.get("evidence") or []), "giudice": judge}


def verdicts(archive, cid=None):
    """Assembla i due verdetti d'asse, il tier e la catena delle motivazioni. Non salva nulla (DESIGN §7).

    `cid` è un'azienda sola, una sequenza di aziende, oppure None per tutto l'archivio: una sola
    scansione per chiamata, perché ogni lettura degli esiti costa un giro su tutti gli annunci.
    """
    preferred = filters().get("preferred_categories", [])
    decisions = archive.evaluations(cid)
    # ponytail: legge gli esiti LLM salvati senza rivalidare la loro cache, troppo cara su 23k
    # annunci; la scadenza resta visibile in aggregato da pipeline.summary().
    saved = {}
    # Scoped reads already know their opportunity IDs. Use the existing (task, record_id)
    # primary key instead of loading every saved LLM response for each search page.
    saved_sql = "SELECT record_id,data FROM enrichments WHERE task='jev:selection'"
    saved_args = ()
    if cid is not None:
        saved_sql += " AND record_id IN (SELECT value FROM json_each(?))"
        saved_args = (json.dumps(list(decisions)),)
    for row in archive.db.execute(saved_sql, saved_args):
        saved[row["record_id"]] = json.loads(row["data"]).get("result") or {}
    # Un annuncio chiuso resta nella catena con il suo verdetto, ma non tiene più in piedi un tier.
    closed = {r[0] for r in archive.db.execute("SELECT opportunity_id FROM closures")}
    from jobhunter.evaluation import review
    scoped = None if cid is None else list(decisions)
    users, agents = review.user_judgements(archive, scoped), review.agent_judgements(archive, scoped)
    roles = {}
    for oid, decision in decisions.items():
        # La cascata ha un solo giudice semantico: un «non so» di Jev resta aperto alla revisione.
        chain = [j for j in (regex_judgement(decision),
                             llm_judgement(saved.get(oid), "jev")) if j]
        # Sopra la cascata: tu, poi l'agente. Restano nella catena per mostrare cosa hanno cambiato.
        overrides = [j for j in (users.get(oid), agents.get(oid)) if j]
        final = tier.role_verdict(chain, overrides)
        # Un «tieni» del livello di revisione vale come ruolo prioritario: e' gia' un giudizio sul
        # singolo ruolo, quindi fa salire anche un'azienda fuori dalle categorie preferite a
        # B-esperienza. Senza, tenere un ruolo non spostava quasi mai il tier (utente, 22/09/2026).
        reviewed_keep = final["giudice"] in ("utente", "agente") and final["verdetto"] == tier.KEEP
        roles[oid] = {**final, "primary": chain[0]["primary"] or reviewed_keep,
                      "catena": chain + overrides[::-1], "chiuso": oid in closed}
    if cid is None:
        scope, own_scope, arguments = "", "", ()
    elif isinstance(cid, str):
        scope, own_scope, arguments = " WHERE company_id=?", " WHERE id=?", (cid,)
    else:
        scope, own_scope = " WHERE company_id IN (SELECT value FROM json_each(?))", " WHERE id IN (SELECT value FROM json_each(?))"
        arguments = (json.dumps(sorted(cid)),)
    grouped = {}
    for row in archive.db.execute("SELECT id,company_id FROM opportunities" + scope, arguments):
        grouped.setdefault(row["company_id"], []).append(row["id"])
    assigned = {}
    for row in archive.db.execute(
            "SELECT company_id,category,rank,method,reason FROM categories" + scope + " ORDER BY rank", arguments):
        assigned.setdefault(row['company_id'], []).append(dict(row))
    result = {}
    for row in archive.db.execute("SELECT id FROM companies" + own_scope, arguments):
        categories = assigned.get(row["id"], [])
        labels = [item['category'] for item in categories]
        company = categories[0] if categories else {"category": tier.UNCLASSIFIED, "method": "unknown", "reason": "Dati aziendali insufficienti"}
        verdict = tier.company_verdict(labels, preferred)
        own = {oid: roles[oid] for oid in grouped.get(row["id"], []) if oid in roles}
        result[row["id"]] = {"azienda": {"verdetto": verdict, "categoria": company["category"],
                                        "categorie": labels,
                                        "motivo": company["reason"], "giudice": company["method"]},
                             "ruoli": own, "tier": tier.tier(verdict, [r for r in own.values() if not r["chiuso"]])}
    return result


def requirements(text):
    """Extract explicit requirements with quotes; absent, waived or ambiguous clauses stay unknown."""
    from jobhunter.evaluation.enrichment import lines
    text = "\n".join(lines(text))
    mandatory, preferred, quotes, ranges = [], [], [], []
    management = False
    restrictions = []
    section = None
    for sentence in re.split(r"(?<=[.!?;])\s+|\n", text):
        lower = sentence.casefold()
        heading = lower.strip(" :*-\t")
        if re.fullmatch(r"(?:(?:minimum|basic|required) )?qualifications|requirements|your profile|requisiti|profil recherché", heading):
            section = "required"
        elif re.fullmatch(r"(?:preferred|desired|additional) qualifications|nice.to.have|requisiti preferenziali", heading):
            section = "preferred"
        elif re.fullmatch(r"responsibilities|benefits|about (?:us|the role|you)|what we offer|responsabilità", heading):
            section = None
        match = re.search(r"\b(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*\+?\s*(?:years?|anni|ans|jahre[n]?|jaar|lat|lata)\s+(?:[^\W\d_]+(?:[-’'][^\W\d_]+)*\s+){0,6}(?:d['’])?(?:experience|esperienza|expérience|erfahrung|ervaring|doświadczenia)\b", lower)
        if not match:
            match = re.search(r"\bexperience\s*\((\d{1,2})\s*years?\s+or more\)", lower)
        if match:
            # Bind the waiver to this experience phrase, not an unrelated requirement.
            negated = bool(re.search(r"\bnon\s+(?:(?:sono|è|e')\s+)?(?:richiest\w*|necessari\w*|obbligator\w*)\s*$", lower[:match.start()])
                           or re.match(r"\s*(?:(?:is|are|è|sono)\s+)?(?:not\s+(?:required|necessary|mandatory)|non\s+(?:richiest\w*|necessari\w*|obbligator\w*))\b", lower[match.end():]))
            optional = section == "preferred" or bool(re.search(r"preferred|nice.to.have|desirable|ideally|preferibil|preferenzial|a plus|souhait|wünschenswert|mile widziane", lower))
            required = section == "required" or bool(re.search(r"required|must|at least|minimum|minimaal|or more|requirement|almeno|richiest|obbligator|mindestens|wymagan|requis", lower))
            if not negated and optional:
                preferred.append(int(match[1]))
            elif not negated and required:
                mandatory.append(int(match[1]))
                if match.re.groups > 1 and match[2]:
                    ranges.append([int(match[1]), int(match[2])])
            quotes.append(sentence.strip())
        if re.search(r"(?:you will|responsibilities include|must)\s+(?:manage|lead)\s+(?:a |the |our )?team|direct reports|people management (?:is )?required", lower) and not re.search(r"no |not required|without", lower):
            management = True
            quotes.append(sentence.strip())
        if re.search(r"must (?:reside|be based)|right to work|work authori[sz]ation|no visa sponsorship|remote (?:within|only)|residenza obbligatoria", lower):
            restrictions.append(sentence.strip())
    return {"description_usable": bool(text),
            "required_years": max(mandatory) if mandatory else None, "required_ranges": ranges,
            "preferred_years": max(preferred) if preferred else None,
            "people_management": management, "eligibility_quotes": restrictions, "evidence": list(dict.fromkeys(quotes)),
            "unknown": [] if mandatory else ["Esperienza obbligatoria non determinata"]}


def feedback_reasons():
    """Shared human-readable reasons for scoped decisions."""
    return {"interesting": "Attività interessante", "too_senior": "Troppa esperienza richiesta", "management": "Responsabilità manageriali", "commercial": "Ruolo commerciale o marketing", "sector": "Settore non interessante", "location": "Località o vincoli di lavoro", "stack": "Mansioni o tecnologie", "not_now": "Non ora", "no_current_roles": "Nessun ruolo adatto adesso", "company_not_interested": "Azienda non interessante", "required_language": "Lingua obbligatoria incompatibile", "other": "Altro"}


def role_snapshot(archive, cid, decisions=None):
    """Fingerprint meaningful role facts, excluding formatting and observation timestamps."""
    result = {}
    decisions = archive.evaluations(cid) if decisions is None else decisions
    for row in archive.db.execute("SELECT id,data FROM opportunities WHERE company_id=?", (cid,)):
        job = json.loads(row["data"])
        facts = {k: job.get(k) for k in ("title", "locations", "remote_policy", "salary", "employment_type")}
        facts["requirements"] = decisions[row['id']]["requirements"]
        result[row["id"]] = identity(json.dumps(facts, sort_keys=True))
    return result


def saved(archive):
    """Le aziende e i ruoli messi da parte a mano, con quello che serve per riparlarne in chat.

    La scelta manuale non tocca i verdetti della pipeline: vive nella tabella feedback e basta.
    Qui si legge l'ultima decisione ancora valida, azienda per azienda e ruolo per ruolo.
    """
    latest = {}
    for row in archive.db.execute("""SELECT company_id,opportunity_id,status,note,created_at FROM feedback
        WHERE undone_at IS NULL ORDER BY id"""):
        latest[(row['company_id'], row['opportunity_id'])] = dict(row)
    companies = {}
    for (cid, oid), event in latest.items():
        if event['status'] != 'saved':
            continue
        entry = companies.setdefault(cid, {'id': cid, 'roles': [], 'company_saved': False, 'saved_at': event['created_at']})
        entry['saved_at'] = max(entry['saved_at'], event['created_at'])
        if oid is None:
            entry['company_saved'] = True
        else:
            entry['roles'].append(oid)
    if not companies:
        return {'items': []}
    assessment = verdicts(archive, list(companies))
    items = []
    for cid, entry in companies.items():
        row = archive.db.execute('SELECT name,website FROM companies WHERE id=?', (cid,)).fetchone()
        if row is None:
            continue
        roles = {r['id']: dict(r) for r in archive.db.execute(
            "SELECT id,json_extract(data,'$.title') title,json_extract(data,'$.application_url') url "
            "FROM opportunities WHERE company_id=?", (cid,))}
        state = assessment[cid]
        items.append({**entry, 'name': row['name'], 'website': row['website'],
                      'tier': state['tier'], 'tier_label': tier.label(state['tier']),
                      'category': state['azienda']['categoria'],
                      'roles': [{'id': oid, 'title': roles.get(oid, {}).get('title', 'Ruolo non più in archivio'),
                                 'url': roles.get(oid, {}).get('url', ''),
                                 'verdetto': state['ruoli'].get(oid, {}).get('verdetto', '')} for oid in entry['roles']]})
    return {'items': sorted(items, key=lambda item: item['saved_at'], reverse=True)}


def metrics(archive):
    """Measure distinct company decisions and reasons without treating undecided items as rejection."""
    latest = "SELECT f.* FROM feedback f WHERE opportunity_id IS NULL AND undone_at IS NULL AND id=(SELECT MAX(x.id) FROM feedback x WHERE x.company_id=f.company_id AND x.opportunity_id IS NULL AND x.undone_at IS NULL)"
    rows = archive.db.execute(latest).fetchall()
    kept = sum(r["status"] in ("saved", "contacted") for r in rows)
    discarded = sum(r["status"] == "discarded" for r in rows)
    return {"saved_or_contacted": kept, "discarded": discarded,
            "save_fraction_decided": kept/(kept+discarded) if kept+discarded else None,
            "reasons": [dict(r) for r in archive.db.execute("SELECT d.reason,count(DISTINCT f.company_id) AS companies FROM feedback f JOIN feedback_detail d ON d.event_id=f.id WHERE f.undone_at IS NULL GROUP BY d.reason")]}


def proposals(archive, proposal_id=None, state=None):
    """Registrare le esclusioni di settore che si ripetono nelle tue decisioni.

    Attenzione: da quando la coda non esiste più, accettare una proposta non cambia nulla in
    automatico. Resta la traccia di una preferenza ricorrente, da riportare a mano nel profilo.
    """
    if proposal_id:
        if state not in ("accepted", "dismissed", "disabled"):
            raise ValueError("Invalid preference rule state")
        with archive.db:
            changed = archive.db.execute("UPDATE preference_rules SET state=?,updated_at=? WHERE id=?", (state, now(), proposal_id))
            if not changed.rowcount:
                raise ValueError("Proposal not found")
        return {"id": proposal_id, "state": state}
    counts = archive.db.execute("SELECT c.category,count(DISTINCT f.company_id) AS n FROM feedback f JOIN feedback_detail d ON d.event_id=f.id JOIN categories c ON c.company_id=f.company_id WHERE f.undone_at IS NULL AND f.opportunity_id IS NULL AND f.status='discarded' AND d.reason='sector' AND f.id=(SELECT max(x.id) FROM feedback x WHERE x.company_id=f.company_id AND x.opportunity_id IS NULL AND x.undone_at IS NULL) AND c.category!='Da classificare' GROUP BY c.category HAVING n>=?", (filters()["proposal_min_companies"],)).fetchall()
    with archive.db:
        for row in counts:
            archive.db.execute("INSERT OR IGNORE INTO preference_rules VALUES(?,?,?,?)", (identity(row["category"]), row["category"], "proposed", now()))
    return {"items": [dict(r) for r in archive.db.execute("SELECT * FROM preference_rules ORDER BY updated_at DESC")]}


def research_brief(archive, cid):
    """Prepare targeted chat research from missing facts, keeping source attribution explicit."""
    company = archive.show(cid)
    jobs = [j for j in company["opportunities"] if j.get("verdict", {}).get("verdetto") == tier.KEEP]
    return {"company_id": cid, "name": company["name"], "website": company["website"], "questions": ["Qual è l'attività aziendale?", "Quali ruoli sono ancora aperti?", "Quali requisiti sono obbligatori e quali preferenziali?", "Da quali paesi si può lavorare?"],
            "search_queries": [company["name"] + " official careers", company["name"] + " " + (jobs[0]["title"] if jobs else "data science engineering jobs")],
            "opportunities": [{"id": j["id"], "url": j["application_url"], "requirements": j["selection"]["requirements"]} for j in jobs[:3]], "save_with": "evidence COMPANY_ID URL --note FACTS"}


def shortlist(archive, limit=30, offset=0, tiers=("A", "B-attesa", "B-esperienza")):
    """Return company groups by tier; never save rejection feedback."""
    assessment = verdicts(archive)
    titles = {r[0]: r[1] for r in archive.db.execute("SELECT id,json_extract(data,'$.title') FROM opportunities")}
    groups = {cid: state for cid, state in assessment.items() if state["tier"] in tiers}
    ids = list(groups)[max(0, offset):max(0, offset) + min(max(limit, 1), 100)]
    items = [{"id": cid, "name": archive.db.execute("SELECT name FROM companies WHERE id=?", (cid,)).fetchone()[0],
              "tier": groups[cid]["tier"], "company_verdict": groups[cid]["azienda"],
              "roles": [{"id": oid, "title": titles.get(oid), **verdict} for oid, verdict in groups[cid]["ruoli"].items()
                        if verdict["verdetto"] == tier.KEEP]} for cid in ids]
    counts = Counter(state["tier"] for state in assessment.values())
    logger.info("Assessed %s companies across both axes", len(assessment))
    return {"total": len(groups), "tier_counts": dict(counts), "items": items,
            "note": "Il tier è calcolato in lettura dai due verdetti d'asse; nessuna azienda viene esclusa dall'archivio."}
