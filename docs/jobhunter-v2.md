# JobHunter 2: utilizzo e manutenzione

## Percorso attivo

`main.py` avvia `jobhunter.cli`. Il nucleo è `workspace.py`, la raccolta è `collection.py`, il server è `dashboard.py` e gli asset sono in `dashboard/`. Il codice e la documentazione legacy sono stati rimossi. Il flusso è raccolta → mapping e normalizzazione → raggruppamento aziendale → categoria → ricerca e confronto in chat → feedback → consultazione ed export.

Il percorso normale non chiama LLM. I comandi espliciti `enrich` possono usare Ollama locale per impaginazione e categorie. Codex legge il profilo e i dati con la CLI, ragiona nella chat e può salvare una valutazione strutturata. La stessa operazione `assess` è il punto d'ingresso per un futuro produttore API, locale o cloud; nessuna infrastruttura API speculativa è stata aggiunta.

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

### Monitor della pipeline

La tab **Pipeline** legge `/api/pipeline` e mostra raccolta, normalizzazione,
descrizioni, classificazione aziende, impaginazione opzionale, filtri, statistiche,
coda e decisioni. **Aggiorna stato** rilegge i dati, senza avviare queste fasi.
`jobhunter/pipeline.py` aggrega l'archivio e riusa il monitor dei report per distinguere
un workflow terminato da un processo fermo senza report finale.

Copertura e date hanno significati distinti. Le categorie mancanti distinguono le
aziende mai esaminate da quelle esaminate ma senza informazioni sufficienti.
Filtri e impaginazioni superati da modifiche ai contenuti vengono contati a parte;
i filtri verificano anche le regole e il codice di estrazione attuali. Le categorie
mostrano le assegnazioni salvate, senza certificare la loro attualità online.
L'impaginazione è un comando separato, non un passaggio automatico di `collect-all`.

Le date descrivono l'ultimo dato o esito salvato per fase, non il completamento di
tutto l'archivio. `pipeline_updates` registra i futuri calcoli di filtri e statistiche;
le date storiche assenti restano non registrate. Le statistiche registrano il
completamento anche quando richieste su un sottoinsieme. Gli originali, le note e
le decisioni non vengono modificati dal monitor.

### Ricerca e paginazione

Località, fonte e compatibilità devono corrispondere allo stesso annuncio. La riga
aziendale mostra località, titoli e conteggio dei soli annunci che soddisfano questi
filtri; il dettaglio conserva tutti gli annunci. La località è quella dichiarata
dalla fonte per il ruolo, non una sede aziendale verificata. La ricerca testuale
generale cerca anche nelle descrizioni e non sostituisce il campo località.

Gli esiti di compatibilità sono salvati nella tabella derivata `search_eligibility`.
La tabella contiene anche requisiti, motivazioni e priorità, condivisi da metriche,
coda, domande di revisione, dettaglio aziendale, shortlist e controlli di qualità.
Queste viste non rieseguono l'estrazione delle descrizioni già valutate. La migrazione
dal vecchio indice dei soli stati richiede un primo ricalcolo; un riavvio ordinario
riusa gli esiti salvati. Il fingerprint del codice copre soltanto le funzioni che
estraggono e valutano i requisiti, non modifiche alla coda o alla classificazione.
La paginazione riusa questi esiti tra connessioni e riavvii. Una modifica al contenuto
di un annuncio ne causa il ricalcolo; modifiche alle regole o al codice di estrazione
invalidano gli esiti precedenti. Il primo caricamento senza esiti validi deve ancora
analizzare le descrizioni. Nessuna chiamata online o a modelli avviene nella ricerca.
I dati originali e le decisioni dell'utente restano nelle rispettive tabelle.

Il server occupa la porta in modo esclusivo anche su Windows. Un secondo `serve`
sulla stessa porta fallisce con un messaggio esplicito, evitando due versioni del
dashboard attive sullo stesso indirizzo. Chiudere il server esistente prima di
riavviarlo; ricaricare il browser non riavvia il processo Python.

