"""Testo di partenza e input dei modelli: da HTML grezzo alle righe che un modello può citare.

Qui non vive nessun modello. `lines()` normalizza il testo una volta sola per tutti —
regex, giudizio remoto, recupero dei dati aziendali — e `selection_input()` e
`company_input()` costruiscono ciò che viene spedito, con scritto da dove viene.

Il passaggio con Ollama è stato rimosso il 10 settembre 2026: sui ruoli non decideva nulla
(0 verdetti su 4.918 giudicabili) e sulle aziende il modello remoto fa lo stesso lavoro
dentro una chiamata che si paga comunque (docs/pipeline-completa.md).
"""

import copy
import html
from html.parser import HTMLParser
import json
import re

from jobhunter.workspace import categories


class TextExtractor(HTMLParser):
    """Remove markup and non-content script/style bodies while retaining readable words."""

    def __init__(self):
        """Initialize a buffer and the hidden-element nesting counter."""
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        """Separate HTML blocks and suppress executable or stylesheet content."""
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        """Restore visibility and terminate block elements."""
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        """Keep only visible source text."""
        if not self.hidden:
            self.parts.append(data)


def lines(text):
    """Convert HTML and common Markdown formatting to stable source lines."""
    parser = TextExtractor()
    parser.feed(text)
    value = html.unescape("".join(parser.parts))
    value = re.sub(r"\\([\\`*_{}\[\]()#+.!-])", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1 (\2)", value)
    value = re.sub(r"\*\*([^*]+)\*\*\s*-{3,}", r"\n\1\n", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"(?<=\S) +\* +", "\n", value)
    return [s for line in value.splitlines() if (s := re.sub(r"^\s*(?:#{1,6}\s+|[-*•]\s+)", "", line).strip()) and not re.fullmatch(r"[-_`]{3,}", s)]


def company_input(archive, cid, cache=None):
    """Tutta l'evidenza aziendale disponibile, con scritto da dove viene.

    Ordine: i campi della fonte, poi il testo grezzo che `company_profile` ha raccolto quando
    la descrizione manca ancora, poi le frasi estratte dagli annunci. `evidence_source` dice
    al modello con che tipo di testo ha a che fare: la pagina di un'azienda e un annuncio di
    lavoro non si leggono allo stesso modo (vedi docs/data-model.md).

    `cache` è il dizionario di **una sola preparazione**, non una cache di processo: `job_facts`
    ripulisce l'HTML di *tutti* gli annunci dell'azienda, e chi prepara una richiesta per azienda
    lo chiedeva una volta per ruolo in attesa — costo quadratico proprio sulle aziende con più
    annunci. Si restituisce sempre una copia, così chi la riceve può arricchirla (per esempio con
    `verified_web_facts`) senza che l'aggiunta compaia nelle altre richieste. Senza `cache` il
    comportamento è quello di prima: si rilegge tutto.
    """
    from jobhunter.evaluation.company_evidence import job_facts
    if cache is not None and cid in cache:
        return copy.deepcopy(cache[cid])
    row = archive.db.execute("SELECT name,sectors,description FROM companies WHERE id=?", (cid,)).fetchone()
    evidence = job_facts(archive, cid, row['name'])
    facts = [v for v in (row["sectors"], row["description"]) if v]
    source = "descrizione_salvata" if row["description"] else ""
    if not row["description"]:
        from jobhunter.acquisition.company_profile import rewrite_input
        raw = rewrite_input(archive, cid)
        if raw:
            facts.append(raw["source_text"])
            source = raw["source"]
    facts += [e["text"] for e in evidence]
    result = {"company": row["name"], "facts": list(dict.fromkeys(facts)), "evidence_source": source,
              'job_evidence': evidence, "categories": [*categories(), "Da classificare"]}
    if cache is None:
        return result
    cache[cid] = result
    return copy.deepcopy(result)


def selection_input(archive, oid):
    """Supply the role text and nothing else: il profilo viaggia nel prompt di sistema.

    Il prompt di sistema è identico fra una richiesta e l'altra, il testo del ruolo no. Tenendo il
    profilo nella parte stabile, Ollama riusa la KV cache di quel prefisso invece di ricalcolarlo
    per ognuno dei ruoli da giudicare.
    """
    job = json.loads(archive.db.execute("SELECT data FROM opportunities WHERE id=?", (oid,)).fetchone()[0])
    return {"title": job.get("title", ""), "description": "\n".join(lines(job.get("description", "")))}
