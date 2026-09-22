"""Persist and evaluate ordered, multi-label company sectors behind one small interface."""

from jobhunter.evaluation.tier import UNCLASSIFIED
from jobhunter.workspace import now


def assignments(archive, cid):
    """Return one company's categories in display and decision order."""
    return [dict(row) for row in archive.db.execute(
        '''SELECT category,rank,confidence,method,reason,updated_at FROM categories
           WHERE company_id=? AND category!=? ORDER BY rank,category''', (cid, UNCLASSIFIED))]


def summary(archive, cid):
    """Expose multi-label data while retaining the first-label fields used by older callers."""
    rows = assignments(archive, cid)
    if not rows:
        return {'category': UNCLASSIFIED, 'categories': [], 'category_assignments': [],
                'category_method': 'unknown', 'category_reason': 'Dati aziendali insufficienti'}
    first = rows[0]
    return {'category': first['category'], 'categories': [row['category'] for row in rows],
            'category_assignments': rows, 'category_method': first['method'],
            'category_reason': first['reason']}


def replace(archive, cid, labels, method, reason, confidences=None, preserve_chat=True):
    """Atomically replace automatic labels; an explicit chat assignment remains authoritative."""
    if preserve_chat and archive.db.execute(
            "SELECT 1 FROM categories WHERE company_id=? AND method='chat'", (cid,)).fetchone():
        return False
    confidences = confidences or {}
    stamp = now()
    archive.db.execute('DELETE FROM categories WHERE company_id=?', (cid,))
    archive.db.executemany(
        '''INSERT INTO categories(company_id,category,rank,confidence,method,reason,updated_at)
           VALUES(?,?,?,?,?,?,?)''',
        [(cid, label, rank, confidences.get(label), method, reason, stamp)
         for rank, label in enumerate(labels, 1)])
    return True


def verdict(labels, preferred):
    """An axis is interesting when any reliable company category is preferred."""
    from jobhunter.evaluation.tier import company_verdict
    return company_verdict(labels, preferred)
