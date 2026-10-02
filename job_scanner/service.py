"""job_scanner pipeline: search + ATS -> dedup -> filter -> sort/cap -> score -> append."""

import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import requests
import yaml

from job_scanner import (
    ats_client,
    date_utils,
    experience_filter,
    jd_client,
    scoring_client,
    search_client,
    sheets_repo,
)
from job_scanner.location_filter import passes_location_filter
from job_scanner.title_filter import passes_title_filter
from job_scanner.url_utils import normalize_url

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).parent
_CONFIG_DIR = _PACKAGE_DIR.parent / "config"

_ATS_LISTERS = {
    "greenhouse": "list_greenhouse_jobs",
    "lever": "list_lever_jobs",
    "ashby": "list_ashby_jobs",
}


def _load_config() -> dict:
    def load_yaml(path: Path) -> dict:
        return yaml.safe_load(path.read_text()) or {}

    queries = load_yaml(_PACKAGE_DIR / "queries.yaml")
    return dict(
        settings=load_yaml(_PACKAGE_DIR / "settings.yaml"),
        queries=queries.get("queries") or [],
        company_sites=queries.get("company_sites") or [],
        companies=load_yaml(_PACKAGE_DIR / "companies.yaml").get("companies") or [],
        candidate_md=(_CONFIG_DIR / "candidate.md").read_text(),
        profile_yaml=(_CONFIG_DIR / "profile.yaml").read_text(),
    )


def run_pipeline(max_jobs: int | None) -> dict:
    config = _load_config()
    settings = config["settings"]
    tz_name = settings["timezone"]
    run_start = datetime.now(ZoneInfo(tz_name))
    sheet_id = os.environ["SHEET_ID"]

    existing_urls = sheets_repo.get_existing_urls(sheet_id)

    ats_jobs = _collect_ats_jobs(config["companies"], settings)
    search_jobs = _collect_search_jobs(config["queries"] + config["company_sites"], settings)
    candidates = ats_jobs + search_jobs
    found = len(candidates)

    new_jobs = _dedup(candidates, existing_urls)
    kept = [job for job in new_jobs if _passes_filters(job, settings)]
    filtered = len(new_jobs) - len(kept)

    for job in kept:
        job["posted"] = _parse_posted(job, run_start, tz_name)
    ordered = _newest_first(kept)

    cap = max_jobs if max_jobs is not None else settings["max_jobs_per_run"]
    to_score = ordered[:cap]
    skipped_cap = len(ordered) - len(to_score)
    logger.info(
        "found %d, new %d, filtered %d, to score %d, over cap %d",
        found, len(new_jobs), filtered, len(to_score), skipped_cap,
    )

    date_found = date_utils.format_date_found(run_start, tz_name)
    max_years = settings["experience_filter"]["max_years_required"]
    rows = []
    failed = 0
    for job in to_score:
        try:
            jd_text, jd_source = _get_jd(job, settings)
            years, phrase = experience_filter.required_years(jd_text)
            if years is not None and years > max_years:
                filtered += 1
                logger.info("DROP experience %d | %s | %s | %s", years, phrase, job["title"], job["url"])
                continue
            rows.append(_score_to_row(job, jd_text, jd_source, config, settings, date_found))
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise
        except Exception as exc:
            failed += 1
            logger.warning("Scoring failed for %s: %s: %s", job["url"], type(exc).__name__, exc)

    if not rows and failed:
        raise RuntimeError(f"Every scoring attempt failed ({failed} jobs); nothing written")
    if rows:
        sheets_repo.append_jobs(sheet_id, rows)
        logger.info("Appended %d rows", len(rows))

    return dict(
        found=found,
        new=len(new_jobs),
        filtered=filtered,
        scored=len(rows),
        failed=failed,
        skipped_cap=skipped_cap,
    )


def _collect_search_jobs(queries: list[dict], settings: dict) -> list[dict]:
    search_cfg = settings["search"]
    jobs = []
    attempted = 0
    succeeded = 0
    for entry in queries:
        if not entry.get("enabled", True):
            continue
        query = entry["query"]
        attempted += 1
        any_page_ok = False
        for page in range(1, settings["max_pages_per_query"] + 1):
            try:
                results = search_client.search(
                    query=query,
                    tbs=search_cfg["tbs"],
                    gl=search_cfg["gl"],
                    num=search_cfg["num"],
                    page=page,
                    timeout=settings["request_timeout_seconds"],
                )
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                logger.warning("Search %r page %d failed: %s", entry.get("name"), page, exc)
                break
            any_page_ok = True
            if not results:
                break
            for result in results:
                jobs.append(
                    dict(
                        is_ats=False,
                        title=result.get("title") or "",
                        company="",
                        location=None,
                        snippet=result.get("snippet") or "",
                        url=result.get("link") or "",
                        raw_date=result.get("date") or "",
                        jd_text=None,
                        source_query=query,
                    )
                )
        succeeded += any_page_ok
    if attempted and not succeeded:
        raise RuntimeError(f"Every search query failed ({attempted} queries)")
    logger.info("Search: %d results from %d/%d queries", len(jobs), succeeded, attempted)
    return jobs


