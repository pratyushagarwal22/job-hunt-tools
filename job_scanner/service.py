"""job_scanner pipeline: search + ATS -> dedup -> filter -> sort/cap -> score -> append."""

import logging
import os
import re
import time
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
from job_scanner.location_filter import location_drop_reason
from job_scanner.title_filter import title_drop_reason
from job_scanner.url_utils import normalize_url

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).parent
_CONFIG_DIR = _PACKAGE_DIR.parent / "config"

_ATS_LISTERS = {
    "greenhouse": "list_greenhouse_jobs",
    "lever": "list_lever_jobs",
    "ashby": "list_ashby_jobs",
}

_FAIL_MESSAGE_CHARS = 150

_SCORE_BUCKETS = [("8-10", 8.0), ("6-7.9", 6.0), ("3-5.9", 3.0), ("1-2.9", float("-inf"))]


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
    started = time.monotonic()
    config = _load_config()
    settings = config["settings"]
    tz_name = settings["timezone"]
    run_start = datetime.now(ZoneInfo(tz_name))
    sheet_id = os.environ["SHEET_ID"]
    cap = max_jobs if max_jobs is not None else settings["max_jobs_per_run"]
    model = settings["scoring"]["model"]

    existing_urls = sheets_repo.get_existing_urls(sheet_id)
    logger.info(
        "RUN start %s | cap %d | ats_lookback_days %d | model %s | existing URLs %d",
        run_start.strftime("%Y-%m-%d %H:%M %Z"), cap, settings["ats_lookback_days"], model,
        len(existing_urls),
    )

    ats_jobs = _collect_ats_jobs(config["companies"], settings)
    search_jobs, query_stats, serper_calls = _collect_search_jobs(
        config["queries"] + config["company_sites"], settings
    )
    candidates = ats_jobs + search_jobs

    new_jobs, dedup_counts = _dedup(candidates, existing_urls)
    funnel = dict(found=len(candidates), **dedup_counts, new=len(new_jobs),
                  title_dropped=0, location_dropped=0)
    kept = []
    for job in new_jobs:
        stage = _drop_stage(job, settings)
        job["passed"] = stage is None
        if stage is None:
            kept.append(job)
        else:
            funnel[f"{stage}_dropped"] += 1
    _log_queries(query_stats, new_jobs, serper_calls)

    for job in kept:
        job["posted"] = _parse_posted(job, run_start, tz_name)
    ordered = _newest_first(kept)
    to_score = ordered[:cap]
    over_cap = ordered[cap:]
    for job in over_cap:
        logger.info("DROP cap | %s | %s", _log_title(job), job["url"])

    date_found = date_utils.format_date_found(run_start, tz_name)
    max_years = settings["experience_filter"]["max_years_required"]
    rows = []
    scores = []
    usages = []
    jd_counts = dict(full_fetched=0, snippet=0, ats_full=0)
    experience_dropped = 0
    failed = 0
    for job in to_score:
        try:
            jd_text, jd_source = _get_jd(job, settings)
            jd_counts[_jd_kind(job, jd_source)] += 1
            years, phrase = experience_filter.required_years(jd_text)
            if years is not None and years > max_years:
                experience_dropped += 1
                logger.info("DROP experience %d | %s | %s | %s", years, phrase, _log_title(job), job["url"])
                continue
            row, result, usage = _score_to_row(job, jd_text, jd_source, config, settings, date_found)
            rows.append(row)
            scores.append(result.match_score)
            usages.append(usage)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise
        except Exception as exc:
            failed += 1
            logger.warning("FAIL job %s | %s: %s", job["url"], type(exc).__name__, _short_message(exc))

    funnel.update(
        eligible=len(ordered),
        over_cap=len(over_cap),
        experience_dropped=experience_dropped,
        scored=len(rows),
        failed=failed,
    )
    histogram = _score_histogram(scores)
    total_usage, cost = _total_cost(usages, settings, model)
    _log_report(funnel, jd_counts, histogram, total_usage, cost, model)
    _write_step_summary(funnel, query_stats, histogram, total_usage, cost, model)

    if not rows and failed:
        raise RuntimeError(f"Every scoring attempt failed ({failed} jobs); nothing written")
    if rows:
        sheets_repo.append_jobs(sheet_id, rows)
        logger.info("Appended %d rows", len(rows))
    logger.info("RUN end | elapsed %.1fs", time.monotonic() - started)

    return dict(
        found=funnel["found"],
        new=funnel["new"],
        filtered=funnel["title_dropped"] + funnel["location_dropped"] + experience_dropped,
        scored=len(rows),
        failed=failed,
        skipped_cap=len(over_cap),
    )


