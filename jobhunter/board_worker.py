"""Isolate a paginated board query so its parent can bound execution time."""

import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def main():
    """Persist every completed page and stop on empty, short, duplicate or capped results."""
    from jobspy import scrape_jobs
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = Path(sys.argv[2])
    rows, seen = [], set()
    report = {"rows": rows, "stop": "page_cap", "pages": 0}
    for page in range(spec.pop("max_pages")):
        delay = spec.get("pause", 1)
        args = {k: v for k, v in spec.items() if k != "pause"}
        args["offset"] = page * args["results_wanted"]
        frame = scrape_jobs(**args)
        batch = json.loads(frame.to_json(orient="records", date_format="iso"))
        new = []
        for row in batch:
            key = row.get("job_url") or json.dumps(row, sort_keys=True)
            if key not in seen:
                seen.add(key)
                new.append(row)
        rows.extend(new)
        report["pages"] = page + 1
        report["stop"] = "empty_or_blocked" if not batch else "no_new_urls" if not new else "short_page" if len(batch) < args["results_wanted"] else "page_cap"
        output.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        if report["stop"] != "page_cap":
            break
        time.sleep(delay)
    logger.info("Board query finished: %s", report["stop"])


if __name__ == "__main__":
    main()
