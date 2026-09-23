"""Company archive, normalization and reversible decisions; no network or LLM calls."""

from __future__ import annotations

import ast
import json
import logging
import sqlite3
import threading
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from jobhunter.normalization import COMPANY_FIELDS, clean, identity, normalize, number, url  # noqa: F401

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
STATUSES = ("new", "review", "saved", "discarded", "contacted")
_SEARCH_INDEX_LOCK = threading.Lock()
# Il dettaglio di un'esecuzione conserva l'esito di ogni elemento, fino a qualche MB per riga.
# Gli elenchi della dashboard ne mostrano solo il numero: la riga completa resta nel database.
BRIEF_DETAIL = "json_remove(detail,'$.items') detail, json_array_length(detail,'$.items') items_omitted"


def now():
    """Return an ISO UTC observation timestamp."""
    return datetime.now(timezone.utc).isoformat()


def categories():
    """Read the shared sector vocabulary; the assignment itself lives in company_axis."""
    from jobhunter.evaluation.company_axis import vocabulary
    return vocabulary()


def settings(path=None):
    """Load the explicit application configuration without reading secrets."""
    return json.loads(Path(path or ROOT / "config/app.json").read_text(encoding="utf-8"))


def install_examples(root=ROOT):
    """Create each personal file that is still missing from its copy in `examples/`.

    Profilo, filtri, ricerche e domande di Jev appartengono a chi usa il tool e non sono
    versionati: `examples/` ne contiene una versione di esempio con gli stessi percorsi.
    Un file esistente non viene mai sovrascritto. Restituisce i percorsi creati.
    """
    root = Path(root)
    created = []
    for source in sorted((root / "examples").rglob("*")):
        target = root / source.relative_to(root / "examples")
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            created.append(target.relative_to(root).as_posix())
    if created:
        logger.info("Created %d personal files from examples/", len(created))
    return created


def search_rules_hash(rules):
    """Invalidate decisions only for changed rules or actual extraction functions."""
    selected = {
        'evaluation/selection.py': {'evaluate', 'requirements'},
        'evaluation/languages.py': {'language_requirements', 'detect_language'},
        'evaluation/enrichment.py': {'lines', 'TextExtractor'},
    }
    stable_imports = {
        'jobhunter.evaluation.enrichment': 'jobhunter.enrichment',
        'jobhunter.evaluation.languages': 'jobhunter.languages',
    }
    code = []
    for name, functions in selected.items():
        tree = ast.parse((ROOT / 'jobhunter' / name).read_text(encoding='utf-8'))
        for node in tree.body:
            if getattr(node, 'name', None) not in functions:
                continue
            for nested in ast.walk(node):
                if isinstance(nested, ast.ImportFrom):
                    nested.module = stable_imports.get(nested.module, nested.module)
            code.append(ast.dump(node))
    return identity(json.dumps(rules, sort_keys=True) + ''.join(code))


