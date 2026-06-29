# JobHunter

JobHunter is an autonomous data aggregation pipeline that scrapes, structures, and semantically scores technical job openings from multiple web origins.

It parses generic job boards (using JobSpy), opaque embed applications (Airtable platforms like ClimateTechList), and specific dynamic company/job-board sites using Playwright + Crawl4AI. Once fetched, the engine runs all descriptions through Semantic Neural Embeddings to rank results by how closely they match a custom user profile.

---

## What It Can Do

- **Full Network Extraction**: Automate pulling from generalized platforms (LinkedIn, Indeed), specialized Airtables, and bespoke DOM/React sites.
- **Granular Control Engine**: Select which sources run, bypass cache logic, execute partial stages (just parse mapping, just scoring text embeddings, etc).
- **Semantics Based Scoring**: Extracts strings from Python files to evaluate relevancy via `all-MiniLM-L6-v2`. (Is this job a "Physics-Informed RL Role" or a generic "Senior Cloud Eng" role?)
- **Multi-Source Unification**: Merges outputs from highly incongruent schema into a pristine, single flattened CSV Grouped by Company.

## Quick Start

### 1. Requirements

You must be on Python 3.9+. 

```bash
python -m venv .venv
source .venv/bin/activate  # (Or .venv\Scripts\activate on Windows)
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment

Create a `.env` in the root folder.
```env
OPENAI_API_KEY=""
LLM_PROVIDER="ollama"          # optional local summaries via Custom Scraper mapping
LLM_MODEL="llama3.2:3b"
OLLAMA_ENDPOINT="http://localhost:11434/api/generate"
# plus any TELEGRAM_BOT_TOKEN logic going forward
```

### 3. Execution

Execute a full pipeline run with default settings:

```bash
python main.py
```

JobHunter operates via a highly customizable Orchestrator CLI. You can target particular steps or sources. Check out [`docs/orchestrator.md`](docs/orchestrator.md) for full commands. Common patterns:

```bash
# Only run Airtable scraper ignoring cache limitations
python main.py --sources airtable --force-scrape

# Only map and merge specific custom Playwright integrations
python main.py --sources custom --sites climatebase.org --steps map,merge
```

## Documentation Reference 

Detailed explanations of mechanics, schemas, and configurations.

- [**Architecture Overview**](docs/architecture.md): Module dependency graph and package layout.
- [**Pipeline Flow**](docs/pipeline.md): What variables exist at each `scrape -> map -> filter -> score -> merge` layer.
- [**CLI Commands**](docs/orchestrator.md): Details on the granular flags for `--steps`, `--sources` and force overrides.
- [**Mapping & Merging**](docs/mapping_and_merging.md): Field mapping specs for all three source types.
- [**Triage & Scoring**](docs/triage_and_scoring.md): LLM-based scoring, company multipliers, and interactive triage.
- [**Adding New Sources**](docs/adding_sources.md): Developer guide for integrating a new web property.
- [**API Reference**](docs/api/index.html): Auto-generated HTML docs from source docstrings (browsable locally).

### Regenerating API Docs

```bash
python docs/generate_docs.py
```

## Output

Under default runtime, JobHunter merges its aggregated datasets across all scrapers into a flat file at `data/all_jobs_merged.csv`, grouped closely by Company Name, and sorted internally by Semantic Fit Score mapping to your criteria in `user_info/profile.md`. 