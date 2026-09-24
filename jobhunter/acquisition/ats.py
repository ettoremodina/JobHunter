"""Bacheche ATS pubbliche: tutti gli annunci aperti di un'azienda, con testo e data, in una lettura.

Greenhouse, Lever, Ashby e gli altri pubblicano senza chiave l'elenco completo degli annunci di
un'azienda. Rispetto ai board generalisti portano il testo completo e la data esatta nella stessa
risposta, e un annuncio che manca da una bacheca che ha risposto è chiuso davvero.

Due metà, con un file in mezzo che l'utente può correggere a mano (`config/ats_watchlist.json`):
- `discover()` cerca la bacheca delle aziende di Tier A/B, dalla strada più economica: URL degli
  annunci già in archivio, poi sito aziendale, poi slug ricavato dal nome. Ogni bacheca trovata
  viene letta una volta e tenuta solo se il nome che dichiara è quello dell'azienda.
- `rows()` legge le bacheche dell'elenco e restituisce righe nel formato comune di `normalize()`.
"""

import html
import json
import logging
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ElementTree
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from jobhunter.acquisition.collection import html_text, session
from jobhunter.operations import cancellation
from jobhunter.workspace import ROOT, clean, now, url

logger = logging.getLogger(__name__)

# Dove un URL o una pagina rivelano la bacheca. Lo slug è il primo gruppo non vuoto; per Workday
# servono tre parti (tenant, istanza, sito), unite con "/".
PATTERNS = {
    "greenhouse": r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_(?:board|app)(?:/js)?\?for=)?([a-z0-9_-]+)"
                  r"|boards-api\.greenhouse\.io/v1/boards/([a-z0-9_-]+)",
    "lever": r"(?<!eu\.)(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9._-]+)",
    "lever_eu": r"(?:jobs|api)\.eu\.lever\.co/(?:v0/postings/)?([a-z0-9._-]+)",
    "ashby": r"jobs\.ashbyhq\.com/([a-z0-9._%-]+)",
    "workable": r"apply\.workable\.com/(?:api/v1/widget/accounts/)?([a-z0-9_-]+)",
    "smartrecruiters": r"(?:jobs|careers)\.smartrecruiters\.com/([a-z0-9_-]+)|api\.smartrecruiters\.com/v1/companies/([a-z0-9_-]+)",
    "recruitee": r"([a-z0-9-]+)\.recruitee\.com",
    "personio": r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)",
    "teamtailor": r"([a-z0-9-]+)\.teamtailor\.com",
    "workday": r"([a-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?([a-z0-9_-]+)",
}
COMPILED = {name: re.compile(pattern, re.I) for name, pattern in PATTERNS.items()}
# Pezzi di URL che somigliano a uno slug ma sono la piattaforma stessa.
GENERIC = {"www", "app", "api", "embed", "assets", "static", "cdn", "careers", "career", "jobs", "j",
           "v0", "v1", "wday", "oneclick-ui", "widget", "company", "images", "css", "js", "login", "signin"}
# ATS provati con lo slug ricavato dal nome: rispondono «non esiste» in modo netto e dichiarano il
# nome dell'azienda. Workday ha tre parti da indovinare; Recruitee chiude le connessioni dopo una
# serie di sottodomini inesistenti (osservato il 24/09/2026), quindi si trova solo da annunci e siti.
GUESSABLE = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "personio", "teamtailor")
# Link corti che nascondono la bacheca dietro un redirect.
SHORT_LINKS = re.compile(r"https?://(?:grnh\.se/[\w-]+|apply\.workable\.com/j/[\w-]+)", re.I)
CAREERS = re.compile(r"career|karriere|jobs|join|work-with-us|lavora|vacatures|vacanc|stellen|emplois|empleo|trabaja|opportunit", re.I)
LEGAL = {"gmbh", "ag", "spa", "srl", "bv", "ltd", "limited", "inc", "sa", "sas", "se", "ab", "as", "asa", "oy", "oyj",
         "nv", "plc", "llc", "kg", "co", "corp", "group", "holding", "holdings", "the", "company", "sarl", "srls", "aps"}
