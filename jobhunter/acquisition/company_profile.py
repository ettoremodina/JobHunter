"""Dati aziendali propri: settore, sito e descrizione, presi dalle pagine pubbliche dell'azienda.

DESIGN §2: l'asse azienda vale quanto l'evidenza che ha. Finora la descrizione aziendale
entrava da un'unica porta, l'importazione: se la fonte non l'allegava, restava vuota per
sempre. Qui l'archivio va a cercarsela.

Due strade, in ordine di resa misurata (vedi DESIGN.md):

1. La scheda di un aggregatore, quando è lei l'indirizzo che abbiamo salvato. Espone
   settore, sito vero e descrizione in una tabella prevedibile: 93/83/80% su 30 provate.
2. La home del sito aziendale: dati strutturati, poi meta description, poi la pagina
   «chi siamo». Un solo script, nessuna regola per sito.

Nessun campo già pieno viene sovrascritto senza `force`, e ogni scrittura porta la sua
provenienza: `description_provenance.method` dice da quale delle strade è arrivata.
"""

import html as htmllib
from html.parser import HTMLParser
import json
import logging
import re
import time
from urllib.parse import urljoin, urlsplit

from jobhunter.acquisition.collection import access_block, fetch
from jobhunter.evaluation.enrichment import lines
from jobhunter.normalization import url
from jobhunter.workspace import ROOT, now

logger = logging.getLogger(__name__)

# Schede di aggregatori: l'indirizzo salvato non è il sito dell'azienda, ma la pagina che
# quell'aggregatore le dedica. Vale la pena leggerla: contiene il sito vero.
PROFILE_HOSTS = {'climatetechlist.com'}
# Bacheche e gestionali di recruiting: non sono l'azienda e non la descrivono.
NOT_A_COMPANY_SITE = {
    'climatebase.org', 'linkedin.com', 'indeed.com', 'glassdoor.com', 'ziprecruiter.com',
    'monster.com', 'welcometothejungle.com', 'jobteaser.com', 'wellfound.com', 'builtin.com',
    'greenhouse.io', 'lever.co', 'myworkdayjobs.com', 'workday.com', 'breezy.hr',
    'smartrecruiters.com', 'workable.com', 'recruitee.com', 'teamtailor.com', 'personio.de',
    'bamboohr.com', 'jobvite.com', 'ashbyhq.com', 'join.com', 'softgarden.io', 'eightfold.ai',
    'successfactors.com', 'icims.com', 'taleo.net', 'applytojob.com', 'pinpointhq.com',
    'oraclecloud.com', 'avature.net',
}
# Suffissi a due livelli: senza, "co.uk" passerebbe per un dominio.
TWO_LEVEL = {'co.uk', 'ac.uk', 'gov.uk', 'org.uk', 'nhs.uk', 'net.uk', 'sch.uk', 'me.uk',
             'com.au', 'net.au', 'org.au', 'co.nz', 'co.jp', 'co.za', 'com.br', 'com.mx',
             'co.in', 'com.tr', 'com.sg', 'com.hk', 'com.pl', 'org.pl', 'gov.pl', 'co.at'}
ABOUT = re.compile(r'(about|chi-siamo|chi_siamo|who-we-are|our-story|company|azienda|'
                   r'qui-sommes|ueber-uns|uber-uns|nosotros|om-oss)', re.I)
MINIMUM_DESCRIPTION = 80


def registrable(address):
    """Dominio registrabile all'incirca: ultime due etichette, tre sui suffissi noti."""
    host = (urlsplit(address).hostname or '').lower().removeprefix('www.')
    parts = host.split('.')
    if len(parts) >= 3 and '.'.join(parts[-2:]) in TWO_LEVEL:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host


def kind(address):
    """Dice che cosa è l'indirizzo salvato per un'azienda, prima di andarci."""
    if not (address or '').strip():
        return 'nessuno'
    domain = registrable(address)
    if not domain:
        return 'nessuno'
    if domain in PROFILE_HOSTS:
        return 'scheda_aggregatore'
    if domain in NOT_A_COMPANY_SITE:
        return 'non_aziendale'
    return 'sito_aziendale'


