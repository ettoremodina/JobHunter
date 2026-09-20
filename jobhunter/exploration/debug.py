"""Lenti di esplorazione: domande precise sull'archivio, in sola lettura.

La tab Aziende serve a scegliere; questa serve a controllare. Ogni lente è una domanda sola
(«quali aziende non hanno una descrizione?»), con il suo conteggio e la sua pagina di righe.
Aggiungere una lente significa aggiungere una voce a LENSES, non una funzione nuova ovunque.
"""

import logging

logger = logging.getLogger(__name__)

# Il campo `facet` divide una lente in gruppi (il motivo dello scarto, lo stato del recupero):
# senza, «annunci scartati» sarebbe un elenco di tredicimila righe senza una domanda dentro.
LENSES = {
    'aziende-senza-descrizione': {
        'label': 'Aziende senza descrizione', 'unit': 'aziende',
        'sql': """SELECT c.id, c.id company_id, c.name, '' facet, c.website detail, '' url FROM companies c
                  WHERE trim(c.description)='' ORDER BY c.name COLLATE NOCASE"""},
    'annunci-senza-descrizione': {
        'label': 'Annunci senza descrizione', 'unit': 'annunci',
        'sql': """SELECT o.id, o.company_id, c.name, '' facet, json_extract(o.data,'$.title') detail,
                         json_extract(o.data,'$.application_url') url
                  FROM opportunities o JOIN companies c ON c.id=o.company_id
                  WHERE trim(COALESCE(json_extract(o.data,'$.description'),''))=''
                  ORDER BY c.name COLLATE NOCASE"""},
    'annunci-scartati-dal-regex': {
        'label': 'Annunci scartati dal regex, per motivo', 'unit': 'annunci', 'facet_label': 'Motivo',
        'sql': """SELECT o.id, o.company_id, c.name, json_extract(e.decision,'$.reasons[0]') facet,
                         json_extract(o.data,'$.title') detail, json_extract(o.data,'$.application_url') url
                  FROM search_eligibility e JOIN opportunities o ON o.id=e.opportunity_id
                  JOIN companies c ON c.id=o.company_id WHERE e.status='excluded'
                  ORDER BY c.name COLLATE NOCASE"""},
    'annunci-scartati-dal-modello': {
        'label': 'Annunci scartati dal modello remoto', 'unit': 'annunci',
        'sql': """SELECT o.id, o.company_id, c.name, '' facet, json_extract(o.data,'$.title') detail,
                         json_extract(o.data,'$.application_url') url
                  FROM enrichments n JOIN opportunities o ON o.id=n.record_id
                  JOIN companies c ON c.id=o.company_id
                  WHERE n.task='remote:selection' AND json_extract(n.data,'$.result.decision')='exclude'
                  ORDER BY c.name COLLATE NOCASE"""},
    'annunci-senza-verdetto': {
        'label': 'Annunci lasciati aperti dal regex e mai chiamati al modello', 'unit': 'annunci',
        'sql': """SELECT o.id, o.company_id, c.name, '' facet, json_extract(o.data,'$.title') detail,
                         json_extract(o.data,'$.application_url') url
                  FROM search_eligibility e JOIN opportunities o ON o.id=e.opportunity_id
                  JOIN companies c ON c.id=o.company_id
                  WHERE e.status='review' AND NOT EXISTS(
                      SELECT 1 FROM enrichments n WHERE n.task='remote:selection' AND n.record_id=o.id)
                  ORDER BY c.name COLLATE NOCASE"""},
    'ruoli-buoni-azienda-cieca': {
        'label': 'Aziende con ruoli compatibili ma senza evidenza aziendale', 'unit': 'aziende',
        'sql': """SELECT c.id, c.id company_id, c.name, '' facet,
                         'ruoli compatibili: ' || (SELECT count(*) FROM search_eligibility e
                          JOIN opportunities o ON o.id=e.opportunity_id
                          WHERE o.company_id=c.id AND e.status='potential') detail,
                         c.website url FROM companies c
                  WHERE COALESCE((SELECT category FROM categories WHERE company_id=c.id),'Da classificare')='Da classificare'
                    AND EXISTS(SELECT 1 FROM search_eligibility e JOIN opportunities o ON o.id=e.opportunity_id
                               WHERE o.company_id=c.id AND e.status='potential')
                  ORDER BY c.name COLLATE NOCASE"""},
    'aziende-non-categorizzate': {
        'label': 'Aziende senza categoria', 'unit': 'aziende',
        'sql': """SELECT c.id, c.id company_id, c.name, '' facet,
                         CASE WHEN trim(c.description)='' THEN 'senza descrizione' ELSE 'con descrizione' END detail,
                         c.website url FROM companies c
                  WHERE COALESCE((SELECT category FROM categories WHERE company_id=c.id),'Da classificare')='Da classificare'
                  ORDER BY c.name COLLATE NOCASE"""},
    'aziende-decise-a-mano': {
        'label': 'Aziende su cui hai già deciso', 'unit': 'aziende', 'facet_label': 'Decisione',
        'sql': """SELECT c.id, c.id company_id, c.name,
                         CASE f.status WHEN 'discarded' THEN 'scartate' WHEN 'contacted' THEN 'contattate'
                              WHEN 'saved' THEN 'salvate' WHEN 'review' THEN 'da approfondire'
                              ELSE f.status END facet,
                         COALESCE(NULLIF(f.note,''),'senza nota') detail, c.website url
                  FROM feedback f JOIN companies c ON c.id=f.company_id
                  WHERE f.opportunity_id IS NULL AND f.undone_at IS NULL AND f.status!='new'
                    AND f.id=(SELECT max(x.id) FROM feedback x
                              WHERE x.company_id=f.company_id AND x.opportunity_id IS NULL AND x.undone_at IS NULL)
                  ORDER BY f.created_at DESC"""},
    'recupero-descrizione-fallito': {
        'label': 'Recupero della descrizione non riuscito, per esito', 'unit': 'annunci', 'facet_label': 'Esito',
        'sql': """SELECT o.id, o.company_id, c.name, a.status facet, json_extract(o.data,'$.title') detail, a.source_url url
                  FROM description_attempts a JOIN opportunities o ON o.id=a.opportunity_id
                  JOIN companies c ON c.id=o.company_id WHERE a.status!='available'
                  ORDER BY a.checked_at DESC"""},
    'annunci-per-lingua': {
        'label': 'Annunci per lingua dell’originale', 'unit': 'annunci', 'facet_label': 'Lingua',
        'sql': """SELECT o.id, o.company_id, c.name, json_extract(e.decision,'$.requirements.written_in.name') facet,
                         json_extract(o.data,'$.title') detail, json_extract(o.data,'$.application_url') url
                  FROM search_eligibility e JOIN opportunities o ON o.id=e.opportunity_id
                  JOIN companies c ON c.id=o.company_id
                  ORDER BY c.name COLLATE NOCASE"""},
    'annunci-in-lingua-sconosciuta': {
        'label': 'Annunci in una lingua che non conosci', 'unit': 'annunci', 'facet_label': 'Lingua',
        'sql': """SELECT o.id, o.company_id, c.name, json_extract(e.decision,'$.requirements.written_in.name') facet,
                         json_extract(o.data,'$.title') detail, json_extract(o.data,'$.application_url') url
                  FROM search_eligibility e JOIN opportunities o ON o.id=e.opportunity_id
                  JOIN companies c ON c.id=o.company_id
                  WHERE json_extract(e.decision,'$.requirements.written_in.known')=0
                  ORDER BY c.name COLLATE NOCASE"""},
    'localita-da-mappare': {
        'label': 'Località senza città riconosciuta', 'unit': 'annunci', 'facet_label': 'Località',
        'sql': """SELECT o.id, o.company_id, c.name, p.raw facet, json_extract(o.data,'$.title') detail,
                         json_extract(o.data,'$.application_url') url
                  FROM places p JOIN opportunities o ON o.id=p.opportunity_id
                  JOIN companies c ON c.id=o.company_id WHERE p.to_map=1
                  ORDER BY c.name COLLATE NOCASE"""},
}


