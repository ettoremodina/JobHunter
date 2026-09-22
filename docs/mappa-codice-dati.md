# Mappa del codice e dei dati

Verifica del 15 settembre 2026 sul commit `59d8d87`. Copre i punti 1 e 22 di TO-DO; descrive il codice presente, senza riorganizzarlo. Non serve una riscrittura per rendere leggibile questo flusso.

## Da dove si parte

| Avvio | Percorso effettivo |
|---|---|
| `python main.py COMANDO` | [main.py](../main.py) → [cli.py](../jobhunter/cli.py), `parser`, `execute`, `main` |
| `python server.py` | Scorciatoia della stessa CLI con `serve` |
| Doppio clic su `Avvia JobHunter.pyw` | Avvia direttamente `dashboard.create_server` e apre la tab Pipeline |
| Dashboard | [dashboard.py](../jobhunter/exploration/dashboard.py) espone API e asset di `dashboard/`; `pipeline_actions.start` registra e avvia le operazioni |

La sequenza configurata in [pipeline_ui.json](../config/pipeline_ui.json) è `collection → filters → descriptions → remote`. È una sequenza di quattro azioni eseguibili: il numero delle card descrittive non equivale al numero dei comandi. Normalizzazione e raggruppamento avvengono dentro l'importazione.

## Cosa succede al dato

| Fase | Unità di lavoro e comportamento | Codice e salvataggio |
|---|---|---|
| Raccolta | Legge righe dalle fonti. Un listing può avere titolo e URL ma nessuna descrizione. | [collection.py](../jobhunter/acquisition/collection.py), `collect`; [sweep.py](../jobhunter/acquisition/sweep.py), `sweep`. Snapshot su disco, resoconto in `runs`. |
| Normalizzazione e identità | Converte i campi, risolve l'azienda, inserisce o aggiorna l'annuncio e la sua provenienza. | [normalization.py](../jobhunter/normalization.py), `normalize`; [workspace.py](../jobhunter/workspace.py), `Archive.ingest`. Tabelle `companies`, `opportunities`, `observations`. |
| Filtri locali | Valuta ciascun annuncio su titolo e testo disponibile, estrae esperienza e lingue. Gli esiti sono `potential`, `excluded`, `review`. | [selection.py](../jobhunter/evaluation/selection.py), `evaluate`; `Archive.evaluations`. Cache `search_eligibility`, invalidata da contenuto e regole. |
| Recupero testi | Visita i dettagli mancanti ammessi dal filtro del listing, rispetta rinvii e blocchi per host. Il testo aziendale ha un recupero distinto. | [descriptions.py](../jobhunter/acquisition/descriptions.py), `recover`; aggiorna il JSON dell'annuncio e `description_attempts`. [company_profile.py](../jobhunter/acquisition/company_profile.py), `recover`, aggiorna `companies` e `company_profile_attempts` tramite comando esplicito `company-profile`. |
| Giudice remoto | Prepara una richiesta combinata per azienda, con i ruoli da giudicare e le schede richieste. Anteprima senza chiamate; esecuzione esplicita con API. | [company_batch.py](../jobhunter/evaluation/company_batch.py), `prepare`, `run`, `apply`; trasporto e validazione in [remote_llm.py](../jobhunter/evaluation/remote_llm.py). Derivati in `enrichments`, categoria aziendale in `categories`, report su disco. |
| Consultazione e scelta | Combina categoria aziendale e giudizi sui ruoli in un Tier; ricerca, metriche e debug leggono queste dimensioni. L'utente salva o scarta aziende e singoli ruoli. | `selection.verdicts`, [tier.py](../jobhunter/evaluation/tier.py), `tier`; `Archive.feedback`, `undo`. Il Tier è calcolato, il feedback è persistente e separato. |

Dopo un nuovo testo, il prossimo `evaluations()` ricalcola il filtro invalidato. Non occorre interpretare la posizione della card come prova che tutti gli annunci abbiano attraversato ogni fase.

La categoria aziendale è un altro asse. [company_axis.py](../jobhunter/evaluation/company_axis.py) gestisce assegnazioni a regole o da chat; [company_evidence.py](../jobhunter/evaluation/company_evidence.py) estrae fatti aziendali dai ruoli. Nel codice verificato `ingest` non chiama `categorize`: il comando esplicito e l'esito remoto sono percorsi distinti.

### Il remoto non analizza tutto indistintamente

`company_batch.prepare` manda alla selezione i ruoli `non_so` con testo leggibile, più un campione degli esclusi locali. Riusa risultati correnti, salta input troppo grandi e richiede sintesi dei ruoli già `tieni` nelle aziende Tier A o B-esperienza. La scheda aziendale può essere richiesta indipendentemente dai ruoli, quando esistono fatti e la categoria è irrisolta o il Tier lo prevede.