class PageFacts(HTMLParser):
    """Legge in un passaggio le tabelle, i blocchi JSON-LD, le meta e i link della pagina."""

    def __init__(self):
        """Prepara gli accumulatori; nessun contenuto della pagina viene eseguito."""
        super().__init__(convert_charrefs=True)
        self.cells, self.blocks, self.meta, self.links = [], [], {}, []
        self.headings, self.paragraphs = [], []
        self.cell, self.href = [], ''
        self.in_cell = self.in_script = self.in_heading = self.in_paragraph = False

    def handle_starttag(self, tag, attrs):
        """Apre celle, intestazioni, paragrafi e raccoglie meta e link."""
        values = dict(attrs)
        if tag == 'td' or tag == 'th':
            self.in_cell, self.cell, self.href = True, [], ''
        elif tag == 'script' and (values.get('type') or '').lower() == 'application/ld+json':
            self.in_script, self.cell = True, []
        elif tag == 'meta':
            name = (values.get('name') or values.get('property') or '').lower()
            if name in ('description', 'og:description', 'twitter:description') and values.get('content'):
                self.meta.setdefault(name, values['content'].strip())
        elif tag in ('h1', 'h2', 'h3'):
            self.in_heading, self.cell = True, []
        elif tag == 'p':
            self.in_paragraph, self.cell = True, []
        if tag == 'a' and values.get('href'):
            self.links.append(values['href'])
            if self.in_cell:
                self.href = values['href']

    def handle_endtag(self, tag):
        """Chiude l'elemento aperto e archivia il testo raccolto."""
        text = ''.join(self.cell).strip()
        if tag in ('td', 'th') and self.in_cell:
            self.cells.append((text, self.href))
            self.in_cell = False
        elif tag == 'script' and self.in_script:
            self.blocks.append(text)
            self.in_script = False
        elif tag in ('h1', 'h2', 'h3') and self.in_heading:
            self.headings.append((len(self.paragraphs), text))
            self.in_heading = False
        elif tag == 'p' and self.in_paragraph:
            self.paragraphs.append(text)
            self.in_paragraph = False

    def handle_data(self, data):
        """Accumula solo dentro un elemento che ci interessa."""
        if self.in_cell or self.in_script or self.in_heading or self.in_paragraph:
            self.cell.append(data)

    def table(self):
        """Accoppia le celle a due a due: etichetta minuscola, testo e link del valore."""
        pairs = {}
        for index in range(0, len(self.cells) - 1, 2):
            label = self.cells[index][0].strip().casefold()
            value, href = self.cells[index + 1]
            if label and label not in pairs:
                pairs[label] = (value.strip(), href)
        return pairs

    def section(self, title):
        """Il primo paragrafo che segue un'intestazione con questo titolo."""
        for position, heading in self.headings:
            if heading.strip().casefold() == title and position < len(self.paragraphs):
                return self.paragraphs[position]
        return ''

    def organisation(self):
        """La descrizione dichiarata da un nodo Organization dei dati strutturati."""
        found = []

        def walk(value):
            """Visita ricorsivamente gli oggetti strutturati."""
            if isinstance(value, list):
                for child in value:
                    walk(child)
            elif isinstance(value, dict):
                declared = value.get('@type', '')
                types = declared if isinstance(declared, list) else [declared]
                if any(str(t).endswith(('Organization', 'Corporation', 'Company')) for t in types):
                    text = value.get('description')
                    if isinstance(text, str) and text.strip():
                        found.append(text.strip())
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
        for block in self.blocks:
            try:
                walk(json.loads(block))
            except (ValueError, TypeError):
                logger.debug('Invalid JSON-LD block')
        return found[0] if found else ''


def clean(text):
    """Una riga sola, entità sciolte, spazi normalizzati."""
    return ' '.join(htmllib.unescape(text or '').split())


def outbound(address):
    """Toglie i parametri di tracciamento che l'aggregatore attacca ai link in uscita."""
    parts = urlsplit(address or '')
    if parts.scheme not in ('http', 'https'):
        return ''
    return url(parts._replace(query='', fragment='').geturl())


def profile_page(html):
    """Settore, sito ufficiale e descrizione da una scheda aziendale di aggregatore."""
    parser = PageFacts()
    parser.feed(html)
    table = parser.table()
    return {'sectors': clean(table.get('vertical', ('', ''))[0]),
            'website': outbound(table.get('website', ('', ''))[1]),
            'description': clean(parser.section('company info'))}