### Classificazione dalle descrizioni dei ruoli

`jobhunter/company_evidence.py` raccoglie dichiarazioni esplicite dell'attività
aziendale da tutti gli annunci salvati dell'azienda. Basta un ruolo informativo;
gli altri possono avere descrizioni mancanti. Ogni estratto conserva URL e ID
dell'annuncio. Campi aziendali e settore esplicito hanno precedenza, gli estratti
sono il ripiego delle regole e vengono forniti anche al classificatore locale.

La selezione degli estratti è conservativa: non usa titoli, requisiti del candidato,
ricerche di personale o dichiarazioni generiche di valori come prova del settore.
Attività contraddittorie restano da classificare. L'estrazione non comprende tutte
le formulazioni possibili; una descrizione presente può ancora non fornire una
prova sufficiente. Le assegnazioni manuali restano protette.
L'importazione aggiorna le categorie delle sole aziende coinvolte, evitando di
riclassificare l'intero archivio a ogni batch. `categorize` senza argomenti mantiene
la possibilità di riesaminare tutto l'archivio.
Il recupero descrizioni aggiorna le categorie delle aziende modificate al termine
del batch, anche quando avviato fuori dal workflow completo.

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

SQLite mantiene aziende, opportunità, osservazioni per fonte, feedback, valutazioni, evidenze, preferenze aggiuntive e resoconti di acquisizione. I vecchi JSON di aziende, blacklist e memoria LLM sono stati eliminati su richiesta dell'utente durante il reset ufficiale. Portfolio, profili e configurazioni sono conservati.

## Contratto e identità

Una azienda è identificata da `id`; contiene nome, sito ufficiale quando noto, descrizione e settori. I nomi normalizzati vengono raggruppati; domini ufficiali noti e in conflitto rimangono distinti. Quando il dominio manca, omonimi reali restano un limite da verificare manualmente. Non viene tentata una fusione fuzzy per nome o titolo.

Ogni opportunità conserva titolo, sedi, policy remoto, contratto, seniority, salario strutturato, testo, date e URL. Gli identificativi derivano da azienda e URL di candidatura normalizzato. La deduplicazione tra fonti è precisa quando condividono quell'URL; lo stesso ruolo con URL diversi resta distinto all'interno della sola scheda aziendale. Nessuna somiglianza testuale viene considerata prova di identità.

I campi assenti restano null o liste vuote. Il salario non viene convertito fra periodi o valute. `posted_at` conserva il valore della fonte quando non è una data assoluta affidabile. `last_seen` non garantisce che l'offerta sia tuttora aperta. Le aziende non vengono filtrate automaticamente per parole come senior presenti in un altro ruolo.

Il record di valutazione richiede `reasoning`; opzionalmente `missing_information` e `relevant_opportunity_ids`, entrambi liste di stringhe. Gli ID devono appartenere all'azienda. I record possono contenere `author` e altre informazioni strutturate. Il campo derivato `stale` segnala cambiamenti ai contenuti, alle evidenze, al profilo originale o alle preferenze aggiuntive. Non esiste un punteggio sintetico obbligatorio.

Gli stati personali sono `new`, `review`, `saved`, `discarded`, `contacted`. Il feedback sul ruolo mantiene l'azienda nello stato precedente. Gli eventi si possono annullare; ripetere consecutivamente la stessa decisione e nota non la duplica. Le aggiunte al profilo vengono conservate separatamente dall'originale.

## Raccolta e limiti

`config/app.json` controlla database, directory grezzi, limiti, timeout, pausa fra richieste, TTL, fonti e porta. Ogni fonte usa anche il proprio YAML. La raccolta scrive una directory nuova con `raw.json`, `report.json` e, per browser, copie delle pagine. Solo i record validi entrano nell'archivio. Le osservazioni precedenti non vengono eliminate per un errore o un run parziale.

