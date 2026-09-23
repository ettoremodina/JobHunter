# Avviare e seguire la pipeline dalla web app

Su Windows aprire `Avvia JobHunter.pyw` con doppio clic. Il launcher usa il Python della `.venv` quando disponibile e apre direttamente la pagina Pipeline. Serve l'associazione Windows dei file `.pyw` con Python. Una piccola finestra permette di riaprire il browser o fermare il server quando non ci sono operazioni in corso. I log sono in `dashboard.log`. Se una dashboard è già attiva sulla porta configurata, viene aperta quella: dopo aggiornamenti del codice occorre riavviare il vecchio server.

## Card e avvio

La mappa mostra la sequenza con card e frecce. Ogni card riporta copertura, data e stato. Cliccare per aprire parametri, risultato dell'ultima esecuzione e pulsante di avvio. Normalizzazione e raggruppamento sono automatici durante l'importazione; la card spiega il comportamento. Ogni card ha un riquadro «Cosa fa» che dice su cosa lavora il passaggio e se classifica o si limita a preparare. La valutazione manuale non è un passaggio della pipeline: avviene dopo, nella tab Salvate.

Le operazioni disponibili sono raccolta per fonte, filtri locali, recupero descrizioni,
giudizio Jev e schede Qwen. La rilettura degli HTML salvati è una riparazione una tantum
disponibile dalla CLI con `reparse-descriptions`; l'impaginazione locale con Ollama è
stata rimossa. L'avvio chiama direttamente le funzioni Python esistenti, senza accettare
comandi shell o percorsi arbitrari dal browser.

“Continua da qui” avvia in sequenza i passaggi principali successivi. La finestra mostra l'elenco e permette di impostare il successivo passaggio LLM remoto, comprese le chiamate contemporanee. Se il recupero descrizioni è successivo al passaggio scelto, considera tutte le descrizioni eleggibili. Il recupero mantiene esclusioni, cooldown e limiti della fonte già previsti dalla pipeline.

La sequenza prosegue dopo un risultato parziale e si ferma su errore o interruzione.
“Riprova” significa riaprire il passaggio, verificare i parametri e avviarlo di nuovo: i
risultati validi già salvati vengono riutilizzati dove previsto dai rispettivi task. Non
esistono retry automatici a pagamento. Il passaggio remoto ritenta da solo la richiesta
che il provider ha **rifiutato** (HTTP 429 o 5xx): non ha generato niente, quindi non c'è
spesa da duplicare. Un fallimento di trasporto, dove l'esito di fatturazione è ignoto,
ferma la run come prima.

## Schede Qwen e ordine dei passaggi

La sequenza automatica è raccolta e normalizzazione, filtri locali, recupero descrizioni, giudice Jev e schede Qwen. Jev decide ruoli e categorie; Qwen non giudica e scrive soltanto le schede degli annunci compatibili di Tier A e B e le schede delle aziende di Tier A e B.

La card Qwen usa un ordine deterministico e una sola richiesta per azienda, che contiene tutte le schede ancora mancanti di quell'azienda. I risultati validi vengono riutilizzati: una parte respinta resta da completare senza riscrivere le parti già salvate. Le aziende oltre `max_jobs` o `max_input_chars` vengono differite intere, senza troncamento.

L’anteprima è offline e conta chiamate, schede e aziende differite senza leggere la chiave. I token delle esecuzioni includono anche le risposte respinte; il costo monetario potrebbe non essere restituito dal provider. Gli originali, i giudizi e le sintesi restano separati e recuperabili.

## Stato, persistenza e interruzioni