def company_site(html, address):
    """Descrizione dal sito aziendale: dati strutturati, poi meta, poi «chi siamo»."""
    parser = PageFacts()
    parser.feed(html)
    declared = clean(parser.organisation())
    if len(declared) >= MINIMUM_DESCRIPTION:
        return {'description': declared, 'method': 'sito_dati_strutturati', 'follow': ''}
    meta = clean(parser.meta.get('description') or parser.meta.get('og:description')
                 or parser.meta.get('twitter:description'))
    follow = ''
    for href in parser.links:
        candidate = urljoin(address, href)
        if (registrable(candidate) == registrable(address) and ABOUT.search(urlsplit(candidate).path or '')
                and candidate.rstrip('/') != address.rstrip('/')):
            follow = candidate
            break
    if len(meta) >= MINIMUM_DESCRIPTION:
        return {'description': meta, 'method': 'sito_meta', 'follow': follow}
    return {'description': '', 'method': '', 'follow': follow}


def about_page(html):
    """Il testo di una pagina «chi siamo», ridotto alle righe leggibili."""
    if access_block(html):
        raise ValueError(access_block(html))
    parser = PageFacts()
    parser.feed(html)
    declared = clean(parser.organisation())
    if len(declared) >= MINIMUM_DESCRIPTION:
        return declared
    longest = max((clean(p) for p in parser.paragraphs), key=len, default='')
    if len(longest) >= MINIMUM_DESCRIPTION:
        return longest
    text = ' '.join(lines(html))
    return clean(text)[:2000] if len(text) >= MINIMUM_DESCRIPTION else ''


def ad_text(archive, cid):
    """Il testo dell'annuncio piu' lungo dell'azienda: l'evidenza che hanno quasi tutte."""
    row = archive.db.execute("""SELECT id,data FROM opportunities WHERE company_id=?
        AND length(trim(COALESCE(json_extract(data,'$.description'),'')))>0
        ORDER BY length(json_extract(data,'$.description')) DESC, id LIMIT 1""", (cid,)).fetchone()
    if row is None:
        return None
    job = json.loads(row['data'])
    body = "\n".join(lines(job.get('description', '')))
    return {'opportunity_id': row['id'], 'url': job.get('source_url', ''),
            'text': (job.get('title', '') + "\n" + body)[:12000]}


def one(archive, company, timeout, force=False, follow_about=True):
    """Prova le strade nell'ordine e registra **tutte** quelle valutate, non solo quella riuscita.

    Si ferma alla prima che restituisce un testo. Le altre restano marcate `non_applicabile`
    o `non_provata`, cosi' una descrizione mancante non si confonde mai con una mai cercata.
    """
    address = company['website']
    source = kind(address)
    attempts = []
    result = {'id': company['id'], 'name': company['name'], 'kind': source, 'status': 'vuota',
              'sectors': '', 'website': '', 'description': '', 'strategy': '', 'url': address,
              'requests': 0, 'attempts': attempts}

    def note(strategy, status, url='', found='', detail='', retry_after=None):
        """Aggiunge una riga alla traccia di questa azienda."""
        attempts.append({'strategy': strategy, 'status': status, 'url': url, 'found': found,
                         'detail': detail, 'retry_after': retry_after})

    def blocked(exc):
        """Un rifiuto della fonte non e' un errore nostro: si ritenta, non si indaga."""
        return 'bloccata' if '403' in str(exc) or '999' in str(exc) or '429' in str(exc) else 'errore'

    if source == 'scheda_aggregatore':
        try:
            result['requests'] += 1
            html = fetch(address, timeout)
            if access_block(html):
                raise ValueError(access_block(html))
            found = profile_page(html)
            note('scheda_aggregatore', 'trovata' if found['description'] else 'vuota', address,
                 found['description'], 'settore=%s sito=%s' % (found['sectors'] or '-', found['website'] or '-'))
            result.update(found, strategy='scheda_aggregatore', url=address,
                          status='trovata' if found['description'] else 'vuota')
            if found['description']:
                note('sito_aziendale', 'non_provata', detail='la scheda ha gia dato una descrizione')
                note('annuncio', 'non_provata', detail='la scheda ha gia dato una descrizione')
                return result
            # La scheda ha dato il sito vero ma non il testo: si prosegue su quel sito.
            address = found['website'] or ''
            source = kind(address)
        except (OSError, ValueError) as exc:
            note('scheda_aggregatore', blocked(exc), address,
                 detail='%s: %s' % (type(exc).__name__, str(exc)[:160]))
            address, source = '', 'nessuno'
    elif source == 'non_aziendale':
        note('sito_aziendale', 'non_applicabile', address, detail='bacheca o gestionale di recruiting')
    elif source == 'nessuno':
        note('sito_aziendale', 'non_applicabile', detail='nessun indirizzo salvato')

    if source == 'sito_aziendale' and address:
        try:
            result['requests'] += 1
            html = fetch(address, timeout)
            if access_block(html):
                raise ValueError(access_block(html))
            found = company_site(html, address)
            if not found['description'] and follow_about and found['follow']:
                result['requests'] += 1
                text = about_page(fetch(found['follow'], timeout))
                if text:
                    found = {'description': text, 'method': 'sito_chi_siamo', 'follow': found['follow']}
                    address = found['follow']
            if found['description']:
                note('sito_aziendale', 'trovata', address, found['description'], found['method'])
                result.update(description=found['description'], strategy=found['method'],
                              url=address, status='trovata')
                note('annuncio', 'non_provata', detail='il sito ha gia dato una descrizione')
                return result
            note('sito_aziendale', 'vuota', address, detail='nessun testo utilizzabile nella pagina')
        except (OSError, ValueError) as exc:
            note('sito_aziendale', blocked(exc), address,
                 detail='%s: %s' % (type(exc).__name__, str(exc)[:160]))

    # Ultima strada, senza rete: il testo dell'annuncio. Per chi arriva solo da LinkedIn e'
    # l'unica che esista; il contratto dei dati è in docs/data-model.md.
    candidate = ad_text(archive, company['id'])
    if candidate is None:
        note('annuncio', 'non_applicabile', detail='nessun annuncio con descrizione in archivio')
        return result
    note('annuncio', 'trovata', candidate['url'], candidate['text'],
         'da sintetizzare dall annuncio ' + candidate['opportunity_id'])
    # Il testo grezzo di un annuncio non e' una descrizione aziendale: la sintesi la fa il
    # passaggio di riscrittura, che legge proprio questa traccia.
    result.update(status='da_sintetizzare', strategy='annuncio', url=candidate['url'])
    return result


