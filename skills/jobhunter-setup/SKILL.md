---
name: jobhunter-setup
description: Set up JobHunter for a new user — interview them, then write their profile, rule filter, job searches and Jev questions, and run a first small, checked collection. Use when installing JobHunter, configuring a profile or preferences, or adapting its filters to a different person.
---

# JobHunter setup

Guide one person from a fresh clone to a first batch of judged job ads that they have checked by hand. Reply in the user's language; the files you write may be in English or Italian.

Work from the repository root (the folder with `main.py`). Use the interpreter of the local `.venv` when it exists. The user-facing guide with the same content is `docs/getting-started.md`: read it once before step 1.

## Ground rules

- **Personal files** stay on the user's machine and are ignored by Git: `user_context/`, `config/role_filters.json`, `config/jobspy.yaml`, `config/system-one-questions.json`, `config/review_questions.json`, `.env.local`, `data/`. They start as copies of `examples/`, a fictional junior data scientist; every example value is replaced or consciously kept.
- **API keys** go only into `.env.local`, typed there by the user. You create the file from `.env.local.example`; the user pastes the values.
- **Paid calls** (`--execute`) run only after the user says yes to that specific command, with the number of records stated.
- **Certain vs. judged:** the rule filter holds only exclusions that are certain from the title or an explicit requirement. Anything that needs reading the duties belongs to Jev's questions.

## Steps

### 1. Install and initialize

Check Python 3.11+, create `.venv` if missing, install `requirements.txt`, run `playwright install chromium`, then `python main.py init`.

Done when `init` prints its JSON and the personal files listed above exist.

### 2. Interview

Ask a few questions at a time, and offer the user the option to paste a CV or a LinkedIn summary instead of answering one by one. Fill this sheet:

| Topic | What to capture |
|---|---|
| Background | degree, years of full-time experience, main roles and projects, tools |
| Target roles | the kinds of work that fit, described by activities, plus typical job titles |
| Preferred sectors | chosen from the names in `config/categories.json`; say whether strong roles outside them still count |
| Excluded work | job families they don't want, described by the work |
| Experience | the most years of **mandatory** experience they accept |
| Languages | languages they can work in |
| Internships, PhD | whether internships and student posts are excluded; whether PhD positions count |
| Places | countries to search |
| Sources | whether the climate boards (ClimateTechList, Climatebase) are relevant |

Done when every row has an answer or an explicit “no preference”, and the user has confirmed your summary of the sheet.

### 3. Write the profile

Rewrite `user_context/portfolio-evidence.md` as facts, one per line. Then rewrite `user_context/llm-selection-profile.md` with the four sections of the example: who they are, what they look for, what to exclude, constraints. Remove the “Example file” line from both.

Done when the user has read both files and confirmed them.

### 4. Rule filter

Edit `config/role_filters.json` following [references/role-filters.md](references/role-filters.md).

Done when every exclude group is either justified by the sheet or removed, the title patterns cover the target titles, and `preferred_categories`, `max_required_years` and `allowed_languages` match the sheet.

### 5. Searches and sources

In `config/jobspy.yaml`, set `search_queries` to 2–5 target titles and `locations` to the countries in the sheet; keep `site_names`, `hours_old` and `results_wanted` unless the user asks. Tell the user the number of searches per run: titles × countries × boards. In `config/app.json`, set `"enabled": false` on `airtable` and `climatebase.org` when the climate boards are not relevant.

Done when the user has approved the searches and the count.

### 6. Jev's questions

Edit `config/system-one-questions.json` following [references/jev-questions.md](references/jev-questions.md).

Done when the compatible-duties criteria describe the target work from the sheet, the family options cover the excluded work, and the seniority question uses the same number of years as `max_required_years`.

### 7. Check the configuration

Run `python skills/jobhunter-setup/scripts/check_config.py`. Fix each reported problem and run it again.

Done when it prints `OK`.

### 8. Keys

Explain the two optional keys: `TYPESAFE_API_KEY` for Jev, which judges roles and sectors, and `JOBHUNTER_API_KEY` for Qwen, which writes summary cards. Explain what happens without each one, as described in the guide. Create `.env.local` from `.env.local.example` if it is missing, and let the user paste their keys.

Done when the user says the keys are in place, or that they will run without them.

### 9. First run

```bash
python main.py collect jobspy --limit 20
python main.py fetch-descriptions --limit 50
python main.py system-one --limit 10
```

The last command is a free preview. Show the user what it would send, then ask before running it again with `--execute`. Then start `python main.py serve` and walk through the result with the user:

- ads the filter discarded that they want → fix `role_filters.json` (step 4);
- ads Jev misjudged → fix the questions (step 6).

Done when the user has looked at the judged sample and either accepts it or every change they asked for is applied and re-checked with step 7.

### 10. Hand-off

Tell the user the next moves:

- `python main.py collect-all` for the full collection;
- the dashboard's Pipeline tab for the remaining steps;
- the `skills/jobhunter` skill to review the roles Jev leaves “unsure”, where confirmed rules accumulate in `user_context/selection/regole.md`.