def _collect_ats_jobs(companies: list[dict], settings: dict) -> list[dict]:
    jobs = []
    for company in companies:
        ats = company.get("ats")
        lister_name = _ATS_LISTERS.get(ats)
        if lister_name is None:
            logger.warning("Company %r: unknown ats %r, skipped", company.get("name"), ats)
            continue
        listed = getattr(ats_client, lister_name)(
            company["slug"],
            company["name"],
            lookback_days=settings["ats_lookback_days"],
            timeout=settings["request_timeout_seconds"],
        )
        for item in listed:
            jobs.append(
                dict(
                    is_ats=True,
                    title=item.get("title") or "",
                    company=item.get("company") or company["name"],
                    location=item.get("location") or "",
                    snippet="",
                    url=item.get("url") or "",
                    raw_date=item.get("posted_date"),
                    jd_text=item.get("jd_text") or "",
                    source_query=f"{ats}:{company['name']}",
                )
            )
    logger.info("ATS: %d jobs from %d companies", len(jobs), len(companies))
    return jobs


def _dedup(jobs: list[dict], existing_urls: set[str]) -> list[dict]:
    seen = set(existing_urls)
    unique = []
    for job in jobs:
        if not job["url"]:
            continue
        url = normalize_url(job["url"])
        if url in seen:
            continue
        seen.add(url)
        unique.append(dict(job, url=url, raw_url=job["url"]))
    return unique


def _passes_filters(job: dict, settings: dict) -> bool:
    title_cfg = settings["title_filter"]
    if not passes_title_filter(job["title"], title_cfg["include"], title_cfg["exclude"]):
        return False
    return passes_location_filter(
        job["location"],
        job["title"],
        job["snippet"],
        # Raw URL: normalization strips params like ?location= that the filter needs.
        job["raw_url"],
        settings["location_filter"]["exclude"],
        is_ats=job["is_ats"],
    )


def _parse_posted(job: dict, run_start: datetime, tz_name: str) -> datetime | None:
    if job["is_ats"]:
        return date_utils.parse_ats_date(job["raw_date"], tz_name)
    return date_utils.parse_relative_date(job["raw_date"], run_start)


def _newest_first(jobs: list[dict]) -> list[dict]:
    dated = [job for job in jobs if job["posted"] is not None]
    undated = [job for job in jobs if job["posted"] is None]
    return sorted(dated, key=lambda job: job["posted"], reverse=True) + undated


def _get_jd(job: dict, settings: dict) -> tuple[str, str]:
    """Full JD when available, else title + snippet (search) or title + location (ATS)."""
    if job["is_ats"]:
        if job["jd_text"]:
            return job["jd_text"], "full"
        return f"{job['title']}\n{job['location']}", "snippet"
    jd_text, jd_source = jd_client.fetch_jd(
        job["url"], settings["request_timeout_seconds"], settings["jd_min_chars"]
    )
    if jd_source != "full" or not jd_text:
        return f"{job['title']}\n{job['snippet']}", "snippet"
    return jd_text, jd_source


def _score_to_row(
    job: dict, jd_text: str, jd_source: str, config: dict, settings: dict, date_found: str
) -> list:
    scoring = settings["scoring"]
    hints = {}
    if job["is_ats"]:
        hints = dict(title_hint=job["title"], company_hint=job["company"])

    result = scoring_client.score_job(
        jd_text=jd_text,
        jd_source=jd_source,
        candidate_md=config["candidate_md"],
        profile_yaml=config["profile_yaml"],
        model=scoring["model"],
        max_tokens=scoring["max_tokens"],
        max_jd_chars=scoring["max_jd_chars"],
        force_tool_choice=scoring["force_tool_choice"],
        **hints,
    )

    if job["is_ats"]:
        title, company = job["title"], job["company"]
        location = job["location"] or result.location
    else:
        title, company, location = result.title, result.company, result.location

    posted = job["posted"]
    return [
        "",
        result.match_score,
        jd_source,
        title,
        company,
        location,
        result.visa_mention,
        result.core_requirements,
        result.reason,
        job["url"],
        date_utils.format_posted_date(posted, settings["timezone"]) if posted else "",
        date_found,
        job["source_query"],
    ]
