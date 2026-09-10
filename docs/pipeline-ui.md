# Avviare e seguire la pipeline dalla web app

Su Windows aprire `Avvia JobHunter.pyw` con doppio clic. Il launcher usa il Python della `.venv` quando disponibile e apre direttamente la pagina Pipeline. Serve l'associazione Windows dei file `.pyw` con Python. Una piccola finestra permette di riaprire il browser o fermare il server quando non ci sono operazioni in corso. I log sono in `dashboard.log`. Se una dashboard è già attiva sulla porta configurata, viene aperta quella: dopo aggiornamenti del codice occorre riavviare il vecchio server.

## Card e avvio

La mappa mostra la sequenza con card e frecce. Ogni card riporta copertura, data e stato. Cliccare per aprire parametri, risultato dell'ultima esecuzione e pulsante di avvio. Normalizzazione e raggruppamento sono automatici durante l'importazione; la card spiega il comportamento. La valutazione manuale apre la coda del feedback loop.

Le operazioni disponibili sono raccolta per fonte, filtri locali, recupero descrizioni, passaggio LLM remoto e aggiornamento della coda. La rilettura degli HTML salvati e l'impaginazione con Ollama sono state tolte dalle card: la prima è una riparazione una tantum, la seconda non produce più nulla da quando le schede dei ruoli arrivano da Qwen. Restano disponibili dalla CLI con `reparse-descriptions` ed `enrich description`. L'avvio chiama direttamente le funzioni Python esistenti, senza accettare comandi shell o percorsi arbitrari dal browser.

“Continua da qui” avvia in sequenza i passaggi principali successivi. La finestra mostra l'elenco e permette di impostare il successivo passaggio LLM remoto, comprese le chiamate contemporanee. Se il recupero descrizioni è successivo al passaggio scelto, considera tutte le descrizioni eleggibili. Il recupero mantiene esclusioni, cooldown e limiti della fonte già previsti dalla pipeline.

La sequenza si ferma al primo risultato parziale o errore. “Riprova” significa riaprire il passaggio, verificare i parametri e avviarlo di nuovo: i risultati validi già salvati vengono riutilizzati dove previsto dai rispettivi task. Non esistono retry automatici a pagamento.

## Pilot Qwen e ordine dei passaggi

La sequenza automatica è raccolta e normalizzazione, filtri locali, recupero descrizioni eleggibili, selezione e schede con Qwen, coda e feedback. Importare annunci non avvia più la classificazione delle aziende. Le categorie locali sono state rimosse dalla mappa. Il comando CLI esplicito resta disponibile. Le statistiche restano nella tab Metriche.

La card “Selezione e schede con Qwen” parte da 100 aziende distinte in ordine deterministico. Per ogni azienda valuta prima i ruoli non esclusi localmente, più un campione deterministico di cinque esclusi locali complessivi per controllare falsi negativi. Il limite è configurabile con remote_audit_excluded. Non riesamina a pagamento tutti gli esclusi.

Solo quando esiste almeno un ruolo con decisione attuale keep o review, una singola chiamata company-summary produce categoria e descrizione aziendale. Seguono le sintesi dei soli ruoli sopravvissuti. Una selezione respinta dalla validazione, mancante o saltata per dimensione non autorizza l’arricchimento: viene contata in awaiting_selection. Le categorie assegnate in chat restano protette.

L’anteprima è offline: dove manca la selezione, gli arricchimenti indicati sono un limite superiore, non una previsione degli esiti del modello. I report distinguono esclusi locali, audit, esclusi remoti, risultati validi, risposte respinte e aziende senza sopravvissuti. I token includono le risposte respinte; il costo monetario potrebbe non essere restituito dal provider.

Le esclusioni Qwen governano la spesa di arricchimento ma restano proposte nella dashboard: la coda utilizza ancora i filtri locali. Gli originali, i giudizi e le sintesi restano separati e recuperabili.

## Stato, persistenza e interruzioni

Il funnel mostra una barra per asse e per tier, con etichette e conteggi direttamente sotto ogni barra. Anche le quote nulle o molto piccole mantengono un conteggio leggibile. Le card usano lo stesso formato per lavoro completato e rimanente. Il pannello attivo mostra avanzamento e nuovi esiti; consumi e contatori aggiuntivi sono in «Consumi e dettagli». L'origine dei giudizi è espandibile nel funnel. I conteggi dell'archivio e quelli della singola esecuzione restano distinti.

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

Le chiamate Qwen restano sequenziali, con pausa configurata in remote_llm.json. Il parallelismo è già presente nel recupero HTTP delle descrizioni, non nell’inferenza remota.


### Progresso remoto
Lo step attivo ha sfondo e bordo evidenziati, etichetta e barra per aziende attraversate. I dettagli mostrano azienda corrente, annunci valutati, keep/review/exclude, cache, esclusioni locali, chiamate API, input/output token della sola run e aziende differite. Le vecchie run indicano i contatori non disponibili. Il nuovo raggruppamento richiede il riavvio del server dopo la run corrente.


### Funnel e unita di misura
Il funnel globale espone aziende e annunci come barre proporzionali: archivio, esclusi locali e rimasti, seguiti dalla divisione dei rimasti fra tenere, verificare, escludere e senza giudizio remoto. Le percentuali sono calcolate sul totale della sezione. Una azienda e esclusa localmente solo se non ha annunci rimasti. Cache locale mancante o scaduta non causa esclusioni implicite.
I giudizi Qwen salvati sui soli annunci rimasti formano quattro gruppi disgiunti: keep, review, exclude, senza giudizio. La loro somma coincide con gli annunci rimasti. Sono risultati storici salvati, non una certificazione di validita con il prompt corrente; la UI esplicita questo limite. Gli audit di annunci esclusi localmente non rientrano nella partizione. La presenza di schede aziendali non misura la selezione.
La run corrente rimane separata: aziende attraversate includono quelle saltate; risultati in cache nelle vecchie run possono comprendere piu elaborazioni dello stesso annuncio. Token input/output riguardano solo la run. Nessuna operazione di filtraggio o API viene avviata dal monitor.