# Parole con cui le pagine delle bacheche circondano il nome dell'azienda nel titolo.
BOILERPLATE = {"jobs", "job", "careers", "career", "at", "bei", "chez", "presso", "karriere", "stellenangebote",
               "vacancies", "openings", "open", "positions", "current", "teamtailor", "personio", "recruitee",
               "lever", "ashby", "workable", "work", "with", "us", "join", "en", "de", "home", "page"}


def detect(text):
    """Every board referenced in a URL or an HTML page, as {"ats", "slug"} without duplicates."""
    found = {}
    for name, pattern in COMPILED.items():
        for match in pattern.finditer(text or ""):
            parts = [g for g in match.groups() if g]
            if name == "workday":
                # Per Workday il sito è un nome scelto dall'azienda: «career» o «jobs» sono validi.
                if parts[2].lower() == "wday":
                    continue
                slug = "/".join((parts[0].lower(), parts[1].lower(), parts[2]))
            else:
                slug = parts[0].strip(".")
                if slug.lower() in GENERIC or len(slug) < 2:
                    continue
            found.setdefault((name, slug.lower()), {"ats": name, "slug": slug})
    return list(found.values())


def words(name):
    """Significant lowercase words of a company name: no accents, punctuation or legal form."""
    text = unicodedata.normalize("NFKD", html.unescape(name or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", "", text)
    return [w for w in re.findall(r"[a-z0-9]+", text) if w not in LEGAL]


def same_company(ours, theirs, exact=False):
    """True when the name a board declares is the company's own.

    Uguale una volta tolti spazi, forma giuridica e parole di contorno dei titoli («Jobs at»,
    «Karriere bei»). Senza `exact` basta anche una sequenza di parole contenuta nell'altra
    («ALTEN» e «ALTEN Italia»): va bene quando la bacheca viene da un annuncio o dal sito
    dell'azienda. Per uno slug indovinato dal nome serve `exact`, altrimenti «Nova» prenderebbe
    la bacheca di «Nova Credit».
    """
    a, b = words(ours), [w for w in words(theirs) if w not in BOILERPLATE]
    if not a or not b:
        return False
    if "".join(a) == "".join(b):
        return True
    if exact:
        return False
    short, long = sorted((a, b), key=len)
    return any(long[i:i + len(short)] == short for i in range(len(long) - len(short) + 1))


def guesses(name):
    """Slugs a company would plausibly choose: its name joined, then hyphenated."""
    parts = words(name)
    return [s for s in dict.fromkeys(("".join(parts), "-".join(parts))) if len(s) >= 4]


class Pace:
    """At most one request per ATS every `interval` seconds, whatever the number of threads."""

    def __init__(self, interval):
        self.interval, self.locks, self.next = interval, defaultdict(threading.Lock), defaultdict(float)

    def __call__(self, key):
        with self.locks[key]:
            wait = self.next[key] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.next[key] = time.monotonic() + self.interval


def call(method, address, timeout, attempts=3, **kwargs):
    """One request; transport errors are retried with a pause, HTTP statuses are the caller's.

    I ritentativi servono per gli ATS, che a volte perdono una connessione; un sito aziendale che
    non risponde va lasciato subito (`attempts=1`), altrimenti costa quasi un minuto ad azienda.
    """
    import requests
    for attempt in range(attempts):
        try:
            return session().request(method, address, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts - 1:
                raise
            time.sleep(3 * (attempt + 1))


def day(value):
    """ISO date of a timestamp in any of the formats the ATSs use, or None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
    text = str(value)
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return None


def title_of(page):
    """Text of the first <title>, or ''."""
    match = re.search(r"<title[^>]*>(.*?)</title>", page or "", re.S | re.I)
    return clean(html.unescape(match.group(1))) if match else ""


# --- Nome dichiarato dalla bacheca: None se la bacheca non esiste ---------------------------------

def info(board, get):
    """Name a board declares, '' when it exists but says nothing, None when it does not exist."""
    ats, slug = board["ats"], board["slug"]
    if ats == "greenhouse":
        r = get("GET", f"https://boards-api.greenhouse.io/v1/boards/{slug}")
        return r.json().get("name", "") if r.ok else None
    if ats in ("lever", "lever_eu"):
        r = get("GET", f"https://jobs.{'eu.' if ats == 'lever_eu' else ''}lever.co/{slug}")
        return title_of(r.text) if r.ok else None
    if ats == "ashby":
        r = get("GET", f"https://jobs.ashbyhq.com/{slug}")
        match = re.search(r'"organization":\{[^{}]*?"name":"((?:\\.|[^"\\])*)"', r.text) if r.ok else None
        return json.loads(f'"{match.group(1)}"') if match else None
    if ats == "workable":
        r = get("GET", f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
        return r.json().get("name", "") if r.ok else None
    if ats == "smartrecruiters":
        r = get("GET", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1")
        content = r.json().get("content") if r.ok else None
        # Uno slug sbagliato risponde 200 con un elenco vuoto: senza annunci non si può verificare.
        return content[0].get("company", {}).get("name", "") if content else None
    if ats == "recruitee":
        r = get("GET", f"https://{slug}.recruitee.com/api/offers/")
        offers = r.json().get("offers") if r.ok else None
        return None if offers is None else offers[0].get("company_name", "") if offers else ""
    if ats == "personio":
        r = get("GET", f"https://{slug}.jobs.personio.de/", allow_redirects=False)
        return title_of(r.text) if r.status_code == 200 else None
    if ats == "teamtailor":
        r = get("GET", f"https://{slug}.teamtailor.com/jobs.rss", allow_redirects=False)
        if r.status_code != 200:
            return None
        return clean(ElementTree.fromstring(r.content).findtext("channel/title"))
    if ats == "workday":
        tenant, instance, site = slug.split("/")
        r = get("POST", f"https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                json={"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}})
        # Workday non espone il nome dell'azienda: il tenant è il nome più vicino che abbiamo.
        return tenant if r.ok else None
    raise ValueError(f"Unknown ATS {ats}")


# --- Annunci di una bacheca nel formato comune ------------------------------------------------------

def jobs(board, get, known=None, cap=None):
    """All open ads of one board as common rows (without company fields) and whether the list is complete.

    Workday and SmartRecruiters give the text only with a second call per ad. `known` maps the URL of
    each ad the archive already has with its text to the fields saved from that call: the call is
    spent only on new ads, and a known ad keeps its date and places. Senza, l'elenco Workday
    sovrascriverebbe la data con niente e le sedi con «2 Locations». `cap` bounds very large boards;
    a capped list is not complete, so its missing ads must not be closed.
    """
    ats, slug = board["ats"], board["slug"]
    known = known or {}
    rows = []
    if ats == "greenhouse":
        r = get("GET", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        r.raise_for_status()
        for j in r.json()["jobs"]:
            kind = next((m.get("value") for m in j.get("metadata") or [] if (m.get("name") or "").lower() == "employment type"), None)
            rows.append({"title": j["title"], "source_url": j["absolute_url"], "description": html_text(html.unescape(j.get("content") or "")),
                         "posted_at": day(j.get("first_published") or j.get("updated_at")),
                         "locations": [(j.get("location") or {}).get("name")], "employment_type": kind if isinstance(kind, str) else None})
    elif ats in ("lever", "lever_eu"):
        r = get("GET", f"https://api.{'eu.' if ats == 'lever_eu' else ''}lever.co/v0/postings/{slug}?mode=json")
        r.raise_for_status()
        for j in r.json():
            parts = [j.get("openingPlain"), j.get("descriptionPlain"),
                     *(f"{x.get('text', '')}\n{html_text(x.get('content'))}" for x in j.get("lists") or []), j.get("additionalPlain")]
            categories = j.get("categories") or {}
            rows.append({"title": j["text"], "source_url": j["hostedUrl"], "description": "\n\n".join(p.strip() for p in parts if p and p.strip()),
                         "posted_at": day(j.get("createdAt")), "locations": categories.get("allLocations") or [categories.get("location")],
                         "remote_policy": j.get("workplaceType") if j.get("workplaceType") in ("remote", "hybrid") else None,
                         "employment_type": categories.get("commitment")})
    elif ats == "ashby":
        r = get("GET", f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        r.raise_for_status()
        for j in r.json()["jobs"]:
            if j.get("isListed") is False:
                continue
            places = [j.get("location"), *((x or {}).get("location") for x in j.get("secondaryLocations") or [])]
            mode = (j.get("workplaceType") or "").lower()
            rows.append({"title": j["title"], "source_url": j["jobUrl"], "description": j.get("descriptionPlain") or html_text(j.get("descriptionHtml")),
                         "posted_at": day(j.get("publishedAt")), "locations": places,
                         "remote_policy": "remote" if j.get("isRemote") or mode == "remote" else "hybrid" if mode == "hybrid" else None,
                         "employment_type": j.get("employmentType")})
    elif ats == "workable":
        r = get("GET", f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
        r.raise_for_status()
        for j in r.json()["jobs"]:
            places = [", ".join(filter(None, (x.get("city"), x.get("region"), x.get("country")))) for x in j.get("locations") or []]
            rows.append({"title": j["title"], "source_url": j.get("shortlink") or j["url"], "description": html_text(j.get("description")),
                         "posted_at": day(j.get("published_on") or j.get("created_at")),
                         "locations": places or [", ".join(filter(None, (j.get("city"), j.get("country"))))],
                         "remote_policy": "remote" if j.get("telecommuting") else None, "employment_type": j.get("employment_type")})
    elif ats == "smartrecruiters":
        offset, total = 0, None
        while total is None or offset < total:
            cancellation.check()
            r = get("GET", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
            r.raise_for_status()
            page = r.json()
            total = page.get("totalFound", 0)
            for j in page.get("content") or []:
                address = f"https://jobs.smartrecruiters.com/{slug}/{j['id']}"
                where = j.get("location") or {}
                row = {"title": j["name"], "source_url": address, "posted_at": day(j.get("releasedDate")),
                       "locations": [where.get("fullLocation") or ", ".join(filter(None, (where.get("city"), where.get("country"))))],
                       "remote_policy": "remote" if where.get("remote") else "hybrid" if where.get("hybrid") else None,
                       "employment_type": (j.get("typeOfEmployment") or {}).get("label")}
                if url(address) in known:
                    row.update(known[url(address)])
                elif cap is None or len(rows) < cap:
                    detail = get("GET", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j['id']}")
                    if detail.ok:
                        sections = ((detail.json().get("jobAd") or {}).get("sections") or {}).values()
                        row["description"] = "\n\n".join(f"{s.get('title', '')}\n{html_text(s.get('text'))}" for s in sections if s.get("text"))
                rows.append(row)
            offset += 100
            if not page.get("content") or (cap and len(rows) >= cap):
                break
    elif ats == "recruitee":
        r = get("GET", f"https://{slug}.recruitee.com/api/offers/")
        r.raise_for_status()
        for j in r.json()["offers"]:
            rows.append({"title": j["title"], "source_url": j["careers_url"],
                         "description": "\n\n".join(filter(None, (html_text(j.get("description")), html_text(j.get("requirements"))))),
                         "posted_at": day(j.get("published_at") or j.get("created_at")),
                         "locations": [j.get("location") or ", ".join(filter(None, (j.get("city"), j.get("country"))))],
                         "remote_policy": "remote" if j.get("remote") else "hybrid" if j.get("hybrid") else None,
                         "employment_type": j.get("employment_type_code")})
    elif ats == "personio":
        r = get("GET", f"https://{slug}.jobs.personio.de/xml?language=en")
        r.raise_for_status()
        for j in ElementTree.fromstring(r.content).iter("position"):
            text = "\n\n".join(f"{d.findtext('name') or ''}\n{html_text(d.findtext('value'))}" for d in j.iter("jobDescription"))
            places = [j.findtext("office"), *(o.text for o in j.iter("office") if o is not j.find("office"))]
            rows.append({"title": j.findtext("name"), "source_url": f"https://{slug}.jobs.personio.de/job/{j.findtext('id')}",
                         "description": text, "posted_at": day(j.findtext("createdAt")), "locations": places,
                         "employment_type": j.findtext("schedule") or j.findtext("employmentType")})
    elif ats == "teamtailor":
        r = get("GET", f"https://{slug}.teamtailor.com/jobs.rss")
        r.raise_for_status()
        namespace = {"tt": "https://teamtailor.com/locations"}
        for j in ElementTree.fromstring(r.content).iter("item"):
            places = [", ".join(filter(None, (p.findtext("tt:city", namespaces=namespace), p.findtext("tt:country", namespaces=namespace))))
                      for p in j.iterfind("tt:locations/tt:location", namespace)]
            remote = (j.findtext("remoteStatus") or "").lower()
            rows.append({"title": j.findtext("title"), "source_url": j.findtext("link"), "description": html_text(j.findtext("description")),
                         "posted_at": day(j.findtext("pubDate")), "locations": places,
                         "remote_policy": remote if remote in ("remote", "hybrid") else None})
    elif ats == "workday":
        tenant, instance, site = slug.split("/")
        base = f"https://{tenant}.{instance}.myworkdayjobs.com"
        offset, total = 0, None
        while total is None or offset < total:
            cancellation.check()
            r = get("POST", f"{base}/wday/cxs/{tenant}/{site}/jobs", json={"limit": 20, "offset": offset, "searchText": "", "appliedFacets": {}})
            r.raise_for_status()
            page = r.json()
            # Solo la prima pagina riporta il totale: le successive dicono 0.
            total = page.get("total") if total is None else total
            for j in page.get("jobPostings") or []:
                if not j.get("externalPath"):
                    continue
                address = f"{base}/{site}{j['externalPath']}"
                row = {"title": j["title"], "source_url": address, "locations": [j.get("locationsText")]}
                if url(address) in known:
                    row.update(known[url(address)])
                elif cap is None or len(rows) < cap:
                    detail = get("GET", f"{base}/wday/cxs/{tenant}/{site}{j['externalPath']}")
                    if detail.ok:
                        posting = detail.json().get("jobPostingInfo") or {}
                        row.update(description=html_text(posting.get("jobDescription")), posted_at=day(posting.get("startDate")),
                                   employment_type=posting.get("timeType"),
                                   locations=[posting.get("location"), *(posting.get("additionalLocations") or [])])
                rows.append(row)
            offset += 20
            if not page.get("jobPostings") or (cap and len(rows) >= cap):
                break
    else:
        raise ValueError(f"Unknown ATS {ats}")
    complete = cap is None or len(rows) < cap
    return rows[:cap] if cap else rows, complete


# --- Elenco delle bacheche ------------------------------------------------------------------------

def load(path):
    """The watchlist file, or an empty one."""
    if not path.exists():
        return {"boards": [], "checked": {}, "rejected": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, empty in (("boards", []), ("checked", {}), ("rejected", [])):
        data.setdefault(key, empty)
    return data


def rows(archive, source, cfg, limit=None):
    """Read every board of the watchlist; return common rows, errors and the companies read completely.

    Il nome e il sito dell'azienda vengono dall'archivio, non dalla bacheca: così gli annunci
    finiscono sulla stessa azienda di Tier A/B da cui la bacheca è stata trovata.
    """
    watch = load(ROOT / source["config"])
    companies = {r["id"]: (r["name"], r["website"]) for r in archive.db.execute("SELECT id,name,website FROM companies")}
    known = {}
    for address, posted, places, kind in archive.db.execute("""SELECT json_extract(data,'$.source_url'), json_extract(data,'$.posted_at'),
            json_extract(data,'$.locations'), json_extract(data,'$.employment_type') FROM opportunities
            WHERE coalesce(json_extract(data,'$.description'),'')!='' AND (json_extract(data,'$.source_url') LIKE '%myworkdayjobs.com%'
               OR json_extract(data,'$.source_url') LIKE '%smartrecruiters.com%')"""):
        saved = {"posted_at": posted, "locations": json.loads(places) if places else None, "employment_type": kind}
        known[url(address)] = {k: v for k, v in saved.items() if v}
    pace = Pace(source.get("request_interval_seconds", 0.2))
    timeout = cfg["timeout_seconds"]

    def read(board):
        """One board: its rows with the company fields, or the error that stopped it."""
        cancellation.check()
        name, website = companies.get(board.get("company_id"), (board.get("company"), ""))

        def get(method, address, **kwargs):
            pace(board["ats"])
            return call(method, address, timeout, **kwargs)
        try:
            found, complete = jobs(board, get, known, source.get("max_jobs_per_board"))
        except cancellation.Cancelled:
            raise
        except Exception as exc:
            logger.warning("ATS board %s/%s failed: %s", board["ats"], board["slug"], exc)
            return [], {"board": f"{board['ats']}/{board['slug']}", "company": name, "error": str(exc)[:300]}, None
        for row in found:
            row.update(company_name=name, website_url=website, locations=[p for p in row.get("locations") or [] if p])
        return found, None, name if complete else None

    collected, errors, answered = [], [], set()
    # ponytail: una bacheca di gruppo condivisa da più aziende (Hitachi Energy e Hitachi Rail) si
    # legge una volta, per la prima in elenco; per sceglierne un'altra basta riordinare il file.
    seen, boards = set(), []
    for board in watch["boards"]:
        if (board["ats"], board["slug"].lower()) not in seen:
            seen.add((board["ats"], board["slug"].lower()))
            boards.append(board)
    workers = source.get("workers", 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(boards), workers):
            for found, error, name in pool.map(read, boards[start:start + workers]):
                collected.extend(found)
                if error:
                    errors.append(error)
                elif name:
                    answered.add(name)
            logger.info("ATS: %s/%s boards read, %s ads", min(start + workers, len(boards)), len(boards), len(collected))
            if limit and len(collected) >= limit:
                break
    return collected[:limit] if limit else collected, errors, answered


def discover(archive, cfg, names=True, limit=None, force=False):
    """Find the board of each Tier A/B company not yet in the watchlist and save what was found.

    Ogni azienda viene provata una volta ogni `recheck_days`: le strade (annunci, sito, nome) sono
    le stesse finché l'archivio non cambia, e riprovarle ogni volta sarebbe solo traffico.
    """
    from jobhunter.evaluation.selection import verdicts
    source = cfg["sources"]["ats"]
    settings = source["discovery"]
    path = ROOT / source["config"]
    watch = load(path)
    states = verdicts(archive)
    listed = {b.get("company_id") for b in watch["boards"]}
    today = now()[:10]

    def done(cid):
        """What a previous check already covered: {"on": date, "names": bool}, or None."""
        record = watch["checked"].get(cid)
        record = {"on": record, "names": False} if isinstance(record, str) else record
        if not record or force or (datetime.fromisoformat(today) - datetime.fromisoformat(record["on"])).days >= settings["recheck_days"]:
            return None
        return record
    targets = [cid for cid, state in states.items() if state.get("tier") in settings["tiers"] and cid not in listed
               and (not done(cid) or names and not done(cid)["names"])]
    targets = targets[:limit] if limit else targets
    companies = {r["id"]: dict(r) for r in archive.db.execute("SELECT id,name,website FROM companies")}
    links = defaultdict(set)
    for row in archive.db.execute("""SELECT company_id, json_extract(data,'$.source_url'), json_extract(data,'$.application_url')
                                     FROM opportunities WHERE company_id IN (SELECT value FROM json_each(?))""", (json.dumps(targets),)):
        links[row[0]].update(filter(None, row[1:]))
    for row in archive.db.execute("""SELECT o.company_id, s.source_url FROM observations s JOIN opportunities o ON o.id=s.opportunity_id
                                     WHERE o.company_id IN (SELECT value FROM json_each(?))""", (json.dumps(targets),)):
        if row[1]:
            links[row[0]].add(row[1])
    pace = Pace(settings["probe_interval_seconds"])
    timeout = settings["timeout_seconds"]

    def get(method, address, key=None, **kwargs):
        pace(key or urlsplit(address).hostname)
        response = call(method, address, timeout, **kwargs)
        # Un 429 non dice che la bacheca non esiste: l'azienda va ricontrollata (Workable, 24/09/2026).
        if response.status_code == 429:
            raise RuntimeError(f"HTTP 429 from {urlsplit(address).hostname}")
        return response

    def one(cid):
        """Boards of one company: accepted, rejected with the name they declared, and how they were found."""
        cancellation.check()
        company = companies[cid]
        accepted, rejected, failed = [], [], []
        # Annunci e sito già letti in un giro precedente senza nomi: resta solo lo slug dal nome.
        names_only = bool(done(cid))

        def check(candidates, method, strict, attempts=3):
            for board in candidates:
                if any(b["ats"] == board["ats"] and b["slug"].lower() == board["slug"].lower() for b in accepted + rejected):
                    continue
                try:
                    declared = info(board, lambda m, a, **k: get(m, a, board["ats"], attempts=attempts, **k))
                except Exception as exc:
                    logger.debug("ATS check failed for %s %s: %s", board, company["name"], exc)
                    failed.append(board)
                    continue
                if declared is None:
                    continue
                # Un titolo fatto solo di contorno («Jobs at») non dice niente del nome.
                if not [w for w in words(declared) if w not in BOILERPLATE]:
                    declared = ""
                # Workday dichiara solo il tenant; una bacheca vuota non dichiara niente. Dagli
                # annunci basta che il nome non contraddica l'azienda; dal nome serve che coincida.
                unknown = declared == "" or board["ats"] == "workday" and not strict
                entry = {**board, "company_id": cid, "company": company["name"], "declared_name": declared, "found_by": method, "added": today}
                if unknown and not strict or same_company(company["name"], declared, exact=strict):
                    accepted.append(entry)
                elif not strict:
                    # Da annuncio o sito un altro nome merita uno sguardo; da uno slug indovinato è un omonimo.
                    rejected.append(entry)

        text = "" if names_only else " ".join(links[cid])
        check(detect(text), "annuncio", strict=False)
        for short in sorted(set(SHORT_LINKS.findall(text)))[:2]:
            try:
                r = get("GET", short)
                check(detect(" ".join([*(h.headers.get("location") or "" for h in r.history), r.url, r.text[:300_000]])), "annuncio", strict=False)
            except Exception as exc:
                logger.debug("Short link %s failed: %s", short, exc)
        if not accepted and company["website"] and not names_only:
            try:
                home = get("GET", company["website"], attempts=1)
                pages = [home.text]
                hrefs = re.findall(r'href=["\']([^"\'#]+)["\']', home.text)
                careers = [h for h in dict.fromkeys(hrefs) if CAREERS.search(h) and not detect(h)][:2]
                from urllib.parse import urljoin
                for href in careers:
                    try:
                        pages.append(get("GET", urljoin(home.url, href), attempts=1).text)
                    except Exception:
                        pass
                check(detect(" ".join(pages)), "sito", strict=False)
            except Exception as exc:
                logger.debug("Website %s failed: %s", company["website"], exc)
        if not accepted and names:
            candidates = [{"ats": ats, "slug": slug} for slug in guesses(company["name"])
                          for ats in GUESSABLE]
            # Greenhouse, Lever e Personio rispondono a uno slug inesistente dopo 4-8 secondi: senza
            # ritentativi un timeout costa 15 secondi, non un minuto, e l'azienda resta da ricontrollare.
            check(candidates, "nome", strict=True, attempts=1)
            guessed = [b for b in accepted if b["found_by"] == "nome"]
            if len({b["ats"] for b in guessed}) > 1:
                # Lo stesso nome su più ATS: quasi sempre omonimi (Voltus Inc. su Lever, Voltus GmbH su
                # Personio). Nessuna viene seguita; restano fra gli scarti da rivedere.
                accepted[:] = [b for b in accepted if b not in guessed]
                rejected.extend({**b, "ambiguous": True} for b in guessed)
        return cid, accepted, rejected, failed

    found = {"annuncio": 0, "sito": 0, "nome": 0}
    with ThreadPoolExecutor(max_workers=settings["workers"]) as pool:
        for index, (cid, accepted, rejected, failed) in enumerate(pool.map(one, targets), 1):
            watch["boards"].extend(accepted)
            watch["rejected"].extend(r for r in rejected if not any(x["ats"] == r["ats"] and x["slug"] == r["slug"] for x in watch["rejected"]))
            # Una bacheca che non ha risposto per un errore di rete non è una bacheca assente.
            if not failed or accepted:
                watch["checked"][cid] = {"on": today, "names": names}
            for board in accepted:
                found[board["found_by"]] += 1
            if index % 25 == 0 or index == len(targets):
                path.write_text(json.dumps(watch, ensure_ascii=False, indent=1), encoding="utf-8")
                logger.info("ATS discovery: %s/%s companies, boards %s", index, len(targets), found)
    path.write_text(json.dumps(watch, ensure_ascii=False, indent=1), encoding="utf-8")
    by_ats = defaultdict(int)
    for board in watch["boards"]:
        by_ats[board["ats"]] += 1
    return {"status": "success", "checked_companies": len(targets), "new_boards": found,
            "boards": len(watch["boards"]), "companies_with_board": len({b.get('company_id') for b in watch["boards"]}),
            "by_ats": dict(sorted(by_ats.items(), key=lambda x: -x[1])), "rejected": len(watch["rejected"]), "path": str(path)}
