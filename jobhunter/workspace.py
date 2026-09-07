"""Company archive, normalization and reversible decisions; no network or LLM calls."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
STATUSES = ("new", "review", "saved", "discarded", "contacted")


def now():
    """Return an ISO UTC observation timestamp."""
    return datetime.now(timezone.utc).isoformat()


def categories():
    """Read shared company-sector rules, independent of acquisition source."""
    return json.loads((ROOT / "config/categories.json").read_text(encoding="utf-8"))


def settings(path=None):
    """Load the explicit application configuration without reading secrets."""
    return json.loads(Path(path or ROOT / "config/app.json").read_text(encoding="utf-8"))


def clean(value):
    """Normalize display text while treating legacy sentinels as missing."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(filter(None, (clean(v) for v in value)))
    text = re.sub(r"\s+", " ", str(value)).strip()
    return "" if text.lower() in {"n/a", "none", "null", "nan"} else text


def url(value):
    """Accept a single HTTP URL and remove fragments and tracking parameters."""
    text = clean(value)
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or re.search(r"\s", text):
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "/", urlencode(query), ""))


def identity(text):
    """Create a stable compact ID from an identity key."""
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def number(value):
    """Parse a finite optional numeric salary without guessing units."""
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError):
        return None


def normalize(row, source):
    """Map a source row to an opportunity without flattening its relationships."""
    if not isinstance(row, dict):
        raise ValueError("Each record must be an object")
    name = clean(row.get("company_name") or row.get("company") or row.get("Company") or row.get("Organization Name"))
    title = clean(row.get("title") or row.get("Position Title") or row.get("Job Title"))
    link = url(row.get("source_url") or row.get("original_url") or row.get("job_url") or row.get("url") or row.get("Apply to Job") or row.get("Job Posting URL"))
    if not name or not title or not link:
        raise ValueError("company_name, title and a single HTTP source URL are required")
    locations = row.get("locations") or [row.get("location") or row.get("Job Location") or row.get("Country")]
    if not isinstance(locations, list):
        locations = [locations]
    salary = row.get("salary") or {}
    if not isinstance(salary, dict):
        salary = {"raw_text": clean(salary)}
    remote = row.get("is_remote")
    return {
        "company_name": name, "title": title, "source_url": link,
        "application_url": url(row.get("application_url") or row.get("job_url_direct")) or link,
        "website_url": url(row.get("website_url") or row.get("company_url_direct")),
        "company_profile_url": url(row.get("company_url")),
        "company_description": clean(row.get("company_description") or row.get("company_info")),
        "sectors": clean(row.get("sectors") or row.get("company_vertical") or row.get("Company Vertical") or row.get("company_industry")),
        "locations": list(dict.fromkeys(filter(None, map(clean, locations)))),
        "remote_policy": clean(row.get("remote_policy") or row.get("work_model")) or ("remote" if remote is True or str(remote).lower() == "true" else None),
        "eligible_countries": row.get("eligible_countries") or [],
        "employment_type": clean(row.get("employment_type") or row.get("job_type")) or None,
        "seniority": clean(row.get("seniority") or row.get("job_level")) or None,
        "salary": {"min": number(salary.get("min", row.get("min_amount"))), "max": number(salary.get("max", row.get("max_amount"))),
                   "currency": clean(salary.get("currency") or row.get("currency")) or None,
                   "period": clean(salary.get("period") or row.get("interval")) or None,
                   "raw_text": clean(salary.get("raw_text")) or None},
        "description": str(row.get("description") or "").strip(),
        "posted_at": clean(row.get("posted_at") or row.get("date_posted") or row.get("date_first_listed") or row.get("Date first listed")) or None,
        "source": source,
    }


