"""Greenhouse, Lever, Ashby, and Workday public job APIs. One GET per board or job.

A failed request, non-2xx status or non-JSON body raises AtsError; the caller decides
whether to skip the company.
"""

import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from job_scanner import date_utils

_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}"
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_WORKDAY_HOST = ".myworkdayjobs.com"
_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.I)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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


def workday_api_url(url: str) -> str | None:
    """cxs detail URL for a Workday job page, or None."""
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    if not host.endswith(_WORKDAY_HOST):
        return None
    tenant = host.split(".", 1)[0]
    if not tenant or host == _WORKDAY_HOST.lstrip("."):
        return None
    segments = [seg for seg in parts.path.split("/") if seg]
    lowered = [seg.lower() for seg in segments]
    if "apply" in lowered:
        segments = segments[: lowered.index("apply")]
    if segments and _LOCALE.fullmatch(segments[0]):
        segments = segments[1:]
    site, rest = _workday_job_path(segments)
    if site is None:
        return None
    return f"https://{host}/wday/cxs/{tenant}/{site}/job/{rest}"


def get_workday_job(url: str, timeout: float) -> dict:
    """One Workday posting. Errors raise AtsError (status or error type only)."""
    api = workday_api_url(url)
    if api is None:
        raise AtsError("workday", "", "not a Workday job URL")
    try:
        response = requests.get(
            api,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AtsError("workday", "", type(exc).__name__) from exc
    if not response.ok:
        raise AtsError("workday", "", f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise AtsError("workday", "", "invalid JSON") from None
    info = body.get("jobPostingInfo") if isinstance(body, dict) else None
    if not isinstance(info, dict):
        raise AtsError("workday", "", "missing jobPostingInfo")
    country = info.get("country")
    descriptor = country.get("descriptor") if isinstance(country, dict) else None
    extras = info.get("additionalLocations")
    return dict(
        title=info.get("title") or "",
        jd_text=_html_to_text(info.get("jobDescription") or ""),
        location=info.get("location") or "",
        additional_locations=[str(item) for item in extras] if isinstance(extras, list) else [],
        country=descriptor or None,
        start_date=info.get("startDate") or None,
    )


def _workday_job_path(segments: list[str]) -> tuple[str | None, str | None]:
    if len(segments) >= 3 and segments[1].lower() == "job":
        return segments[0], "/".join(segments[2:])
    if len(segments) >= 4 and [s.lower() for s in segments[1:3]] == ["jobs", "details"]:
        return segments[0], "/".join(segments[3:])
    return None, None


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