Gli esiti vivono nella tab Metriche: la pagina Pipeline resta il pannello operativo. Ogni card dichiara cosa riceve e mostra soltanto lavoro completato, da fare e bloccato. Non presenta compatibili, scartati o indecisi, perché quelli sono esiti dei giudici e non stati di esecuzione. Il filtro regex può solo escludere; tutti gli annunci non scartati e leggibili passano a Jev. La card Qwen espone due code separate: schede annuncio sui ruoli compatibili di Tier A e B esperienza, schede azienda su tutte le aziende Tier A, B attesa e B esperienza. Non somma mai annunci e aziende. Il pannello attivo mostra avanzamento della run; consumi e contatori aggiuntivi sono in «Consumi e dettagli».

La pagina aggiorna lo stato ogni trenta secondi mentre è aperta. La frequenza limita il lavoro del monitor sull'intero archivio; il pulsante Aggiorna stato permette una lettura manuale. Mentre un passaggio lavora, la pagina rilegge ogni due secondi soltanto la sua riga (`/api/pipeline/active`), così progresso e pulsanti restano allineati; alla fine del passaggio rilegge la pagina intera una volta. Le esecuzioni vengono salvate in SQLite nella tabella `pipeline_jobs`: parametri, orario, stato, progressi, risultato e impronta degli input. Chiudere la scheda del browser non ferma il worker. Il server deve rimanere attivo.

Il server riusa conteggi di Pipeline e Metriche finché nessuno scrive nell'archivio e nessun file in `config/` o `user_context/` cambia (`ReadCache` in `jobhunter/exploration/dashboard.py`, basata su `PRAGMA data_version`). Riaprire una tab senza modifiche costa quindi meno di mezzo secondo; la prima lettura dopo una scrittura ricalcola tutto e richiede qualche secondo. Stato del workflow esterno e processi attivi si rileggono sempre. Gli elenchi delle esecuzioni, qui e nella pagina Fonti, non inviano l'esito di ogni singolo elemento (`items`, fino a qualche MB per esecuzione): ne mostrano il numero, e l'elenco completo resta nella riga SQLite e nel report su disco.

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
Lo step attivo ha sfondo e bordo evidenziati, etichetta e barra per le sole aziende Tier A/B in coda. I dettagli mostrano azienda corrente, schede richieste, cache, chiamate API, input/output token della sola run e aziende differite. Le esecuzioni avviate prima di questa correzione restano riconoscibili come "vecchia coda" e continuano a mostrare l'intero archivio attraversato. Il nuovo raggruppamento richiede il riavvio del server dopo la run corrente.


### La tab Metriche: elaborazione, esiti e aziende

Tutte le metriche dell'archivio stanno in una pagina sola, ma non condividono lo stesso significato. In alto, lo **stato di elaborazione** usa una riga per passaggio e separa completati, da elaborare e bloccati dai dati. Il totale in ingresso può cambiare scendendo nella pipeline; la riga lo rende esplicito.

Segue l'**albero degli esiti**. Il nodo Regex divide soltanto fra scartati e annunci che passano a Jev. Il nodo Jev mostra compatibili, scartati e indecisi usando come totale solo gli annunci che Jev ha già analizzato. Pendenti e bloccati rimangono nella sezione operativa e non ricevono un esito inventato. Un riepilogo complessivo dichiara il proprio totale analizzato.

La sezione **Dagli annunci compatibili alle aziende** rende visibile il cambio di unità e ripartisce quelle aziende in gruppi che sommano allo stesso totale. La successiva **Mappa di tutte le aziende** riparte dall'intero archivio e lo dichiara: non è la continuazione del primo gruppo. Qualità dei dati, categorie ereditate dagli annunci e geografia restano una sezione separata e seguono il filtro della pagina.

Le evidenze del verdetto sono attuali. La regex mostra le regole applicate; Jev mostra la frase dell'annuncio usata per il giudizio. Qwen non produce verdetti né evidenze di selezione. I vecchi contatori Qwen di selezione non sono supportati dalla UI corrente.

Gli esiti Jev sono quelli salvati dalle esecuzioni. Nessuna API o modello viene invocato dalla lettura delle metriche.

### Unita di misura

