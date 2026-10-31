"""Greenhouse, Lever and Ashby public job board API clients. One GET per board.

A failed request, non-2xx status or non-JSON body raises AtsError; the caller decides
whether to skip the company.
"""

import html
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from job_scanner import date_utils

_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}"
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def list_greenhouse_jobs(
    slug: str, company: str, lookback_days: int, timeout: float = 15
) -> list[dict]:
    body = _get_json(_GREENHOUSE_URL.format(slug=slug), "greenhouse", slug, timeout)

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

    jobs = []
    for item in body.get("jobs", []):
        jobs.append(
            dict(
                title=item.get("title"),
                company=company,
                location=item.get("location"),
                url=item.get("jobUrl"),
                posted_date=item.get("publishedAt"),
                jd_text=_ashby_jd_text(item),
                source="ashby",
            )
        )
    return _within_lookback(jobs, lookback_days)


def get_ashby_board(slug: str, timeout: float = 15) -> list[dict]:
    """Every posting on one Ashby board (no lookback filter), as dicts of id, title, url, jd_text, location."""
    body = _get_json(_ASHBY_URL.format(slug=slug), "ashby", slug, timeout)
    return [
        dict(
            id=item.get("id") or "",
            title=item.get("title"),
            url=item.get("jobUrl"),
            jd_text=_ashby_jd_text(item),
            location=item.get("location"),
        )
        for item in body.get("jobs", [])
    ]


def get_greenhouse_job(slug: str, job_id, timeout: float = 15) -> dict:
    """One Greenhouse posting: jd_text and location. Errors raise AtsError."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
    item = _get_json(url, "greenhouse", slug, timeout)
    return dict(
        jd_text=_html_to_text(html.unescape(item.get("content") or "")),
        location=(item.get("location") or {}).get("name"),
    )


def get_lever_job(slug: str, posting_id, timeout: float = 15) -> dict:
    """One Lever posting: jd_text and location. Errors raise AtsError."""
    url = f"https://api.lever.co/v0/postings/{slug}/{posting_id}"
    item = _get_json(url, "lever", slug, timeout)
    return dict(
        jd_text=_lever_jd_text(item),
        location=(item.get("categories") or {}).get("location"),
    )


def _ashby_jd_text(item: dict) -> str:
    return (item.get("descriptionPlain") or "").strip() or _html_to_text(
        item.get("descriptionHtml") or ""
    )


class AtsError(Exception):
    """One company's board could not be listed. `detail` is e.g. "HTTP 404" or "ConnectTimeout"."""

    def __init__(self, ats: str, slug: str, detail: str):
        super().__init__(f"{ats} slug {slug!r}: {detail}")
        self.detail = detail


def _get_json(url: str, ats: str, slug: str, timeout: float):
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise AtsError(ats, slug, type(exc).__name__) from exc
    if not response.ok:
        raise AtsError(ats, slug, f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise AtsError(ats, slug, "invalid JSON") from None


def _html_to_text(markup: str) -> str:
    return BeautifulSoup(markup, "html.parser").get_text("\n", strip=True)


def _lever_jd_text(item: dict) -> str:
    parts = [item.get("descriptionPlain") or ""]
    for section in item.get("lists") or []:
        parts.append(section.get("text") or "")
        parts.append(_html_to_text(section.get("content") or ""))
    parts.append(item.get("additionalPlain") or "")
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _within_lookback(jobs: list[dict], lookback_days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    kept = []
    for job in jobs:
        posted = date_utils.parse_ats_date(job["posted_date"], "UTC")
        if posted is not None and posted >= cutoff:
            kept.append(job)
    return kept
