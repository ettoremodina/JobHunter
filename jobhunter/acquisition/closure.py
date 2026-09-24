"""Annunci non più visti: chiusi in archivio, mai cancellati, e alleggeriti dei loro HTML.

Dopo una raccolta completa, un annuncio che la sua fonte avrebbe potuto ritrovare e non ha ritrovato
è probabilmente chiuso. «Avrebbe potuto» dipende dalla fonte: Airtable restituisce tutto l'elenco,
JobSpy solo gli annunci pubblicati nelle ultime `hours_old` ore. Un annuncio fuori da quella finestra,
o senza data, non si chiude: non ricomparirebbe comunque, e meglio un annuncio in più da verificare
che uno buono nascosto. Se ricompare in una raccolta successiva, `Archive.ingest` lo riapre.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from jobhunter.workspace import now

logger = logging.getLogger(__name__)


def detect(archive, started, windows, max_share, scopes=None):
    """Close the ads each completed source could have seen again since `started` and did not.

    `windows` maps every source that completed the sweep to its look-back in hours, or None when it
    lists everything it has. `scopes` optionally limits a source to some companies: an ATS lists
    everything only for the boards that answered. A source that would close more than `max_share`
    of its open ads is skipped and reported: a truncated or blocked response must not close half
    the archive.
    """
    closed, skipped = [], {}
    begin = datetime.fromisoformat(started)
    for source, hours in windows.items():
        rows = archive.db.execute("""SELECT o.id, o.company_id, json_extract(o.data,'$.posted_at') posted, max(s.observed_at) seen
            FROM opportunities o JOIN observations s ON s.opportunity_id=o.id
            WHERE o.id IN (SELECT opportunity_id FROM observations WHERE source=?)
              AND o.id NOT IN (SELECT opportunity_id FROM closures)
            GROUP BY o.id""", (source,)).fetchall()
        if scopes and source in scopes:
            rows = [r for r in rows if r["company_id"] in scopes[source]]
        # Un giorno di margine sul bordo della finestra: la data della fonte spesso non ha l'ora.
        cutoff = None if hours is None else (begin - timedelta(hours=hours) + timedelta(days=1)).date().isoformat()
        gone = [r["id"] for r in rows if r["seen"] < started
                and (cutoff is None or (r["posted"] and r["posted"][:10] >= cutoff))]
        if len(gone) > max_share * len(rows):
            skipped[source] = {"would_close": len(gone), "open": len(rows)}
            logger.warning("Closure skipped for %s: %s of %s open ads not seen again", source, len(gone), len(rows))
            continue
        closed += [(oid, source) for oid in gone]
    stamp = now()
    with archive.db:
        archive.db.executemany("INSERT OR IGNORE INTO closures VALUES(?,?,?)",
                               [(oid, stamp, f"Non ritrovato da {source} nella raccolta del {started[:10]}") for oid, source in closed])
    logger.info("Closed %s ads not seen again", len(closed))
    return {"closed": len(closed), "skipped_sources": skipped, "ids": sorted({oid for oid, _ in closed})}


def compact(ids, directory):
    """Delete the saved HTML pages of closed ads; their extracted text stays in the archive.

    Le pagine grezze sono il grosso del disco (circa 116 KB ad annuncio contro 6 KB di testo estratto)
    e le rilegge solo la riparazione una tantum `reparse-descriptions`, che salta i file mancanti.
    """
    wanted, freed, removed = set(ids), 0, 0
    for root, _, files in os.walk(directory):
        for name in files:
            if name.endswith(".html") and name.split("-")[0].removesuffix(".html") in wanted:
                path = Path(root) / name
                freed += path.stat().st_size
                path.unlink()
                removed += 1
    logger.info("Removed %s HTML pages of closed ads, %.1f MB", removed, freed / 1e6)
    return {"files": removed, "bytes": freed}
