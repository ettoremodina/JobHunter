# Getting started

*[Leggi in italiano](getting-started.it.md)*

This guide takes you from a fresh clone to your first judged job ads. It covers installation, your profile, the filters and the first run. For how the pipeline works, open the illustrated overview in [`docs/jobhunter-overview.html`](jobhunter-overview.html).

JobHunter runs on your computer. Your profile, your preferences, the archive and your API keys never leave it: every personal file is ignored by Git.

> **Prefer a guided setup?** Ask your coding agent (Codex, Claude Code or similar) to *“read `skills/jobhunter-setup/SKILL.md` and follow it”*. It interviews you and writes the files described below for you.

The dashboard and the command-line messages are in Italian. The configuration files accept English or Italian text.

## 1. Install

You need Python 3.11 or newer and Git.

```bash
git clone <this-repository-url> jobhunter
cd jobhunter
python -m venv .venv
```

Activate the environment. On Windows run `.venv\Scripts\activate`; on macOS and Linux run `source .venv/bin/activate`. Then:

```bash
pip install -r requirements.txt
playwright install chromium
python main.py init
```

`init` creates the local database in `data/` and copies the example files from `examples/` wherever your own file is still missing. It never overwrites a file you already have.

## 2. Your personal files

After `init` you own these files. They start as a worked example of a fictional candidate, so replace their content with yours.

| File | What it controls |
|---|---|
| `user_context/portfolio-evidence.md` | Your experience and projects, written as facts. The source of truth for everything else. |
| `user_context/llm-selection-profile.md` | Who you are, what you want, what to exclude, your constraints. Context for the summary cards and for the review agent. |
| `config/role_filters.json` | The rule filter: excluded titles, maximum required experience, languages, preferred sectors. |
| `config/jobspy.yaml` | LinkedIn and Indeed searches: job titles, countries, ad age. |
| `config/system-one-questions.json` | The questions Jev answers: which duties fit you, which job families you exclude. |
| `config/review_questions.json` | Groups of titles the review agent asks you about. |
| `user_context/selection/regole.md` | Review rules you confirm in chat. Starts empty. |
| `.env.local` | Your API keys. |

Everything in `data/` (archive, downloaded pages, reports) is also local.

## 3. Write your profile

Start with `user_context/portfolio-evidence.md`: roles, projects, tools and results, one fact per line. Keep it factual; the other files are derived from it.

Then write `user_context/llm-selection-profile.md` with four short sections:

- **Who you are:** degree, years of experience, the thread that runs through your work.
- **What you look for:** the kinds of roles that fit, and the sectors you prefer.
- **What to exclude:** job families you don't want, described by the work, not by the title.
- **Constraints:** maximum required experience, languages, internships, anything that is a hard no.

## 4. Set the rule filter

`config/role_filters.json` decides what gets discarded for free, before any model is called. **Only put certain exclusions here.** Anything that needs judgement belongs to Jev (step 6).

| Field | Meaning |
|---|---|
| `exclude_title_patterns` | Named regular expressions. A title that matches one is discarded, for example `seniority` or `management`. |
| `exclude_title_exceptions` | For a group above, a pattern that rescues a title. For example, a mechanical title that mentions “data”. |
| `user_title_rules` | Extra title rules added after reviews. |
| `preferred_title_pattern` | Titles in your target family. They get priority, but are not kept automatically. |
| `primary_title_pattern` | A stricter set: roles strong enough to count even at a company outside your preferred sectors. |
| `max_required_years` | The most years of **mandatory** experience you accept. “Preferred” years never exclude. |
| `allowed_languages` | Languages you can work in. Others exclude only when the ad makes them mandatory. |
| `preferred_categories` | Your preferred sectors. Use names exactly as they appear in `config/categories.json`. |

Leave the other fields at their defaults to begin with. After a change, filter verdicts refresh on their own: nothing needs to be re-run.

## 5. Choose sources and searches

Sources are listed in `config/app.json` under `sources`. Each one has `"enabled": true` or `false`.

| Source | What it brings |
|---|---|
| `jobspy` | LinkedIn and Indeed, driven by `config/jobspy.yaml`. |
| `airtable` | ClimateTechList, a board of climate companies. It includes the sector. |
| `ats` | The public job boards (Greenhouse, Lever, Workday...) of the companies you care about: full text and exact date. Build the list with `python main.py ats-discover`. |
| `climatebase.org` | Climatebase, read with an automated browser. Disabled since September 2026: the site blocks automated browsers. |

