# Fonti dello scraper e aggiunta di un sito

Verifica del punto 12, 15 settembre 2026, sul codice locale, aggiornata il 24 settembre con le bacheche ATS. Il disegno attuale basta per aggiungere fonti simili a quelle esistenti. Non esiste un protocollo Python per sito né un registro di plugin. Ci sono quattro rami in `collection.collect()` e `sweep.sweep()`, con normalizzazione comune in `normalization.normalize()`.

## Contratti effettivi

| Fonte configurata | Acquisizione | Requisiti e limiti |
|---|---|---|
| `airtable` | `collection.airtable_rows()` | Legge `embed_url` dal YAML, estrae endpoint e header dall'HTML pubblico, poi colonne e celle da `data.table`. Dipende da un protocollo embed non stabile. Restituisce i nomi delle colonne originali; quelli riconosciuti sono in `normalize()`. Non salva la risposta embed originale, ma salva le righe estratte. |
| `jobspy` | JobSpy, board `linkedin` e `indeed` | YAML con `site_names`, `search_queries`, `locations`, `hours_old`, `results_wanted`. Le righe sono quelle del DataFrame JobSpy serializzato in JSON. Paginazione e accesso dipendono dalla libreria e dal board. |
| `ats` | `ats.rows()` | Legge le bacheche di `config/ats_watchlist.json`, una per azienda seguita. Vedi «Bacheche ATS» qui sotto. |
| `climatebase.org` | `collection.browser_rows()` | YAML con `entry_point`, eventuale `request_delay_seconds`; `link_contains` nel JSON app. Scopre link sullo stesso hostname, usa bottoni inglesi `load more`/`show more` oppure scroll. Estrae solo `JobPosting` JSON-LD dalle pagine dettaglio, prima via HTTP e poi con browser se manca. |
| `inclimate.com` | Stesso ramo browser | Disabilitato per accesso richiesto. La presenza del YAML non prova che il parser funzioni. |

Climatebase è disabilitata dal 24 settembre 2026: ogni pagina del sito, anche `robots.txt`, risponde 403 con la verifica anti-bot di Cloudflare, sia via HTTP sia con Playwright. Aggirarla non è un'opzione. Le aziende che vi comparivano si seguono dalle loro bacheche ATS.

`filtered_entry_point` nel YAML Climatebase non viene letto. Conta `entry_point`. Il browser non segue una paginazione generica "Next", non interpreta card HTML arbitrarie e non scopre link su un hostname diverso. `discovery.json` registra il motivo di arresto, senza garantire l'esaurimento del sito. Un `kind` sconosciuto oggi cade nel ramo browser di `collect()`, mentre lo sweep lo rifiuta durante l'ordinamento: usare solo i tre valori esistenti.

Ogni riga deve avere `company_name`, `title` e un singolo `source_url` HTTP o HTTPS. Il normalizzatore accetta anche gli alias già presenti per JobSpy e Airtable. Per una fonte nuova conviene produrre direttamente i nomi comuni. Campi facoltativi utili:

```json
{
  "company_name": "Example",
  "title": "Simulation Engineer",
  "source_url": "https://example.org/jobs/123",
  "application_url": "https://example.org/apply/123",
  "website_url": "https://example.org",
  "company_description": "Descrizione dell'azienda dalla fonte",
  "sectors": "Energy",
  "locations": ["Milano, Italy"],
  "description": "Testo originale dell'annuncio",
  "posted_at": "2026-09-15",
  "employment_type": "FULL_TIME",
  "remote_policy": "remote",
  "salary": {"min": 35000, "max": 45000, "currency": "EUR", "period": "YEAR"}
}
```

`Archive.ingest()` separa i campi aziendali da quelli del ruolo, normalizza gli URL e registra le osservazioni di fonte. Le righe rifiutate finiscono nel report. La descrizione aziendale e quella dell'annuncio hanno significati diversi, non vanno copiate una nell'altra. Il worker ora conserva una sola riga per URL anche quando la stessa pagina JobSpy lo ripete.

## Bacheche ATS

