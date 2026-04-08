### Project Architecture Overview
---

This project is designed to run in two modes:

* **GitHub Actions** for scheduled automation.
* **Local execution** for development, debugging, and LLM provider experimentation.

The LLM layer should be provider-agnostic so you can swap between a hosted model and a local model without changing the pipeline logic.

### Phase 1: Project Scaffolding
**Goal:** Create the folder structure and environment.

* **Task 1.1:** Create a Python project with the following structure:
    * `scraper.py` (Handles fetching HTML)
    * `processor.py` (Handles LLM extraction, screening, and provider selection)
    * `notifier.py` (Handles Telegram messages)
    * `main.py` (The orchestrator)
    * `config/` (Runtime configuration for model, mode, and paths)
    * `user_info/` (Input folder with your profile and website list)
    * `data/` (Persistent run artifacts and state)
    * `.github/workflows/job_hunt.yml` (The GitHub Actions config)
    * `requirements.txt` (List dependencies for both hosted and local execution)
* **Task 1.2:** Set up a `.env` file for local testing to store:
    * `OPENAI_API_KEY`
    * `TELEGRAM_BOT_TOKEN`
    * `TELEGRAM_CHAT_ID`
    * `LLM_PROVIDER`
    * `LLM_MODEL`
    * `RUN_MODE` (`local` or `github_actions`)

* **Task 1.3:** Add user-managed input files under `user_info/`:
    * `profile.md` or `profile.json` for your bio, skills, preferences, and constraints
    * `websites.txt` or `websites.json` for the list of sites to inspect

---

### Phase 1.5: Run Records & Manual Inspection
**Goal:** Save every intermediate step so each run can be inspected later.

* **The Data Shape:** Create a run directory for each execution, for example `data/runs/2026-04-08T09-00-00/`.
* **Store Each Step:** Persist the inputs and outputs for each stage, including:
    * source URL or site name
    * raw HTML or fetched page text
    * cleaned text passed to the LLM
    * prompt metadata and model name
    * LLM response before parsing
    * parsed job results
    * deduplication decisions
    * notification payloads
    * errors and warnings
    * run status and failure reason when a site or the full pipeline breaks
* **Recommended Files:**
    * `run.json` for metadata about the execution
    * `sources.json` for the list of sites processed
    * `steps/` for per-site step outputs
    * `results.json` for the final normalized output

This makes it possible to manually inspect one run without relying on logs alone.
It also makes failed runs actionable, because the temporary files should contain enough context to diagnose what broke and fix it quickly.

---

### Phase 1.6: Initialization Loop & Cached Navigation Profiles
**Goal:** Start from homepage URLs only, discover job paths once, and reuse them.

* **Initialization Pass:** Run a discovery pass that traverses each homepage, gathers candidate links, and selects the best jobs/careers URL.
* **LLM Guidance:** Use the selected provider (OpenAI/Ollama/local mode) to rank discovered candidates and choose the best job listing path.
* **Cached Profiles:** Save results to `data/site_profiles.json`, including:
    * selected URL
    * candidate list and scores
    * selection reason
    * interaction recipe (entry URL, extraction strategy, filter/sort/pagination hints)
* **Reuse in Future Runs:** Normal runs should use the cached `selected_url` so they do not rediscover navigation every time.
* **Refresh Option:** Support a refresh mode when websites change navigation.

* **CLI Flags:**
    * `--init-discovery` to initialize profiles before processing
    * `--refresh-discovery` to force profile refresh

---

### Phase 2: The "Smart" Scraper (`scraper.py`)
**Goal:** A function that takes a URL and returns clean, readable text.

* **Prompt for Copilot:** > "Write a Python class `JobScraper`. It should have a method `fetch_content(url)`. 
    > 1. First, try fetching the page using `requests` and `BeautifulSoup`. 
    > 2. Strip out all `<script>`, `<style>`, and `<nav>` tags to reduce token count.
    > 3. If the resulting text is too short (indicating a JS-heavy site), fallback to using `Playwright` to render the page and then extract the text.
    > 4. Return the cleaned string of text."

---

### Phase 3: AI Extraction & Screening (`processor.py`)
**Goal:** Use the selected LLM provider to turn messy text into structured job data.

* **Prompt for Copilot:**
    > "Write a script using the OpenAI Python SDK. Create a function `process_text(raw_text, user_description)`. 
    > Use a System Prompt that tells the AI: 'You are a career assistant. Scan the following text for job listings. For each job, determine if it matches the user's profile. Return a JSON list of matches containing: title, company, link, and a 1-sentence reason why it fits.' 
    > Make the LLM backend configurable so it can use a hosted API or a local model. Keep the parsing and result schema identical regardless of provider."

