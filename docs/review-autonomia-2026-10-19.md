# Review: si può lanciare JobHunter la sera e trovare risultati al mattino?

Review del 19 ottobre 2026. Codice a `b27a550` (nessuna modifica dal 25 settembre). Fatta da
quattro reviewer in parallelo (fonti, passaggi a pagamento, orchestrazione e stato, logica di
selezione), con verifica sul codice dei punti principali, più prove dal vivo gratuite e test.

## Stato delle correzioni

- **Passi 1 e 2 applicati il 19/10/2026, ancora senza commit.** Risolti A1, A2, C1, B3 (interruttore
  sugli errori, non un tetto di spesa) e la parte di A5 che riguarda descrizioni, `collect-all` e
  passaggi a pagamento. Un PID riusato non conta più come vivo. Le righe orfane non bloccano gli
  avvii. I report si chiudono a qualunque uscita. Jev e Qwen proseguono dopo un errore su un singolo
  record e si fermano, chiudendo `failed`, per chiave o credito rifiutati o su troppi errori. La
  sequenza prende lo stato del suo passaggio peggiore. Ci sono 11 test nuovi, e tutti i 190 test
  passano.
- **Restano aperti** i passi 3-7: LinkedIn 429, ATS a bacheca, comando notturno, cache e limiti,
  regole di selezione.

## Verdetto

- **Cosa va.** Il nucleo è solido: ogni risposta dei modelli si salva appena arriva, le cache evitano di
  ripagare e un'interruzione si riprende senza danni. Passano tutti i 179 test unitari. Oggi le fonti
  rispondono e le due chiavi API sono valide.
- **Cosa no.** Oggi una notte da sola non funziona. Mancano un comando unico che faccia tutto e la
  protezione contro lo sleep del PC. Al primo intoppo (un 429 di LinkedIn, un errore di rete, una
  risposta malformata) i passaggi si fermano, ma lo stato finale scrive comunque «Sequenza completata».
  In più la dashboard oggi è bloccata da uno stato fantasma del 24 settembre.
- **Anche i risultati hanno errori silenziosi.** Alcune aziende hanno categorie sbagliate dedotte da
  parole generiche. Gli annunci JobSpy vecchi non scadono mai. Alcune lingue solo «desiderate» e alcuni
  anni di esperienza mal letti escludono ruoli validi.
- **Cosa rifare.** Sei interventi mirati sull'autonomia e un giro sulle regole di selezione, elencati
  in fondo, prima di fidarsi di una notte. Nessuno richiede di riscrivere l'architettura.

## Prove dal vivo (19/10, gratuite, niente scritto nell'archivio)

| Cosa | Esito |
|---|---|
| Test unitari | 179/179 passano |
| `tests/pipeline_browser_check.py` | passa |
| `tests/browser_check.py` | **fallisce**, ma è il test a essere vecchio: si aspetta 5 menu filtro (ora 6, c'è «Tipo: dottorati») e 4 pulsanti di raccolta (ora 5, c'è ATS). Con i due conteggi corretti in una copia, passa tutto senza errori nella pagina |
| Chiave Qwen (DashScope) | valida, il modello configurato è disponibile |
| Chiave Jev (TypeSafe) | valida (richiesta vuota respinta con 422, non con 401). Il credito residuo non si può leggere |
| Airtable | 11.264 righe |
| JobSpy LinkedIn / Indeed | 10/10 righe ciascuno; LinkedIn senza descrizione, come previsto |
| ATS, una bacheca per provider | 8 provider su 9 rispondono; la bacheca Personio di prova dà 404 (bacheca rimossa o spostata) |
| Pagine annuncio LinkedIn | **la prima ha risposto 429**, le due successive (3 s dopo) sono andate a buon fine |

## Cosa serve oggi per una notte completa (a mano)

1. Sbloccare lo stato del 24/9 (vedi A1).
2. Da terminale: `python main.py collect-all` e aspettare che finisca. È l'unico comando che raccoglie
   da tutte le fonti; non concatena altro.
3. Aprire la dashboard e lanciare «Continua da qui» dal passo *Filtri*, in modalità *execute*, con
   «tutte le aziende» spuntato per Qwen. Le schede Qwen si possono scrivere solo dalla dashboard: non
   esiste un comando da terminale.
4. Lasciare il PC acceso e collegato alla corrente: a batteria va in sospensione dopo 3 minuti e niente lo impedisce.

