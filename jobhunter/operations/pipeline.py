"""Read-only pipeline coverage and timestamps, without running the work being monitored."""

import json
import logging
from collections import Counter

from jobhunter.workspace import ROOT, now, search_rules_hash
from jobhunter.evaluation.selection import filters, verdicts
from jobhunter.evaluation.tier import TIERS, KEEP, DROP, UNKNOWN, NO_EVIDENCE
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
    usable = set()
    jobs = {}
    filters_outdated = set()
    for row in db.execute('''SELECT o.id,o.company_id,o.data,o.content_hash,o.last_seen,o.first_seen,
            e.status,e.decision,e.content_hash cached_hash,e.rules_hash
            FROM opportunities o LEFT JOIN search_eligibility e ON e.opportunity_id=o.id'''):
        job = json.loads(row['data'])
        jobs[row['id']] = job
        decision = json.loads(row['decision']) if row['decision'] else {}
        if row['status']:
            if row['cached_hash'] == row['content_hash'] and row['rules_hash'] == rules_hash:
                role_counts['filtered'] += 1
            else:
                role_counts['filters_stale'] += 1
                filters_outdated.add(row['id'])
        else:
            role_counts['filters_missing'] += 1
            filters_outdated.add(row['id'])
        if (job.get('description') or '').strip():
            with_description.add(row['company_id'])
            described.add(row['id'])
            role_counts['with_description'] += 1
        if decision.get('verification', {}).get('description_usable',
                                                bool((job.get('description') or '').strip())):
            usable.add(row['id'])
            role_counts['judgeable'] += 1
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
    regex_axis = Counter()
    role_states = {}
    for state in assessment.values():
        for oid, verdict in state['ruoli'].items():
            role_states[oid] = verdict
            axis[verdict['verdetto']] += 1
            judges[verdict['giudice'] or 'nessuno'] += 1
            chain = {j['giudice']: j['verdetto'] for j in verdict['catena']}
            regex_axis[chain['regex']] += 1
            # Senza descrizione il regex gira lo stesso, ma vede solo il titolo: un'esclusione dal
            # titolo resta definitiva, tenere o non sapere no. Quella parzialità va detta.
            if oid not in usable and chain['regex'] != DROP:
                regex_axis['solo_titolo'] += 1
    # Partizione operativa al confine fra regex e Jev. Jev e' l'unico giudice semantico:
    # una sua risposta `review` resta visibile e attende l'utente.
    handoff = Counter()
    for oid, job in jobs.items():
        judged_by = {j['giudice']: j['verdetto'] for j in role_states[oid]['catena']}
        regex_verdict = judged_by['regex']
        if oid in filters_outdated:
            handoff['stale'] += 1
        elif regex_verdict == DROP:
            handoff['regex_discarded'] += 1
        elif oid not in usable:
            handoff['blocked'] += 1
        elif judged_by.get('jev') in (KEEP, DROP):
            handoff['jev_compatible' if judged_by['jev'] == KEEP else 'jev_discarded'] += 1
        elif 'jev' not in judged_by:
            handoff['jev_ready'] += 1
        else:
            handoff['jev_review'] += 1
    handoff_order = (
        ('seg-gone', 'Scartati dal regex', 'regex_discarded'),
        ('seg-keep', 'Compatibili secondo Jev', 'jev_compatible'),
        ('seg-gone', 'Scartati da Jev', 'jev_discarded'),
        ('seg-review', 'Indecisi dopo Jev', 'jev_review'),
        ('seg-in', 'Candidati a Jev', 'jev_ready'),
        ('seg-pending', 'Bloccati: dati non utilizzabili', 'blocked'),
        ('seg-stale', 'Da aggiornare o verificare', 'stale'),
    )
    handoff_parts = [[color, label, handoff[key]] for color, label, key in handoff_order]
    axis = Counter({
        KEEP: handoff['jev_compatible'],
        DROP: handoff['regex_discarded'] + handoff['jev_discarded'],
        UNKNOWN: handoff['jev_review'] + handoff['jev_ready'] + handoff['blocked'] + handoff['stale'],
    })
    judges = Counter({
        'regex': handoff['regex_discarded'],
        'jev': handoff['jev_compatible'] + handoff['jev_discarded'],
        'nessuno': axis[UNKNOWN],
    })
    described_count = role_counts['judgeable']
    # Un annuncio senza esito locale salvato non e' ne' dentro ne' fuori: resta un residuo visibile,
    # cosi' la somma delle quote di ogni tappa e' sempre l'archivio intero e il denominatore non cambia.
    senza_esito = max(0, count - regex_axis[KEEP] - regex_axis[UNKNOWN] - regex_axis[DROP])
    blind = {r[0] for r in db.execute("SELECT id FROM companies WHERE description=''")} - with_description
    funnel = {'archive': {'jobs': count, 'companies': company_count},
              'ruolo': {key: axis[key] for key in ('tieni', 'non_so', 'scarta')},
              'giudici': {key: value for key, value in judges.items() if value},
              'azienda': {key: company_axis[key] for key in ('interessante', 'evidenza_mancante', 'non_interessante')},
              'tier': {key: tiers[key] for key in TIERS},
              'basis': ('Il tier si ricalcola dagli esiti salvati dei due assi. Gli annunci “da aggiornare o '
                        'verificare” possono cambiarlo alla prossima elaborazione. Gli esiti automatici sono proposte, '
                        'non decisioni definitive.'),
              'handoff': {
                  'title': 'Dal regex a Jev', 'unit': 'annunci', 'base': count,
                  'parts': handoff_parts,
                  'note': ('È una partizione dell’archivio letta adesso. Jev vede solo ciò che il regex non ha deciso, '
                           'quindi le quote non si sovrappongono. “Candidati” indica annunci che la preparazione può '
                           'ancora selezionare; limiti, payload e parametri decidono quali partiranno. Una risposta '
                           '“review” corrente è già stata valutata e non implica una nuova chiamata.'),
                  'counts': {key: handoff[key] for _, _, key in handoff_order}},
              # Il percorso di un annuncio, passaggio per passaggio, sempre sullo stesso denominatore:
              # ogni tappa riparte dall'archivio intero e mostra quanti ne sono gia' usciti, cosi' una
              # quota non va mai riletta contro un totale diverso da quello della tappa precedente.
              'percorso': [
                  {'title': '1 · Raccolta', 'unit': 'annunci in archivio', 'base': count,
                   'parts': [['seg-in', 'raccolti e normalizzati', count]],
                   'note': 'Questo totale è il denominatore di tutte le tappe: ogni barra qui sotto è larga uguale.'},
                  {'title': '2 · Recupero descrizioni', 'unit': 'annunci', 'base': count,
                   'parts': [['seg-keep', 'con testo utilizzabile dai giudici', described_count],
                             ['seg-pending', 'senza mansioni utilizzabili', count - described_count]],
                   'note': ('La presenza di una stringa non basta: qui il testo conta solo se contiene mansioni che il '
                            'giudice semantico può leggere. Nessun annuncio viene eliminato in questo passaggio.')},
                   {'title': '3 · Filtro regex', 'unit': 'annunci', 'base': count,
                    'parts': [['seg-gone', 'usciti: scartati dalle regole', regex_axis[DROP]],
                              ['seg-review', 'non scartati: passano a Jev', regex_axis[UNKNOWN]],
                              ['seg-pending', 'senza esito locale salvato', senza_esito]],
                   'note': f"Di questi, {it(regex_axis['solo_titolo'])} giudicati sul solo titolo perché la descrizione manca: "
                           'il verdetto può cambiare quando il testo arriva. Le esclusioni dal titolo restano valide.'},
                  {'title': '4 · Giudice 2 · Jev', 'unit': 'annunci', 'base': count,
                   'parts': handoff_parts,
                   'note': ('I «non so» del regex passano a Jev, unico giudice semantico. La barra distingue risultati '
                            'correnti, review già valutate, candidati a una futura preparazione, blocchi sui dati e '
                            'risultati da aggiornare. Non prevede le chiamate che partiranno.')},
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
              measure='', extra=(), parts=None, base=None, inflow='', workloads=()):
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
        workloads = list(workloads)
        workload_done = sum(item['completed'] for item in workloads)
        workload_total = sum(item['total'] for item in workloads)
        state_done = workload_done if workloads else done
        state_total = workload_total if workloads else total
        steps.append({'id': key, 'title': title, 'done': done, 'total': total, 'inflow': inflow,
                      'base': total if base is None else base,
                      'done_label': done_label, 'rest_label': rest_label, 'scope': scope,
                      'measure': measure, 'extra': list(extra), 'parts': parts, 'workloads': workloads,
                      'pending': total - done if total is not None else None, 'stale': stale,
                      'updated_at': updated, 'note': note,
                      'state': 'empty' if not state_total and state_total is not None else 'stale' if stale else
                      'complete' if state_total is not None and state_done == state_total else
                      'partial' if state_done else 'missing'})

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
    stage('filters', 'Filtro regex su titolo e descrizione', role_counts['filtered'], count, dates.get('filters'),
          f"Legge solo gli annunci: il titolo con i pattern, la descrizione per anni richiesti, gestione di "
          f"persone e lingue. Anni, lingue e gestione di persone restano qui anche adesso: sono conteggi e "
          f"confronti, e a un modello System One non si chiedono. La descrizione dell'azienda non la guarda: "
          f"quella decide la categoria, nel passaggio dedicato all'asse azienda. "
          f"Il regex può soltanto escludere: {it(regex_axis[DROP])} annunci scartati e "
          f"{it(regex_axis[UNKNOWN])} passati a Jev.",
          'annunci analizzati con le regole attuali', 'da rianalizzare: regole o testo sono cambiati',
          'Su tutti gli annunci in archivio.',
          role_counts['filters_stale'] + role_counts['filters_missing'],
           measure='Copertura sugli annunci in archivio',
          inflow=f'Riceve tutti i {it(count)} annunci in archivio, con o senza descrizione. Torna a contare per annuncio.',
           extra=[{'title': 'Esito del regex', 'total': count, 'parts': [
               ['seg-review', 'non scartati: passano a Jev', regex_axis[UNKNOWN]],
               ['seg-exclude', 'scartati dalle regole', regex_axis[DROP]]]},
              {'title': 'Evidenza usata per giudicare', 'total': count, 'parts': [
                  ['seg-in', 'titolo e descrizione', role_counts['with_description']],
                  ['seg-pending', 'solo il titolo: verdetto provvisorio', regex_axis['solo_titolo']],
                  ['seg-out', 'solo il titolo, ma già escluso dal titolo stesso',
                   count - role_counts['with_description'] - regex_axis['solo_titolo']]]}])
    # I non scartati dal regex, letti dallo stesso `handoff`: decisi da Jev, ancora indecisi,
    # non ancora interrogati, bloccati o scaduti. La somma è esatta.
    jev_decided = handoff['jev_compatible'] + handoff['jev_discarded']
    jev_date = db.execute("SELECT max(created_at) FROM enrichments WHERE task IN ('jev:selection','jev:category')").fetchone()[0]
    methods = Counter({r['method']: r['n'] for r in db.execute(
        "SELECT method,count(DISTINCT company_id) n FROM categories GROUP BY method")})
    stage('jev', 'Giudice 2 · Jev sceglie il ruolo e il settore', jev_decided + handoff['jev_review'],
          regex_axis[UNKNOWN] - handoff['blocked'], jev_date,
          'È l’ultimo giudice automatico: quello che lascia indeciso resta indeciso finché non lo guardi tu. '
          'Nella stessa richiesta valuta il ruolo e, quando serve, il settore dell’azienda. Le domande sono '
          'indipendenti; il codice applica soglie e cancelli e salva i due risultati separatamente.',
          'annunci con una risposta corrente', 'candidati non ancora interrogati',
          'Sugli annunci non scartati e con mansioni utilizzabili.',
          measure='Annunci non scartati dal regex', base=regex_axis[UNKNOWN],
          inflow=f"Riceve i {it(regex_axis[UNKNOWN])} annunci non scartati dal regex. {it(handoff['blocked'])} sono bloccati da dati "
                 f"non utilizzabili, quindi la copertura si misura sui "
                 f"{it(regex_axis[UNKNOWN] - handoff['blocked'])} restanti.",
          parts=[['seg-keep', 'compatibili', handoff['jev_compatible']],
                 ['seg-exclude', 'scartati', handoff['jev_discarded']],
                 ['seg-review', 'indecisi: nessun altro giudice automatico li rivede', handoff['jev_review']],
                 ['seg-in', 'candidati, non ancora interrogati', handoff['jev_ready']],
                 ['seg-pending', 'bloccati: manca la descrizione o non è utilizzabile', handoff['blocked']],
                 ['seg-stale', 'da aggiornare o verificare', handoff['stale']]],
          extra=[{'title': 'Asse azienda · settori', 'total': company_count, 'parts': [
              ['seg-keep', 'assegnate da Jev', methods['jev']],
              ['seg-in', 'assegnate dalle regole o a mano',
               sum(v for k, v in methods.items() if k != 'jev')],
              ['seg-pending', '«Da classificare»: nessuna prova sufficiente', company_axis[NO_EVIDENCE]]]}])
    # L'ultimo passaggio non giudica: si misura su quante schede mancano, non su quanti verdetti.
    # Come per i verdetti, qui si conta la presenza di una riga e non la validità della sua cache:
    # rivalidarla vorrebbe dire ricostruire il payload di ogni ruolo a ogni lettura della pagina.
    written_cards = {row[0] for row in db.execute("SELECT record_id FROM enrichments WHERE task='remote:job-summary'")}
    written_company_cards = {row[0] for row in db.execute("SELECT record_id FROM enrichments WHERE task='remote:company-summary'")}
    wanted_cards, wanted_company_cards = set(), set()
    wanted_card_tiers = Counter()
    for cid, state in assessment.items():
        if state['tier'] in ('A', 'B-attesa', 'B-esperienza'):
            wanted_company_cards.add(cid)
        if state['tier'] in ('A', 'B-esperienza'):
            eligible = {oid for oid, verdict in state['ruoli'].items()
                        if verdict['verdetto'] == KEEP and oid in usable}
            wanted_cards |= eligible
            wanted_card_tiers[state['tier']] += len(eligible)
    carded_jobs = len(wanted_cards & written_cards)
    carded_companies = len(wanted_company_cards & written_company_cards)
    company_card_tiers = {key: tiers[key] for key in ('A', 'B-attesa', 'B-esperienza')}
    stage('remote', 'Schede · Qwen per annunci e aziende',
          carded_jobs, len(wanted_cards), remote[0],
          'Non giudica e non assegna categorie: quelle arrivano dai giudici precedenti. Scrive, ed è l’unico '
          'passaggio che lo fa. Una scheda si produce dopo l’assegnazione del tier e solo su Tier A e B: '
          'riscrivere la scheda di un’azienda che poi si scarta è lavoro pagato e buttato (DESIGN §4).',
          'ruoli sopravvissuti con una scheda', 'ancora senza scheda',
          'Le due code usano unità diverse e seguono i tier correnti.',
          measure='Ruoli compatibili di Tier A e B, con mansioni leggibili',
          inflow='Qwen non giudica. Scrive due tipi di scheda, conteggiati separatamente.',
          workloads=[
              {'id': 'job-cards', 'label': 'Schede annuncio', 'completed': carded_jobs,
               'total': len(wanted_cards), 'tiers': {key: wanted_card_tiers[key]
                                                     for key in ('A', 'B-esperienza')},
               'note': (f"{it(wanted_card_tiers['A'])} ruoli Tier A + "
                        f"{it(wanted_card_tiers['B-esperienza'])} Tier B esperienza")},
              {'id': 'company-cards', 'label': 'Schede azienda', 'completed': carded_companies,
               'total': len(wanted_company_cards), 'tiers': company_card_tiers,
               'note': (f"{it(tiers['A'])} Tier A + {it(tiers['B-attesa'])} Tier B attesa + "
                        f"{it(tiers['B-esperienza'])} Tier B esperienza")},
          ])
    runs = [dict(r) for r in db.execute('''SELECT source,status,created_at,
        json_extract(detail,'$.task') task FROM runs ORDER BY id DESC LIMIT 12''')]
    order = ['collection', 'normalization', 'descriptions', 'filters', 'jev', 'remote']
    steps.sort(key=lambda step: order.index(step['id']))
    logger.info('Pipeline snapshot: %s opportunities, %s companies', count, company_count)
    return {'generated_at': now(), **totals, 'funnel': funnel, 'oldest_observation': oldest, 'steps': steps,
            'description_attempts': attempts, 'last_description_attempt': recovered[1], 'runs': runs,
            'workflow': workflow(archive, cfg, root)}


def workflow(archive, cfg, root=ROOT):
    """Stato dell'eventuale workflow esterno su questo archivio. È vivo: età del checkpoint compresa."""
    state = snapshot(root, cfg) if archive.path.resolve() == (root / cfg['database']).resolve() else {
        'status': 'not_found', 'message': 'Nessun report collegato a questo archivio.'}
    return {k: state[k] for k in ('status', 'phase_label', 'started_at', 'checkpoint_age_seconds', 'warnings', 'message') if k in state}