La selezione dei ruoli da sintetizzare precede la risposta: un ruolo appena promosso dal remoto può richiedere una seconda esecuzione per ottenere la scheda. Una richiesta per azienda non significa una richiesta per annuncio, né garantisce che ogni azienda selezionata generi una chiamata.

`selection.verdicts` applica la cascata regex → remoto: il primo giudizio deciso prevale. L'audit remoto degli esclusi non ribalta automaticamente lo scarto regex. Inoltre legge i giudizi remoti salvati senza rivalidarne la cache; `remote_llm.current_result` verifica invece l'attualità per preparazione e presentazione. Un risultato obsoleto può quindi ancora contribuire al Tier: limite del comportamento corrente, non garanzia di attualità.

## Aziende, annunci, originali e derivati

Un'azienda ha molti annunci. Il nome normalizzato e il dominio noto risolvono l'identità aziendale; domini noti in conflitto restano distinti. L'ID dell'annuncio deriva da ID aziendale e URL di candidatura normalizzato. Stesso URL tra fonti può convergere sullo stesso annuncio; URL diversi restano record distinti. Omonimi senza dominio e duplicati con URL diversi sono limiti del raggruppamento.

Lo schema eseguibile è in [workspace.py](../jobhunter/workspace.py), `Archive.__init__`. I campi sorgente e i loro alias sono in `normalization.normalize`.

| Dato | Dove sta | Cosa conservare o ricostruire |
|---|---|---|
| Risposta acquisita e pagine | `data/collection/`, `data/descriptions/` | Originali per verificare il parsing; non sostituiti dalle sintesi. |
| Azienda corrente | `companies` | Nome, sito, descrizione aziendale, settori, provenienza e date. |
| Annuncio corrente | `opportunities.data`, JSON | Titolo, URL, località, contratto, salario, descrizione del ruolo e date della fonte. `company_id` collega l'azienda; il testo aziendale non è duplicato qui. |
| Provenienza | `observations` | Una riga per annuncio/fonte/URL, con ultima osservazione. Non è lo storico completo di ogni versione. |
| Filtri e geografia normalizzata | `search_eligibility`, `places`, `place_index` | Derivati ricostruibili da dati e configurazione. Località del ruolo, non sede verificata dell'azienda. |
| Sintesi e giudizi | `enrichments` | Chiave `task,record_id`; `remote:selection` e `remote:job-summary` per annuncio, `remote:company-summary` per azienda. Modello, impronte e risultato separati dagli originali. Una nuova versione sostituisce quella corrente della stessa chiave. |
| Categorie | `categories` | Una per azienda, con metodo e motivazione. Le assegnazioni `chat` sono protette dall'aggiornamento remoto. |
| Scelte e conoscenza personale | `feedback`, `feedback_detail`, `assessments`, `evidence`, `preferences`, `preference_rules` | Note, decisioni annullabili, valutazione corrente per azienda, fonti aggiunte e preferenze. Non ricostruibili dai soli snapshot. |
| Stato operativo | `runs`, `pipeline_jobs`, `pipeline_updates`, `pipeline_cancellations`, tabelle dei tentativi | Esiti e avanzamento, data, parametri e richieste di arresto. `personal_queue` e `impressions` restano nello schema, anche se la UI usa Salvate. |

SQLite contiene lo stato corrente normalizzato: una nuova acquisizione può aggiornarlo. Gli snapshot su disco conservano ciò che arrivò dalle fonti. `first_seen` e `last_seen` indicano osservazioni locali, non pubblicazione o apertura verificata dell'offerta. Uno scarto di selezione non cancella l'annuncio.

Il percorso predefinito è `data/jobhunter.sqlite3`, configurato in [app.json](../config/app.json). `--db` cambia il database, ma **non sposta automaticamente i report e gli snapshot**, molti dei quali sono risolti rispetto alla radice del progetto. SQLite usa WAL: per una copia consistente di un archivio attivo usare il backup SQLite, non copiare soltanto il file principale.

`collect-all --fresh` chiama `Archive.reset_collection`: crea prima un backup in `data/backups/`, poi elimina dati acquisiti non protetti e derivati. Conserva riferimenti personali secondo le query di protezione dello schema. È un reset esplicito, non una normale raccolta.

Le preferenze non hanno un unico contenitore: `config/role_filters.json` governa il filtro, `user_context/llm-selection-profile.md` è il profilo remoto configurato; feedback e aggiunte personali stanno anche in SQLite. Una nota salvata non va interpretata come regola già applicata al prompt remoto. `interview.review_rule` applica regole esplicite e salva traccia in `data/profile-rules/`.

## Dimensioni osservate

La worktree non contiene `data/`. Ho consultato solo aggregati nell'archivio originale `<repository>`, con connessione SQLite `mode=ro` e `query_only`, senza istanziare `Archive`. Rilevazione del 15 settembre 2026; l'archivio può cambiare durante altre esecuzioni.

