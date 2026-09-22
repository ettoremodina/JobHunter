# Uso e manutenzione

Questa guida copre il percorso operativo corrente. Per le decisioni di prodotto usa [DESIGN.md](../DESIGN.md); per trovare il codice usa [mappa-codice-dati.md](mappa-codice-dati.md).

## Avvio locale

Il launcher Windows `Avvia JobHunter.pyw` usa `.venv`, avvia il server e apre la pagina Pipeline. In alternativa:

```powershell
.\.venv\Scripts\python.exe main.py serve
```

Il server ascolta soltanto su `127.0.0.1`. Il database predefinito è `data/jobhunter.sqlite3`.

Per inizializzare un archivio vuoto:

```powershell
.\.venv\Scripts\python.exe main.py init
```

`init` crea le tabelle mancanti. Non cancella dati esistenti.

## Operazioni comuni

Consulta sempre l'help corrente prima di copiare un comando da note esterne:

```powershell
.\.venv\Scripts\python.exe main.py --help
.\.venv\Scripts\python.exe main.py system-one --help
.\.venv\Scripts\python.exe main.py codex-session --help
```

Letture frequenti:

```powershell
.\.venv\Scripts\python.exe main.py stats
.\.venv\Scripts\python.exe main.py search --query energy --limit 20
.\.venv\Scripts\python.exe main.py show AZIENDA_ID
.\.venv\Scripts\python.exe main.py saved
.\.venv\Scripts\python.exe main.py analytics
```

Raccolta limitata e recupero dei testi:

```powershell
.\.venv\Scripts\python.exe main.py collect jobspy --limit 20
.\.venv\Scripts\python.exe main.py fetch-descriptions --limit 50 --workers 8
.\.venv\Scripts\python.exe main.py company-profile --limit 50
```

`collect-all` raccoglie tutte le query configurate. Non esegue automaticamente gli altri passaggi della pipeline. Dalla dashboard puoi avviare un singolo passaggio o usare "Continua da qui".

## Modelli e costi

`system-one` e `llm` preparano un'anteprima senza rete e senza credenziali. `--execute` autorizza l'invio al provider configurato.

```powershell
.\.venv\Scripts\python.exe main.py system-one --limit 10
.\.venv\Scripts\python.exe main.py system-one --limit 10 --execute
.\.venv\Scripts\python.exe main.py llm job-summary --record-id RUOLO_ID
```

La pipeline usa Jev come giudice e Qwen per scrivere schede. Qwen non decide il Tier. Non esiste un retry automatico quando l'esito di fatturazione è incerto.

## Revisione con Codex

Le sessioni persistenti hanno due modalità:

```powershell
.\.venv\Scripts\python.exe main.py codex-session start --mode indecisi --batch-size 5
.\.venv\Scripts\python.exe main.py codex-session start --mode selezione --tier A --batch-size 5
```

La prima usa soltanto annunci con descrizione che Jev ha lasciato in `review`. La seconda esplora aziende già ammesse nei Tier A e B. Le Salvate restano un insieme separato. Dettagli in [conversazioni-codex.md](conversazioni-codex.md).

## Stato dei processi

Per seguire un processo da un altro terminale:

```powershell
.\.venv\Scripts\python.exe main.py status
.\.venv\Scripts\python.exe main.py status --watch --interval 10
.\.venv\Scripts\python.exe main.py status --json
```

`status` legge i report di avanzamento senza aprire SQLite. `Ctrl+C` ferma il monitor, non il worker. La dashboard conserva in SQLite i lavori avviati dal pannello e permette di chiedere l'interruzione al primo punto sicuro.

## Dati da preservare

Non cancellare o riscrivere senza una richiesta esplicita:

- `data/jobhunter.sqlite3` e i suoi backup;
- `data/collection/` e gli altri snapshot delle fonti;
- `user_context/`;
- `.env` e i file di segreti;
- note e feedback personali.

Gli output derivati possono essere ricostruibili, ma contengono anche costi, prove e cronologia utili. Trattali come dati finché non hai verificato i chiamanti e il percorso di recupero.

## Prestazioni della dashboard

La prima pagina valida la cache dei filtri soltanto per le aziende mostrate. I report globali continuano a validare tutto l'archivio, ma Metriche e Pipeline leggono dalla cache il flag «mansioni utilizzabili»: non ripetono il parsing HTML su ogni annuncio. La tab Metriche usa un solo endpoint e non aspetta il riepilogo operativo della Pipeline. Dopo un cambio alle regole, la prima lettura può ricostruire la cache; le successive la riusano. Il benchmark ripetibile vive in `tests/benchmark_search.py`; il test di regressione controlla la quantità di lavoro, non una soglia instabile in millisecondi.

## Verifica

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B tests\browser_check.py
.\.venv\Scripts\python.exe -B tests\pipeline_browser_check.py
.\.venv\Scripts\python.exe -B tests\debug_browser_check.py --output data\qa\debug.png
```

I test usano database temporanei. Le prove con fonti reali o provider a pagamento richiedono un'autorizzazione distinta.

## Problemi frequenti

- Un `403`, `429` o blocco esplicito ferma nuove richieste verso l'host. I risultati già salvati restano.
- Un `404` non dimostra che il ruolo sia chiuso. Il tentativo viene registrato e rinviato.
- Una descrizione mancante blocca il giudizio semantico. Non equivale a uno scarto.
- Una scheda Qwen mancante non cambia il verdetto della pipeline.
- Dopo modifiche al codice della dashboard, riavvia il server esistente sulla porta configurata.