def save(archive, found, force=False):
    """Riempie solo i campi vuoti, registra la provenienza e l'intera traccia dei tentativi."""
    row = archive.db.execute('SELECT website,description,sectors FROM companies WHERE id=?',
                             (found['id'],)).fetchone()
    if row is None:
        raise ValueError('Company not found')
    written = []
    # Il sito vero sostituisce sempre la scheda dell'aggregatore: quella non era un sito.
    replace_site = found['kind'] == 'scheda_aggregatore'
    updates = {'website': (found['website'], replace_site or not row['website'].strip()),
               'sectors': (found['sectors'], force or not row['sectors'].strip()),
               'description': (found['description'], force or not row['description'].strip())}
    provenance = json.dumps({'source': found['strategy'], 'url': found['url'], 'retrieved_at': now(),
                             'rewritten_by': None, 'rewritten_at': None}, ensure_ascii=False)
    with archive.db:
        for attempt in found.get('attempts', []):
            archive.record_profile_attempt(found['id'], attempt['strategy'], attempt['status'],
                                           attempt['url'], attempt['found'], attempt['detail'],
                                           attempt['retry_after'])
        for field, (value, allowed) in updates.items():
            if value and allowed:
                archive.db.execute('UPDATE companies SET %s=? WHERE id=?' % field, (value, found['id']))
                written.append(field)
        if 'description' in written:
            archive.db.execute('UPDATE companies SET description_provenance=? WHERE id=?',
                               (provenance, found['id']))
    return written


def recover(archive, limit=None, company_ids=None, force=False, only_missing=True,
            follow_about=True, timeout=25, delay=1.0, progress=None):
    """Riempie i dati aziendali mancanti lasciando traccia di ogni strada provata."""
    scope = 'SELECT id,name,website,description,sectors FROM companies'
    arguments = ()
    if company_ids is not None:
        scope += ' WHERE id IN (SELECT value FROM json_each(?))'
        arguments = (json.dumps(sorted(company_ids)),)
    candidates = []
    for row in archive.db.execute(scope + ' ORDER BY id', arguments):
        company = dict(row)
        # Una scheda di aggregatore si legge comunque: porta il sito vero, che non abbiamo.
        if (only_missing and not force and company['description'].strip()
                and kind(company['website']) != 'scheda_aggregatore'):
            continue
        candidates.append(company)
    selected = candidates if limit is None else candidates[:limit]
    report = {'task': 'company_profile', 'eligible': len(candidates), 'selected': len(selected),
              'written': {}, 'strategies': {}, 'esiti': {}, 'items': [], 'started_at': now()}
    for index, company in enumerate(selected, 1):
        found = one(archive, company, timeout, force=force, follow_about=follow_about)
        found['written'] = save(archive, found, force=force)
        for field in found['written']:
            report['written'][field] = report['written'].get(field, 0) + 1
        if found['strategy']:
            report['strategies'][found['strategy']] = report['strategies'].get(found['strategy'], 0) + 1
        report['esiti'][found['status']] = report['esiti'].get(found['status'], 0) + 1
        report['items'].append({k: v for k, v in found.items() if k not in ('description', 'attempts')} |
                               {'description_chars': len(found['description']),
                                'attempts': [{k: v for k, v in a.items() if k != 'found'}
                                             for a in found['attempts']]})
        logger.info('Company profile %s: %s (%s)', company['id'], found['status'], found['strategy'] or '-')
        if progress:
            progress({'phase': 'company_profile', 'done': index, 'total': len(selected),
                      'trovata': report['esiti'].get('trovata', 0), 'name': company['name']})
        if found['requests']:
            time.sleep(delay)
    report['finished_at'] = now()
    report['status'] = 'partial' if report['esiti'].get('errore') else 'success'
    archive.run('company_profile', report['status'], report)
    return report