La sequenza della dashboard che parte da *Raccolta* non è la raccolta completa: legge **una sola
fonte, al massimo 100 annunci**. `company-profile` e `ats-discover` restano comandi separati.

## Problemi, per gravità

Etichette: **[verificato]** = letto sul percorso esatto del codice o riprodotto; **[plausibile]** =
ragionamento sul codice, non riprodotto.

### A. Fermano o sprecano la notte

**A1. La dashboard oggi rifiuta ogni avvio.** [verificato] Il report della raccolta del 24/9
(`data/collection/2026-09-24T090030.073862_0000-official/report.json`) è rimasto su «running».
Il suo PID (37228) oggi appartiene a un altro programma (`R.exe`), e `process_alive`
(`jobhunter/operations/progress.py`) controlla solo che il PID esista. `main.py status` risponde
«running, processo vivo», e `start()` (`pipeline_actions.py`) risponde «Un workflow esterno risulta in
esecuzione». Lo stesso difetto vale per le righe `pipeline_jobs` dopo un crash o un riavvio.
Rimedio: confrontare l'orario di avvio del processo con quello del report.

**A2. Jev e Qwen si fermano al primo errore che non è un 429/5xx.** [verificato] In
`system_one.py` e `company_batch.py`, qualunque errore di trasporto (timeout, DNS, connessione
chiusa), un 401, un 400 o una risposta troncata (`finish_reason` diverso da `stop`) impostano
`stopped`. Da quel momento non parte più nessuna richiesta.
- È già successo: il job 48 (Qwen, 22/9) si è fermato a 4.043 aziende su 5.908 per una sola risposta incompleta.
- Le aziende vanno sempre nello stesso ordine e quella rifiutata non entra in cache, quindi si fermerebbe ogni notte nello stesso punto.
- Al 22/9 Jev aveva circa lo 0,5% di errori di trasporto, quindi una run da qualche migliaio di chiamate quasi certamente si ferma presto.

Rimedio: un errore di contenuto deve far fallire solo quel record. Gli errori prima dell'invio vanno
ritentati. La run si ferma solo per errori di autenticazione o di credito, o se gli errori superano una
soglia.

**A3. Un solo 429 di LinkedIn blocca il recupero descrizioni per 24 ore.** [verificato sul codice;
429 osservato oggi] In `descriptions.recover`, un 403, 429 o 999 mette l'host fra i bloccati per tutto
il lotto. Gli annunci restanti diventano «skipped», e il tentativo bloccato si salva con
`retry_after` a 24 ore, che fermano anche le run successive.
- Oggi LinkedIn ha risposto 429 alla prima pagina e servito le due successive, quindi il blocco è a raffiche.
- In sequenza le descrizioni partono subito dopo la raccolta JobSpy, che ha appena interrogato
  LinkedIn molte volte: è il momento peggiore.
- Mancano le descrizioni di 3.302 annunci LinkedIn su 6.403, e senza descrizione Jev non li giudica.

Rimedio: una pausa crescente e un nuovo tentativo, non lo stop per 24 ore. Lasciare una pausa fra la
raccolta e le descrizioni.

**A4. La fase ATS non salva nulla finché non ha letto tutte le bacheche, ed è muta.** [verificato]
`ats.rows` accumula tutto in memoria e l'importazione avviene solo alla fine. Durante la fase il
report non viene aggiornato. È coerente con il 24/9: Airtable importato, poi la fase ATS morta senza
lasciare traccia (nessuna riga `ats` in `runs`). Su questo archivio la fonte ATS non è mai arrivata in
fondo.
- Le bacheche Workday e SmartRecruiters fanno una chiamata in più per ogni annuncio nuovo.
- I lotti da 8 bacheche aspettano la più lenta.
- Lo «Stop» non arriva ai thread ATS: lo stato di cancellazione è `threading.local`.
- Un 429 su una pagina di dettaglio viene ignorato e la bacheca risulta comunque «completa».

Rimedio: importare e salvare il checkpoint bacheca per bacheca, e contare i blocchi.

**A5. Eccezioni non catturate lasciano report «running» per sempre.** [verificato]
- `fetch()` legge con `response.raw.read`, che solleva eccezioni di urllib3 (`ReadTimeoutError`,
  `ProtocolError`, `DecodeError`). Non sono né `OSError` né `ValueError`, quindi escono da
  `descriptions.recover`, che non ha try/finally.