The two climate boards are useful only if you care about that sector; disable them otherwise.

In `config/jobspy.yaml`, set:

- `search_queries`: job titles to search for;
- `locations`: countries;
- `site_names`: `linkedin`, `indeed`, or both;
- `hours_old`: maximum ad age in hours (168 is one week);
- `results_wanted`: results per search.

Every title is searched in every country on every board, so the number of searches grows fast. Start small.

## 6. Adapt Jev's questions

Jev is the model that reads job duties and answers typed questions with probabilities. Its questions live in `config/system-one-questions.json`, and they encode your profile, so rewrite them for yours:

- in `selection → mansioni_compatibili`, describe the work that fits you (`true`) and the work that doesn't (`false`);
- in `selection → famiglia_esclusa`, list the job families you exclude, one option each;
- in `selection → seniority_fuori_profilo`, keep the number of years consistent with `max_required_years`.

**Keep the keys unchanged.** The code reads the question names (`mansioni_descritte`, `mansioni_compatibili`, `famiglia_esclusa`, `posto_per_studenti`, `seniority_fuori_profilo`, `prova`) and the family options `nessuna`, `produzione_manutenzione` and `officina_ricambi`. You can add or remove the other families. You can write the texts in English.

The thresholds that turn Jev's probabilities into keep, unsure or discard are in `config/system_one.json`. The defaults are a reasonable start.

## 7. Add your API keys

Both keys are optional:

| Key | Service | Without it |
|---|---|---|
| `TYPESAFE_API_KEY` | Jev by TypeSafe: judges roles and company sectors | Every role that passes the filter stays “unsure”. |
| `JOBHUNTER_API_KEY` | Qwen on Alibaba Cloud Model Studio: writes the summary cards | No summary cards. Verdicts are not affected. |

Copy `.env.local.example` to `.env.local`, then paste each key after its `=` with no quotes or spaces. `.env.local` is ignored by Git. Never put a key in a config file.

A Jev run over an archive of about 23,000 ads costs about $1.20. Every paid command has a free preview.

## 8. First run

First check your files for common mistakes: invalid regular expressions, sector names missing from the vocabulary, renamed question keys, profiles still set to the example. It prints `OK` when everything is in order.

```bash
python skills/jobhunter-setup/scripts/check_config.py
```

Start with a small batch and check it by hand before scaling up.

```bash
python main.py collect jobspy --limit 20
python main.py fetch-descriptions --limit 50
python main.py system-one --limit 10
python main.py system-one --limit 10 --execute
python main.py serve
```

1. `collect` downloads a few ads.
2. `fetch-descriptions` fetches their text.
3. `system-one` without `--execute` is a free preview. It shows what would be sent, without a key and without calls.
4. `--execute` makes the paid calls.
5. `serve` opens the dashboard at <http://127.0.0.1:8000>. On Windows you can also double-click `Avvia JobHunter.pyw`.

In the dashboard, open **Aziende** to see companies and their verdicts, and **Metriche** to see why ads were discarded. If the filter discards roles you want, fix `role_filters.json`; if Jev misjudges them, fix its questions.

When the sample looks right:

- run `python main.py collect-all` for the full collection;
- open the **Pipeline** tab, start the next step and tick “Continua da qui con i passaggi successivi” to run the rest in sequence.

## 9. Review uncertain roles with an agent

Roles Jev can't decide are marked “unsure” and wait for you. The skill in `skills/jobhunter/` lets a coding agent go through them with you in small batches. It asks questions, proposes rules, and records only what you confirm. The rules you confirm go to `user_context/selection/regole.md`.

To use it, point your agent at `skills/jobhunter/SKILL.md`, or copy the folder into your agent's skills folder. For example, `.claude/skills/` for Claude Code or `~/.codex/skills/` for Codex. More detail in [conversazioni-codex.md](conversazioni-codex.md) (Italian).

## Keeping your data private

Your personal files, `.env.local` and `data/` are listed in `.gitignore`. Before pushing a fork, run `git status` and check that none of them appear.