Una azienda e esclusa localmente solo se non ha annunci rimasti. Cache locale mancante o scaduta non causa esclusioni implicite.
I giudizi Jev salvati sui soli annunci passati dal regex formano quattro gruppi disgiunti: keep, review, exclude, senza giudizio. La loro somma coincide con gli annunci affidati al secondo giudice. Sono risultati storici salvati, non una certificazione di validità con le domande correnti; la UI esplicita questo limite. La presenza di schede Qwen non misura la selezione.
La run corrente rimane separata: per le nuove esecuzioni il denominatore comprende soltanto aziende Tier A/B, incluse quelle già coperte dalla cache. Le esecuzioni precedenti possono ancora riportare tutte le aziende dell'archivio attraversate e quelle saltate. Token input/output riguardano solo la run. Nessuna operazione di filtraggio o API viene avviata dal monitor.

### Salvate: la scelta a mano, dopo la pipeline

La tab «Salvate» ha preso il posto di «La mia coda». Nella tab Aziende «Salva azienda» e «Salva ruolo» mettono da parte quello che interessa; la tab Salvate li elenca e apre la scheda al suo interno, senza i filtri della tab Aziende. Da lì si prepara il testo per la chat, per una singola azienda o per tutte le salvate insieme: la pagina prepara un testo da copiare e non invia nulla.

La scelta manuale è un campo a parte e non tocca i verdetti della pipeline: nella scheda si legge la tua decisione accanto al giudizio del regex o del modello, non al suo posto. Per questo la valutazione manuale non è più una card della pipeline, e con la coda è sparito anche lo step «Coda di selezione», che serviva solo a riempirla. Le domande «Da chiarire insieme» e le preferenze proposte restano in fondo alla tab, in un riquadro richiudibile; accettare una preferenza ora è solo un promemoria, perché la coda che le applicava non esiste più.

### Debug: le domande di controllo

La tab «Debug» risponde a una domanda per volta sull'archivio, in sola lettura: quali aziende non hanno una descrizione, quali annunci sono stati scartati e per quale motivo, quali sono rimasti aperti dal regex senza mai arrivare al modello, quali non hanno categoria, quali località non trovano una città nella mappa. Le domande divise per motivo mostrano i gruppi con il loro conteggio; ogni riga apre l'azienda nella tab Aziende. Aggiungere una domanda significa aggiungere una voce a `LENSES` in `jobhunter/exploration/debug.py`.

Fra le domande c'è anche la lingua in cui l'annuncio è scritto, riconosciuta sul testo originale contando le parole funzione: quando l'annuncio passa viene tradotto e riassunto, e quell'informazione andrebbe persa. Non è la lingua richiesta dall'annuncio, che resta un requisito a parte: serve a segnalare gli annunci in una lingua che non conosci, anche quando non la chiedono. Sotto le venti parole la risposta è «non determinata» invece di una lingua a caso.

I filtri della tab Aziende sono Tier, ricerca testuale, Paese, Città, Fonte e Categoria. Il Tier si sceglie con i pulsanti in cima ai filtri: ciascuno mostra quante aziende conta nell'archivio intero, filtra con un solo clic e resta scelto alla visita successiva; «Azzera» torna a «Tutte». Con un solo tier scelto la colonna Tier si nasconde. Nell'elenco <kbd>↓</kbd>/<kbd>j</kbd> e <kbd>↑</kbd>/<kbd>k</kbd> aprono l'azienda successiva o precedente, anche oltre il bordo della pagina. «Asse ruolo» e «Stato» sono stati tolti: il primo chiedeva il verdetto del regex sui ruoli, il secondo le decisioni già prese, e sono due domande da controllo, non da scelta. Vivono nella tab Debug, con le lenti «Annunci scartati dal regex», «Aziende con ruoli compatibili ma senza evidenza aziendale» e «Aziende su cui hai già deciso». La colonna Stato resta nella tabella, e la CLI conserva `search --status` e `--eligibility`.