- In `sweep.py` il file di configurazione di ogni fonte si legge fuori dal `try`. Anche la chiusura
  degli annunci e la rimozione degli HTML sono fuori dal `try`.
- Nei passaggi a pagamento, un `database is locked` o un `IncompleteRead` interrompono la run e
  scartano le risposte già pagate in volo.

Ognuno di questi lascia uno stato «running» che poi blocca gli avvii, come in A1.

**A6. Nessun comando unico e nessuna protezione dallo sleep.** [verificato] Vedi la procedura sopra.
La sequenza gira in un thread del server della dashboard: se si chiude il server o il PC va in
sospensione, la run muore. Nessuna chiamata a `SetThreadExecutionState`. Le variabili Telegram in
`.env` non sono usate da nessuna parte, quindi non arriva nessuna notifica di fine o di errore.

**A7. Le query LinkedIn sulla Repubblica Ceca vanno sempre in crash.** [verificato nei log] JobSpy
solleva `Invalid country string: 'dominican republic'` leggendo una scheda e perde tutta la query. È
successo in 7 log su 7 query LinkedIn/Czech Republic esaminate. Lo sweep JobSpy resta quindi sempre
«partial».

### B. La run finisce, ma i dati sono sbagliati o mancano senza segnale

**B1. Qwen con il limite predefinito ripassa sempre le stesse 100 aziende.** [verificato]
`company_batch.run` taglia la lista a `company_limit` (100) **prima** di scartare le aziende già fatte.
Senza «tutte le aziende», ogni run guarda le stesse 100, non avanza e risulta riuscita.

**B2. Cambiare un parametro tecnico fa ricomprare tutto.** [verificato]
- La firma della cache di Qwen include l'intero blocco `company_batch` della configurazione, compresi
  `workers` e `max_attempts`. Cambiare `workers` nel file rende vecchie le 852 schede aziendali e le
  913 schede ruolo.
- La firma delle categorie di Jev include `flag_above` e `choice_confidence`, che sono anche soglie del
  ruolo. Ritoccarle invalida le 4.485 categorie.

**B3. Nessun tetto di spesa né interruttore sul tasso di rifiuti.** [verificato] In sequenza Jev
gira con `all`, quindi senza limite. Se un cambio di formato del provider rende inutilizzabili tutte le
risposte, la run le paga tutte e non salva nulla. La notte dopo le ripaga. Il danno è limitato a una
passata per run. Il volume misurato a settembre per una passata Qwen completa è di circa 3.600
chiamate e 9 milioni di token. Il prezzo attuale non l'ho verificato.

**B4. Chiusura degli annunci: guardia debole e, per Airtable, mai attiva.** [verificato]
- `closure.detect` confronta gli annunci da chiudere, presi solo nella finestra di 7 giorni, con tutti
  gli annunci aperti della fonte. Così la soglia del 50% non protegge davvero.
- Airtable si chiude solo con stato `success`, ma ha sempre 18 righe rifiutate. Quindi i suoi circa
  11.000 annunci non vengono mai chiusi.
- Per l'ATS, un'azienda con più bacheche conta come «letta per intero» anche se una sola bacheca ha
  risposto: gli annunci delle altre rischiano la chiusura.

Ricorda che la chiusura cancella gli HTML salvati.

**B5. Pagine «morbide» di LinkedIn.** [plausibile] Una pagina di login servita con codice 200 non
contiene la descrizione e viene registrata come `unsupported_parser` senza data di nuovo tentativo:
quell'annuncio non verrà più ritentato finché non cambia la versione del parser.

#### Logica di selezione

Dove indicato, i conteggi vengono dal reviewer su una copia dell'archivio. I percorsi del codice li ho
verificati io.

**B6. Categorie aziendali sbagliate prese dalle parole degli annunci, e mai più riviste.**
[verificato]
- Quando settore e descrizione dell'azienda non bastano, `company_axis.categorize` cerca parole chiave
  nelle frasi degli annunci. Parole generiche come «building» o «space» bastano a decidere: «we are
  building an AI team» diventa *Edilizia*, «the analytics space» diventa *Aerospazio*.
- Il risultato si salva con metodo `rules`, che Jev considera definitivo e non rivede mai
  (`system_one.py`, `pending`).
