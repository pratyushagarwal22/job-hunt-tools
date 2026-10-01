"""Greenhouse, Lever and Ashby public job board API clients. One GET per company."""

import html
import logging
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}"
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def list_greenhouse_jobs(
    slug: str, company: str, lookback_days: int, timeout: float = 15
) -> list[dict]:
    body = _get_json(_GREENHOUSE_URL.format(slug=slug), "greenhouse", slug, timeout)
    if body is None:
        return []

    jobs = []
    for item in body.get("jobs", []):
        jobs.append(
            dict(
                title=item.get("title"),
                company=company,
                location=(item.get("location") or {}).get("name"),
                url=item.get("absolute_url"),
                posted_date=item.get("first_published") or item.get("updated_at"),
                jd_text=_html_to_text(html.unescape(item.get("content") or "")),
                source="greenhouse",
            )
        )
    return _within_lookback(jobs, lookback_days)


def list_lever_jobs(
    slug: str, company: str, lookback_days: int, timeout: float = 15
) -> list[dict]:
    body = _get_json(_LEVER_URL.format(slug=slug), "lever", slug, timeout)
    if body is None:
        return []

    jobs = []
    for item in body:
        jobs.append(
            dict(
                title=item.get("text"),
                company=company,
                location=(item.get("categories") or {}).get("location"),
                url=item.get("hostedUrl"),
                posted_date=item.get("createdAt"),
                jd_text=_lever_jd_text(item),
                source="lever",
            )
        )
    return _within_lookback(jobs, lookback_days)


def list_ashby_jobs(
    slug: str, company: str, lookback_days: int, timeout: float = 15
) -> list[dict]:
    body = _get_json(_ASHBY_URL.format(slug=slug), "ashby", slug, timeout)
    if body is None:
        return []

    jobs = []
    for item in body.get("jobs", []):
        jobs.append(
            dict(
                title=item.get("title"),
                company=company,
                location=item.get("location"),
                url=item.get("jobUrl"),
                posted_date=item.get("publishedAt"),
                jd_text=(item.get("descriptionPlain") or "").strip()
                or _html_to_text(item.get("descriptionHtml") or ""),
                source="ashby",
            )
        )
    return _within_lookback(jobs, lookback_days)


def _get_json(url: str, ats: str, slug: str, timeout: float):
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("%s slug %r: request failed: %s", ats, slug, exc)
        return None
    if not response.ok:
        logger.warning("%s slug %r: HTTP %s", ats, slug, response.status_code)
        return None
    try:
        return response.json()
    except ValueError:
        logger.warning("%s slug %r: response is not JSON", ats, slug)
        return None


def _html_to_text(markup: str) -> str:
    return BeautifulSoup(markup, "html.parser").get_text("\n", strip=True)


def _lever_jd_text(item: dict) -> str:
    parts = [item.get("descriptionPlain") or ""]
    for section in item.get("lists") or []:
        parts.append(section.get("text") or "")
        parts.append(_html_to_text(section.get("content") or ""))
    parts.append(item.get("additionalPlain") or "")
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _parse_posted(value) -> datetime | None:
    # Temporary: Task 4's date_utils replaces this.
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _within_lookback(jobs: list[dict], lookback_days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    kept = []
    for job in jobs:
        posted = _parse_posted(job["posted_date"])
        if posted is not None and posted >= cutoff:
            kept.append(job)
    return kept