* **Implementation Note:** The processor should return both the parsed jobs and the raw model output so the run artifacts can capture the full intermediate state.

---

### Phase 4: State Management & Deduplication
**Goal:** Ensure you don't get alerted for the same job every week.

* **The Logic:** Create a simple `seen_jobs.json` file. Before sending a notification, check if the job link is already in this file. 
* **Prompt for Copilot:**
    > "Add a helper function to `main.py` that loads a local JSON file of 'seen_links'. After the AI extracts jobs, filter out any links that have already been seen. Append new links to the file and save it."

---

### Phase 5: The Telegram Notifier (`notifier.py`)
**Goal:** Send the final report to your phone.

* **Prompt for Copilot:**
    > "Write a function `send_telegram_report(jobs)` that takes a list of job dictionaries. Format them into a single, clean Markdown message (e.g., **Title** @ Company - [Link]). Use the `requests` library to POST this message to the Telegram Bot API."

* **Failure Alerts:** If a run fails, send an alert message instead of a normal job summary. The alert should say what went wrong, such as a site being unreachable, a scraping timeout, a parsing failure, or an LLM/provider error, so you can correct the issue quickly.

---

### Phase 6: GitHub Actions Workflow (`job_hunt.yml`)
**Goal:** Automate the run.

* **The Config:**
    * **Schedule:** `cron: '0 9 * * 1'` (Every Monday at 9 AM).
    * **Permissions:** Ensure the action has "Write" permissions so it can commit the updated `seen_jobs.json` back to your repository.
    * **Steps:** 1. Checkout code.
        2. Set up Python.
        3. Install dependencies and Playwright browsers (`playwright install chromium`).
        4. Run `python main.py` in GitHub Actions mode.
        5. Auto-commit the updated `seen_jobs.json` and any other intended state files.

* **Local Bypass:** The same pipeline should be runnable locally without GitHub Actions by selecting `RUN_MODE=local` or a CLI flag such as `--local`. Local runs should write the same `data/runs/` artifacts.

---

### Implementation Tips for Copilot

1.  **Iterate:** Don't ask Copilot to write the whole thing at once. Ask it for `scraper.py` first, test it, then move to the next file.
2.  **Tokens are Money:** Remind Copilot to write code that "cleans" the HTML (removing headers/footers) before sending it to the LLM. You don't want to pay for the AI to read a website's "Terms of Service" or "Privacy Policy."
3.  **Error Handling:** Tell Copilot to "Wrap each website in its own failure boundary so that if one website fails, the whole script doesn't crash."
4.  **Traceability:** Preserve every intermediate artifact in `data/runs/` before filtering or deduping so you can inspect what the model saw and produced.
5.  **Failure Visibility:** When something breaks, notify on the failure itself and include the recorded error details from the run artifact so the problem is obvious from the alert.

---

### Operational Suggestions
**Goal:** Make the pipeline easier to debug, retry, and maintain.

* **Run Schema:** Define a strict run record with fields like `run_id`, `status`, `failed_stage`, `site_url`, `error_type`, `error_message`, `timestamps`, and `artifact_paths`.
* **Failure Categories:** Classify failures as `site unreachable`, `timeout`, `scrape parse failure`, `empty content`, `LLM provider failure`, or `notification failure`.
* **Retries:** Add retry logic with backoff for transient network failures so temporary outages do not create unnecessary alerts.
* **Per-Site Artifacts:** Store artifacts in a per-site subfolder inside each run directory so one broken website can be inspected independently.
* **Dry Run Mode:** Add a mode that runs scraping and processing, saves all artifacts, but skips Telegram delivery.
* **CLI Flags:** Support flags like `--local`, `--dry-run`, `--run-id`, and `--site-filter` to make debugging specific failures faster.
* **Run Manifest:** Keep a manifest for the latest successful run and the latest failed run.
* **Retention Rules:** Keep failed runs longer than successful runs so the most valuable debugging history stays available.
* **Tests:** Add tests for alert formatting, failure capture, and run-record creation.
* **Provider Selection:** Make the LLM provider explicit in configuration rather than inferred so switching between hosted and local models is predictable.

**How many websites are on your initial list?** If it's a small list (under 10), we can probably stick to simple `requests` logic for most of them.