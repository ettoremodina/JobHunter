# JobHunter

*[Read in English](README.md)*

JobHunter raccoglie annunci di lavoro da più bacheche, li raggruppa per azienda e li fa passare davanti a una serie di giudici, dal più economico al più attento, finché resta una lista corta. Gira tutto sul tuo computer: SQLite e gli snapshot locali sono la fonte dei dati, e una dashboard locale, una CLI e un agente in chat leggono lo stesso archivio.

Per capire come funziona apri la panoramica illustrata [docs/jobhunter-overview.html](docs/jobhunter-overview.html).

## Primi passi

La guida [docs/getting-started.it.md](docs/getting-started.it.md) porta da una copia appena clonata ai primi annunci giudicati. In breve:

```bash
python -m venv .venv
pip install -r requirements.txt
playwright install chromium
python main.py init
python main.py serve
```

`init` crea il database e i tuoi file personali (profilo, filtri, ricerche) a partire dagli esempi in `examples/`. Per una configurazione guidata, chiedi al tuo agente di programmazione di seguire [skills/jobhunter-setup/SKILL.md](skills/jobhunter-setup/SKILL.md).

La dashboard risponde su <http://127.0.0.1:8000>. Su Windows puoi anche aprire `Avvia JobHunter.pyw`.

## Come funziona

1. Raccoglie e normalizza gli annunci.
2. Scarta con regole gratuite i ruoli chiaramente fuori profilo.
3. Recupera le descrizioni mancanti e i dati aziendali.
4. Usa Jev per i casi che richiedono un giudizio sulle mansioni.
5. Calcola i Tier dai due assi, azienda e ruolo.
6. Genera con Qwen le schede delle aziende già ammesse.
7. Lascia all'utente la revisione degli indecisi e la scelta finale.

Regole, Jev, schede e decisioni personali restano dati distinti. Il Tier viene calcolato in lettura e non viene salvato.

## Documentazione

L'indice è in [docs/README.md](docs/README.md). I riferimenti principali:

- [architettura e mappa del codice](docs/mappa-codice-dati.md);
- [pipeline completa](docs/pipeline-completa.md);
- [uso e manutenzione](docs/jobhunter-v2.md);
- [configurazione](docs/configuration.md);
- [revisione ed esplorazione in chat](docs/conversazioni-codex.md).

## Test

Dopo `python main.py init`:

```bash
python -B -m unittest discover -s tests -v
python -B tests/browser_check.py
```

I test usano database temporanei.

## Dati personali

Profilo, filtri, ricerche, chiavi (`.env.local`) e archivio (`data/`) restano sul tuo computer: sono elencati in `.gitignore`. Prima di pubblicare un fork, controlla `git status`.