- JobSpy: query e località dal YAML, `hours_old` propagato, limite globale applicato anche se la libreria restituisce risultati per più board. La libreria governa i propri timeout e retry: il limite dei risultati non è un limite rigido alla durata di una chiamata.
- Airtable: usa il pubblico embed e risolve colonne, scelte e righe collegate. Gli header dell'embed sono usati per la richiesta corrente. L'elenco può essere acquisito interamente a monte anche se l'import è limitato. L'embed non contiene le descrizioni: `collect-all` visita i dettagli ClimateTechList degli annunci unici dopo l'import. Il mapping conserva località completa e paese, remoto, contratto e salario testuale senza dedurre importi o periodi.
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

## Filtri dal portfolio

`user_context/portfolio-evidence.md` conserva integralmente l'allegato; `user_context/search-profile.md` distingue fatti e preferenze. `config/role_filters.json` contiene le regole iniziali. `shortlist --limit 20` restituisce aziende con ruoli potenzialmente pertinenti e conteggi degli esclusi e dei ruoli da verificare. `show` espone la motivazione sul singolo ruolo, visibile anche in dashboard.

Engineering e development sono mantenuti, come chiarito dall'utente. Seniority, management e marketing/commerciale vengono verificati sul titolo, non sul testo dell'azienda o su riferimenti a colleghi senior. Sono suggerimenti, non feedback salvati. Tutti i dati rimangono ricercabili. Queste regole non interpretano ancora requisiti in anni nel corpo dell'annuncio, titoli in tutte le lingue o vincoli geografici: la chat verifica i candidati.

## Pass opzionali Ollama

```powershell
python main.py shortlist --limit 20
python main.py enrich description --limit 3
python main.py enrich category --limit 3
```

Ollama deve essere avviato, ad esempio con `ollama serve`. Modello e limiti sono in `config/local_llm.json`; `--model` seleziona un modello già disponibile. Nessun download automatico e nessuna chiamata durante ricerca, apertura dashboard o scraping. L'endpoint deve essere loopback. Il default usa il modello già presente `llama3.2:3b`. I batch hanno limiti espliciti; input troppo lunghi vengono saltati interamente, mai troncati in silenzio. `--force` ripete anche risultati in cache.

