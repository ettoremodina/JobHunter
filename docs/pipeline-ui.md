# Avviare e seguire la pipeline dalla web app

Su Windows aprire `Avvia JobHunter.pyw` con doppio clic. Il launcher usa il Python della `.venv` quando disponibile e apre direttamente la pagina Pipeline. Serve l'associazione Windows dei file `.pyw` con Python. Una piccola finestra permette di riaprire il browser o fermare il server quando non ci sono operazioni in corso. I log sono in `dashboard.log`. Se una dashboard è già attiva sulla porta configurata, viene aperta quella: dopo aggiornamenti del codice occorre riavviare il vecchio server.

## Card e avvio

La mappa mostra la sequenza con card e frecce. Ogni card riporta copertura, data e stato. Cliccare per aprire parametri, risultato dell'ultima esecuzione e pulsante di avvio. Normalizzazione e raggruppamento sono automatici durante l'importazione; la card spiega il comportamento. Ogni card ha un riquadro «Cosa fa» che dice su cosa lavora il passaggio e se classifica o si limita a preparare. La valutazione manuale non è un passaggio della pipeline: avviene dopo, nella tab Salvate.

Le operazioni disponibili sono raccolta per fonte, filtri locali, recupero descrizioni e passaggio LLM remoto. La rilettura degli HTML salvati e l'impaginazione con Ollama sono state tolte dalle card: la prima è una riparazione una tantum, la seconda non produce più nulla da quando le schede dei ruoli arrivano da Qwen. Restano disponibili dalla CLI con `reparse-descriptions` ed `enrich description`. L'avvio chiama direttamente le funzioni Python esistenti, senza accettare comandi shell o percorsi arbitrari dal browser.

“Continua da qui” avvia in sequenza i passaggi principali successivi. La finestra mostra l'elenco e permette di impostare il successivo passaggio LLM remoto, comprese le chiamate contemporanee. Se il recupero descrizioni è successivo al passaggio scelto, considera tutte le descrizioni eleggibili. Il recupero mantiene esclusioni, cooldown e limiti della fonte già previsti dalla pipeline.

La sequenza si ferma al primo risultato parziale o errore. “Riprova” significa riaprire il passaggio, verificare i parametri e avviarlo di nuovo: i risultati validi già salvati vengono riutilizzati dove previsto dai rispettivi task. Non esistono retry automatici a pagamento. Il passaggio remoto ritenta da solo la sola richiesta che il provider ha **rifiutato** (HTTP 429 o 5xx): non ha generato niente, quindi non c'è spesa da duplicare. Un fallimento di trasporto, dove l'esito di fatturazione è ignoto, ferma la run come prima.

## Schede Qwen e ordine dei passaggi

La sequenza automatica è raccolta e normalizzazione, filtri locali, recupero descrizioni, giudice Jev e schede Qwen. Jev decide ruoli e categorie; Qwen non giudica e scrive soltanto le schede degli annunci compatibili di Tier A e B e le schede delle aziende di Tier A e B.

La card Qwen usa un ordine deterministico e una sola richiesta per azienda, che contiene tutte le schede ancora mancanti di quell'azienda. I risultati validi vengono riutilizzati: una parte respinta resta da completare senza riscrivere le parti già salvate. Le aziende oltre `max_jobs` o `max_input_chars` vengono differite intere, senza troncamento.

L’anteprima è offline e conta chiamate, schede e aziende differite senza leggere la chiave. I token delle esecuzioni includono anche le risposte respinte; il costo monetario potrebbe non essere restituito dal provider. Gli originali, i giudizi e le sintesi restano separati e recuperabili.

## Stato, persistenza e interruzioni

Il funnel, gli assi e il percorso vivono ora tutti nella tab Metriche: la pagina Pipeline resta il pannello operativo. Ogni card si apre dichiarando quanti elementi ha ricevuto e da quale passaggio: e' l'unico posto in cui un totale nato da una sottrazione viene spiegato, e in cui un cambio di unita di misura fra annunci e aziende si dichiara invece di lasciare il salto al lettore. Il giudice 2 disegna una barra sola, sui «non so» che il regex gli ha passato; la copertura resta pero misurata sugli annunci chiamabili, perche una quota ferma per descrizione mancante non deve tenere la card per sempre su copertura parziale. I numeri scritti nelle frasi hanno la stessa forma di quelli scritti nelle barre. Le card usano lo stesso formato per lavoro completato e rimanente. Il pannello attivo mostra avanzamento e nuovi esiti; consumi e contatori aggiuntivi sono in «Consumi e dettagli». I conteggi dell'archivio e quelli della singola esecuzione restano distinti.

