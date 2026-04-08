"""CLI entrypoint orchestrating scraping, LLM processing, persistence, and alerts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from job_hunt.config.settings import Settings, load_settings
from job_hunt.core.errors import categorize_error
from job_hunt.core.run_store import RunStore
from job_hunt.core.types import RunRecord, SiteRunStep
from job_hunt.core.verification import (
    apply_profile_filters,
    has_blacklisted_path_token,
    is_homepage,
    link_is_reachable,
    normalize_link,
    verify_jobs,
)
from job_hunt.notify.notifier import TelegramNotifier
from job_hunt.processing.processor import JobProcessor
from job_hunt.scraping.discovery import SiteDiscovery
from job_hunt.scraping.scraper import JobScraper


def now_iso() -> str:
    """Return current UTC timestamp in ISO format."""

    return datetime.now(tz=timezone.utc).isoformat()


def build_run_id() -> str:
    """Build a stable run id based on UTC timestamp."""

    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def log_progress(message: str) -> None:
    """Print a timestamped progress line for local observability."""

    print(f"[{now_iso()}] {message}")


def load_profile(user_info_dir: Path) -> str:
    """Load user profile from markdown or json file."""

    profile_md = user_info_dir / "profile.md"
    profile_json = user_info_dir / "profile.json"
    if profile_md.exists():
        return profile_md.read_text(encoding="utf-8")
    if profile_json.exists():
        return profile_json.read_text(encoding="utf-8")
    raise FileNotFoundError("missing user profile in user_info/profile.md or user_info/profile.json")


def load_sites(user_info_dir: Path) -> list[str]:
    """Load website homepages from text or json input file."""

    websites_txt = user_info_dir / "websites.txt"
    websites_json = user_info_dir / "websites.json"

    if websites_txt.exists():
        lines = websites_txt.read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]

    if websites_json.exists():
        import json

        payload = json.loads(websites_json.read_text(encoding="utf-8"))
        sites = payload.get("sites", payload)
        if isinstance(sites, list):
            return [str(site).strip() for site in sites if str(site).strip()]
    raise FileNotFoundError("missing website list in user_info/websites.txt or user_info/websites.json")


def filter_sites(sites: list[str], site_filter: str | None) -> list[str]:
    """Filter sites by substring when a site filter is provided."""

    if not site_filter:
        return sites
    token = site_filter.lower()
    return [site for site in sites if token in site.lower()]


def dedupe_jobs(jobs: list[dict], seen_links: set[str]) -> tuple[list[dict], set[str]]:
    """Remove already-seen links and return updated seen-link set."""

    deduped: list[dict] = []
    new_links = set(seen_links)
    for job in jobs:
        link = normalize_link(str(job.get("link", "")).strip())
        title = str(job.get("title", "")).strip().lower()
        if not link or not title:
            continue
        dedupe_key = f"{link}::{title}"
        if dedupe_key in new_links:
            continue
        new_links.add(dedupe_key)
        deduped.append(job)
    return deduped, new_links


def load_site_policies(path: Path) -> dict:
    """Load optional per-domain scrape policy configuration."""

    if not path.exists():
        return {"domains": {}}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {"domains": {}}


def get_site_policy(site_url: str, policies: dict, settings: Settings) -> dict:
    """Resolve scrape timeout/retry policy for a site using domain mapping."""

    hostname = urlparse(site_url).netloc.lower()
    domain_policies = policies.get("domains", {}) if isinstance(policies, dict) else {}
    policy = domain_policies.get(hostname, {}) if isinstance(domain_policies, dict) else {}

    return {
        "timeout_seconds": int(policy.get("timeout_seconds", settings.request_timeout_seconds)),
        "retries": int(policy.get("retries", settings.scrape_retries)),
        "playwright_wait_until": str(policy.get("playwright_wait_until", "networkidle")),
    }


def resolve_status(success_count: int, failure_count: int) -> str:
    """Resolve run status from success and failure counters."""

    if failure_count == 0:
        return "success"
    if success_count == 0:
        return "failed"
    return "partial_failed"


def profile_needs_refresh(
    site_profile: dict | None,
    revalidate_days: int,
    timeout_seconds: int,
) -> bool:
    """Detect whether cached discovery profile should be refreshed before running."""

    if not site_profile:
        return True
    selected_url = str(site_profile.get("selected_url", "")).strip()
    if not selected_url:
        return True
    if is_homepage(selected_url):
        return True
    if has_blacklisted_path_token(selected_url):
        return True
    discovered_at = site_profile.get("discovered_at")
    if discovered_at:
        try:
            discovered_dt = datetime.fromisoformat(str(discovered_at))
            if discovered_dt < datetime.now(tz=timezone.utc) - timedelta(days=revalidate_days):
                return True
        except Exception:
            return True
    if not link_is_reachable(selected_url, timeout_seconds=timeout_seconds):
        return True
    return False


def build_quality_report(
    run_id: str,
    sites: list[str],
    parsed_jobs_total: int,
    validated_jobs_total: int,
    rejected_jobs_total: int,
    dead_links_total: int,
    aggregated_jobs: list[dict],
    site_quality: list[dict],
    previous_success_run: str | None,
    previous_success_results_count: int,
    failure_categories: dict[str, int],
    previous_quality_report: dict | None,
    slo_drift_threshold_ratio: float,
) -> dict:
    """Build run-level quality metrics and simple regression signals."""

    validation_rate = (validated_jobs_total / parsed_jobs_total) if parsed_jobs_total else 0.0
    dead_link_rate = (dead_links_total / parsed_jobs_total) if parsed_jobs_total else 0.0
    unique_companies = len({str(job.get("company", "")).strip().lower() for job in aggregated_jobs if job.get("company")})
    parse_success_rate = (sum(1 for entry in site_quality if entry.get("parsed", 0) > 0) / len(site_quality)) if site_quality else 0.0
    jobs_per_site = (len(aggregated_jobs) / len(sites)) if sites else 0.0

    previous_validation_rate = float((previous_quality_report or {}).get("validation_rate", 0.0))
    previous_jobs_per_site = float((previous_quality_report or {}).get("jobs_per_site", 0.0))
    validation_rate_drop = max(previous_validation_rate - validation_rate, 0.0)
    jobs_per_site_drop = max(previous_jobs_per_site - jobs_per_site, 0.0)

    validation_rate_drift = (
        (validation_rate_drop / previous_validation_rate) >= slo_drift_threshold_ratio
        if previous_validation_rate > 0
        else False
    )
    jobs_per_site_drift = (
        (jobs_per_site_drop / previous_jobs_per_site) >= slo_drift_threshold_ratio
        if previous_jobs_per_site > 0
        else False
    )

    return {
        "run_id": run_id,
        "sites": len(sites),
        "parsed_jobs_total": parsed_jobs_total,
        "validated_jobs_total": validated_jobs_total,
        "rejected_jobs_total": rejected_jobs_total,
        "dead_links_total": dead_links_total,
        "validation_rate": round(validation_rate, 4),
        "dead_link_rate": round(dead_link_rate, 4),
        "parse_success_rate": round(parse_success_rate, 4),
        "jobs_per_site": round(jobs_per_site, 4),
        "delivered_jobs_total": len(aggregated_jobs),
        "unique_companies_total": unique_companies,
        "failure_categories": failure_categories,
        "site_quality": site_quality,
        "regression": {
            "previous_success_run": previous_success_run,
            "previous_success_results_count": previous_success_results_count,
            "results_count_delta": len(aggregated_jobs) - previous_success_results_count,
            "previous_validation_rate": previous_validation_rate,
            "previous_jobs_per_site": previous_jobs_per_site,
            "validation_rate_drop": round(validation_rate_drop, 4),
            "jobs_per_site_drop": round(jobs_per_site_drop, 4),
            "slo_drift_threshold_ratio": slo_drift_threshold_ratio,
            "validation_rate_drift": validation_rate_drift,
            "jobs_per_site_drift": jobs_per_site_drift,
            "slo_drift_detected": validation_rate_drift or jobs_per_site_drift,
        },
    }


def initialize_site_profiles(
    store: RunStore,
    sites: list[str],
    user_profile: str,
    llm_provider: str,
    llm_model: str,
    timeout_seconds: int,
    ollama_endpoint: str,
    openai_api_key: str | None,
    use_playwright_fallback: bool,
    refresh_discovery: bool,
) -> dict[str, dict]:
    """Discover and cache the best job URL and recipe for each homepage."""

    profiles = store.load_site_profiles()
    discoverer = SiteDiscovery(
        timeout_seconds=timeout_seconds,
        use_playwright_fallback=use_playwright_fallback,
    )

    for homepage in sites:
        if not refresh_discovery and homepage in profiles:
            log_progress(f"Discovery cache hit: {homepage}")
            continue
        try:
            log_progress(f"Discovering navigation for: {homepage}")
            discovery = discoverer.discover_with_guidance(
                homepage=homepage,
                llm_provider=llm_provider,
                llm_model=llm_model,
                user_profile=user_profile,
                timeout_seconds=timeout_seconds,
                ollama_endpoint=ollama_endpoint,
                openai_api_key=openai_api_key,
            )
            profiles[homepage] = discovery.to_dict()
            log_progress(
                f"Discovery selected URL for {homepage}: {profiles[homepage].get('selected_url', homepage)}"
            )
        except Exception as exc:
            log_progress(f"Discovery failed for {homepage}: {exc}")
            profiles[homepage] = {
                "homepage": homepage,
                "selected_url": homepage,
                "candidates": [],
                "selected_by": "fallback",
                "selection_reason": "Discovery failed, fallback to homepage",
                "interaction_recipe": {
                    "entry_url": homepage,
                    "strategy": "open_and_extract",
                    "steps": [{"action": "open", "target": homepage}],
                },
                "visited_pages": 0,
                "discovered_at": now_iso(),
                "error": str(exc),
            }

    store.save_site_profiles(profiles)
    return profiles


def execute(
    settings: Settings,
    run_id: str,
    site_filter: str | None,
    init_discovery: bool,
    refresh_discovery: bool,
) -> int:
    """Execute one full pipeline run, including optional discovery initialization."""

    store = RunStore(
        data_dir=settings.data_dir,
        manifest_path=settings.manifest_file,
        seen_jobs_path=settings.seen_jobs_file,
    )

    run_dir, steps_dir = store.initialize(run_id)
    log_progress(f"Run started: {run_id}")
    log_progress(f"Artifacts directory: {run_dir}")
    profile = load_profile(settings.user_info_dir)
    sites = filter_sites(load_sites(settings.user_info_dir), site_filter)
    log_progress(f"Loaded {len(sites)} site(s) for processing")

    manifest_before_run = store.load_manifest()
    previous_success_run = manifest_before_run.get("latest_success_run")

    site_profiles = store.load_site_profiles()
    missing_profiles = [site for site in sites if site not in site_profiles]
    stale_profiles = [
        site
        for site in sites
        if profile_needs_refresh(
            site_profile=site_profiles.get(site),
            revalidate_days=settings.profile_revalidate_days,
            timeout_seconds=settings.request_timeout_seconds,
        )
    ]

    if init_discovery or missing_profiles or stale_profiles:
        log_progress(
            "Initializing discovery profiles"
            if init_discovery or stale_profiles
            else "Initializing discovery for missing profiles"
        )
        initialize_site_profiles(
            store=store,
            sites=sites,
            user_profile=profile,
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            timeout_seconds=settings.request_timeout_seconds,
            ollama_endpoint=settings.ollama_endpoint,
            openai_api_key=settings.openai_api_key,
            use_playwright_fallback=settings.use_playwright_fallback,
            refresh_discovery=refresh_discovery,
        )
        site_profiles = store.load_site_profiles()
        log_progress("Discovery initialization completed")

    run_record = RunRecord(
        run_id=run_id,
        started_at=now_iso(),
        ended_at=None,
        status="running",
        run_mode=settings.run_mode,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        dry_run=settings.dry_run,
        total_sites=len(sites),
    )

    store.save_sources(run_dir, sites)

    scraper = JobScraper(
        timeout_seconds=settings.request_timeout_seconds,
        retries=settings.scrape_retries,
        retry_backoff_seconds=settings.retry_backoff_seconds,
        min_clean_text_length=settings.min_clean_text_length,
        use_playwright_fallback=settings.use_playwright_fallback,
    )
    processor = JobProcessor(
        provider=settings.llm_provider,
        model=settings.llm_model,
        timeout_seconds=settings.request_timeout_seconds,
        ollama_endpoint=settings.ollama_endpoint,
        openai_api_key=settings.openai_api_key,
    )
    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        timeout_seconds=settings.request_timeout_seconds,
    )

    seen_links = store.load_seen_links()
    aggregated_jobs: list[dict] = []
    site_policies = load_site_policies(settings.site_policies_file)
    parsed_jobs_total = 0
    validated_jobs_total = 0
    rejected_jobs_total = 0
    dead_links_total = 0
    site_quality: list[dict] = []

    for site_url in sites:
        target_url = site_profiles.get(site_url, {}).get("selected_url", site_url)
        policy = get_site_policy(site_url=site_url, policies=site_policies, settings=settings)
        log_progress(f"Processing site: {site_url}")
        if target_url != site_url:
            log_progress(f"Using discovered target URL: {target_url}")
        log_progress(
            f"Using policy timeout={policy['timeout_seconds']} retries={policy['retries']} wait_until={policy['playwright_wait_until']}"
        )
        step = SiteRunStep(site_url=site_url, status="running", started_at=now_iso())

        try:
            log_progress("Scraping started")
            scrape_result = scraper.fetch_content(
                url=target_url,
                retries_override=policy["retries"],
                timeout_seconds_override=policy["timeout_seconds"],
                playwright_wait_until=policy["playwright_wait_until"],
            )
            step.fetch_method = scrape_result.fetch_method
            step.attempts = scrape_result.attempts
            step.raw_html = scrape_result.raw_html
            step.cleaned_text = scrape_result.cleaned_text
            log_progress(
                f"Scraping completed via {step.fetch_method} with {step.attempts} attempt(s)"
            )
        except Exception as exc:
            step.status = "failed"
            step.failed_stage = "scrape"
            step.error_type = categorize_error(exc)
            step.error_message = str(exc)
            step.ended_at = now_iso()
            store.save_step(steps_dir, step)
            run_record.failure_count += 1
            run_record.failures.append(
                {
                    "site_url": site_url,
                    "failed_stage": "scrape",
                    "error_type": step.error_type,
                    "error_message": f"target_url={target_url}; {step.error_message}",
                }
            )
            run_record.sites_processed += 1
            log_progress(f"Scraping failed for {site_url}: {step.error_type} | {step.error_message}")
            continue

        try:
            log_progress("LLM processing started")
            process_result = processor.process_text(
                raw_text=step.cleaned_text or "",
                user_description=profile,
                source_url=target_url,
            )
            step.prompt = process_result.prompt
            step.llm_provider = settings.llm_provider
            step.llm_model = settings.llm_model
            step.llm_raw_output = process_result.raw_output
            step.parsed_jobs = process_result.jobs
            log_progress(f"LLM processing completed with {len(step.parsed_jobs)} parsed job(s)")
        except Exception as exc:
            step.status = "failed"
            step.failed_stage = "process"
            step.error_type = categorize_error(exc)
            step.error_message = str(exc)
            step.ended_at = now_iso()
            store.save_step(steps_dir, step)
            run_record.failure_count += 1
            run_record.failures.append(
                {
                    "site_url": site_url,
                    "failed_stage": "process",
                    "error_type": step.error_type,
                    "error_message": f"target_url={target_url}; {step.error_message}",
                }
            )
            run_record.sites_processed += 1
            log_progress(f"Processing failed for {site_url}: {step.error_type} | {step.error_message}")
            continue

        parsed_jobs_total += len(step.parsed_jobs)
        validated_jobs, review_jobs, rejected_jobs, verification_summary = verify_jobs(
            jobs=step.parsed_jobs,
            source_url=target_url,
            timeout_seconds=settings.request_timeout_seconds,
        )
        validated_jobs, profile_filtered = apply_profile_filters(validated_jobs, profile)
        step.validated_jobs = validated_jobs
        step.rejected_jobs = rejected_jobs + profile_filtered
        step.review_candidates = review_jobs
        step.verification_summary = verification_summary.to_dict()

        validated_jobs_total += len(validated_jobs)
        rejected_jobs_total += len(step.rejected_jobs)
        dead_links_total += len(
            [entry for entry in step.rejected_jobs if "link not reachable" in entry.get("reasons", [])]
        )

        site_quality.append(
            {
                "site_url": site_url,
                "target_url": target_url,
                "parsed": len(step.parsed_jobs),
                "validated": len(validated_jobs),
                "review": len(review_jobs),
                "rejected": len(step.rejected_jobs),
                "average_confidence": verification_summary.average_confidence,
            }
        )

        log_progress(
            f"Verification accepted {len(validated_jobs)}, review {len(review_jobs)}, rejected {len(step.rejected_jobs)} job(s)"
        )

        deduped_jobs, seen_links = dedupe_jobs(validated_jobs, seen_links)
        step.deduped_jobs = deduped_jobs
        step.status = "success"
        step.ended_at = now_iso()
        store.save_step(steps_dir, step)
        log_progress(f"Deduplication kept {len(deduped_jobs)} new job(s)")

        aggregated_jobs.extend(deduped_jobs)
        run_record.success_count += 1
        run_record.sites_processed += 1
        log_progress(f"Site completed: {site_url}")

    store.save_seen_links(seen_links)
    store.save_results(run_dir, aggregated_jobs)
    log_progress(f"Saved results with {len(aggregated_jobs)} total new job(s)")

    previous_success_results_count = 0
    previous_quality_report: dict | None = None
    if previous_success_run:
        previous_results_file = settings.data_dir / "runs" / previous_success_run / "results.json"
        previous_results_payload = store.read_json(previous_results_file, {"jobs": []})
        previous_jobs = previous_results_payload.get("jobs", []) if isinstance(previous_results_payload, dict) else []
        if isinstance(previous_jobs, list):
            previous_success_results_count = len(previous_jobs)
        previous_quality_file = settings.data_dir / "runs" / previous_success_run / "quality_report.json"
        previous_quality_payload = store.read_json(previous_quality_file, {})
        previous_quality_report = previous_quality_payload if isinstance(previous_quality_payload, dict) else None

    failure_categories: dict[str, int] = {}
    for failure in run_record.failures:
        key = str(failure.get("error_type", "unknown"))
        failure_categories[key] = failure_categories.get(key, 0) + 1

    quality_report = build_quality_report(
        run_id=run_id,
        sites=sites,
        parsed_jobs_total=parsed_jobs_total,
        validated_jobs_total=validated_jobs_total,
        rejected_jobs_total=rejected_jobs_total,
        dead_links_total=dead_links_total,
        aggregated_jobs=aggregated_jobs,
        site_quality=site_quality,
        previous_success_run=previous_success_run,
        previous_success_results_count=previous_success_results_count,
        failure_categories=failure_categories,
        previous_quality_report=previous_quality_report,
        slo_drift_threshold_ratio=settings.slo_drift_threshold_ratio,
    )
    store.save_quality_report(run_dir, quality_report)
    log_progress(
        "Quality report saved "
        f"(validation_rate={quality_report['validation_rate']}, dead_link_rate={quality_report['dead_link_rate']})"
    )

    if quality_report.get("regression", {}).get("slo_drift_detected"):
        log_progress("SLO drift detected against previous baseline")
        run_record.failures.append(
            {
                "site_url": "run",
                "failed_stage": "slo",
                "error_type": "slo drift",
                "error_message": (
                    f"validation_rate_drift={quality_report['regression']['validation_rate_drift']}; "
                    f"jobs_per_site_drift={quality_report['regression']['jobs_per_site_drift']}"
                ),
            }
        )
        run_record.failure_count += 1

    run_record.status = resolve_status(
        success_count=run_record.success_count,
        failure_count=run_record.failure_count,
    )
    run_record.failed_stage = "multiple" if run_record.failure_count else None

    if not settings.dry_run:
        if aggregated_jobs:
            try:
                log_progress("Sending Telegram job report")
                notifier.send_job_report(run_id=run_id, jobs=aggregated_jobs)
                log_progress("Telegram job report sent")
            except Exception as exc:
                run_record.failures.append(
                    {
                        "site_url": "notification",
                        "failed_stage": "notify",
                        "error_type": categorize_error(exc),
                        "error_message": str(exc),
                    }
                )
                run_record.failure_count += 1
                log_progress(f"Failed sending job report: {exc}")

        if run_record.failures:
            try:
                log_progress("Sending Telegram failure alert")
                notifier.send_failure_alert(run_id=run_id, failures=run_record.failures)
                log_progress("Telegram failure alert sent")
            except Exception as exc:
                run_record.failures.append(
                    {
                        "site_url": "notification",
                        "failed_stage": "notify",
                        "error_type": categorize_error(exc),
                        "error_message": str(exc),
                    }
                )
                run_record.failure_count += 1
                log_progress(f"Failed sending failure alert: {exc}")

        run_record.status = resolve_status(
            success_count=run_record.success_count,
            failure_count=run_record.failure_count,
        )
    else:
        log_progress("Dry-run mode enabled: Telegram notifications skipped")

    run_record.ended_at = now_iso()
    store.save_run_record(run_dir, run_record)
    store.update_manifest(run_id, run_record.status)
    store.enforce_retention(
        success_days=settings.retention_success_days,
        failed_days=settings.retention_failed_days,
    )
    log_progress(f"Run finished with status: {run_record.status}")

    return 1 if run_record.status == "failed" else 0


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for mode, filtering, and discovery options."""

    parser = argparse.ArgumentParser(description="Run the job monitoring pipeline")
    parser.add_argument("--local", action="store_true", help="Force local mode")
    parser.add_argument(
        "--mode",
        choices=["local", "github_actions"],
        default=None,
        help="Explicit run mode override",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip Telegram delivery")
    parser.add_argument("--run-id", default=None, help="Custom run id")
    parser.add_argument(
        "--site-filter",
        default=None,
        help="Only process websites containing this substring",
    )
    parser.add_argument(
        "--init-discovery",
        action="store_true",
        help="Run homepage discovery and save site profiles before processing",
    )
    parser.add_argument(
        "--refresh-discovery",
        action="store_true",
        help="Force discovery refresh even if cached profiles exist",
    )
    return parser.parse_args()


def main() -> int:
    """Program entrypoint that loads settings and executes a run."""

    args = parse_args()
    root_dir = Path(__file__).resolve().parent
    cli_mode = "local" if args.local else args.mode
    settings = load_settings(root_dir=root_dir, cli_mode=cli_mode, dry_run=args.dry_run)
    run_id = args.run_id or build_run_id()
    return execute(
        settings=settings,
        run_id=run_id,
        site_filter=args.site_filter,
        init_discovery=args.init_discovery,
        refresh_discovery=args.refresh_discovery,
    )


if __name__ == "__main__":
    raise SystemExit(main())
