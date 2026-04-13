"""
This is the main orchestrator script.
It reads all site configurations from the `sites/` directory,
runs the scraper and mapper for each site sequentially,
and finally aggregates all structured results into a single CSV file.
"""
import os
import glob
import json
import csv
import asyncio
import logging
import argparse

# Import the main functions from our scraping and mapping modules
from core_scraper import scrape_site
from core_mapper import main as map_site

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def process_site(site_name: str, use_llm: bool):
    """Run the scraping and mapping pipeline for a single site."""
    logger.info(f"=== Starting pipeline for {site_name} ===")
    
    logger.info(f"Step 1: Scraping {site_name}")
    try:
        await scrape_site(site_name)
    except Exception as e:
        logger.error(f"Scraping failed for {site_name}: {e}")
        return

    logger.info(f"Step 2: Mapping {site_name}")
    try:
        await map_site(site_name, use_llm=use_llm)
    except Exception as e:
        logger.error(f"Mapping failed for {site_name}: {e}")
        return
        
    logger.info(f"=== Finished pipeline for {site_name} ===")

def generate_csv(output_filename="all_jobs_results.csv"):
    """Reads all structured_results.json and exports them to a unified CSV."""
    logger.info("Aggregating results into a final CSV...")
    all_jobs = []
    keys_set = set()
    
    # Parent directory for all scraping runs
    runs_dir = os.path.join("data", "runs", "scrape")
    if not os.path.exists(runs_dir):
        logger.warning(f"No scrape data found at {runs_dir}.")
        return

    # Loop through each domain folder
    for domain in os.listdir(runs_dir):
        domain_path = os.path.join(runs_dir, domain)
        if not os.path.isdir(domain_path):
            continue
            
        result_path = os.path.join(domain_path, "structured_results.json")
        if os.path.exists(result_path):
            try:
                with open(result_path, 'r', encoding='utf-8') as f:
                    jobs = json.load(f)
                    for job in jobs:
                        job['source_site'] = domain  # Track where it came from
                        keys_set.update(job.keys())
                        all_jobs.append(job)
            except Exception as e:
                logger.error(f"Error reading {result_path}: {e}")

    if not all_jobs:
        logger.warning("No structured jobs found to export.")
        return

    # Ensure consistent order of columns. Put the most important ones first.
    priority_keys = [
        "title", "company_name", "location", "work_model", 
        "job_type", "posted_date", "source_site", "original_url", 
        "summary", "description"
    ]
    # Add any extra keys that might vary between sites to the end
    other_keys = sorted(list(keys_set - set(priority_keys)))
    fieldnames = [k for k in priority_keys if k in keys_set] + other_keys

    # Write the CSV file
    try:
        with open(output_filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for job in all_jobs:
                writer.writerow(job)
        logger.info(f"✅ Exported {len(all_jobs)} jobs to {output_filename}")
    except Exception as e:
        logger.error(f"Failed to write CSV file: {e}")

async def run_all(use_llm: bool):
    """Discover all sites and orchestrate the full pipeline."""
    # Find all .yaml files in the sites directory
    site_files = glob.glob(os.path.join("sites", "*.yaml"))
    site_names = [os.path.basename(f).replace(".yaml", "") for f in site_files]
    
    if not site_names:
        logger.error("No site configuration files found in 'sites/' directory.")
        return

    logger.info(f"Found {len(site_names)} sites to process: {', '.join(site_names)}")

    # Process each site chronologically
    for site in site_names:
        await process_site(site, use_llm)

    # Compile the final result
    generate_csv()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run full scrape and map pipeline for all sites.")
    parser.add_argument("--use-llm", action="store_true", help="Use LLM to generate summaries (default: False)")
    args = parser.parse_args()
    
    asyncio.run(run_all(args.use_llm))