La pagina aggiorna lo stato ogni trenta secondi mentre è aperta. La frequenza limita il lavoro del monitor sull'intero archivio; il pulsante Aggiorna stato permette una lettura manuale. Le esecuzioni vengono salvate in SQLite nella tabella `pipeline_jobs`: parametri, orario, stato, progressi, risultato e impronta degli input. Chiudere la scheda del browser non ferma il worker. Il server deve rimanere attivo.

La web app accetta una sola operazione alla volta, comprese le raccolte avviate dalla pagina Fonti. Le nuove esecuzioni usano anche un controllo SQLite per evitare duplicati da un'altra istanza della dashboard. I workflow esterni rilevati come attivi sull'archivio principale impediscono un nuovo avvio; avvii CLI successivi non partecipano a questo coordinamento, quindi non mescolarli con il pannello operativo.

Se il processo proprietario termina senza registrare il risultato, l'esecuzione appare interrotta e può essere ripresa. Un processo ancora vivo o non verificabile viene trattato prudentemente come attivo. La finestra del launcher impedisce la chiusura volontaria durante un lavoro; spegnimento del PC o terminazione forzata possono comunque interromperlo.

“Dati cambiati: da aggiornare” confronta gli input correnti con quelli dell'ultima esecuzione riuscita del passaggio. Il confronto è conservativo a livello di archivio e configurazione: può segnalare lavoro da controllare anche se il singolo task poi riutilizza la cache. Un'anteprima non vale come elaborazione completata. La copertura dei filtri controlla anche le impronte già presenti. Per gli altri risultati storici senza impronta di esecuzione non si inventa una certezza di aggiornamento.

Le date indicano l'ultima esecuzione o l'ultima evidenza disponibile, non garantiscono che tutti gli annunci siano stati elaborati. La card LLM remoto conta risultati aziendali salvati, senza certificare la copertura o la qualità dei singoli ruoli.

Configurazione di etichette, limiti del campione, sequenza e frequenza di aggiornamento: `config/pipeline_ui.json`. Limiti specifici di raccolta, descrizioni e modelli restano nelle configurazioni dei rispettivi componenti. Il server conserva controlli di origine, token CSRF e operazioni consentite.

La card Recupero descrizioni mostra soltanto la copertura degli annunci non esclusi dagli esiti locali attuali. Gli esiti assenti o scaduti non escludono implicitamente un annuncio. Il conteggio dei mancanti non è una promessa di richieste: feedback sulle aziende, fonti supportate, cooldown e limiti possono ridurre ulteriormente il lavoro eseguibile. Anche il recupero effettivo usa i filtri completi correnti, compresi i dati già disponibili, anziché rivalutare soltanto il titolo.

## Interrompere un passaggio

Ogni card operativa e la sua finestra hanno Interrompi, abilitato solo per il lavoro attivo. Una richiesta di stop viene salvata in pipeline_cancellations e vale anche per il resto di una sequenza. Il worker registra interrupted al primo punto sicuro: confine fra richieste, record o passaggi. I risultati completati restano salvati. Le operazioni atomiche e le chiamate di rete già partite possono finire o raggiungere il timeout prima dello stop; il tasto non annulla la fatturazione di richieste già inviate.

Il recupero descrizioni cancella i task ancora in coda e salva le risposte dei worker già attivi. Le fasi di normalizzazione si interrompono tramite la raccolta cui appartengono. Feedback e dettagli informativi non sono processi da interrompere. I worker avviati prima dell’aggiornamento del codice non possono riconoscere il nuovo protocollo: riavviare il server quando non ci sono lavori in corso.

Le chiamate Qwen sono parallele. `company_batch.workers` stabilisce quante richieste possono restare in volo; appena una termina, il pool viene rifornito senza aspettare le altre. Preparazione e scrittura SQLite restano sul thread principale. `request_delay_seconds` cadenzia le partenze fra i worker, non limita il numero di chiamate al minuto.


### Progresso remoto
Lo step attivo ha sfondo e bordo evidenziati, etichetta e barra per aziende attraversate. I dettagli mostrano azienda corrente, annunci valutati, keep/review/exclude, cache, esclusioni locali, chiamate API, input/output token della sola run e aziende differite. Le vecchie run indicano i contatori non disponibili. Il nuovo raggruppamento richiede il riavvio del server dopo la run corrente.


### La tab Metriche: percorso, assi e distribuzioni

Tutte le metriche dell'archivio stanno in una pagina sola. Si apre con il percorso degli annunci: cinque tappe, dalla raccolta alle aziende risultanti. Ogni tappa è una barra larga quanto l'intero archivio, sempre lo stesso denominatore. Il numero e la percentuale sono scritti dentro ogni quota, così una quota di una tappa si confronta a occhio con quella di un'altra senza rifare il conto.