def prepare(archive):
    """Le due cache che le lenti leggono: senza, mostrerebbero lo stato di ieri."""
    from jobhunter.exploration import places
    archive.refresh_search_eligibility()
    places.refresh(archive)


def lenses(archive):
    """Elenco delle lenti con il loro conteggio, e i gruppi di quelle divise per motivo."""
    prepare(archive)
    result = []
    for key, lens in LENSES.items():
        rows = archive.db.execute(f"SELECT facet, count(*) n FROM ({lens['sql']}) GROUP BY facet ORDER BY n DESC").fetchall()
        facets = [{'value': row['facet'] or '', 'count': row['n']} for row in rows if lens.get('facet_label')]
        result.append({'id': key, 'label': lens['label'], 'unit': lens['unit'],
                       'facet_label': lens.get('facet_label', ''), 'facets': facets,
                       'count': sum(row['n'] for row in rows)})
    logger.info('Lenti di debug: %s', {item['id']: item['count'] for item in result})
    return {'lenses': result}


def rows(archive, key, value='', offset=0, limit=50):
    """Una pagina di righe della lente scelta, eventualmente ristretta a un gruppo."""
    if key not in LENSES:
        raise ValueError('Lente sconosciuta')
    prepare(archive)
    lens = LENSES[key]
    where = ' WHERE facet=?' if value else ''
    arguments = ([value] if value else []) + [min(max(int(limit), 1), 200), max(int(offset), 0)]
    total = archive.db.execute(f"SELECT count(*) FROM ({lens['sql']})" + where,
                               [value] if value else []).fetchone()[0]
    items = archive.db.execute(f"SELECT * FROM ({lens['sql']})" + where + ' LIMIT ? OFFSET ?', arguments).fetchall()
    return {'id': key, 'label': lens['label'], 'unit': lens['unit'], 'value': value, 'total': total,
            'items': [dict(row) for row in items]}