def _collect_search_jobs(queries: list[dict], settings: dict) -> tuple[list[dict], list[dict], int]:
    """Returns (jobs, per-query stats, Serper requests sent)."""
    search_cfg = settings["search"]
    jobs = []
    stats = []
    calls = 0
    for entry in queries:
        if not entry.get("enabled", True):
            continue
        query = entry["query"]
        stat = dict(name=entry.get("name") or query, pages=0, results=0)
        index = len(stats)
        stats.append(stat)
        for page in range(1, settings["max_pages_per_query"] + 1):
            calls += 1
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
                logger.warning("FAIL search %s page %d | %s", stat["name"], page, _error_detail(exc))
                break
            stat["pages"] += 1
            if not results:
                break
            stat["results"] += len(results)
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
                        query_index=index,
                        page=page,
                    )
                )
    if stats and not any(stat["pages"] for stat in stats):
        raise RuntimeError(f"Every search query failed ({len(stats)} queries)")
    return jobs, stats, calls


def _collect_ats_jobs(companies: list[dict], settings: dict) -> list[dict]:
    jobs = []
    empty = []
    errors = []
    for company in companies:
        ats = company.get("ats")
        lister_name = _ATS_LISTERS.get(ats)
        if lister_name is None:
            errors.append(f"{ats}:{company.get('slug')} (unknown ats)")
            continue
        try:
            listed = getattr(ats_client, lister_name)(
                company["slug"],
                company["name"],
                lookback_days=settings["ats_lookback_days"],
                timeout=settings["request_timeout_seconds"],
            )
        except ats_client.AtsError as exc:
            errors.append(f"{ats}:{company['slug']} ({exc.detail})")
            continue
        if not listed:
            empty.append(company["name"])
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
    logger.info(
        "ATS polled %d companies | %d jobs within %d-day lookback",
        len(companies), len(jobs), settings["ats_lookback_days"],
    )
    logger.info("ATS 0 jobs (%d): %s", len(empty), ", ".join(empty) or "none")
    logger.info("ATS errors (%d): %s", len(errors), ", ".join(errors) or "none")
    return jobs


def _dedup(jobs: list[dict], existing_urls: set[str]) -> tuple[list[dict], dict]:
    seen = set()
    counts = dict(dupes_sheet=0, dupes_run=0, no_url=0)
    unique = []
    for job in jobs:
        if not job["url"]:
            counts["no_url"] += 1
            continue
        url = normalize_url(job["url"])
        if url in existing_urls:
            counts["dupes_sheet"] += 1
            continue
        if url in seen:
            counts["dupes_run"] += 1
            continue
        seen.add(url)
        unique.append(dict(job, url=url, raw_url=job["url"]))
    return unique, counts


def _drop_stage(job: dict, settings: dict) -> str | None:
    """None if the job passes the title and location filters; else "title" or "location"."""
    title_cfg = settings["title_filter"]
    reason = title_drop_reason(
        job["title"], title_cfg["include"], title_cfg["exclude"],
        title_cfg.get("always_allow") or [],
    )
    if reason is not None:
        logger.info("DROP title %s | %s | %s", reason, _log_title(job), job["url"])
        return "title"
    location_cfg = settings["location_filter"]
    location = location_drop_reason(
        job["location"],
        job["title"],
        # Raw URL: normalization strips params like ?location= that the filter needs.
        job["raw_url"],
        location_cfg["exclude"],
        is_ats=job["is_ats"],
        us_signals=location_cfg.get("us_signals") or [],
    )
    if location is not None:
        keyword, where = location
        logger.info("DROP location %s (%s) | %s | %s", keyword, where, _log_title(job), job["url"])
        return "location"
    return None


def _log_title(job: dict) -> str:
    """Title for DROP lines; "|" would break the " | " field separators."""
    return job["title"].replace("|", "/")


def _log_queries(stats: list[dict], new_jobs: list[dict], serper_calls: int) -> None:
    for stat in stats:
        stat.update(new=0, passed=0, late=0)
    for job in new_jobs:
        if job["is_ats"]:
            continue
        stat = stats[job["query_index"]]
        stat["new"] += 1
        if job["passed"]:
            stat["passed"] += 1
            if job["page"] >= 2:
                stat["late"] += 1
    for stat in stats:
        logger.info(
            "QUERY %s | pages %d | results %d | new %d | passed %d | new+passed from page 2+ %d",
            stat["name"], stat["pages"], stat["results"], stat["new"], stat["passed"], stat["late"],
        )
    logger.info("QUERY total Serper calls %d", serper_calls)


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


def _jd_kind(job: dict, jd_source: str) -> str:
    if jd_source != "full":
        return "snippet"
    return "ats_full" if job["is_ats"] else "full_fetched"


