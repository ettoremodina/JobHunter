# JobHunter

*[Leggi in italiano](README.it.md)*

JobHunter collects job ads from several boards, groups them by company, and runs them past a series of judges, from the cheapest to the most careful, until a short list remains. Everything runs on your computer: SQLite and local snapshots hold the data, and a local dashboard, a CLI and a chat agent all read the same archive.

To see how it works, open the illustrated overview at [docs/jobhunter-overview.html](docs/jobhunter-overview.html).

## Getting started

The guide [docs/getting-started.md](docs/getting-started.md) takes you from a fresh clone to your first judged job ads. In short:

```bash
python -m venv .venv
pip install -r requirements.txt
playwright install chromium
python main.py init
python main.py serve
```

`init` creates the database and your personal files (profile, filters, searches) from the examples in `examples/`. For a guided setup, ask your coding agent to follow [skills/jobhunter-setup/SKILL.md](skills/jobhunter-setup/SKILL.md).

The dashboard runs at <http://127.0.0.1:8000>. On Windows you can also open `Avvia JobHunter.pyw`. The dashboard, the CLI messages and most of the in-depth documentation in `docs/` are in Italian.

## How it works

1. Collects and normalizes job ads.
2. Discards, with free rules, the roles that are clearly out of scope.
3. Fetches missing descriptions and company data.
4. Uses Jev for the cases that need a judgement on the duties.
5. Computes Tiers from two axes: company and role.
6. Has Qwen write summary cards for the companies already admitted.
7. Leaves the uncertain cases and the final choice to the user.

Rules, Jev, summaries and personal decisions stay separate. The Tier is computed on read and never stored.

## Tests

After `python main.py init`:

```bash
python -B -m unittest discover -s tests -v
python -B tests/browser_check.py
```

Tests use temporary databases.

## Your data stays local

Your profile, filters, searches, API keys (`.env.local`) and archive (`data/`) stay on your computer: they are listed in `.gitignore`. Before pushing a fork, check `git status`.