- Secondo il reviewer, 8 delle 185 aziende di Tier A e 35 di Tier B in attesa dipendono da queste categorie.

**B7. Gli annunci JobSpy non si chiudono mai, e niente scade per età.** [verificato]
- Per JobSpy la chiusura guarda solo gli annunci pubblicati nella finestra di 7 giorni. Gli annunci più
  vecchi, cioè tutti i 12.130 già in archivio, restano aperti per sempre.
- `fresh_days: 30` in `role_filters.json` non è letto da nessuna parte del codice.
- Il reviewer conta 695 ruoli che oggi sostengono un Tier A o B: sono annunci JobSpy con più di 31
  giorni o senza data.

**B8. Lingue: le formulazioni morbide diventano requisiti.** [verificato]
- Mancano «preferably», «ideally», «desirable», «a plus», «idealerweise», «est un plus».
- «Fluent in German and/or English» esclude.
- Il reviewer conta 28 annunci esclusi solo per una lingua desiderata.
- Inoltre l'impronta delle regole (`search_rules_hash`) non include le costanti di `languages.py`:
  correggere quelle liste lascerebbe in vista i verdetti vecchi come se fossero aggiornati.

**B9. «No direct reports» esclude come se fosse gestione di persone.** [verificato dal reviewer]
La negazione riconosce «no», «not required» e «without», ma non «will not have», «does not include»,
«None» o un titolo di sezione vuoto. Sono 13 gli annunci esclusi solo per questo.

**B10. Un feedback successivo cancella il tuo «salvato».** [verificato] `review.py` prende l'ultima riga
di feedback e la scarta se non è salvato/scartato. Se segni «contattato» un ruolo salvato, il ruolo
perde il tuo verdetto e il regex torna a decidere.

**B11. Anni di esperienza letti male.** [verificato dal reviewer su input sintetici]
- «1 to 3 years» e «between 1 and 3 years» si leggono come 3 ed escludono, mentre «1-3» resta aperto.
- «Up to 5 years» si legge 5.
- «3 years or a PhD» esclude, perché l'alternativa del titolo di studio è ignorata: sono 49 casi su 841
  esclusioni per esperienza.

**B12. Verdetti di Jev non più validi mostrati come attuali.** [verificato dal reviewer]
- 47 risposte riguardano annunci il cui testo è cambiato dopo la risposta.
- Le domande di Jev (`system-one-questions.json`) sono state modificate il 23/9, dopo l'ultima run del
  22/9. Quindi tutte le 6.783 risposte risultano vecchie e la prossima run le ripagherà tutte. È voluto,
  ma va saputo.

**B13. Un annuncio senza descrizione non può mai diventare «tieni».** [verificato] Il regex può solo
escludere e Jev non parte senza testo. Il reviewer conta 449 annunci con titolo prioritario fermi così.
In più 43 aziende figurano come «B · attesa: nessun ruolo ora» anche se un ruolo adatto ce l'hanno.

**B14. Altri difetti minori della selezione.**
- Annunci chiusi e aziende che hai scartato restano candidati per le chiamate pagate. Oggi non succede,
  perché la tabella `closures` è vuota.
- Le varianti del nome («Porsche AG» e «Porsche», «CSEM» e «CSEM SA») creano aziende separate:
  sono 58 in più.
- I dottorati sono esclusi da parole del tema di ricerca («Electricity Markets», «Mechanical
  Characterisation»).

### C. Lo stato dice cose non vere

**C1. «Sequenza completata» anche quando non lo è.** [verificato]
- Jev e Qwen restituiscono solo `success` o `partial`, mai `failed`, anche quando si sono fermati
  presto o la chiave è stata rifiutata.
- La sequenza si ferma solo su `failed`.
- Lo stato della sequenza è quello dell'ultimo passo.
- `start()` controlla che la chiave esista, non che funzioni.

**C2. «Parziale» è lo stato di sempre.** [verificato] Descrizioni, Airtable, JobSpy e Qwen chiudono
sempre «partial», per motivi innocui: host non supportati, 18 righe vuote, query senza risultati.
Così un blocco totale non si distingue da una notte normale.

**C3. La pagina Pipeline smette di aggiornarsi al primo errore di rete.** [verificato]
`schedulePipeline` in `dashboard/app.js` mostra l'errore ma non riprogramma il controllo, e la pagina
resta ferma sull'ultimo «In esecuzione».

