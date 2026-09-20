"""Da una località scritta a mano a una coppia paese-città, senza indovinare.

I paesi stanno in `config/geography.json`, le città in `config/cities.json`. Quello che non cade
nella mappatura non viene inventato: resta com'è, e `unmapped()` lo elenca per frequenza perché
qualcuno lo aggiunga alla mappa. Il risultato è memorizzato per annuncio: normalizzare
ventitremila annunci a ogni ricerca costerebbe più della ricerca stessa.
"""

import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from jobhunter.normalization import identity

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
# Parole che dicono «nessun luogo»: non sono città e non vanno segnalate come mappatura mancante.
ANYWHERE = re.compile(r'(?i)^(remote|anywhere|ovunque|hybrid|onsite|on-?site|office|in|flexible|zdalnie|worldwide|global|eu|emea|europe|job|jobs)$')
VERSION = 6
SPLIT = re.compile(r'[,;|/()\[\]]| - | · ')


# Lettere che non sono una vocale più un accento: NFKD non le scompone, e senza questa tabella
# «København» e «Copenhagen» finirebbero in due città diverse (e «Wrocław» non troverebbe «Wroclaw»).
STROKES = str.maketrans({'ø': 'o', 'æ': 'ae', 'œ': 'oe', 'ł': 'l', 'đ': 'd', 'ð': 'd', 'þ': 'th', 'ı': 'i', 'ŋ': 'n'})