def _score_to_row(
    job: dict, jd_text: str, jd_source: str, config: dict, settings: dict, date_found: str
) -> tuple[list, scoring_client.ScoreResult, scoring_client.Usage]:
    scoring = settings["scoring"]
    hints = {}
    if job["is_ats"]:
        hints = dict(title_hint=job["title"], company_hint=job["company"])

    result, usage = scoring_client.score_job(
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
    row = [
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
    return row, result, usage


def _error_detail(exc: Exception) -> str:
    match = re.search(r"HTTP \d{3}", str(exc))
    return match.group(0) if match else type(exc).__name__


def _short_message(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    if len(message) > _FAIL_MESSAGE_CHARS:
        return message[: _FAIL_MESSAGE_CHARS - 3] + "..."
    return message


def _score_histogram(scores: list[float]) -> dict[str, int]:
    histogram = {label: 0 for label, _ in _SCORE_BUCKETS}
    for score in scores:
        label = next(label for label, floor in _SCORE_BUCKETS if score >= floor)
        histogram[label] += 1
    return histogram


def _total_cost(
    usages: list[scoring_client.Usage], settings: dict, model: str
) -> tuple[scoring_client.Usage, float | None]:
    """Summed usage and estimated USD cost; cost is None when the model has no valid price."""
    total = scoring_client.Usage(
        input=sum(u.input for u in usages),
        output=sum(u.output for u in usages),
        cache_read=sum(u.cache_read for u in usages),
        cache_write=sum(u.cache_write for u in usages),
    )
    prices = (settings.get("prices") or {}).get(model)
    try:
        cost = (
            total.input * float(prices["input"])
            + total.output * float(prices["output"])
            + total.cache_read * float(prices["cache_read"])
            + total.cache_write * float(prices["cache_write"])
        ) / 1_000_000
    except (KeyError, TypeError, ValueError):
        cost = None
    return total, cost


def _cost_text(cost: float | None, model: str) -> str:
    return f"est ${cost:.4f} ({model})" if cost is not None else f"cost unknown ({model})"


def _log_report(
    funnel: dict, jd_counts: dict, histogram: dict, usage: scoring_client.Usage,
    cost: float | None, model: str,
) -> None:
    logger.info(
        "FUNNEL found %d = dupes (sheet) %d + dupes (in-run) %d + no URL %d + new %d"
        " | new = title dropped %d + location dropped %d + eligible %d"
        " | eligible = over cap %d + experience dropped %d + scored %d + failed %d",
        funnel["found"], funnel["dupes_sheet"], funnel["dupes_run"], funnel["no_url"],
        funnel["new"], funnel["title_dropped"], funnel["location_dropped"], funnel["eligible"],
        funnel["over_cap"], funnel["experience_dropped"], funnel["scored"], funnel["failed"],
    )
    logger.info(
        "JD full fetched %d | snippet fallback %d | ATS full %d",
        jd_counts["full_fetched"], jd_counts["snippet"], jd_counts["ats_full"],
    )
    logger.info("SCORES %s", " | ".join(f"{label}: {n}" for label, n in histogram.items()))
    logger.info(
        "COST input %d | output %d | cache read %d | cache write %d | %s",
        usage.input, usage.output, usage.cache_read, usage.cache_write, _cost_text(cost, model),
    )


def _write_step_summary(
    funnel: dict, query_stats: list[dict], histogram: dict, usage: scoring_client.Usage,
    cost: float | None, model: str,
) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return

    def cell(value) -> str:
        return str(value).replace("|", "\\|")

    funnel_labels = [
        ("found", "Found"), ("dupes_sheet", "Dupes (sheet)"), ("dupes_run", "Dupes (in-run)"),
        ("no_url", "No URL"), ("new", "New"), ("title_dropped", "Title dropped"),
        ("location_dropped", "Location dropped"), ("eligible", "Eligible"),
        ("over_cap", "Over cap"), ("experience_dropped", "Experience dropped"),
        ("scored", "Scored"), ("failed", "Failed"),
    ]
    lines = ["## Job Scanner run", "", "| Stage | Count |", "|---|---:|"]
    lines += [f"| {label} | {funnel[key]} |" for key, label in funnel_labels]
    lines += ["", "### Queries", "",
              "| Query | Pages | Results | New | Passed | Page 2+ |", "|---|---:|---:|---:|---:|---:|"]
    lines += [
        f"| {cell(s['name'])} | {s['pages']} | {s['results']} | {s['new']} | {s['passed']} "
        f"| {s['late']} |"
        for s in query_stats
    ]
    lines += ["", "### Scores", "", "| " + " | ".join(histogram) + " |",
              "|" + "---:|" * len(histogram), "| " + " | ".join(map(str, histogram.values())) + " |"]
    lines += ["", "### Cost", "",
              f"Input {usage.input}, output {usage.output}, cache read {usage.cache_read}, "
              f"cache write {usage.cache_write} tokens. {_cost_text(cost, model)}.", ""]
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        logger.warning("Could not write GITHUB_STEP_SUMMARY: %s", type(exc).__name__)
