"""Check a user's personal JobHunter files before the first run.

Run from the repository root: `python skills/jobhunter-setup/scripts/check_config.py`.
Prints one line per problem and exits with status 1, or prints OK and exits with 0.
It reads files only: no network, no database, no API keys.
"""

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_MARKER = "Example file: a fictional candidate"
JEV_QUESTIONS = {"mansioni_descritte", "mansioni_compatibili", "famiglia_esclusa",
                 "posto_per_studenti", "seniority_fuori_profilo", "prova"}
JEV_FAMILIES = {"nessuna", "produzione_manutenzione", "officina_ricambi"}


def regexes(filters):
    """Yield (field, pattern) for every regular expression in role_filters.json."""
    for group in ("exclude_title_patterns", "exclude_title_exceptions", "language_patterns"):
        for key, pattern in filters.get(group, {}).items():
            yield f"{group}.{key}", pattern
    for field in ("preferred_title_pattern", "primary_title_pattern"):
        if field in filters:
            yield field, filters[field]
    for rule in filters.get("user_title_rules", []):
        for field in ("pattern", "evidence_pattern"):
            if field in rule:
                yield f"user_title_rules.{rule.get('id')}.{field}", rule[field]


def problems(root=ROOT):
    """Return a list of human-readable problems in the personal files under `root`."""
    found = []
    read = lambda rel: (root / rel).read_text(encoding="utf-8")
    for rel in ("user_context/portfolio-evidence.md", "user_context/llm-selection-profile.md"):
        if not (root / rel).exists():
            found.append(f"{rel}: missing, run `python main.py init`")
        elif EXAMPLE_MARKER in read(rel):
            found.append(f"{rel}: still the fictional example")
    try:
        filters = json.loads(read("config/role_filters.json"))
        vocabulary = set(json.loads(read("config/categories.json")))
        for name in set(filters.get("preferred_categories", [])) - vocabulary:
            found.append(f"role_filters.json: preferred category {name!r} is not in config/categories.json")
        for field, pattern in regexes(filters):
            try:
                re.compile(pattern)
            except re.error as exc:
                found.append(f"role_filters.json: {field} is not a valid regex ({exc})")
        missing = set(filters.get("allowed_languages", [])) - set(filters.get("language_patterns", {}))
        for language in missing:
            found.append(f"role_filters.json: allowed language {language!r} has no entry in language_patterns")
    except (OSError, ValueError) as exc:
        found.append(f"role_filters.json or categories.json: {exc}")
    try:
        questions = json.loads(read("config/system-one-questions.json"))["selection"]
        for key in JEV_QUESTIONS - set(questions):
            found.append(f"system-one-questions.json: question {key!r} is missing")
        families = set(questions.get("famiglia_esclusa", {}).get("criteria", {}))
        for key in JEV_FAMILIES - families:
            found.append(f"system-one-questions.json: family option {key!r} is missing")
    except (OSError, ValueError, KeyError) as exc:
        found.append(f"system-one-questions.json: {exc}")
    try:
        searches = yaml.safe_load(read("config/jobspy.yaml")) or {}
        for key in ("search_queries", "locations", "site_names"):
            if not searches.get(key):
                found.append(f"jobspy.yaml: {key} is empty")
    except (OSError, yaml.YAMLError) as exc:
        found.append(f"jobspy.yaml: {exc}")
    return found


if __name__ == "__main__":
    issues = problems()
    print("\n".join(issues) if issues else "OK")
    sys.exit(1 if issues else 0)