Codice in `jobhunter/acquisition/ats.py`. Greenhouse, Lever (anche l'istanza EU), Ashby, Workable, SmartRecruiters, Recruitee, Personio, Teamtailor e Workday pubblicano senza chiave l'elenco completo degli annunci aperti di un'azienda. Verificati dal vivo il 24 settembre 2026, con testo e data sul 100% degli annunci letti.

**Elenco.** `config/ats_watchlist.json` è personale e non versionato; `init` lo crea vuoto da `examples/`. Ha tre chiavi:
- `boards`: una voce per bacheca, con `ats`, `slug` (per Workday `tenant/istanza/sito`), `company_id`, `company`, il nome che la bacheca dichiara e la strada che l'ha trovata;
- `checked`: quando ogni azienda è stata cercata e se anche per nome;
- `rejected`: le bacheche scartate perché dichiarano un altro nome, da rivedere a mano.

Si può aggiungere una voce a mano. Senza `company_id` gli annunci vanno all'azienda con quel nome.

**Scoperta.** `python main.py ats-discover [--limit N] [--no-names] [--force]` cerca le aziende dei tier in `sources.ats.discovery.tiers` (A e B) non ancora in elenco. Le strade, dalla più economica:
1. gli URL degli annunci già in archivio, compresi i link corti `grnh.se` e `apply.workable.com/j/`, seguiti fino alla bacheca;
2. il sito aziendale e al massimo due pagine «careers» collegate, cercando link e script delle bacheche;
3. lo slug ricavato dal nome, provato sugli ATS che dicono «non esiste» in modo netto (`GUESSABLE`).

Ogni candidata viene letta una volta. Si tiene se il nome che dichiara è quello dell'azienda (`same_company`). Dagli annunci e dal sito basta che il nome non la contraddica; dallo slug deve coincidere. Workday dichiara solo il tenant.

Il ritmo è di una richiesta per ATS ogni `probe_interval_seconds`. Recruitee e Greenhouse hanno chiuso le connessioni dopo una raffica di sottodomini e slug inesistenti. Per questo Recruitee non si prova per nome. Un'azienda il cui controllo è fallito per errori di rete resta da ricontrollare; le altre si ricontrollano dopo `recheck_days`.

**Raccolta.** `ats.rows()` legge ogni bacheca in `workers` thread, con al massimo una richiesta per ATS ogni `request_interval_seconds`. Workday e SmartRecruiters danno il testo solo con una seconda chiamata per annuncio: la si paga solo per gli annunci che l'archivio non ha già con testo. Nome e sito dell'azienda vengono dall'archivio, non dalla bacheca, così gli annunci finiscono sulla stessa azienda di Tier A/B. Oltre `max_jobs_per_board` la lettura si ferma.

**Chiusure.** Una bacheca letta per intero dice quali annunci sono chiusi. `sweep` passa a `closure.detect` solo le aziende la cui bacheca ha risposto senza errori e senza toccare il tetto.

**Google Jobs.** Provato il 24 settembre 2026 con JobSpy 1.1.82, l'ultima versione pubblicata: zero risultati con qualsiasi query, e l'avviso «initial cursor not found». Non è in `site_names`.

## Percorso raccolta, worker e descrizioni

- `collect <fonte> --limit N` esegue un campione limitato, scrive `raw.json` e `report.json` in una nuova cartella di raccolta e importa in SQLite. Usa cache delle esecuzioni riuscite, salvo `--force`. Per Airtable e JobSpy recupera poi le descrizioni mancanti dei soli annunci osservati in quel campione. Per JobSpy abilita anche il recupero LinkedIn durante la ricerca.
- La raccolta avviata dalla pipeline chiama lo stesso `collect()` con `recover_descriptions=False`. Questo disabilita il recupero aggiuntivo Airtable/JobSpy; il ramo browser continua a visitare i dettagli per estrarre le righe JSON-LD.
- `collect-all` chiama `sweep()`. Per JobSpy crea una specifica per query, paese e board, lancia `board_worker` in processi con timeout tramite `board_query()` e importa le pagine completate. Salva specifiche, log, risultati per query e report. Il worker arresta la query su pagina vuota, corta, senza URL nuovi o al limite di pagine. Una pagina vuota può anche indicare blocco, non prova assenza di annunci. Lo sweep non avvia il recupero aggiuntivo delle descrizioni. Alla fine segna come chiusi gli annunci non ritrovati (vedi «Annunci chiusi» in [data-model.md](data-model.md)), salvo che una fonte ne chiuderebbe più di `max_closed_share`: in quel caso la salta e lo scrive nel report.
- `fetch-descriptions` chiama `descriptions.recover()`. I thread scaricano HTML e restituiscono testo, provenienza e fatti; il thread proprietario dell'archivio scrive SQLite, `description_attempts` e report. Il recupero usa il `source_url` canonico del ruolo, non prova automaticamente tutte le osservazioni alternative. Dà priorità alle aziende senza evidenza e rispetta decisioni aziendali e cooldown.

Il recupero descrizioni ammette inizialmente solo gli hostname esatti in `config/descriptions.json`: attualmente LinkedIn e ClimateTechList, con e senza `www`. Un host ammesso non implica un parser adatto. `extract()` supporta JSON-LD, il contenitore pubblico LinkedIn e microdata SmartRecruiters. ClimateTechList può seguire un solo link esplicito "Apply to Job Posting" verso il datore; quel secondo host non passa dall'allowlist iniziale. Indeed non è ammesso per recuperare descrizioni mancanti, e Climatebase deve normalmente fornirle durante la raccolta browser. Conservare HTML e provenienza per diagnosticare pagine mancanti, blocchi e parser non supportati.

## Aggiungere una fonte

1. Controllare poche pagine campione e scegliere il ramo che corrisponde al sito. La presenza di `JobPosting` JSON-LD e la navigazione compatibile rendono sufficiente il ramo browser. Per un board supportato dalla versione installata di JobSpy si può aggiungere il suo nome a `site_names`; per tenerne separata la provenienza creare una nuova voce `kind: jobspy` e un YAML dedicato.
2. Per un browser creare `config/sites/example.org.yaml`:

   ```yaml
   entry_point: https://example.org/jobs
   request_delay_seconds: 3
   ```

   Aggiungere a `sources` in `config/app.json`:

   ```json
   "example.org": {
     "enabled": true,
     "kind": "browser",
     "config": "config/sites/example.org.yaml",
     "link_contains": "/jobs/"
   }
   ```

3. Se mancano navigazione compatibile o JSON-LD, aggiungere un parser o una funzione di raccolta mirata in `collection.py`, selezionata per questa fonte, che restituisca le righe comuni e gli errori. Collegarla sia a `collect()` sia a `sweep()`, preservando limite, snapshot e report. Nessun nuovo `kind` è necessario per una piccola variante browser. Un nuovo `kind` richiede invece di aggiornare anche l'ordine dei rami dello sweep e la politica di recupero di `collect()`.
4. Se la fonte produce listing senza descrizione, aggiungere gli hostname necessari alla allowlist e, quando JSON-LD non basta, un parser circoscritto in `descriptions.extract()`. Se cambia il comportamento del parser, aggiornare `PARSER_VERSION` perché i tentativi `unsupported_parser` della versione precedente possano essere riprovati. I cooldown degli host rimangono attivi anche con `--force`.
5. Aggiungere fixture offline per righe valide, campo obbligatorio mancante, dettaglio senza descrizione e risposta bloccata. Testare navigazione personalizzata se introdotta. Eseguire i test pertinenti, poi un campione reale da 1 a 3 annunci solo quando richiesto, su SQLite temporaneo e con cartelle temporanee per gli output.

Per il campione CLI copiare `config/app.json` in un file temporaneo, lasciare solo la nuova fonte e impostare `raw_directory` a un percorso temporaneo assoluto. Usare `--db` per il database di prova. Se serve il recupero descrizioni, usare anche una configurazione temporanea per il suo output tramite patch di `descriptions.ROOT` nei test API, come in `tests/test_scraper_pipeline.py`; `--config` della CLI cambia solo la configurazione app, non `config/descriptions.json`.

```powershell
python main.py --config C:/temp/jobhunter-test/app.json --db C:/temp/jobhunter-test/archive.sqlite3 collect example.org --limit 3 --force
python -m unittest discover -s tests -p test_scraper_pipeline.py
python -m unittest discover -s tests -p test_board_worker.py
```

Il comando di campione è un esempio da adattare dopo aver preparato i file temporanei. Non usare `collect-all` per collaudare una fonte. Questa verifica riguarda i contratti nel codice; non certifica che i siti o il protocollo embed siano oggi raggiungibili.