def fold(text):
    """Confrontare «Düsseldorf» e «Dusseldorf» senza dichiararle città diverse."""
    plain = unicodedata.normalize('NFKD', text.casefold().translate(STROKES))
    return ''.join(c for c in plain if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def mapping(signature=''):
    """Leggere le due mappe una volta sola; `signature` serve ai test per rileggerle."""
    regions = json.loads((ROOT / 'config/geography.json').read_text(encoding='utf-8'))
    cities = json.loads((ROOT / 'config/cities.json').read_text(encoding='utf-8'))['paesi']
    countries, codes = {}, {}
    for region in regions:
        for alias in {*region['aliases'], region['name']}:
            (codes if len(alias) <= 3 else countries)[fold(alias)] = region['name']
    names, short, standalone = {}, {}, {}
    for country, entries in cities.items():
        for entry in entries:
            for alias in entry.get('standalone_aliases', []):
                standalone[fold(alias)] = (country, entry['name'])
            for alias in {*entry['aliases'], entry['name']}:
                (short if len(alias) <= 3 else names)[fold(alias)] = (country, entry['name'])
    continents = {region['name']: region['continent'] for region in regions}
    # VERSION entra nell'impronta: cambiando le regole di lettura, il salvato va rifatto come se
    # fosse cambiata la mappa. Alzala quando tocchi resolve() o names_a_city().
    digest = identity(json.dumps([VERSION, regions, cities], sort_keys=True, ensure_ascii=False))
    return {'countries': countries, 'codes': codes, 'cities': names, 'city_codes': short,
            'standalone': standalone,
            'continents': continents, 'by_country': cities, 'digest': digest}


def resolve(raw, maps=None):
    """Paese e città di una singola stringa, o None dove la mappa non arriva.

    Il paese si riconosce anche in mezzo alla frase («Foster City, CA Foster City California
    United States»), la sigla solo quando è un segmento intero: «IT» dentro «IT Manager» è un
    mestiere, non l'Italia. Una città con la sigla corta (MI) vale solo se il paese concorda,
    altrimenti «Detroit, MI» diventerebbe Milano. Le eccezioni standalone_aliases
    nella mappa valgono solo per l'intera stringa, per esempio «MI» isolato.
    """
    maps = maps or mapping()
    text = fold(raw)
    if text.strip() in maps.get('standalone', {}):
        return maps['standalone'][text.strip()]
    segments = [s.strip() for s in SPLIT.split(text) if s.strip()]
    found = [(text.rindex(alias), name) for alias, name in maps['countries'].items()
             if re.search(r'\b' + re.escape(alias) + r'\b', text)]
    found += [(text.rindex(segment), maps['codes'][segment]) for segment in segments if segment in maps['codes']]
    country = max(found)[1] if found else None
    hits = [(len(alias), maps['cities'][alias]) for alias in maps['cities']
            if re.search(r'\b' + re.escape(alias) + r'\b', text)]
    # La sigla vale solo con il paese davanti: «Bakersfield, CA» non è Cagliari.
    hits += [(len(segment), maps['city_codes'][segment]) for segment in segments
             if country and segment in maps['city_codes']]
    hits = [hit for hit in hits if country is None or hit[1][0] == country]
    city = max(hits)[1][1] if hits else None
    return (country or (hits and max(hits)[1][0]) or None, city)


def names_a_city(raw, maps):
    """Distinguere «Kigali, Rwanda» da «Germany»: solo il primo è una città che manca alla mappa.

    Un annuncio che dichiara il paese e basta non ha una città da mappare, e nemmeno uno che dice
    «Remote». Segnalare anche quelli seppellirebbe le città vere sotto migliaia di righe inutili.
    """
    text = fold(SPLIT.split(raw.strip())[0].strip())
    if text in maps['codes']:
        return False
    for alias in maps['countries']:
        text = re.sub(r'\b' + re.escape(alias) + r'\b', ' ', text)
    return any(not ANYWHERE.match(word) for word in text.split())


def of(locations, maps=None):
    """Una riga per località: paese e città quando la mappa arriva, e se resta una città da mappare.

    Anche una località illeggibile produce la sua riga: è quella che dice «già guardata», e senza
    l'annuncio risulterebbe da rifare a ogni lettura.
    """
    maps = maps or mapping()
    rows = {}
    for raw in locations:
        country, city = resolve(raw, maps)
        rows[raw] = {'raw': raw, 'country': country or '', 'city': city or '',
                     'to_map': int(not city and names_a_city(raw, maps))}
    return list(rows.values())


def refresh(archive):
    """Ricalcolare solo gli annunci cambiati, o tutti quando cambia la mappatura."""
    maps = mapping()
    rows = archive.db.execute("""SELECT o.id,o.data FROM opportunities o
        LEFT JOIN place_index p ON p.opportunity_id=o.id AND p.content_hash=o.content_hash AND p.mapping_hash=?
        WHERE p.opportunity_id IS NULL""", (maps['digest'],)).fetchall()
    if not rows:
        return 0
    with archive.db:
        for row in rows:
            archive.db.execute('DELETE FROM places WHERE opportunity_id=?', (row['id'],))
            archive.db.executemany('INSERT OR REPLACE INTO places VALUES(?,?,?,?,?)',
                                   [(row['id'], place['raw'], place['country'], place['city'], place['to_map'])
                                    for place in of(json.loads(row['data']).get('locations', []), maps)])
            archive.db.execute("""INSERT INTO place_index SELECT id,content_hash,? FROM opportunities WHERE id=?
                ON CONFLICT(opportunity_id) DO UPDATE SET content_hash=excluded.content_hash,mapping_hash=excluded.mapping_hash""",
                               (maps['digest'], row['id']))
    logger.info('Località normalizzate per %s annunci', len(rows))
    return len(rows)


def options(archive):
    """Paesi e città presenti in archivio, con quanti annunci li dichiarano."""
    refresh(archive)
    countries = {}
    for row in archive.db.execute("""SELECT country,city,count(DISTINCT opportunity_id) n FROM places
        WHERE country!='' GROUP BY country,city ORDER BY country,city"""):
        entry = countries.setdefault(row['country'], {'name': row['country'], 'count': 0, 'cities': []})
        entry['count'] += row['n']
        if row['city']:
            entry['cities'].append({'name': row['city'], 'count': row['n']})
    return {'countries': sorted(countries.values(), key=lambda c: -c['count'])}


def unmapped(archive, limit=200):
    """Le località che la mappa non legge, dalla più frequente: la lista da dare a chi la estende."""
    refresh(archive)
    rows = archive.db.execute("""SELECT raw,count(DISTINCT opportunity_id) n FROM places WHERE to_map=1
        GROUP BY raw ORDER BY n DESC,raw LIMIT ?""", (max(1, int(limit)),)).fetchall()
    total = archive.db.execute("SELECT count(DISTINCT opportunity_id) FROM places WHERE to_map=1").fetchone()[0]
    known = archive.db.execute("SELECT count(DISTINCT opportunity_id) FROM places WHERE city!=''").fetchone()[0]
    return {'annunci_con_citta': known, 'annunci_da_mappare': total,
            'da_mappare': [{'text': row['raw'], 'count': row['n'], 'paese': resolve(row['raw'])[0] or ''} for row in rows]}


def labels(archive, opportunity_ids):
    """«Città, Paese» per gli annunci indicati: la lista mostra la forma canonica, non quella grezza."""
    if not opportunity_ids:
        return []
    marks = ','.join('?' * len(opportunity_ids))
    rows = archive.db.execute(f"SELECT DISTINCT country,city FROM places WHERE opportunity_id IN ({marks}) AND country!=''",
                              list(opportunity_ids)).fetchall()
    return sorted({', '.join(filter(None, (row['city'], row['country']))) for row in rows})