# Ordine di fiducia: una pagina aziendale batte un annuncio, che parla soprattutto del ruolo.
REWRITE_ORDER = ('scheda_aggregatore', 'sito_aziendale', 'annuncio')


def rewrite_input(archive, cid):
    """Il testo grezzo migliore raccolto su un'azienda, con la strada che l'ha portato.

    Legge la traccia, non il campo `description`: cosi' una riscrittura successiva riparte
    sempre dall'originale e non da una parafrasi di una parafrasi.
    """
    rows = {r['strategy']: r for r in archive.db.execute(
        "SELECT strategy,found,url FROM company_profile_attempts WHERE company_id=? AND status='trovata'", (cid,))}
    name = archive.db.execute('SELECT name FROM companies WHERE id=?', (cid,)).fetchone()
    if name is None:
        raise ValueError('Company not found')
    for strategy in REWRITE_ORDER:
        row = rows.get(strategy)
        if row and (row['found'] or '').strip():
            return {'company': name[0], 'source': strategy, 'url': row['url'],
                    'source_text': row['found'][:12000]}
    return None


def demo():
    """Controllo eseguibile dei due estrattori, senza rete."""
    card = '''<html><body>
      <table><tbody>
        <tr><td>Vertical</td><td>Clean Energy</td></tr>
        <tr><td>Website</td><td><a href="https://sungrowpower.com?ref=climatetechlist.com&amp;utm_source=climatetechlist.com">sungrowpower.com</a></td></tr>
        <tr><td>Additional company info</td><td><a href="https://www.eindata.com/?x=1">EIN</a></td></tr>
      </tbody></table>
      <h2> Company Info </h2><p>Sungrow is a leading provider of solar inverters and energy storage systems worldwide.</p>
    </body></html>'''
    found = profile_page(card)
    assert found['sectors'] == 'Clean Energy', found
    assert found['website'] == 'https://sungrowpower.com/', found
    assert found['description'].startswith('Sungrow is a leading provider'), found

    structured = '''<html><head><script type="application/ld+json">
      {"@type":"Organization","description":"Acme builds industrial heat pumps for food and beverage factories across Europe and Asia."}
      </script><meta name="description" content="Careers at Acme"></head><body></body></html>'''
    found = company_site(structured, 'https://acme.example')
    assert found['method'] == 'sito_dati_strutturati', found
    assert found['description'].startswith('Acme builds industrial'), found

    meta_only = ('<html><head><meta property="og:description" content="Trustpair is the leading payment '
                 'fraud prevention and account validation platform for finance teams.">'
                 '</head><body><a href="/about-us">About</a></body></html>')
    found = company_site(meta_only, 'https://trustpair.example')
    assert found['method'] == 'sito_meta', found
    assert found['follow'] == 'https://trustpair.example/about-us', found

    short = '<html><head><meta name="description" content="Home"></head><body><a href="/chi-siamo">Chi siamo</a></body></html>'
    found = company_site(short, 'https://tiny.example')
    assert found['description'] == '' and found['follow'] == 'https://tiny.example/chi-siamo', found

    assert kind('https://www.climatetechlist.com/company/sungrow?ref=ctl') == 'scheda_aggregatore'
    assert kind('https://acme.breezy.hr/') == 'non_aziendale'
    assert kind('https://www.austrianairlines.co.at/') == 'sito_aziendale'
    assert kind('') == 'nessuno'
    print('company_profile: tutti i controlli passano')


if __name__ == '__main__':
    demo()