**C4. `status` mostra un solo report.** [plausibile] Mostra solo il più recente. Un `collect-all` in
corsa può essere nascosto da un comando lanciato dopo. I comandi da terminale non scrivono in
`pipeline_jobs`, quindi dashboard e terminale possono spendere due volte sullo stesso lavoro.

### D. Minori

- `dashboard.log` (3,4 MB) non ha rotazione. Dal launcher `.pyw` stdout e stderr sono nulli, quindi
  un'eccezione fuori dal logger si perde.
- `collect-all` esce con codice 2 anche nel caso normale («partial»), quindi uno script non lo
  distingue da un errore.
- `collect-all --fresh` cancella anche i risultati pagati di Jev e Qwen. Il backup c'è, ma l'help non
  lo dice.
- `timeout=25` limita solo connessione e pause di lettura, non la durata totale di una richiesta.
- `company-profile` riprova a ogni run i siti morti, senza attesa fra un tentativo e l'altro.
- `tests/browser_check.py` va aggiornato (due conteggi).

## Cosa regge

- Ogni risposta di Jev e Qwen viene validata e salvata appena arriva, nel thread che possiede
  SQLite. Un crash perde solo le chiamate in volo.
- La validazione è severa: probabilità fra 0 e 1, opzioni solo dal catalogo, citazioni che devono
  esistere nel testo. Una risposta sbagliata non diventa mai un verdetto di ripiego. Jev è fissato a
  `jev-1.13.0` su tutte le righe.
- Le cache e il `rekey` evitano di ripagare quando cambiano solo le regole.
- JobSpy gira in sottoprocessi con timeout, e le pagine raccolte si importano man mano.
- Il blocco di un host per le descrizioni è condiviso fra i thread e salvato su disco. Il problema
  di A3 è la sua durata, non il meccanismo.
- La dashboard non avvia due passi insieme. Il lavoro continua anche con la scheda del browser chiusa.
- L'anteprima è la modalità predefinita e le chiavi non finiscono mai nei messaggi di errore.

## Piano di correzione proposto (da confermare)

In ordine, ogni passo verificabile da solo:

1. **Sblocco e stati fantasma.** `process_alive` confronta l'orario di avvio del processo. Ogni
   report e ogni riga `pipeline_jobs` chiude sempre con try/finally. Il report del 24/9 si chiude come
   «interrotto». (A1, A5)
2. **Errori per record, non per run.** Per Jev e Qwen: un errore di contenuto scarta solo quel record,
   gli errori prima dell'invio si ritentano, un interruttore scatta su autenticazione, credito o tasso
   di errori. Se la run si ferma presto chiude `failed`, e la sequenza prende lo stato peggiore fra i
   passi. (A2, C1, B3)
3. **LinkedIn 429.** Pausa crescente e nuovo tentativo invece dello stop per 24 ore. Togliere la
   Repubblica Ceca dalle query LinkedIn. (A3, A7)
4. **ATS a bacheca.** Importazione e checkpoint per bacheca, blocchi contati, Stop funzionante,
   «letta per intero» solo se tutte le bacheche dell'azienda hanno risposto. (A4, B4)
5. **Un comando notturno.** `main.py nightly`: raccolta completa, descrizioni, filtri, Jev, Qwen, una
   riga `pipeline_jobs` per passo, PC tenuto sveglio, riepilogo finale e notifica Telegram facoltativa.
   Si può lanciare a mano o dall'Utilità di pianificazione di Windows. (A6)
6. **Cache e limiti.** Firme della cache solo sui campi che cambiano il risultato; il limite di Qwen
   applicato dopo aver tolto le aziende già fatte. (B1, B2)

7. **Selezione (da discutere insieme, regola per regola).** Categorie dagli annunci con metodo
   provvisorio che Jev può rivedere; scadenza per età (`fresh_days`) o chiusura degli annunci JobSpy
   non più visti; lingue «desiderate»; negazioni dei «direct reports»; anni «X to Y» e alternative col
   dottorato; costanti delle lingue nell'impronta delle regole; feedback salvato che resta valido. (B6-B14)

Il passo 5 è il solo che aggiunge una funzione nuova; gli altri correggono quella esistente. Prima di
una notte vera conviene una prova breve: la stessa sequenza con limiti piccoli, guardando il riepilogo
del mattino.
