# JobHunter

JobHunter raccoglie annunci, raggruppa le opportunità per azienda e conserva giudizi automatici e decisioni personali senza confonderli. SQLite e gli snapshot locali sono la fonte dei dati. La dashboard, la CLI e la skill Codex leggono lo stesso archivio.

## Avvio

Su Windows, apri `Avvia JobHunter.pyw`. Il launcher avvia il server locale, apre la pagina Pipeline e permette di fermarlo in sicurezza.

Da PowerShell:

```powershell
.\.venv\Scripts\python.exe main.py serve
```

La dashboard risponde su <http://127.0.0.1:8000>. Se l'ambiente non esiste ancora:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe main.py init
```

## Flusso attivo

La pipeline esegue questi passaggi:

1. raccoglie e normalizza gli annunci;
2. applica le esclusioni deterministiche ai ruoli;
3. recupera le descrizioni mancanti e i dati aziendali;
4. usa Jev per i casi che richiedono un giudizio semantico;
5. calcola i Tier dai due assi, azienda e ruolo;
6. genera con Qwen le schede delle aziende e dei ruoli già ammessi;
7. lascia all'utente la revisione degli indecisi e la scelta finale.

Regex, Jev, schede Qwen e decisioni personali restano dati distinti. Il Tier viene calcolato in lettura e non viene salvato.

## Comandi utili

```powershell
.\.venv\Scripts\python.exe main.py --help
.\.venv\Scripts\python.exe main.py search --query energy --limit 20
.\.venv\Scripts\python.exe main.py show AZIENDA_ID
.\.venv\Scripts\python.exe main.py saved
.\.venv\Scripts\python.exe main.py codex-session start --mode indecisi
.\.venv\Scripts\python.exe main.py codex-session start --mode selezione --tier A
```

La tab Pipeline avvia le operazioni lunghe e ne conserva lo stato. La tab Metriche spiega popolazioni, passaggi e qualità dei dati. La tab Salvate contiene soltanto aziende e ruoli messi da parte a mano.

## Documentazione

L'indice aggiornato è in [docs/README.md](docs/README.md). I riferimenti principali sono:

- [architettura e mappa del codice](docs/mappa-codice-dati.md);
- [pipeline completa](docs/pipeline-completa.md);
- [uso e manutenzione](docs/jobhunter-v2.md);
- [modello dati](docs/data-model.md);
- [configurazione](docs/configuration.md);
- [revisione ed esplorazione con Codex](docs/conversazioni-codex.md).

## Test

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B tests\browser_check.py
```

I test browser usano un database temporaneo. Per misurare la ricerca su dati sintetici:

```powershell
.\.venv\Scripts\python.exe -B tests\benchmark_search.py --companies 1000 --repeat 3
```

## Dati personali

Questa copia di lavoro contiene configurazioni e contesto personali. Non pubblicarla come distribuzione pulita. Conserva `.env`, `data/`, `user_context/`, backup SQLite e snapshot delle fonti. La cronologia Git contiene le vecchie guide e i report eliminati dalla documentazione corrente.
