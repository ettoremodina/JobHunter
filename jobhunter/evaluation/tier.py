"""Tier calcolato dai due verdetti d'asse: funzioni pure, nessun accesso al database.

DESIGN §2: il tier non si salva mai. Persisterlo ricrea il bug originale, perche'
un'azienda a «scarto» non risalirebbe mai a Tier B quando pubblica un ruolo adatto.
Nessuna categoria, soglia o preferenza e' scritta qui: arrivano tutte da config.
"""

KEEP, DROP, UNKNOWN = "tieni", "scarta", "non_so"
INTERESTING, NOT_INTERESTING, NO_EVIDENCE = "interessante", "non_interessante", "evidenza_mancante"
UNCLASSIFIED = "Da classificare"
TIERS = ("A", "B-attesa", "B-esperienza", "evidenza-mancante", "scarto")


def company_verdict(category, preferred):
    """Asse azienda: categorizzare l'azienda *e'* valutare l'asse (DESIGN §2)."""
    if not category or category == UNCLASSIFIED:
        return NO_EVIDENCE
    return INTERESTING if category in preferred else NOT_INTERESTING


def role_verdict(judgements):
    """Cascata: vince il primo giudice che ha saputo decidere; gli altri non lo rivedono (DESIGN §3)."""
    for judgement in judgements:
        if judgement and judgement.get("verdetto") in (KEEP, DROP):
            return judgement
    return {"verdetto": UNKNOWN, "motivo": "Nessun giudice ha saputo decidere", "prove": [], "giudice": None}


def tier(company, roles):
    """Dai due verdetti d'asse al tier, con due soglie diverse sull'asse ruolo (DESIGN §2)."""
    compatible = [r for r in roles if r.get("verdetto") == KEEP]
    if company == INTERESTING:
        return "A" if compatible else "B-attesa"
    # L'assenza di prove non e' un rifiuto: un ruolo che vale da solo emerge comunque.
    if any(r.get("primary") for r in compatible):
        return "B-esperienza"
    return "evidenza-mancante" if company == NO_EVIDENCE else "scarto"


def label(name):
    """Nome leggibile del tier per interfaccia e report."""
    return {"A": "Tier A · azienda sì · ruolo sì", "B-attesa": "Tier B · attesa · azienda sì · nessun ruolo ora",
            "B-esperienza": "Tier B · esperienza · azienda no · ruolo sì",
            "evidenza-mancante": "Evidenza mancante · coda di lavoro, non un rifiuto",
            "scarto": "Scarto · azienda no · ruolo no"}[name]
