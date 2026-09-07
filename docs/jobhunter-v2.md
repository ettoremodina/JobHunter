# JobHunter 2: utilizzo e manutenzione

## Percorso attivo

`main.py` avvia `jobhunter.cli`. Il nucleo è `workspace.py`, la raccolta è `collection.py`, il server è `dashboard.py` e gli asset sono in `dashboard/`. Il codice e la documentazione legacy sono stati rimossi. Il flusso è raccolta → mapping e normalizzazione → raggruppamento aziendale → categoria → ricerca e confronto in chat → feedback → consultazione ed export.

Nessun LLM viene chiamato dal programma. Codex legge il profilo e i dati con la CLI, ragiona nella chat e può salvare una valutazione strutturata. La stessa operazione `assess` è il punto d'ingresso per un futuro produttore API, locale o cloud; nessuna infrastruttura API speculativa è stata aggiunta.

## Avvio

Python 3.11 o successivo. Archivio, CLI e dashboard usano solo la libreria standard. Per le acquisizioni:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m playwright install chromium
.venv/Scripts/python.exe main.py import-legacy
.venv/Scripts/python.exe main.py serve
```

Se `.venv` esiste già, usarla senza ricrearla. Il server ascolta soltanto su loopback. Aprire [dashboard locale](http://127.0.0.1:8000). `python server.py` è una scorciatoia per la stessa dashboard.

## CLI e dati

```powershell
python main.py search --query energy --location Italy --limit 20
python main.py show ID
python main.py feedback ID saved --note "Da approfondire"
python main.py undo EVENT_ID
python main.py profile --add "Preferisco lavorare dall'Italia"
python main.py evidence ID https://example.org/about --note "Fonte sull'attività aziendale"
python main.py assess ID data/assessment.json
python main.py export data/aziende.csv --status saved
python main.py --db data/prova.sqlite3 import input.json --source ricerca
.venv/Scripts/python.exe main.py collect climatebase.org --limit 10
```

Le opzioni globali `--db` e `--config` precedono il comando. Output operativo in JSON su stdout, diagnostica su stderr. Codice d'uscita 0 per riuscita, 1 per input/operazione non valida, 2 per acquisizione fallita, parziale o con accesso richiesto. Un `empty` indica una risposta senza record, non una prova che la fonte non abbia offerte. Le librerie delle board possono emettere diagnostica propria.

`import-legacy` legge solo gli `structured_results.json` per fonte, mai le stringhe aggregate del vecchio merge. La data iniziale dell'import è il timestamp del file, quindi indica l'età dello snapshot e non una data certa di pubblicazione. I grezzi originali e le preferenze restano intatti. Le righe mancanti di azienda, titolo o URL valido vengono riportate come rifiutate nell'esito. Non sono cancellate dal sorgente.

SQLite mantiene aziende, opportunità, osservazioni per fonte, feedback, valutazioni, evidenze, preferenze aggiuntive e resoconti di acquisizione. Le vecchie blacklist, rating e linee guida non sono convertiti implicitamente: rimangono in `user_context/` e possono essere riesaminati nella chat.

## Contratto e identità

Una azienda è identificata da `id`; contiene nome, sito ufficiale quando noto, descrizione e settori. I nomi normalizzati vengono raggruppati; domini ufficiali noti e in conflitto rimangono distinti. Quando il dominio manca, omonimi reali restano un limite da verificare manualmente. Non viene tentata una fusione fuzzy per nome o titolo.

Ogni opportunità conserva titolo, sedi, policy remoto, contratto, seniority, salario strutturato, testo, date e URL. Gli identificativi derivano da azienda e URL di candidatura normalizzato. La deduplicazione tra fonti è precisa quando condividono quell'URL; lo stesso ruolo con URL diversi resta distinto all'interno della sola scheda aziendale. Nessuna somiglianza testuale viene considerata prova di identità.

I campi assenti restano null o liste vuote. Il salario non viene convertito fra periodi o valute. `posted_at` conserva il valore della fonte quando non è una data assoluta affidabile. `last_seen` non garantisce che l'offerta sia tuttora aperta. Le aziende non vengono filtrate automaticamente per parole come senior presenti in un altro ruolo.

Il record di valutazione richiede `reasoning`; opzionalmente `missing_information` e `relevant_opportunity_ids`, entrambi liste di stringhe. Gli ID devono appartenere all'azienda. I record possono contenere `author` e altre informazioni strutturate. Il campo derivato `stale` segnala cambiamenti ai contenuti, alle evidenze, al profilo originale o alle preferenze aggiuntive. Non esiste un punteggio sintetico obbligatorio.

Gli stati personali sono `new`, `review`, `saved`, `discarded`, `contacted`. Il feedback sul ruolo mantiene l'azienda nello stato precedente. Gli eventi si possono annullare; ripetere consecutivamente la stessa decisione e nota non la duplica. Le aggiunte al profilo vengono conservate separatamente dall'originale.

## Raccolta e limiti

`config/app.json` controlla database, directory grezzi, limiti, timeout, pausa fra richieste, TTL, fonti e porta. Ogni fonte usa anche il proprio YAML. La raccolta scrive una directory nuova con `raw.json`, `report.json` e, per browser, copie delle pagine. Solo i record validi entrano nell'archivio. Le osservazioni precedenti non vengono eliminate per un errore o un run parziale.

- JobSpy: query e località dal YAML, `hours_old` propagato, limite globale applicato anche se la libreria restituisce risultati per più board. La libreria governa i propri timeout e retry: il limite dei risultati non è un limite rigido alla durata di una chiamata.
- Airtable: usa il pubblico embed e risolve colonne, scelte e righe collegate. Gli header dell'embed sono usati per la richiesta corrente. L'elenco può essere acquisito interamente a monte anche se l'import è limitato. Le descrizioni estese non sono recuperate automaticamente per migliaia di annunci: la scheda segnala i campi assenti e Codex può approfondire le aziende selezionate.
- Climatebase: browser condiviso per discovery limitata, fetch diretto JSON-LD dei dettagli e rendering come fallback. Se manca un `JobPosting` valido, segnala il ruolo come fallito invece di inventare un parser generico. L'elenco è un campione; non c'è una garanzia di copertura dell'intera board.
- inClimate: sospesa per paywall dichiarato dall'utente. Nessuna acquisizione viene tentata.

La cache di run richiede un'acquisizione riuscita recente, configurazione identica e un limite precedente almeno pari al richiesto. `--force` permette un nuovo run. Non esiste ancora un controllo remoto dei soli documenti modificati: dentro il campione aggiornato i dettagli vengono recuperati di nuovo. Le opportunità non vengono dichiarate chiuse automaticamente.

## Manutenzione degli adapter

1. Controllare `sources` e `report.json`, distinguendo fallimento, parziale e accesso richiesto. La dashboard mostra gli stessi esiti.
2. Per un cambiamento DOM, ispezionare il sito con browser. Usare `listing.html` e i dettagli salvati per riprodurre parsing e link. Evitare di pubblicare copie di pagine con dati di sessione.
3. Aggiornare solo l'adapter interessato e aggiungere una fixture di regressione priva di credenziali. Per una fonte senza JSON-LD introdurre un parser specifico con dati osservati, senza indovinare selettori.
4. Eseguire i test offline e un piccolo campione live. Una risposta vuota senza evidenza non prova riuscita del parser.
5. Aggiornare la versione adapter e annotare l'esito. Riattivare una fonte sospesa solo quando l'accesso è disponibile.

L'uso interattivo del browser da Codex aiuta nella manutenzione; il comando normale usa Playwright, disponibile anche fuori da Codex.

## Dashboard

Lista aziendale paginata, ricerca su azienda e testo dei ruoli, filtri per località/fonte/stato, scheda con link e salari per ruolo, descrizioni espandibili, decisioni con storico e annullamento, valutazioni della chat, export CSV dei risultati filtrati, preferenze e controllo della raccolta. Il confronto più ragionato avviene nella chat; la tabella consente la scansione delle aziende.

La dashboard serve solo tre asset e le rotte JSON. Non espone `.env`, directory dati o file arbitrari. Le scritture richiedono un token di sessione e controllo origine/host. È un tool locale per un singolo utente, non un servizio remoto con autenticazione multiutente. I testi esterni sono inseriti come testo, gli URL accettano soltanto HTTP/HTTPS e le celle CSV pericolose sono neutralizzate.

## Verifica e ripristino

```powershell
python -B -m unittest discover -s tests -v
.venv/Scripts/python.exe -B tests/browser_check.py
```

Il secondo comando avvia un server effimero con database temporaneo, controlla browser e layout e salva screenshot in `data/qa/`. Non scrive feedback nel database dell'utente. I test utilizzano solo il codice attivo.

Prima di una modifica allo schema, fermare il server e copiare il database con i file WAL/SHM se presenti, oppure usare la funzione backup di SQLite. I sorgenti storici consentono di ricreare le aziende, ma non i nuovi feedback: esportare o salvare anche il database. Non eliminare il database per riparare uno scraper.

## Categorie aziendali

`config/categories.json` definisce un vocabolario comune a tutte le fonti. `sectors` conserva il testo dichiarato dalla fonte; `category` contiene la categoria comune. `category_method` distingue `rules`, `chat` e `unknown`; `category_reason` espone il criterio usato.

`python main.py categorize` applica le regole ai settori aziendali e, se non bastano, alla descrizione aziendale. Le regole non leggono i titoli dei ruoli. Corrispondenze ambigue o assenti restano `Da classificare`. Ogni import aggiorna i suggerimenti, preservando le assegnazioni della chat. Sono suggerimenti lessicali, non classificazioni semantiche certificate.

```powershell
python main.py categories
python main.py search --category "Da classificare" --limit 20
python main.py categorize ID --category "Energia" --reason "Attività aziendale verificata nella fonte allegata"
```

La chat consulta `show` ed eventuali fonti online prima di assegnare la categoria. La categoria descrive l'attività dell'azienda, non l'interesse personale o la mansione. È presente anche in dashboard, filtri ed export. Modificare il vocabolario richiede di riesaminare eventuali assegnazioni manuali con etichette rimosse.

## Lettura e pulizia

La tabella abbrevia le località anche quando la fonte le concatena in un unico campo. Il dettaglio di ogni ruolo permette di espandere il testo completo, senza dividere alla cieca città, regioni e paesi. Le descrizioni mostrano paragrafi, intestazioni ed elenchi Markdown comuni, eliminando gli escape di punteggiatura. Il testo originale resta invariato nel database. Il rendering usa nodi di testo sicuri e non interpreta HTML esterno.

Sono stati rimossi vecchio frontend, orchestrazione, filtri e scoring locali, scraper sostituiti, script esplorativi, documentazione generata e review superate. I vecchi appunti della root e i file eliminati sono recuperabili da `data/maintenance/legacy-before-cleanup.zip`, copia locale esclusa da Git. `data/` e `user_context/` conservano i dati e le preferenze storiche; `import-legacy` resta uno strumento di importazione supportato.
