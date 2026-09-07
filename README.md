# JobHunter

Archivio locale di aziende e opportunità, utilizzabile da Codex, CLI e dashboard. Una scheda per azienda, con ruoli, sedi, salari e link associati. La selezione avviene nella chat. Ollama è opzionale per impaginazione e categorie; nessuna chiave API richiesta.

## Avvio rapido

Python 3.11+. La CLI e la dashboard usano solo la standard library.

```powershell
python main.py import-legacy
python main.py serve
```

Apri [la dashboard locale](http://127.0.0.1:8000). Gli snapshot storici e le note restano invariati. Il database nuovo è `data/jobhunter.sqlite3`.

## Raccolta

Per installare le dipendenze delle fonti in un ambiente esistente:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m playwright install chromium
.venv/Scripts/python.exe main.py collect climatebase.org --limit 10
```

Se manca `.venv`, crearla con `python -m venv .venv`. Configurazione in `config/app.json` e YAML per fonte. inClimate è sospesa per paywall. Ogni acquisizione registra limiti, esiti e grezzi; un campione non è una raccolta completa.

## Uso con Codex

La skill [jobhunter](skills/jobhunter/SKILL.md) permette ricerca, approfondimento online, valutazioni e feedback persistente. Invocala con `$jobhunter`. È installata anche nella directory personale delle skill di questa macchina.

```powershell
python main.py search --query energy --limit 20
python main.py show ID
python main.py feedback ID saved --note "Da approfondire"
python main.py undo EVENT_ID
python main.py export data/aziende.csv --status saved
python main.py --help
```

## Documentazione e test

[Guida operativa, schema e manutenzione](docs/jobhunter-v2.md)

```powershell
python -B -m unittest discover -s tests -v
.venv/Scripts/python.exe -B tests/browser_check.py
```

Il percorso attivo è `cli.py`, `workspace.py`, `collection.py`, `dashboard.py` e `dashboard/`. Il codice legacy è stato rimosso. Snapshot, note personali e database restano in `data/` e `user_context/`.

`.env` era già versionato: aggiungerlo a `.gitignore` non lo rimuove dalla cronologia. Questo intervento non ha modificato segreti o riscritto la storia Git.
