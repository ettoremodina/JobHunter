# Editing `config/system-one-questions.json`

Jev reads the duties of every ad that survives the rule filter and answers typed questions, each on its own:

- **`noul`** questions get a probability that the answer is yes;
- **`choice`** questions get one option chosen from the declared `criteria`, with a confidence.

Jev writes no text and has no other instructions: **the questions are the whole rubric**, read literally. The code turns the answers into keep, unsure or discard with the thresholds in `config/system_one.json`.

## The fixed contract

Question keys and three option keys are read by name in the code. Keep these keys exactly:

- in `selection`: `mansioni_descritte`, `mansioni_compatibili`, `famiglia_esclusa`, `posto_per_studenti`, `seniority_fuori_profilo`, `prova`;
- in `famiglia_esclusa.criteria`: `nessuna` (no excluded family), `produzione_manutenzione` and `officina_ricambi`. The last two are manual-work families that can be discarded from a short description; keep them even if they rarely apply.

Everything else is free text: `instructions` and every `criteria` value. Backtick names such as `mansioni` or `azienda` are fields of the text Jev receives; keep them as they are. The `category` section judges company sectors and is generic, so leave it unchanged.

## What to rewrite

**`mansioni_compatibili`** is the heart of the rubric.

- `instructions`: what counts as fitting work, judged by the activities rather than by the sector or the title.
- `criteria.true`: the target work from the sheet, as a list of activities.
- `criteria.false`: the work that does not fit, including look-alike cases from the sheet. For example, reporting on sales KPIs for a candidate who wants modelling.

**`famiglia_esclusa`** holds one option per excluded job family.

- Add, remove or rewrite the options between `nessuna` and the two manual families so they match the excluded work in the sheet.
- Each option's text says what the family's main work is, and when *not* to choose it. For example: “choose `nessuna` when the person builds software or models, even for a sales team”.
- Option keys are short snake_case names; they appear in the dashboard as reasons.

**`seniority_fuori_profilo`** must use the same number of years as `max_required_years` in `role_filters.json`: “more than N years of mandatory experience” in `instructions` and `criteria.false`, and “at least N+1 years” in `criteria.true`.

**`posto_per_studenti`** defaults to excluding internships and student posts while keeping PhD positions. Adapt its `criteria` if the sheet says otherwise.

**`mansioni_descritte`** and **`prova`** are generic: leave them unchanged.

## Language

Jev reads English best. Writing the rubric in English is fine even when the ads are in other languages; the example is in Italian because it was written for an Italian user.

## Before spending money

Changing a question re-queues every ad that question has judged, so the next `--execute` pays for all of them again. Settle the rubric on the small first batch before the full collection. Changing a role threshold in `config/system_one.json` is free: the verdict is recomputed from the saved probabilities.