| Contenuto | Dimensione rilevata |
|---|---:|
| Aziende / annunci / osservazioni | 5.908 / 23.049 / 23.260 |
| Aziende con descrizione non vuota | 1.931 |
| Annunci con descrizione non vuota | 19.007 |
| Feedback / valutazioni chat / evidenze aggiunte | 4 / 0 / 0 |
| Derivati in `enrichments` / run / esecuzioni pipeline | 1.204 / 2.592 / 40 |
| File SQLite principale | 218.898.432 byte, circa 219 MB |
| `data/collection/` | 1.485 file, circa 171 MB |
| `data/descriptions/` | 22.189 file, circa 2,57 GB |
| `data/remote-llm/` | 2.574 file, circa 3,49 MB |
| `data/backups/` | 7 file, circa 313 MB |

MB e GB sono decimali. Non è il totale dell'intera directory dati e il file SQLite esclude WAL/SHM. Descrizione non vuota non significa utile o aggiornata. I 1.204 derivati non sono 1.204 aziende giudicate: comprendono task e unità diverse. La parte più grande fra le directory misurate sono i file di recupero descrizioni; non vengono proposte cancellazioni.

## Discrepanze della guida precedente

Confronto circoscritto a [jobhunter-v2.md](jobhunter-v2.md), senza rifare la guida generale.

| Affermazione nella guida | Codice attuale |
|---|---|
| Il percorso normale non chiama LLM; API futura | L'azione `remote` della dashboard chiama già `company_batch.run` in modalità execute. Configurazione attuale Qwen `qwen3.8-flash`. La consultazione ordinaria non chiama il modello. |
| `enrich` usa Ollama opzionale | Il parser CLI non espone `enrich`; `enrichment.py` ora prepara testo e input, senza modello locale. |
| `collect-all` recupera dettagli, categorie, filtri, statistiche e coda al termine | `sweep.sweep` conclude con copertura e statistiche dell'archivio; non orchestra quelle fasi. I collector possono comunque acquisire testo insieme ai listing. |
| L'import e il recupero testi aggiornano automaticamente le categorie | `Archive.ingest` termina dopo il salvataggio, senza categorizzazione automatica; consultare i comandi e il percorso remoto sopra. |
| `import-legacy`, `profile`, `queue` come comandi correnti | Assenti dal parser attuale. Esistono `import`, `saved`, `review-questions`, `review-rule` e altri comandi espliciti. |
| Tab La mia coda e filtro solo sui titoli | Dashboard Salvate; `selection.evaluate` usa anche esperienza, management e lingua nel testo. |

Anche `llm TASK` della CLI e l'azione remota dashboard sono due ingressi diversi: il primo usa `remote_llm.run` per task, il secondo `company_batch.run` con richiesta combinata. Non sono sinonimi di pipeline completa.

## Organizzazione del pacchetto

I moduli sono raggruppati per responsabilità. `cli.py`, `workspace.py` e `normalization.py` restano alla radice perché collegano più aree. Il worker e gli esperimenti mantengono gli ingressi storici `python -m jobhunter.board_worker`, `python -m jobhunter.benchmark` e `python -m jobhunter.calibration`.

| Destinazione | Responsabilità e moduli |
|---|---|
| `jobhunter/` | `__init__.py`, `cli.py`, `workspace.py`, `normalization.py` |
| `jobhunter/acquisition/` | `collection.py`, `sweep.py`, `board_worker.py`, `descriptions.py`, `company_profile.py` |
| `jobhunter/evaluation/` | `selection.py`, `tier.py`, `company_axis.py`, `company_evidence.py`, `languages.py`, `enrichment.py`, `remote_llm.py`, `company_batch.py` |
| `jobhunter/exploration/` | `dashboard.py`, `analytics.py`, `debug.py`, `places.py`, `interview.py` |
| `jobhunter/operations/` | `pipeline.py`, `pipeline_actions.py`, `progress.py`, `cancellation.py`, `maintenance.py` |
| `jobhunter/experiments/` | `benchmark.py`, `calibration.py` |

I nomi descrivono responsabilità, non un ordine di esecuzione. `workspace.py` resta condiviso e risolve dati e configurazione dalla radice del progetto.

Non ho trovato moduli del pacchetto dimostrabilmente inutilizzati da cancellare. `calibration.py` è fuori dal flusso ordinario, ma [rewrite-plan.md](rewrite-plan.md) registra la conservazione degli esperimenti su richiesta dell'utente. I tre script datati in `scripts/migrations/` sono interventi manuali storici, già separati dal runtime; l'assenza di import non basta a eliminarli. `server.py` e il launcher sono ingressi alternativi reali. Anche `enrichment.py`, nonostante il nome storico, ha consumatori attivi.

`search_rules_hash` legge le implementazioni in `evaluation/`; gli spostamenti non cambiano il contenuto usato per invalidare la cache. Nessun modulo è stato cancellato.