I prompt sono separati in `config/prompts/description.txt` e `category.txt`. API verificata sulla [documentazione Ollama](https://docs.ollama.com/api/chat).

La pulizia elimina markup HTML e Markdown comune con un parser deterministico. Il modello restituisce una classificazione per ogni riga: titolo, paragrafo o elenco. Il validatore richiede esattamente tante etichette quante sono le righe; il programma ricostruisce il testo nell'ordine originale, senza accettare riscritture generate. Questo evita omissioni o alterazioni LLM di salari, negazioni e requisiti. La qualità dell'impaginazione resta da verificare; il preprocessing non è un parser Markdown completo.

La categorizzazione riceve solo fatti aziendali e tassonomia, senza profilo candidato o titoli dei ruoli. Deve citare un frammento letterale dei fatti. La verifica della citazione non dimostra che la conclusione sia corretta: l'interfaccia la indica come suggerimento locale. Le assegnazioni della chat hanno precedenza. Dopo variazioni ai fatti aziendali, il prossimo import torna alle regole finché il pass locale non viene ripetuto.

La tabella `enrichments` mantiene output, modello, data e impronte di input/prompt/configurazione. Il testo originale non cambia; una versione formattata obsoleta non viene mostrata. Errori e output invalidi sono registrati nei run, senza sostituire gli originali. Il comando restituisce il numero di successi, errori, cache e input saltati. Un modello offline non impedisce l'uso dell'archivio.

## Coda personale e feedback

`queue` prepara dieci aziende e conserva la sessione in SQLite. L'ordine dipende da mansioni, categoria di interesse, esperienza obbligatoria e freschezza. Le motivazioni restano visibili; non è una probabilità di assunzione. Gli elementi già in coda mantengono il posto. Salvataggio, scarto e rinvio liberano lo spazio. Un'azienda scartata resta esclusa; una già valutata può riapparire per un nuovo ruolo pertinente, requisiti modificati o un promemoria scaduto. Cambiare solo la formattazione non genera una novità.

`config/role_filters.json` contiene soglia iniziale di due anni obbligatori, interessi, pesi e dimensione della coda. I requisiti preferenziali non causano esclusione. L'estrazione conserva le frasi di evidenza e tratta come ignoti i vincoli ambigui. Copre espressioni comuni inglesi/italiane, non tutte le lingue e costruzioni. La località è un dato da verificare, senza presumere autorizzazioni al lavoro dell'utente.

```powershell
python main.py queue
python main.py feedback ID discarded --opportunity ROLE_ID --reason too_senior
python main.py feedback ID review --reason not_now --until 2026-10-01
python main.py proposals
python main.py proposals RULE_ID --state accepted
python main.py proposals RULE_ID --state disabled
python main.py metrics
python main.py research-brief ID
```

Motivi condivisi: interesse, seniority, management, commerciale, settore, località, mansioni/tecnologie, non ora, altro. Il feedback sui ruoli non scarta l'azienda. Dopo tre aziende distinte scartate per settore, viene proposta un'esclusione di categoria. Solo l'accettazione la attiva. Altri motivi sono misurati, senza trasformarli automaticamente in regole vaghe.

`metrics` mostra aziende proposte, decisioni e motivi; la frazione salvata considera solo aziende decise. `research-brief` prepara domande e query da usare nella chat con ricerca online; le evidenze vengono salvate tramite `evidence`. La dashboard espone la stessa coda, le decisioni strutturate, il brief e le preferenze proposte.

## Raccolta ampia e run ufficiale

`python main.py collect-all` attraversa tutte le combinazioni configurate di query, paese e board. Airtable acquisisce l'intera risposta pubblica. Climatebase percorre l'URL configurato fino allo stallo o ai limiti di sicurezza, registrando la discovery. inClimate rimane sospesa per paywall.

`config/sweep.json` limita pagine, timeout e concorrenza. JobSpy usa processi isolati e salva le pagine completate anche prima di un timeout. Gli annunci LinkedIn sono acquisiti inizialmente come listing per non scaricare più volte gli stessi dettagli tra query. Con `description_followup_all: true`, il recupero successivo visita ogni annuncio unico senza descrizione su un host supportato dopo aver escluso i titoli chiaramente non compatibili. I casi ambigui restano candidati. Ogni query registra stop, errore, import e scarti. Non viene dichiarata copertura completa di LinkedIn/Indeed: i motori di ricerca e le restrizioni della fonte possono limitare i risultati. Il report distingue questo limite da una riuscita tecnica.

Per rifare la raccolta usare `python main.py collect-all --fresh`. Prima del reset crea un backup SQLite consistente in `data/backups/`. Conserva profili, configurazioni, snapshot, preferenze, feedback, evidenze, valutazioni e categorie manuali; mantiene i record necessari ai riferimenti personali. Azzera dati acquisiti non referenziati, arricchimenti automatici e coda. Gli snapshot storici non vengono reimportati. Al termine ricalcola categorie a regole, filtri su tutte le descrizioni disponibili, statistiche e coda. Il pass non chiama Ollama; `enrich` rimane un comando esplicito.

`python main.py fetch-descriptions --all` riprende il recupero dei testi ancora mancanti e compatibili con il filtro sul listing, senza ripetere la raccolta iniziale. Il vecchio batch limitato resta disponibile con `--limit`. Le richieste conservano la pausa configurata e si fermano per host al primo 403/429 o blocco esplicito, senza canali alternativi. Il report conta richieste, testi salvati, host bloccati, annunci non visitati, host non supportati e descrizioni ancora mancanti. Un recupero completo con testi mancanti ha esito `partial`. `description-coverage` misura la copertura per fonte. I grezzi di ogni dettaglio restano in `data/descriptions/`.

Anche `collect SOURCE --limit N` recupera le descrizioni mancanti, limitandosi agli annunci importati in quella raccolta. La versione adapter 2.1 invalida le vecchie cache di soli listing. Il parser scarta il placeholder promozionale osservato nel JSON-LD ClimateTechList. Se manca testo utile segue il link esplicito `Apply to Job Posting`, cerca JSON-LD sul sito del datore di lavoro e supporta il contenitore microdata osservato su SmartRecruiters. Non considera descrizione il testo generico della pagina e non segue link alternativi dopo un blocco di accesso.

Il collector browser interrompe la fonte al primo HTTP 403/429 o pagina esplicita di accesso negato/rate limit. Registra i link rimasti senza tentare un canale alternativo. Climatebase parte dalla directory senza filtro iniziale e usa una pausa di tre secondi tra i dettagli. Lo stallo della navigazione resta un limite di copertura, non una prova di esaurimento.

## Lettura della dashboard

In Fonti, il numero iniziale 10 è la quantità richiesta per la prossima raccolta manuale, con massimo configurato di 100. Non limita l'archivio e non descrive la run `collect-all`. La pagina mostra separatamente conteggi attuali e data del primo inserimento delle opportunità; quest'ultima non è la data originale di pubblicazione degli annunci.

La mia coda usa filtri e priorità locali, senza chiamare ChatGPT o Ollama. Ogni azienda mostra i ruoli e i motivi riferiti al primo ruolo. Apri e valuta porta al dettaglio con feedback; Prepara testo per la chat genera un testo leggibile da copiare manualmente in Codex, senza inviarlo. Ricarica coda salvata conserva la sessione e riempie gli spazi liberi, senza estrarre una nuova lista casuale.


## Recupero concorrente e benchmark

`python main.py fetch-descriptions --limit 50 --workers 4` recupera un batch di dettagli con quattro worker. `--all` rimuove il limite di batch, ma mantiene il filtro dal listing. Il testo già presente viene riutilizzato. La valutazione iniziale legge il titolo con descrizione vuota; un caso incerto non viene escluso. Il filtro completo continua a usare i testi recuperati.

I worker eseguono rete, parsing e salvataggio dei grezzi. Il thread chiamante scrive in SQLite, senza condividere la connessione tra worker. Il report viene sostituito atomicamente e contiene durata, richieste HTTP, successi, errori, worker e host bloccati. La pausa configurata di tre secondi si applica a ciascun worker: aumentare i worker aumenta anche il ritmo aggregato delle richieste. Un blocco condiviso impedisce nuove richieste all'host dopo 403, 429, 999 o un blocco esplicito; le richieste già partite possono terminare.

`python main.py benchmark-descriptions --batch-size 50 --workers 1 4 8` confronta batch distinti e bilanciati per host. Il manifest conserva gli ID scelti con seed riproducibile; i testi ottenuti restano nell'archivio. Il confronto misura descrizioni salvate al minuto rispetto al batch seriale, oltre a tempi CPU, latenza mediana/p95, errori e richieste effettive. Si ferma prima dei batch successivi se una fonte segnala un blocco. Pagine diverse possono avere costi diversi: i risultati sono un primo confronto, non un benchmark sulle stesse pagine o una garanzia di carico sostenuto.

I report e manifest sono in `data/benchmarks/`. Non viene applicato un limite complessivo di due ore. Il benchmark termina dopo i batch richiesti e non avvia automaticamente l'intero recupero residuo.
# Aggiornamento delle preferenze e della qualità

Il comportamento aggiornato di filtri linguistici, priorità professionali, coda di chiarimento, memoria dei tentativi e calibrazione manuale è descritto in [Preferenze e qualità della selezione](product-quality-2026-09-08.md).
