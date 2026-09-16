"""Read-only pipeline coverage and timestamps, without running the work being monitored."""

import json
import logging
from collections import Counter

from jobhunter.workspace import ROOT, now, search_rules_hash
from jobhunter.evaluation.selection import filters, verdicts
from jobhunter.evaluation.tier import TIERS, KEEP, DROP, UNKNOWN
from jobhunter.operations.progress import snapshot

logger = logging.getLogger(__name__)


def it(value):
    """Migliaia col punto: in una frase un numero deve avere la stessa forma che ha nella barra accanto.

    La soglia a cinque cifre non e' un capriccio: e' quello che fa toLocaleString('it-IT') nel browser,
    che sotto le diecimila non raggruppa. Allineare la prosa al disegno vale piu' della regola tipografica.
    """
    return f'{value:,}'.replace(',', '.') if abs(value) >= 10000 else str(value)


def summary(archive, cfg, root=ROOT):
    """Report saved evidence, missing work and stale derived results for every pipeline stage."""
    db = archive.db
    totals = {name: db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
              for name in ('companies', 'opportunities')}
    count = totals['opportunities']
    company_count = totals['companies']
    rules_hash = search_rules_hash(filters())
    role_counts = Counter()
    dates = {}
    with_description = set()
    described = set()
    for row in db.execute('''SELECT o.id,o.company_id,o.data,o.content_hash,o.last_seen,o.first_seen,
            e.status,e.content_hash cached_hash,e.rules_hash
            FROM opportunities o LEFT JOIN search_eligibility e ON e.opportunity_id=o.id'''):
        job = json.loads(row['data'])
        if row['status']:
            if row['cached_hash'] == row['content_hash'] and row['rules_hash'] == rules_hash:
                role_counts['filtered'] += 1
            else:
                role_counts['filters_stale'] += 1
        if (job.get('description') or '').strip():
            with_description.add(row['company_id'])
            described.add(row['id'])
            role_counts['with_description'] += 1
        for key, value in [('observed', row['last_seen']), ('imported', row['first_seen']),
                           ('description', (job.get('description_provenance') or {}).get('retrieved_at'))]:
            if value:
                dates[key] = max(dates.get(key, ''), value)
    # I due verdetti d'asse e il tier, calcolati adesso. Nessuno di questi numeri è salvato (DESIGN §2).
    assessment = verdicts(archive)
    tiers = Counter(state['tier'] for state in assessment.values())
    company_axis = Counter(state['azienda']['verdetto'] for state in assessment.values())
    axis, judges = Counter(), Counter()
    # L'esito finale non dice cosa ha deciso ogni giudice: la catena sì, ed e' gia' in memoria.
    regex_axis, journey = Counter(), Counter()
    for state in assessment.values():
        for oid, verdict in state['ruoli'].items():
            axis[verdict['verdetto']] += 1
            judges[verdict['giudice'] or 'nessuno'] += 1
            chain = {j['giudice']: j['verdetto'] for j in verdict['catena']}
            regex_axis[chain['regex']] += 1
            # Senza descrizione il regex gira lo stesso, ma vede solo il titolo: un'esclusione dal
            # titolo resta definitiva, tenere o non sapere no. Quella parzialità va detta.
            if oid not in described and chain['regex'] != DROP:
                regex_axis['solo_titolo'] += 1
            if chain['regex'] == UNKNOWN:
                journey['remote_decisi' if chain.get('llm_remoto') in (KEEP, DROP)
                        else 'attesa_descrizione' if oid not in described else 'aperti'] += 1
    giudicabili = journey['remote_decisi'] + journey['aperti']
    described_count = role_counts['with_description']
    # Un annuncio senza esito locale salvato non e' ne' dentro ne' fuori: resta un residuo visibile,
    # cosi' la somma delle quote di ogni tappa e' sempre l'archivio intero e il denominatore non cambia.
    senza_esito = max(0, count - regex_axis[KEEP] - regex_axis[UNKNOWN] - regex_axis[DROP])
    fermi = max(0, count - axis[DROP] - axis[KEEP] - journey['aperti'])
    blind = {r[0] for r in db.execute("SELECT id FROM companies WHERE description=''")} - with_description
    funnel = {'archive': {'jobs': count, 'companies': company_count},
              'ruolo': {key: axis[key] for key in ('tieni', 'non_so', 'scarta')},
              'giudici': dict(judges),
              'azienda': {key: company_axis[key] for key in ('interessante', 'evidenza_mancante', 'non_interessante')},
              'tier': {key: tiers[key] for key in TIERS},
              'basis': ('I due assi si valutano separatamente e il tier si ricalcola a ogni lettura. Gli esiti dei modelli '
                        'sono quelli salvati da tutte le esecuzioni: sono proposte, non esclusioni definitive.'),
              # Il percorso di un annuncio, passaggio per passaggio, sempre sullo stesso denominatore:
              # ogni tappa riparte dall'archivio intero e mostra quanti ne sono gia' usciti, cosi' una
              # quota non va mai riletta contro un totale diverso da quello della tappa precedente.
              'percorso': [
                  {'title': '1 · Raccolta', 'unit': 'annunci in archivio', 'base': count,
                   'parts': [['seg-in', 'raccolti e normalizzati', count]],
                   'note': 'Questo totale è il denominatore di tutte le tappe: ogni barra qui sotto è larga uguale.'},
                  {'title': '2 · Recupero descrizioni', 'unit': 'annunci', 'base': count,
                   'parts': [['seg-keep', 'con il testo completo', described_count],
                             ['seg-pending', 'solo titolo: meno evidenza per i giudici', count - described_count]],
                   'note': 'Nessun annuncio esce di scena qui: il passaggio decide solo con quanta evidenza verrà giudicato.'},
                  {'title': '3 · Giudice 1 · regex', 'unit': 'annunci', 'base': count,
                   'parts': [['seg-gone', 'usciti: scartati dalle regole', regex_axis[DROP]],
                             ['seg-keep', 'compatibili col profilo', regex_axis[KEEP]],
                             ['seg-review', 'da decidere: passano al giudice 2', regex_axis[UNKNOWN]],
                             ['seg-pending', 'senza esito locale salvato', senza_esito]],
                   'note': f"Di questi, {it(regex_axis['solo_titolo'])} giudicati sul solo titolo perché la descrizione manca: "
                           'il verdetto può cambiare quando il testo arriva. Le esclusioni dal titolo restano valide.'},
                  {'title': '4 · Giudice 2 · Qwen', 'unit': 'annunci', 'base': count,
                   'parts': [['seg-gone', 'usciti: scartati da regex o Qwen', axis[DROP]],
                             ['seg-keep', 'compatibili al termine del percorso', axis[KEEP]],
                             ['seg-review', 'ancora da chiamare: API da pagare', journey['aperti']],
                             ['seg-pending', 'fermi in attesa di informazioni', fermi]],
                   'note': f"{it(journey['attesa_descrizione'])} dei fermi aspettano la descrizione; {it(journey['remote_decisi'])} "
                           'annunci sono stati decisi qui. Sono esiti salvati da tutte le esecuzioni: proposte, non esclusioni definitive.'},
                  {'title': '5 · Aziende risultanti', 'unit': 'aziende in archivio', 'base': company_count,
                   'unit_change': True,
                   'parts': [['seg-gone', 'usciti: scarto', tiers['scarto']],
                             ['seg-keep', 'Tier A · azienda e ruolo sì', tiers['A']],
                             ['seg-in', 'Tier B · da tenere d’occhio', tiers['B-attesa'] + tiers['B-esperienza']],
                             ['seg-pending', 'evidenza aziendale mancante', tiers['evidenza-mancante']]],
                   'note': 'Cambia l’unità di misura: qui il totale sono le aziende, non gli annunci. '
                           f"{it(tiers['evidenza-mancante'])} restano ferme in attesa di informazioni, non respinte."}]}
    oldest = db.execute('SELECT min(last_seen) FROM opportunities').fetchone()[0]
    attempts = [dict(r) for r in db.execute('SELECT status,count(*) count FROM description_attempts GROUP BY status')]
    recovered = db.execute('SELECT max(last_success_at),max(checked_at) FROM description_attempts').fetchone()
    dates['description'] = max(filter(None, [dates.get('description'), recovered[0]]), default=None)
    filter_date = db.execute("SELECT updated_at FROM pipeline_updates WHERE step='filters'").fetchone()
    dates['filters'] = filter_date[0] if filter_date else None
    remote = db.execute("SELECT max(created_at) FROM enrichments WHERE task LIKE 'remote:%'").fetchone()
    steps = []

    def stage(key, title, done, total, updated, note, done_label, rest_label='', scope='', stale=0,
              measure='', extra=(), parts=None, base=None, inflow=''):
        """Say what each number counts, so a bare ratio never has to be guessed from the card.

        `extra` porta le barre in piu' di un passaggio che lavora su due popolazioni diverse:
        senza, una card come il giudice 2 mostra meta' del lavoro che ha fatto.

        `inflow` dice quanti ne sono arrivati e da dove: e' l'unico posto in cui un numero derivato
        per sottrazione si spiega, e in cui un cambio di unita' di misura si dichiara invece di
        lasciare al lettore il salto fra annunci e aziende.

        `base` e' il totale che la barra disegna quando differisce da quello su cui si misura la
        copertura: una quota che non potra' mai essere lavorata va vista, non deve pero' tenere
        la card per sempre su «copertura parziale».
        """
        steps.append({'id': key, 'title': title, 'done': done, 'total': total, 'inflow': inflow,
                      'base': total if base is None else base,
                      'done_label': done_label, 'rest_label': rest_label, 'scope': scope,
                      'measure': measure, 'extra': list(extra), 'parts': parts,
                      'pending': total - done if total is not None else None, 'stale': stale,
                      'updated_at': updated, 'note': note,
                      'state': 'empty' if not total and total is not None else 'stale' if stale else
                      'complete' if total is not None and done == total else 'partial' if done else 'missing'})

    stage('collection', 'Raccolta e importazione', count, None, dates.get('observed'),
          'Ultima osservazione registrata sulla fonte. Non certifica che gli annunci siano ancora aperti.',
          'annunci raccolti finora', scope='Le fonti non dichiarano un totale: non esiste una copertura completa da raggiungere.')
    stage('normalization', 'Normalizzazione e raggruppamento', count, count, dates.get('imported'),
          f"Avviene durante ogni importazione. {it(company_count)} aziende distinte; la data indica l'ultimo nuovo annuncio inserito.",
          f'annunci raggruppati sotto {it(company_count)} aziende', scope='Automatico a ogni raccolta: non si avvia a mano.',
          inflow=f'Riceve i {it(count)} annunci raccolti dal passaggio 1.')
    stage('descriptions', 'Recupero descrizioni', company_count - len(blind), company_count, dates.get('description'),
          'Il recupero risponde alla copertura aziendale, non ai filtri sui ruoli: ogni azienda senza evidenza propria ha '
          f"diritto al suo primo annuncio, qualunque cosa il regex pensi di quei ruoli. {it(role_counts['with_description'])} "
          f'annunci su {it(count)} hanno il testo completo. Restano validi fonti supportate, blocchi e decisioni sulle aziende.',
          'aziende con evidenza propria', "ancora cieche: l'asse azienda non è valutabile",
          f'Su tutte le {it(company_count)} aziende in archivio.', measure='Aziende in archivio',
          inflow=f'Riceve le {it(company_count)} aziende del raggruppamento. Cambia unità di misura: qui si conta per '
                 'azienda, non per annuncio, perché a un’azienda cieca basta il testo di un suo annuncio qualsiasi.',
          extra=[{'title': 'Annunci in archivio', 'total': count, 'parts': [
              ['seg-in', 'con il testo completo', role_counts['with_description']],
              ['seg-pending', 'ancora senza testo: nessun giudizio semantico è possibile',
               count - role_counts['with_description']]]}])
    stage('filters', 'Giudice 1 · regex su titolo e descrizione', role_counts['filtered'], count, dates.get('filters'),
          f"Legge solo gli annunci: il titolo con i pattern, la descrizione per anni richiesti, gestione di "
          f"persone e lingue. La descrizione dell'azienda non la guarda: quella decide la categoria, al passaggio 5. "
          f"Il regex marca, non elimina: {it(regex_axis[KEEP])} ruoli compatibili, {it(regex_axis[DROP])} esclusi, "
          f"{it(regex_axis[UNKNOWN])} lasciati ai giudici successivi.",
          'annunci analizzati con le regole attuali', 'da rianalizzare: regole o testo sono cambiati',
          f"Esito: {it(regex_axis[UNKNOWN])} «non so» proseguono verso il modello remoto.", role_counts['filters_stale'],
          measure='Copertura sugli annunci in archivio',
          inflow=f'Riceve tutti i {it(count)} annunci in archivio, con o senza descrizione. Torna a contare per annuncio.',
          extra=[{'title': 'Esito del regex', 'total': count, 'parts': [
              ['seg-keep', 'compatibili col profilo', regex_axis[KEEP]],
              ['seg-review', 'lasciati ai giudici successivi', regex_axis[UNKNOWN]],
              ['seg-exclude', 'scartati dalle regole', regex_axis[DROP]]]},
              {'title': 'Evidenza usata per giudicare', 'total': count, 'parts': [
                  ['seg-in', 'titolo e descrizione', role_counts['with_description']],
                  ['seg-pending', 'solo il titolo: verdetto provvisorio', regex_axis['solo_titolo']],
                  ['seg-out', 'solo il titolo, ma già escluso dal titolo stesso',
                   count - role_counts['with_description'] - regex_axis['solo_titolo']]]}])
    stage('remote', 'Giudice 2 · Qwen sui «non so»', journey['remote_decisi'], giudicabili, remote[0],
          funnel['basis'], 'annunci decisi dal modello remoto', 'ancora senza verdetto',
          'Le schede aziendali si producono dopo il tier, su Tier A e B.',
          measure='«Non so» arrivati dal regex', base=regex_axis[UNKNOWN],
          inflow=f"Riceve i {it(regex_axis[UNKNOWN])} «non so» del passaggio 4. Di questi {it(journey['attesa_descrizione'])} "
                 f'sono fermi senza descrizione e la chiamata non parte: restano {it(giudicabili)} annunci chiamabili, '
                 'ed è su quelli che si misura la copertura.',
          parts=[['seg-keep', 'decisi dal modello remoto', journey['remote_decisi']],
                 ['seg-review', 'ancora senza verdetto: chiamate API da pagare', journey['aperti']],
                 ['seg-pending', 'fermi: manca la descrizione, la chiamata non parte',
                  journey['attesa_descrizione']]])
    runs = [dict(r) for r in db.execute('''SELECT source,status,created_at,
        json_extract(detail,'$.task') task FROM runs ORDER BY id DESC LIMIT 12''')]
    order = ['collection', 'normalization', 'descriptions', 'filters', 'remote']
    steps.sort(key=lambda step: order.index(step['id']))
    workflow = snapshot(root, cfg) if archive.path.resolve() == (root / cfg['database']).resolve() else {
        'status': 'not_found', 'message': 'Nessun report collegato a questo archivio.'}
    logger.info('Pipeline snapshot: %s opportunities, %s companies', count, company_count)
    return {'generated_at': now(), **totals, 'funnel': funnel, 'oldest_observation': oldest, 'steps': steps,
            'description_attempts': attempts, 'last_description_attempt': recovered[1], 'runs': runs,
            'workflow': {k: workflow[k] for k in ('status', 'phase_label', 'started_at', 'checkpoint_age_seconds', 'warnings', 'message') if k in workflow}}