class Archive:
    """Transactional local archive shared by the CLI, Codex and dashboard."""

    def __init__(self, path):
        """Open the archive and initialize missing tables without touching legacy files."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
          website TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', sectors TEXT NOT NULL DEFAULT '',
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
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
        CREATE INDEX IF NOT EXISTS feedback_company ON feedback(company_id,id);
        CREATE TABLE IF NOT EXISTS categories(company_id TEXT PRIMARY KEY REFERENCES companies(id),
          category TEXT NOT NULL, method TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS enrichments(task TEXT NOT NULL, record_id TEXT NOT NULL, source_hash TEXT NOT NULL,
          cache_key TEXT NOT NULL, data TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(task,record_id));
        CREATE TABLE IF NOT EXISTS feedback_detail(event_id INTEGER PRIMARY KEY REFERENCES feedback(id), reason TEXT NOT NULL,
          until_date TEXT, role_snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS personal_queue(company_id TEXT PRIMARY KEY REFERENCES companies(id), priority REAL NOT NULL,
          payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS impressions(company_id TEXT PRIMARY KEY REFERENCES companies(id), first_shown TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preference_rules(id TEXT PRIMARY KEY, category TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)

    def close(self):
        """Release the database connection."""
        self.db.close()

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
        self.db.execute("INSERT OR IGNORE INTO companies VALUES(?,?,?,?,?,?,?,?)", (cid, clean(name), key, website, "", "", stamp, stamp))
        return cid

    def ingest(self, rows, source, observed=None):
        """Import source records transactionally; return counts and rejected row reasons."""
        stamp = observed or now()
        result = {"received": len(rows), "inserted": 0, "updated": 0, "unchanged": 0, "rejected": []}
        with self.db:
            for index, raw in enumerate(rows):
                try:
                    item = normalize(raw, source)
                except (ValueError, TypeError) as exc:
                    result["rejected"].append({"row": index + 1, "reason": str(exc)})
                    continue
                cid = self.company(item["company_name"], item["website_url"], stamp)
                oid = identity(cid + "|" + item["application_url"])
                encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
                digest = identity(encoded)
                old = self.db.execute("SELECT * FROM opportunities WHERE id=?", (oid,)).fetchone()
                if old and old["last_seen"] > stamp:
                    item = json.loads(old["data"])
                    encoded, digest = old["data"], old["content_hash"]
                elif old:
                    # Keep richer content when another board only provides a listing stub.
                    prior = json.loads(old["data"])
                    if len(prior.get("description", "")) > len(item["description"]) and prior["source"] != source:
                        item["description"] = prior["description"]
                    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
                    digest = identity(encoded)
                result["inserted" if old is None else "unchanged" if old["content_hash"] == digest else "updated"] += 1
                self.db.execute("INSERT INTO opportunities VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, content_hash=excluded.content_hash, last_seen=MAX(opportunities.last_seen,excluded.last_seen)",
                                (oid, cid, encoded, digest, stamp, stamp))
                self.db.execute("INSERT INTO observations VALUES(?,?,?,?) ON CONFLICT DO UPDATE SET observed_at=MAX(observations.observed_at,excluded.observed_at)", (oid, source, normalize(raw, source)["source_url"], stamp))
                self.db.execute("UPDATE companies SET description=CASE WHEN ?!='' THEN ? ELSE description END, sectors=CASE WHEN ?!='' THEN ? ELSE sectors END, last_seen=MAX(last_seen,?) WHERE id=?",
                                (item["company_description"], item["company_description"], item["sectors"], item["sectors"], stamp, cid))
        logger.info("Imported %s: %s records, %s rejected", source, len(rows), len(result["rejected"]))
        self.categorize()
        return result

    def search(self, query="", status="", source="", location="", limit=30, offset=0, category=""):
        """Search companies with opportunity text and return one paginated row per company."""
        if status and status not in STATUSES:
            raise ValueError("Unknown status")
        conditions, args = [], []
        if category:
            conditions.append("COALESCE((SELECT category FROM categories WHERE company_id=c.id),'Da classificare')=?")
            args.append(category)
        for value, expression in [(query, "(c.name || ' ' || c.description || ' ' || c.sectors LIKE ? OR EXISTS(SELECT 1 FROM opportunities o WHERE o.company_id=c.id AND o.data LIKE ?))"),
                                   (location, "EXISTS(SELECT 1 FROM opportunities o WHERE o.company_id=c.id AND json_extract(o.data,'$.locations') LIKE ?)"),
                                   (source, "EXISTS(SELECT 1 FROM opportunities o JOIN observations s ON s.opportunity_id=o.id WHERE o.company_id=c.id AND s.source=?)")]:
            if value:
                conditions.append(expression)
                if "LIKE" in expression:
                    args.extend(["%" + value + "%"] * expression.count("?"))
                else:
                    args.append(value)
        status_sql = "COALESCE((SELECT f.status FROM feedback f WHERE f.company_id=c.id AND f.opportunity_id IS NULL AND f.undone_at IS NULL ORDER BY f.id DESC LIMIT 1),'new')"
        if status:
            conditions.append(status_sql + "=?")
            args.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        total = self.db.execute("SELECT count(*) FROM companies c" + where, args).fetchone()[0]
        sql = "SELECT c.*, " + status_sql + " AS status, (SELECT count(*) FROM opportunities o WHERE o.company_id=c.id) AS opportunity_count FROM companies c" + where + " ORDER BY c.last_seen DESC,c.name COLLATE NOCASE,c.id LIMIT ? OFFSET ?"
        items = [dict(r) for r in self.db.execute(sql, args + [min(max(int(limit), 1), 500), max(int(offset), 0)])]
        for item in items:
            item.update(self.category(item["id"]))
            jobs = [json.loads(r[0]) for r in self.db.execute("SELECT data FROM opportunities WHERE company_id=?", (item["id"],))]
            item["locations"] = sorted({x for j in jobs for x in j["locations"]})
            item["titles"] = list(dict.fromkeys(j["title"] for j in jobs))[:5]
        return {"total": total, "items": items}

    def category(self, cid):
        """Expose an assignment together with its provenance and explanation."""
        row = self.db.execute("SELECT category,method AS category_method,reason AS category_reason FROM categories WHERE company_id=?", (cid,)).fetchone()
        return dict(row) if row else {"category": "Da classificare", "category_method": "unknown", "category_reason": "Dati aziendali insufficienti"}

    def categorize(self, cid=None, category=None, reason=""):
        """Refresh rule suggestions or save a chat classification that imports preserve."""
        rules = categories()
        if cid is not None:
            if not self.db.execute("SELECT 1 FROM companies WHERE id=?", (cid,)).fetchone():
                raise ValueError("Company not found")
            if category not in [*rules, "Da classificare"] or not isinstance(reason, str) or not reason.strip():
                raise ValueError("Choose a known category and supply a reason")
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO categories VALUES(?,?,?,?,?)", (cid, category, "chat", reason, now()))
            logger.info("Classified company %s from chat", cid)
            return self.category(cid)
        count = 0
        with self.db:
            for company in self.db.execute("SELECT * FROM companies WHERE id NOT IN (SELECT company_id FROM categories WHERE method='chat')").fetchall():
                from jobhunter.enrichment import company_input
                enriched = self.db.execute("SELECT source_hash FROM enrichments WHERE task='category' AND record_id=?", (company["id"],)).fetchone()
                if enriched and enriched[0] == identity(json.dumps(company_input(self, company["id"]), sort_keys=True, ensure_ascii=False)):
                    continue
                matches = {}
                # Sector labels take precedence; job titles cannot identify a company's industry.
                for field in ("sectors", "description"):
                    value = company[field].casefold()
                    matches = {label: term for label, terms in rules.items() for term in terms if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", value)}
                    if matches:
                        break
                label = next(iter(matches)) if len(matches) == 1 else "Da classificare"
                reason = f"{field}: {matches[label]}" if len(matches) == 1 else "Più settori possibili: " + ", ".join(matches) if matches else "Dati aziendali insufficienti"
                method = "rules" if len(matches) == 1 else "unknown"
                self.db.execute("INSERT INTO categories VALUES(?,?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET category=excluded.category,method=excluded.method,reason=excluded.reason,updated_at=excluded.updated_at WHERE category!=excluded.category OR method!=excluded.method OR reason!=excluded.reason", (company["id"], label, method, reason, now()))
                count += label != "Da classificare"
        logger.info("Category rules matched %s companies", count)
        return {"matched_by_rules": count, "coverage": [dict(r) for r in self.db.execute("SELECT category,method,count(*) AS companies FROM categories GROUP BY category,method")]}

    def basis(self, cid):
        """Fingerprint content and explicit preferences for assessment freshness."""
        hashes = [r[0] for r in self.db.execute("SELECT content_hash FROM opportunities WHERE company_id=? ORDER BY id", (cid,))]
        hashes += [str(r[0]) for r in self.db.execute("SELECT id FROM preferences ORDER BY id")]
        hashes += [r[0] for r in self.db.execute("SELECT id FROM evidence WHERE company_id=? ORDER BY id", (cid,))]
        profile = ROOT / "user_context/profile.md"
        if profile.exists():
            hashes.append(identity(profile.read_text(encoding="utf-8")))
        for path in (ROOT / "user_context/search-profile.md", ROOT / "config/role_filters.json"):
            hashes.append(identity(path.read_text(encoding="utf-8")))
        return identity("|".join(hashes))

    def show(self, cid):
        """Return a company with opportunities, provenance, evidence and feedback."""
        company = self.db.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
        if company is None:
            raise ValueError("Company not found")
        result = dict(company)
        result.update(self.category(cid))
        result["opportunities"] = []
        for row in self.db.execute("SELECT * FROM opportunities WHERE company_id=? ORDER BY last_seen DESC,id", (cid,)):
            job = json.loads(row["data"])
            from jobhunter.selection import evaluate
            job["selection"] = evaluate(job)
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
        return result

    def feedback(self, cid, status, note="", opportunity_id=None, reason="other", until_date=None):
        """Record an explicit company or opportunity decision; identical retries are harmless."""
        self.show(cid)
        from jobhunter.selection import feedback_reasons, role_snapshot
        snapshot = json.dumps(role_snapshot(self, cid))
        if reason not in feedback_reasons():
            raise ValueError("Unknown feedback reason")
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
            self.db.execute("DELETE FROM personal_queue WHERE company_id=?", (cid,))
        return {"event_id": cursor.lastrowid}

    def undo(self, event_id):
        """Undo a specific decision without deleting the audit record."""
        with self.db:
            cursor = self.db.execute("UPDATE feedback SET undone_at=COALESCE(undone_at,?) WHERE id=?", (now(), event_id))
            if not cursor.rowcount:
                raise ValueError("Feedback event not found")
            self.db.execute("DELETE FROM personal_queue")
        return {"undone": event_id}

    def assess(self, cid, data):
        """Persist a structured chat assessment; compatible with a future API producer."""
        self.show(cid)
        if not isinstance(data, dict) or not isinstance(data.get("reasoning"), str) or not data["reasoning"].strip():
            raise ValueError("Assessment requires reasoning text")
        for key in ("missing_information", "relevant_opportunity_ids"):
            if key in data and (not isinstance(data[key], list) or not all(isinstance(v, str) for v in data[key])):
                raise ValueError(key + " must be a list of strings")
        valid_ids = {j["id"] for j in self.show(cid)["opportunities"]}
        if not set(data.get("relevant_opportunity_ids", [])) <= valid_ids:
            raise ValueError("Assessment refers to unknown opportunities")
        with self.db:
            self.db.execute("INSERT INTO assessments VALUES(?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET data=excluded.data,created_at=excluded.created_at,basis=excluded.basis", (cid, json.dumps(data, ensure_ascii=False, allow_nan=False), now(), self.basis(cid)))
        return {"assessed": cid}

    def add_evidence(self, cid, source_url, note):
        """Add attributed research to an existing company, idempotently."""
        self.show(cid)
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
                "runs": [dict(r) for r in self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 30")]}
