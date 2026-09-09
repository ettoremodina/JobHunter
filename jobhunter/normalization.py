"""Il contratto con le fonti: da una riga qualsiasi a un annuncio, senza appiattire le relazioni.

DESIGN §8. Una fonte nuova si aggiunge rispettando questi campi; `normalize()` rifiuta la riga
quando mancano nome azienda, titolo e un singolo URL HTTP. Nessuna preferenza personale vive qui.
"""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# L'annuncio contiene solo il ruolo, l'azienda solo l'azienda: questi campi arrivano dalla fonte,
# risolvono o aggiornano la riga azienda e non vengono salvati sul record (docs/data-model.md).
COMPANY_FIELDS = ("company_name", "company_description", "sectors", "website_url")


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
    locations = row.get("locations") or [row.get("location") or row.get("Location") or row.get("Job Location"), row.get("Country")]
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
        "company_profile_url": url(row.get("company_url") or row.get("ℹ️ Company info")),
        "company_description": clean(row.get("company_description") or row.get("company_info")),
        "sectors": clean(row.get("sectors") or row.get("company_vertical") or row.get("Company Vertical") or row.get("company_industry")),
        "locations": list(dict.fromkeys(filter(None, map(clean, locations)))),
        "remote_policy": clean(row.get("remote_policy") or row.get("work_model") or row.get("Remote")) or ("remote" if remote is True or str(remote).lower() == "true" else None),
        "employment_type": clean(row.get("employment_type") or row.get("job_type") or row.get("Commitment (Beta)")) or None,
        "salary": {"min": number(salary.get("min", row.get("min_amount"))), "max": number(salary.get("max", row.get("max_amount"))),
                   "currency": clean(salary.get("currency") or row.get("currency")) or None,
                   "period": clean(salary.get("period") or row.get("interval")) or None,
                   "raw_text": clean(salary.get("raw_text") or row.get("💰  Salary Range (Beta)")) or None},
        "description": str(row.get("description") or "").strip(),
        "posted_at": clean(row.get("posted_at") or row.get("date_posted") or row.get("date_first_listed") or row.get("Date first listed")) or None,
        "source": source,
    }
