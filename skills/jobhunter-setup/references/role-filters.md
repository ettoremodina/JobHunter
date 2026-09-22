# Editing `config/role_filters.json`

The rule filter runs on every read, costs nothing, and can only **discard**. A discarded ad never reaches Jev, so a wrong exclusion silently hides good roles. Every pattern here must be certain from the title alone.

All patterns are Python regular expressions matched with `re.search(pattern, title, re.I)`: case-insensitive, anywhere in the title. Use `\b` word boundaries and group alternatives, as the existing patterns do. In JSON, every backslash is doubled (`\\b`).

## How a title is decided

1. **Exclusion groups.** If any pattern in `exclude_title_patterns` matches, and the same group's pattern in `exclude_title_exceptions` does not, the ad is discarded. The group name becomes the reason shown in the dashboard.
2. **Explicit requirements.** The code reads the description too: mandatory experience above `max_required_years`, required people management, and mandatory languages outside `allowed_languages` also discard.
3. **User title rules.** Otherwise, `user_title_rules` are tried from last to first; the first match applies its `action`:
   - `exclude` discards;
   - `include` marks the title as a target;
   - `review` sends it on as uncertain.

   With an `evidence_pattern`, an `include` counts only when that pattern also appears in the title or description; without it, the ad goes on as uncertain.
4. **Target family.** A match on `preferred_title_pattern` marks the title as a target. Target or not, every surviving ad goes to Jev: this only orders the work.

`primary_title_pattern` is independent of the above: a match makes the role a **priority** role, one strong enough to put a company outside the preferred sectors into Tier B.

## Adapting each field

| Field | How to adapt it |
|---|---|
| `exclude_title_patterns` | Go through each group against the sheet. Delete a group that conflicts with a target role: for a candidate who wants sales roles, `marketing_sales` must go. Keep `seniority` and `management` unless the candidate is senior. Add a group only for a family the candidate excludes that is recognisable from the title alone. |
| `exclude_title_exceptions` | For each technical group, the words that rescue a title that is really about software, data or modelling. Adapt them to the candidate's field. |
| `preferred_title_pattern` | All the target titles from the sheet, as one alternation. |
| `primary_title_pattern` | The narrower subset of titles the candidate would consider even at a company outside the preferred sectors. |
| `user_title_rules` | Keep or replace the two examples. Each rule needs a unique `id`, a `pattern`, an `action` and a `note` explaining why. |
| `max_required_years` | From the sheet. Keep it consistent with the seniority question in Jev's file. |
| `allowed_languages` | Names that exist as keys in `language_patterns`. Add a key there for any language missing. |
| `preferred_categories` | Exact names from `config/categories.json`. |
| `notes` | The reasoning in plain words. The code ignores it; it is for the user. |
| `queue_size`, `fresh_days`, `priority_weights`, `proposal_min_companies` | Leave unchanged. |

## Checking a pattern

Before saving, try a new pattern on titles you expect it to catch and on titles it must spare:

```bash
python -c "import re; p=r'\b(senior|lead)\b'; print([t for t in ['Senior Data Scientist','Data Scientist','Team Lead ML'] if re.search(p, t, re.I)])"
```

After the first collection, the dashboard's Metriche tab shows how many ads each reason discarded. A reason that discards far more than expected points to a pattern that is too broad.