La fascia scura a sinistra è chi è già uscito nelle tappe precedenti: cresce da una riga alla successiva, e quel bordo che scivola verso destra è il funnel. Quello che resta a destra è ciò che prosegue: compatibili in verde, ancora da decidere in ambra, fermi per informazione mancante in grigio. Nessuna quota è mai affidata al solo colore: il numero è nella barra quando ci sta, e comunque nel riepilogo espandibile sotto il disegno, con le etichette per esteso e le note di ogni tappa. La quinta tappa cambia unità di misura e lo dichiara: lì il totale sono le aziende.

Un annuncio senza esito locale salvato resta un residuo visibile invece di sparire dal conto: la somma delle quote di ogni tappa è sempre l'archivio intero. Le tappe sugli annunci sono monotone, chi è uscito non rientra. Il percorso riguarda sempre tutto l'archivio, anche quando il filtro della pagina è attivo, e la pagina lo dichiara.

Seguono i due assi indipendenti e il tier, ciascuno come partizione completa: una barra impilata per il colpo d'occhio e sotto un metro per quota, con conteggio e percentuale. Poi la qualità e la composizione della selezione, che seguono il filtro della pagina: completezza dei dati, esito dei filtri locali, categorie aziendali e distribuzione geografica. Ogni metro è disegnato contro il totale della scheda, mai contro il massimo della lista, così due voci di schede diverse restano confrontabili. Le distribuzioni lunghe mostrano le prime voci e raccolgono la coda in una riga sola invece di troncarla.

Gli esiti dei modelli sono quelli salvati da tutte le esecuzioni: sono proposte, non esclusioni definitive. Nessuna operazione di filtraggio o API viene avviata dalla lettura.

### Unita di misura

Una azienda e esclusa localmente solo se non ha annunci rimasti. Cache locale mancante o scaduta non causa esclusioni implicite.
I giudizi Jev salvati sui soli annunci passati dal regex formano quattro gruppi disgiunti: keep, review, exclude, senza giudizio. La loro somma coincide con gli annunci affidati al secondo giudice. Sono risultati storici salvati, non una certificazione di validità con le domande correnti; la UI esplicita questo limite. La presenza di schede Qwen non misura la selezione.
La run corrente rimane separata: aziende attraversate includono quelle saltate; risultati in cache nelle vecchie run possono comprendere piu elaborazioni dello stesso annuncio. Token input/output riguardano solo la run. Nessuna operazione di filtraggio o API viene avviata dal monitor.

### Salvate: la scelta a mano, dopo la pipeline

La tab «Salvate» ha preso il posto di «La mia coda». Nella tab Aziende «Salva azienda» e «Salva ruolo» mettono da parte quello che interessa; la tab Salvate li elenca e apre la scheda al suo interno, senza i filtri della tab Aziende. Da lì si prepara il testo per la chat, per una singola azienda o per tutte le salvate insieme: la pagina prepara un testo da copiare e non invia nulla.

La scelta manuale è un campo a parte e non tocca i verdetti della pipeline: nella scheda si legge la tua decisione accanto al giudizio del regex o del modello, non al suo posto. Per questo la valutazione manuale non è più una card della pipeline, e con la coda è sparito anche lo step «Coda di selezione», che serviva solo a riempirla. Le domande «Da chiarire insieme» e le preferenze proposte restano in fondo alla tab, in un riquadro richiudibile; accettare una preferenza ora è solo un promemoria, perché la coda che le applicava non esiste più.

### Debug: le domande di controllo

La tab «Debug» risponde a una domanda per volta sull'archivio, in sola lettura: quali aziende non hanno una descrizione, quali annunci sono stati scartati e per quale motivo, quali sono rimasti aperti dal regex senza mai arrivare al modello, quali non hanno categoria, quali località non trovano una città nella mappa. Le domande divise per motivo mostrano i gruppi con il loro conteggio; ogni riga apre l'azienda nella tab Aziende. Aggiungere una domanda significa aggiungere una voce a `LENSES` in `jobhunter/exploration/debug.py`.

Fra le domande c'è anche la lingua in cui l'annuncio è scritto, riconosciuta sul testo originale contando le parole funzione: quando l'annuncio passa viene tradotto e riassunto, e quell'informazione andrebbe persa. Non è la lingua richiesta dall'annuncio, che resta un requisito a parte: serve a segnalare gli annunci in una lingua che non conosci, anche quando non la chiedono. Sotto le venti parole la risposta è «non determinata» invece di una lingua a caso.

I filtri della tab Aziende sono Tier, ricerca testuale, Paese, Città, Fonte e Categoria. «Asse ruolo» e «Stato» sono stati tolti: il primo chiedeva il verdetto del regex sui ruoli, il secondo le decisioni già prese, e sono due domande da controllo, non da scelta. Vivono nella tab Debug, con le lenti «Annunci scartati dal regex», «Aziende con ruoli compatibili ma senza evidenza aziendale» e «Aziende su cui hai già deciso». La colonna Stato resta nella tabella, e la CLI conserva `search --status` e `--eligibility`.