class Archive:
    """Transactional local archive shared by the CLI, Codex and dashboard."""

    def __init__(self, path):
        """Open the archive and initialize missing tables without touching unrelated files."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
          website TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', sectors TEXT NOT NULL DEFAULT '',
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, description_provenance TEXT NOT NULL DEFAULT '{}');
        CREATE INDEX IF NOT EXISTS company_names ON companies(name_key);
        CREATE TABLE IF NOT EXISTS opportunities(id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id),
          data TEXT NOT NULL, content_hash TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(opportunity_id TEXT REFERENCES opportunities(id), source TEXT,
          source_url TEXT, observed_at TEXT, PRIMARY KEY(opportunity_id, source, source_url));
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT REFERENCES companies(id),
          opportunity_id TEXT REFERENCES opportunities(id), status TEXT NOT NULL, note TEXT NOT NULL,
          created_at TEXT NOT NULL, undone_at TEXT);
        CREATE TABLE IF NOT EXISTS assessments(company_id TEXT PRIMARY KEY REFERENCES companies(id), data TEXT NOT NULL,
          created_at TEXT NOT NULL, basis TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, company_id TEXT REFERENCES companies(id),
          source_url TEXT NOT NULL, note TEXT NOT NULL, observed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
          status TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preferences(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS opportunity_company ON opportunities(company_id);
        CREATE TABLE IF NOT EXISTS search_eligibility(opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id) ON DELETE CASCADE,
          content_hash TEXT NOT NULL, rules_hash TEXT NOT NULL, status TEXT NOT NULL, decision TEXT);
        CREATE TABLE IF NOT EXISTS pipeline_updates(step TEXT PRIMARY KEY, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pipeline_jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, step TEXT NOT NULL,
          status TEXT NOT NULL, parameters TEXT NOT NULL, basis TEXT NOT NULL, pid INTEGER NOT NULL,
          started_at TEXT NOT NULL, finished_at TEXT, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pipeline_cancellations(job_id INTEGER PRIMARY KEY REFERENCES pipeline_jobs(id), requested_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS feedback_company ON feedback(company_id,id);
        CREATE TABLE IF NOT EXISTS categories(company_id TEXT NOT NULL REFERENCES companies(id),
          category TEXT NOT NULL, rank INTEGER NOT NULL, confidence REAL,
          method TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(company_id,category), UNIQUE(company_id,rank));
        CREATE TABLE IF NOT EXISTS enrichments(task TEXT NOT NULL, record_id TEXT NOT NULL, source_hash TEXT NOT NULL,
          cache_key TEXT NOT NULL, data TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(task,record_id));
        CREATE TABLE IF NOT EXISTS feedback_detail(event_id INTEGER PRIMARY KEY REFERENCES feedback(id), reason TEXT NOT NULL,
          until_date TEXT, role_snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS personal_queue(company_id TEXT PRIMARY KEY REFERENCES companies(id), priority REAL NOT NULL,
          payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS impressions(company_id TEXT PRIMARY KEY REFERENCES companies(id), first_shown TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preference_rules(id TEXT PRIMARY KEY, category TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS description_attempts(opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id) ON DELETE CASCADE,
          status TEXT NOT NULL, checked_at TEXT NOT NULL, last_success_at TEXT, retry_after TEXT,
          parser_version TEXT NOT NULL, source_url TEXT NOT NULL, error TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS places(opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
          raw TEXT NOT NULL, country TEXT NOT NULL, city TEXT NOT NULL, to_map INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(opportunity_id,raw));
        CREATE INDEX IF NOT EXISTS places_where ON places(country,city);
        CREATE TABLE IF NOT EXISTS place_index(opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id) ON DELETE CASCADE,
          content_hash TEXT NOT NULL, mapping_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS company_profile_attempts(company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
          strategy TEXT NOT NULL, status TEXT NOT NULL, url TEXT NOT NULL, found TEXT NOT NULL,
          detail TEXT NOT NULL, checked_at TEXT NOT NULL, retry_after TEXT, PRIMARY KEY(company_id,strategy));
        """)
        if 'to_map' not in {r[1] for r in self.db.execute('PRAGMA table_info(places)')}:
            # Quali località aspettano ancora una città nella mappa: prima non lo distinguevamo
            # da «questo annuncio dichiara solo il paese», e il report era illeggibile.
            self.db.execute('ALTER TABLE places ADD COLUMN to_map INTEGER NOT NULL DEFAULT 0')
        if 'decision' not in {r[1] for r in self.db.execute('PRAGMA table_info(search_eligibility)')}:
            self.db.execute('ALTER TABLE search_eligibility ADD COLUMN decision TEXT')
        if 'description_provenance' not in {r[1] for r in self.db.execute('PRAGMA table_info(companies)')}:
            # Quale leva ha prodotto il «chi siamo» e quando: le quattro non hanno la stessa affidabilità.
            self.db.execute("ALTER TABLE companies ADD COLUMN description_provenance TEXT NOT NULL DEFAULT '{}'")
        category_columns = {r[1] for r in self.db.execute('PRAGMA table_info(categories)')}
        if 'rank' not in category_columns:
            # La prima versione ammetteva una sola categoria per azienda. La migrazione conserva
            # ogni assegnazione e la rende la prima etichetta dell'insieme multi-categoria.
            with self.db:
                self.db.execute('ALTER TABLE categories RENAME TO categories_single')
                self.db.execute('''CREATE TABLE categories(
                    company_id TEXT NOT NULL REFERENCES companies(id), category TEXT NOT NULL,
                    rank INTEGER NOT NULL, confidence REAL, method TEXT NOT NULL,
                    reason TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(company_id,category), UNIQUE(company_id,rank))''')
                self.db.execute('''INSERT INTO categories(company_id,category,rank,confidence,method,reason,updated_at)
                    SELECT company_id,category,1,NULL,method,reason,updated_at FROM categories_single
                    WHERE category!='Da classificare' ''')
                self.db.execute('DROP TABLE categories_single')

    def close(self):
        """Release the database connection."""
        self.db.close()

    def reset_collection(self):
        """Back up SQLite consistently, then remove acquired data except personally referenced records."""
        backup = self.path.parent / "backups" / (now().replace(":", "").replace("+", "_") + "-before-rebuild.sqlite3")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(backup)) as destination:
            self.db.backup(destination)
        with self.db:
            self.db.execute("DELETE FROM personal_queue")
            self.db.execute("DELETE FROM enrichments")
            self.db.execute("DELETE FROM categories WHERE method!='chat'")
            self.db.execute("DELETE FROM observations WHERE opportunity_id NOT IN (SELECT opportunity_id FROM feedback WHERE opportunity_id IS NOT NULL)")
            self.db.execute("DELETE FROM opportunities WHERE id NOT IN (SELECT opportunity_id FROM feedback WHERE opportunity_id IS NOT NULL) AND company_id NOT IN (SELECT company_id FROM assessments)")
            # Assessments may refer to roles by ID inside their structured JSON.
            protected = "SELECT company_id FROM feedback UNION SELECT company_id FROM assessments UNION SELECT company_id FROM evidence UNION SELECT company_id FROM opportunities UNION SELECT company_id FROM categories WHERE method='chat'"
            self.db.execute(f"DELETE FROM impressions WHERE company_id NOT IN ({protected})")
            self.db.execute(f"DELETE FROM companies WHERE id NOT IN ({protected})")
        report = {"backup": str(backup), "retained_companies": self.db.execute("SELECT count(*) FROM companies").fetchone()[0], "retained_opportunities": self.db.execute("SELECT count(*) FROM opportunities").fetchone()[0]}
        self.run("collection_reset", "success", report)
        logger.info("Collection reset: %s", report)
        return report

    def company(self, name, website="", observed=None):
        """Resolve a name conservatively, keeping known conflicting domains separate."""
        key = unicodedata.normalize("NFKC", clean(name)).casefold()
        if not key:
            raise ValueError("Company name is required")
        website = url(website)
        rows = self.db.execute("SELECT * FROM companies WHERE name_key=?", (key,)).fetchall()
        host = lambda x: (urlsplit(x).hostname or "").removeprefix("www.")
        matches = [r for r in rows if not website or not r["website"] or host(r["website"]) == host(website)]
        if len(matches) == 1:
            cid = matches[0]["id"]
            if website and not matches[0]["website"]:
                self.db.execute("UPDATE companies SET website=? WHERE id=?", (website, cid))
            return cid
        cid = identity(key + "|" + host(website))
        stamp = observed or now()
        self.db.execute("INSERT OR IGNORE INTO companies(id,name,name_key,website,description,sectors,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?)",
                        (cid, clean(name), key, website, "", "", stamp, stamp))
        return cid

    def ingest(self, rows, source, observed=None):
        """Import source records transactionally; return counts and rejected row reasons."""
        stamp = observed or now()
        result = {"received": len(rows), "inserted": 0, "updated": 0, "unchanged": 0, "rejected": []}
        affected = set()
        with self.db:
            for index, raw in enumerate(rows):
                try:
                    item = normalize(raw, source)
                except (ValueError, TypeError) as exc:
                    result["rejected"].append({"row": index + 1, "reason": str(exc)})
                    continue
                cid = self.company(item["company_name"], item["website_url"], stamp)
                affected.add(cid)
                oid = identity(cid + "|" + item["application_url"])
                company_fields = {k: item.pop(k) for k in COMPANY_FIELDS}
                encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
                digest = identity(encoded)
                old = self.db.execute("SELECT * FROM opportunities WHERE id=?", (oid,)).fetchone()
                if old and old["last_seen"] > stamp:
                    item = json.loads(old["data"])
                    encoded, digest = old["data"], old["content_hash"]
                elif old:
                    # Keep richer content when another board only provides a listing stub.
                    prior = json.loads(old["data"])
                    if prior.get("description") and (not item["description"] or len(prior["description"]) > len(item["description"]) and prior["source"] != source):
                        item["description"] = prior["description"]
                        if prior.get("description_provenance"):
                            item["description_provenance"] = prior["description_provenance"]
                    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
                    digest = identity(encoded)
                result["inserted" if old is None else "unchanged" if old["content_hash"] == digest else "updated"] += 1
                self.db.execute("INSERT INTO opportunities VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, content_hash=excluded.content_hash, last_seen=MAX(opportunities.last_seen,excluded.last_seen)",
                                (oid, cid, encoded, digest, stamp, stamp))
                self.db.execute("INSERT INTO observations VALUES(?,?,?,?) ON CONFLICT DO UPDATE SET observed_at=MAX(observations.observed_at,excluded.observed_at)", (oid, source, normalize(raw, source)["source_url"], stamp))
                if normalize(raw, source)["description"]:
                    self.record_description_attempt(oid, "available", item["source_url"], observed=stamp)
                listing = json.dumps({"method": "listing", "extracted_at": stamp}) if company_fields["company_description"] else ""
                self.db.execute("""UPDATE companies SET description=CASE WHEN ?!='' THEN ? ELSE description END,
                    description_provenance=CASE WHEN ?!='' THEN ? ELSE description_provenance END,
                    sectors=CASE WHEN ?!='' THEN ? ELSE sectors END, last_seen=MAX(last_seen,?) WHERE id=?""",
                                (company_fields["company_description"], company_fields["company_description"], listing, listing,
                                 company_fields["sectors"], company_fields["sectors"], stamp, cid))
        logger.info("Imported %s: %s records, %s rejected", source, len(rows), len(result["rejected"]))
        return result

    def save_description(self, oid, description, provenance, replace=False):
        """Fill an empty description, invalidating derived content without renewing listing dates."""
        from jobhunter.acquisition.descriptions import has_description
        row = self.db.execute("SELECT data FROM opportunities WHERE id=?", (oid,)).fetchone()
        if not row or not description.strip():
            raise ValueError("Known opportunity and nonempty description required")
        job = json.loads(row[0])
        if has_description(job.get("description")) and not replace:
            return False
        job.update(description=description, description_provenance=provenance)
        encoded = json.dumps(job, ensure_ascii=False, sort_keys=True)
        with self.db:
            self.db.execute("UPDATE opportunities SET data=?,content_hash=? WHERE id=?", (encoded, identity(encoded), oid))
        logger.info("Saved recovered description for %s", oid)
        return True

    def record_profile_attempt(self, cid, strategy, status, url='', found='', detail='', retry_after=None):
        """Tenere traccia di ogni strada provata su un'azienda, non solo di quella che ha funzionato.

        Una riga per azienda e per strategia, sovrascritta a ogni nuovo tentativo: dice cosa
        abbiamo provato, quando, com'e' andata e che testo grezzo ne e' uscito. Senza, una
        descrizione mancante non si distingue da una mai cercata.
        """
        self.db.execute("""INSERT INTO company_profile_attempts VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(company_id,strategy) DO UPDATE SET status=excluded.status,url=excluded.url,
            found=excluded.found,detail=excluded.detail,checked_at=excluded.checked_at,retry_after=excluded.retry_after""",
                        (cid, strategy, status, url, found, detail, now(), retry_after))

    def profile_attempts(self, cid):
        """Le strade provate su un'azienda, dalla piu' recente."""
        return [dict(r) for r in self.db.execute(
            'SELECT strategy,status,url,detail,checked_at,retry_after,length(found) found_chars '
            'FROM company_profile_attempts WHERE company_id=? ORDER BY checked_at DESC', (cid,))]

    def record_description_attempt(self, oid, status, source_url, error="", retry_after=None, observed=None, parser_version="source"):
        """Remember fetch outcomes without claiming that a missing page means a closed position."""
        stamp = observed or now()
        self.db.execute("INSERT INTO description_attempts VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(opportunity_id) DO UPDATE SET status=excluded.status,checked_at=excluded.checked_at,last_success_at=COALESCE(excluded.last_success_at,description_attempts.last_success_at),retry_after=excluded.retry_after,parser_version=excluded.parser_version,source_url=excluded.source_url,error=excluded.error",
                        (oid, status, stamp, stamp if status == "available" else None, retry_after, parser_version, source_url, error))

    def refresh_search_eligibility(self, cid=None):
        """Cache role statuses, optionally limiting validation to selected companies."""
        from jobhunter.evaluation.selection import evaluate, filters
        rules = filters()
        rules_hash = search_rules_hash(rules)
        scope, arguments = "", [rules_hash]
        if cid is not None:
            if isinstance(cid, str):
                scope = " AND o.company_id=?"
                arguments.append(cid)
            else:
                company_ids = sorted(cid)
                if not company_ids:
                    return
                scope = " AND o.company_id IN (SELECT value FROM json_each(?))"
                arguments.append(json.dumps(company_ids))
        # Serialize cache rebuilds across dashboard requests; unchanged reads stay inexpensive.
        with _SEARCH_INDEX_LOCK:
            rows = self.db.execute("""SELECT o.id,o.content_hash FROM opportunities o
                LEFT JOIN search_eligibility e ON e.opportunity_id=o.id
                WHERE (e.decision IS NULL OR e.content_hash!=o.content_hash OR e.rules_hash!=?)"""
                + scope, arguments).fetchall()
            updates = []
            for row in rows:
                data = self.db.execute('SELECT data FROM opportunities WHERE id=? AND content_hash=?', (row['id'], row['content_hash'])).fetchone()
                if data is None:
                    continue
                decision = evaluate(json.loads(data[0]), rules)
                updates.append((rules_hash, decision['status'], json.dumps(decision), row['id'], row['content_hash']))
            if updates:
                with self.db:
                    self.db.executemany('''INSERT OR REPLACE INTO search_eligibility
                        (opportunity_id,content_hash,rules_hash,status,decision)
                        SELECT id,content_hash,?,?,? FROM opportunities WHERE id=? AND content_hash=?''', updates)
                    self.db.execute("INSERT OR REPLACE INTO pipeline_updates VALUES('filters',?)", (now(),))

    def evaluations(self, cid=None):
        """Reuse full persisted decisions for queue, review, details and role snapshots.

        `cid` accepts one company, a sequence of companies, or None for the whole archive. The
        refresh validates the same scope, so a page read does not walk unrelated opportunities.
        """
        self.refresh_search_eligibility(cid)
        sql = 'SELECT e.opportunity_id,e.decision FROM search_eligibility e JOIN opportunities o ON o.id=e.opportunity_id'
        if cid is None:
            rows = self.db.execute(sql)
        elif isinstance(cid, str):
            rows = self.db.execute(sql + ' WHERE o.company_id=?', (cid,))
        else:
            rows = self.db.execute(sql + ' WHERE o.company_id IN (SELECT value FROM json_each(?))', (json.dumps(sorted(cid)),))
        return {r[0]: json.loads(r[1]) for r in rows}

    # L'ordinamento vive in SQL, non sulla pagina: ordinare i trenta risultati gia' scelti darebbe
    # una classifica diversa a ogni pagina. Il tier non c'e': non e' salvato, si calcola in lettura.
    SORTS = {"recenti": "c.last_seen DESC", "nome": "c.name COLLATE NOCASE",
             "ruoli": "matching_roles DESC", "categoria": "category COLLATE NOCASE"}

    def search(self, query="", status="", source="", location="", limit=30, offset=0, category="", eligibility="", tier="", sort="recenti", country="", city=""):
        """Paginate companies, requiring role filters to match the same saved opportunity."""
        if status and status not in STATUSES:
            raise ValueError("Unknown status")
        if sort not in self.SORTS:
            raise ValueError("Unknown sort order")
        conditions, args = [], []
        qualifying = None
        if tier:
            from jobhunter.evaluation import tier as tiers
            from jobhunter.evaluation.selection import verdicts
            if tier not in tiers.TIERS:
                raise ValueError("Unknown tier")
            # Il tier non è in SQL perché non si salva mai: si calcola e si filtra sugli ID risultanti.
            assessment = verdicts(self)
            chosen = sorted(cid for cid, state in assessment.items() if state["tier"] == tier)
            conditions.append("c.id IN (SELECT value FROM json_each(?))")
            args.append(json.dumps(chosen))
            # Tier A e B·esperienza esistono *grazie a* certi annunci: gli altri filtri devono
            # incrociarsi su quegli stessi annunci, altrimenti «Tier A a Milano» restituisce
            # un'azienda che è Tier A per un ruolo a Londra e ha un ruolo scartato a Milano.
            # Gli altri tre tier sono definiti dall'assenza di un ruolo compatibile: lì non
            # esiste un annuncio che li giustifichi, e i filtri restano su tutti gli annunci.
            if tier in ("A", "B-esperienza"):
                qualifying = sorted(oid for cid in chosen for oid, verdict in assessment[cid]["ruoli"].items()
                                    if verdict["verdetto"] == tiers.KEEP
                                    and (tier != "B-esperienza" or verdict.get("primary")))
        role_conditions, role_args = [], []
        if qualifying is not None:
            role_conditions.append("o.id IN (SELECT value FROM json_each(?))")
            role_args.append(json.dumps(qualifying))
        if eligibility:
            if eligibility not in ("potential", "review", "excluded"):
                raise ValueError("Unknown eligibility scope")
            self.refresh_search_eligibility()
            role_conditions.append("EXISTS(SELECT 1 FROM search_eligibility e WHERE e.opportunity_id=o.id AND e.status=?)")
            role_args.append(eligibility)
        if location.strip():
            role_conditions.append("json_extract(o.data,'$.locations') LIKE ? ESCAPE '\\'")
            role_args.append('%' + location.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        if country or city:
            # Paese e città sono quelli canonici: «Milano», «Milan» e «MI» sono lo stesso posto,
            # e il confronto avviene sulla mappatura salvata, non sul testo dell'annuncio.
            from jobhunter.exploration import places
            places.refresh(self)
            clause = "EXISTS(SELECT 1 FROM places p WHERE p.opportunity_id=o.id"
            for column, value in (("country", country), ("city", city)):
                if value:
                    clause += f" AND p.{column}=?"
                    role_args.append(value)
            role_conditions.append(clause + ")")
        if source:
            role_conditions.append("EXISTS(SELECT 1 FROM observations s WHERE s.opportunity_id=o.id AND s.source=?)")
            role_args.append(source)
        role_where = ' AND ' + ' AND '.join(role_conditions) if role_conditions else ''
        if role_conditions:
            conditions.append('EXISTS(SELECT 1 FROM opportunities o WHERE o.company_id=c.id' + role_where + ')')
            args.extend(role_args)
        if category:
            if category == 'Da classificare':
                conditions.append("NOT EXISTS(SELECT 1 FROM categories WHERE company_id=c.id)")
            else:
                conditions.append("EXISTS(SELECT 1 FROM categories WHERE company_id=c.id AND category=?)")
                args.append(category)
        if query.strip():
            conditions.append("(c.name || ' ' || c.description || ' ' || c.sectors LIKE ? OR EXISTS(SELECT 1 FROM opportunities o WHERE o.company_id=c.id AND o.data LIKE ?" + role_where + '))')
            args.extend(['%' + query.strip() + '%'] * 2 + role_args)
        status_sql = "COALESCE((SELECT f.status FROM feedback f WHERE f.company_id=c.id AND f.opportunity_id IS NULL AND f.undone_at IS NULL ORDER BY f.id DESC LIMIT 1),'new')"
        if status:
            conditions.append(status_sql + "=?")
            args.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        total = self.db.execute("SELECT count(*) FROM companies c" + where, args).fetchone()[0]
        # I ruoli che passano i filtri si contano gia' qui: servono a ordinare tutto l'insieme, non la pagina.
        matching_count = "(SELECT count(*) FROM opportunities o WHERE o.company_id=c.id" + role_where + ")"
        count_args = list(role_args)
        if query.strip():
            # Come matching_ids: se il testo compare nei ruoli, contare solo quelli.
            # Se compare soltanto nell'azienda, mantenere tutti i ruoli filtrati.
            text_count = "(SELECT count(*) FROM opportunities o WHERE o.company_id=c.id AND o.data LIKE ?" + role_where + ")"
            matching_count = "COALESCE(NULLIF(" + text_count + ",0)," + matching_count + ")"
            count_args = ['%' + query.strip() + '%', *role_args, *role_args]
        selected = ("SELECT c.*, " + status_sql + " AS status, "
                    "(SELECT count(*) FROM opportunities o WHERE o.company_id=c.id) AS opportunity_count, "
                    + matching_count + " AS matching_roles, "
                    "COALESCE((SELECT category FROM categories WHERE company_id=c.id ORDER BY rank LIMIT 1),'Da classificare') AS category")
        sql = selected + " FROM companies c" + where + " ORDER BY " + self.SORTS[sort] + ",c.name COLLATE NOCASE,c.id LIMIT ? OFFSET ?"
        items = [dict(r) for r in self.db.execute(sql, count_args + args + [min(max(int(limit), 1), 500), max(int(offset), 0)])]
        from jobhunter.evaluation import tier as tiers
        from jobhunter.evaluation.selection import verdicts
        if not tier:
            assessment = verdicts(self, [item["id"] for item in items]) if items else {}
        text = query.strip().casefold()
        for item in items:
            item.update(self.category(item["id"]))
            state = assessment[item["id"]]
            item.update(tier=state["tier"], tier_label=tiers.label(state["tier"]), company_verdict=state["azienda"])
            rows = self.db.execute("SELECT o.id,o.data FROM opportunities o WHERE o.company_id=?" + role_where, [item["id"], *role_args]).fetchall()
            # Chiedere un tier e vedersi elencare i ruoli scartati dell'azienda e' rumore: il filtro
            # sceglie le aziende, ma i titoli mostrati devono essere quelli che quel tier riguarda.
            # Eccezione: «B-attesa» significa gia' «nessun ruolo adatto ora». Nascondere gli scartati
            # lascerebbe la scheda vuota proprio dove sono l'unica cosa che l'azienda ha.
            hide_dropped = bool(tier) and tier != 'B-attesa'
            kept = [r for r in rows if not hide_dropped or state["ruoli"].get(r["id"], {}).get("verdetto") != tiers.DROP]
            filtered = bool(role_conditions) or hide_dropped
            # Il testo cercato puo' stare nell'azienda o nell'annuncio. Se qualche annuncio lo contiene
            # sono quelli i ruoli cercati; se nessuno lo contiene l'azienda e' entrata per nome o
            # descrizione, e allora valgono tutti i suoi ruoli.
            wanted = [r for r in kept if text in r["data"].casefold()] if text else []
            if wanted:
                kept, filtered = wanted, True
            jobs = [json.loads(r["data"]) for r in kept]
            # Quali ruoli rispondono ai filtri: la scheda mostra prima questi e tiene gli altri da parte.
            item["matching_ids"] = [r["id"] for r in kept]
            if filtered:
                item["archive_opportunity_count"] = item["opportunity_count"]
                item["opportunity_count"] = len(jobs)
            # La lista mostra la forma canonica quando la mappa la conosce: il testo grezzo della
            # fonte resta nella scheda, dove serve come prova.
            from jobhunter.exploration import places
            item["places"] = places.labels(self, item["matching_ids"])
            item["locations"] = sorted({x for j in jobs for x in j["locations"]})
            item["titles"] = list(dict.fromkeys(j["title"] for j in jobs))[:5]
        return {"total": total, "items": items}

    def category(self, cid):
        """Expose an assignment together with its provenance and explanation."""
        from jobhunter.evaluation.company_axis import category
        return category(self, cid)

    def categorize(self, cid=None, category=None, reason="", company_ids=None):
        """Refresh rule suggestions or save a chat classification that imports preserve."""
        from jobhunter.evaluation.company_axis import categorize
        return categorize(self, cid, category, reason, company_ids)

    def basis(self, cid):
        """Fingerprint content and explicit preferences for assessment freshness."""
        hashes = [r[0] for r in self.db.execute("SELECT content_hash FROM opportunities WHERE company_id=? ORDER BY id", (cid,))]
        hashes += [str(r[0]) for r in self.db.execute("SELECT id FROM preferences ORDER BY id")]
        hashes += [r[0] for r in self.db.execute("SELECT id FROM evidence WHERE company_id=? ORDER BY id", (cid,))]
        for path in (ROOT / "user_context/llm-selection-profile.md", ROOT / "config/role_filters.json"):
            hashes.append(identity(path.read_text(encoding="utf-8")))
        return identity("|".join(hashes))

    def require_company(self, cid):
        """Fail like show() does for an unknown company, without assembling its whole detail."""
        if not self.db.execute("SELECT 1 FROM companies WHERE id=?", (cid,)).fetchone():
            raise ValueError("Company not found")

    def show(self, cid):
        """Return a company with opportunities, provenance, evidence and feedback."""
        company = self.db.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
        if company is None:
            raise ValueError("Company not found")
        result = dict(company)
        result.update(self.category(cid))
        result["opportunities"] = []
        decisions = self.evaluations(cid)
        for row in self.db.execute("SELECT * FROM opportunities WHERE company_id=? ORDER BY last_seen DESC,id", (cid,)):
            job = json.loads(row["data"])
            job["selection"] = decisions[row['id']]
            check = self.db.execute("SELECT status,checked_at,last_success_at,retry_after FROM description_attempts WHERE opportunity_id=?", (row["id"],)).fetchone()
            job["description_check"] = dict(check) if check else None
            enriched = self.db.execute("SELECT * FROM enrichments WHERE task='description' AND record_id=?", (row["id"],)).fetchone()
            if enriched and enriched["source_hash"] == identity(json.dumps(job["description"], sort_keys=True, ensure_ascii=False)):
                job["formatted_description"] = json.loads(enriched["data"])["text"]
                job["formatting_model"] = enriched["model"]
            job.update(id=row["id"], first_seen_at=row["first_seen"], last_seen_at=row["last_seen"])
            job["sources"] = [dict(x) for x in self.db.execute("SELECT source,source_url,observed_at FROM observations WHERE opportunity_id=?", (row["id"],))]
            result["opportunities"].append(job)
        result["feedback"] = [dict(r) for r in self.db.execute("SELECT f.*,d.reason,d.until_date FROM feedback f LEFT JOIN feedback_detail d ON d.event_id=f.id WHERE f.company_id=? ORDER BY f.id DESC", (cid,))]
        result["status"] = next((f["status"] for f in result["feedback"] if not f["undone_at"] and not f["opportunity_id"]), "new")
        result["evidence"] = [dict(r) for r in self.db.execute("SELECT * FROM evidence WHERE company_id=? ORDER BY observed_at DESC", (cid,))]
        assessment = self.db.execute("SELECT * FROM assessments WHERE company_id=?", (cid,)).fetchone()
        result["assessment"] = None if not assessment else {**json.loads(assessment["data"]), "created_at": assessment["created_at"], "stale": assessment["basis"] != self.basis(cid)}
        # I due verdetti d'asse e il tier che ne discende, calcolati adesso e mai salvati (DESIGN §2).
        from jobhunter.evaluation import tier
        from jobhunter.evaluation.selection import verdicts
        state = verdicts(self, cid)[cid]
        result.update(company_verdict=state["azienda"], tier=state["tier"], tier_label=tier.label(state["tier"]))
        for job in result["opportunities"]:
            job["verdict"] = state["ruoli"].get(job["id"], {})
        from jobhunter.evaluation.remote_llm import presentation
        presentation(self, result)
        return result

    def feedback(self, cid, status, note="", opportunity_id=None, reason="other", until_date=None):
        """Record an explicit company or opportunity decision; identical retries are harmless."""
        self.require_company(cid)
        from jobhunter.evaluation.selection import feedback_reasons, role_snapshot
        snapshot = json.dumps(role_snapshot(self, cid))
        if reason not in feedback_reasons():
            raise ValueError("Unknown feedback reason")
        if reason in ("company_not_interested", "no_current_roles") and opportunity_id:
            raise ValueError("This reason applies to the company, not a single role")
        if reason == "company_not_interested" and status != "discarded":
            raise ValueError("A company rejection requires discarded status")
        if reason == "no_current_roles" and status not in ("review", "discarded"):
            raise ValueError("No current roles requires review or discarded status")
        if until_date:
            from datetime import date
            if date.fromisoformat(until_date) <= date.today():
                raise ValueError("Choose a future reminder date")
        if reason == "not_now" and not until_date:
            raise ValueError("A reminder date is required for not_now")
        if status not in STATUSES or not isinstance(note, str) or len(note) > 20000:
            raise ValueError("Invalid status or note")
        if opportunity_id and not self.db.execute("SELECT 1 FROM opportunities WHERE id=? AND company_id=?", (opportunity_id, cid)).fetchone():
            raise ValueError("Opportunity does not belong to company")
        with self.db:
            previous = self.db.execute("SELECT * FROM feedback WHERE company_id=? AND opportunity_id IS ? AND undone_at IS NULL ORDER BY id DESC LIMIT 1", (cid, opportunity_id)).fetchone()
            detail = self.db.execute("SELECT reason,until_date,role_snapshot FROM feedback_detail WHERE event_id=?", (previous["id"],)).fetchone() if previous else None
            if previous and previous["status"] == status and previous["note"] == note and (detail is None and reason == "other" and not until_date or detail is not None and detail["reason"] == reason and detail["until_date"] == until_date and detail["role_snapshot"] == snapshot):
                return {"event_id": previous["id"], "duplicate": True}
            cursor = self.db.execute("INSERT INTO feedback(company_id,opportunity_id,status,note,created_at) VALUES(?,?,?,?,?)", (cid, opportunity_id, status, note, now()))
            self.db.execute("INSERT INTO feedback_detail VALUES(?,?,?,?)", (cursor.lastrowid, reason, until_date, snapshot))
        return {"event_id": cursor.lastrowid}

    def undo(self, event_id):
        """Undo a specific decision without deleting the audit record."""
        with self.db:
            cursor = self.db.execute("UPDATE feedback SET undone_at=COALESCE(undone_at,?) WHERE id=?", (now(), event_id))
            if not cursor.rowcount:
                raise ValueError("Feedback event not found")
        return {"undone": event_id}

    def assess(self, cid, data):
        """Persist a structured chat assessment; compatible with a future API producer."""
        self.require_company(cid)
        if not isinstance(data, dict) or not isinstance(data.get("reasoning"), str) or not data["reasoning"].strip():
            raise ValueError("Assessment requires reasoning text")
        for key in ("missing_information", "relevant_opportunity_ids"):
            if key in data and (not isinstance(data[key], list) or not all(isinstance(v, str) for v in data[key])):
                raise ValueError(key + " must be a list of strings")
        valid_ids = {r[0] for r in self.db.execute("SELECT id FROM opportunities WHERE company_id=?", (cid,))}
        if not set(data.get("relevant_opportunity_ids", [])) <= valid_ids:
            raise ValueError("Assessment refers to unknown opportunities")
        with self.db:
            self.db.execute("INSERT INTO assessments VALUES(?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET data=excluded.data,created_at=excluded.created_at,basis=excluded.basis", (cid, json.dumps(data, ensure_ascii=False, allow_nan=False), now(), self.basis(cid)))
        return {"assessed": cid}

    def add_evidence(self, cid, source_url, note):
        """Add attributed research to an existing company, idempotently."""
        self.require_company(cid)
        link = url(source_url)
        if not link or not clean(note):
            raise ValueError("Evidence requires an HTTP URL and a note")
        eid = identity(cid + link + note)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)", (eid, cid, link, note, now()))
        return {"evidence_id": eid}

    def preference(self, note):
        """Append an explicit preference without rewriting the user's original profile."""
        if not clean(note):
            raise ValueError("Preference cannot be empty")
        with self.db:
            self.db.execute("INSERT INTO preferences(note,created_at) VALUES(?,?)", (note, now()))
        return {"saved": True}

    def run(self, source, status, detail):
        """Store a collection outcome separately from successful observations."""
        with self.db:
            self.db.execute("INSERT INTO runs(source,status,detail,created_at) VALUES(?,?,?,?)", (source, status, json.dumps(detail, ensure_ascii=False), now()))

    def stats(self):
        """Return archive counts and recent collection health."""
        return {"companies": self.db.execute("SELECT count(*) FROM companies").fetchone()[0],
                "opportunities": self.db.execute("SELECT count(*) FROM opportunities").fetchone()[0],
                "first_import_at": self.db.execute("SELECT min(first_seen) FROM opportunities").fetchone()[0],
                "runs": [dict(r) for r in self.db.execute(f"SELECT id,source,status,created_at,{BRIEF_DETAIL} FROM runs ORDER BY id DESC LIMIT 30")]}
