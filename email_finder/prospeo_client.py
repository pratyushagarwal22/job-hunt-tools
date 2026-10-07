"""Prospeo enrich-person client. One request per call, no retries.

Errors carry only the HTTP status and Prospeo's error code: response bodies can hold
personal data and are never put in messages.
"""

import os
import re

import requests

_ENDPOINT = "https://api.prospeo.io/enrich-person"
_CODE = re.compile(r"[A-Z0-9_]{1,64}")


def _code(body) -> str:
    code = body.get("error_code") if isinstance(body, dict) else None
    return f" {code}" if isinstance(code, str) and _CODE.fullmatch(code) else ""


def enrich_person(linkedin_url: str, timeout: float) -> dict | None:
    """Match as {name, company, title, email, email_status}, or None for a Prospeo no-match."""
    api_key = os.environ.get("PROSPEO_API_KEY")
    if not api_key:
        raise RuntimeError("PROSPEO_API_KEY is not set")

    try:
        response = requests.post(
            _ENDPOINT,
            headers={"X-KEY": api_key, "Content-Type": "application/json"},
            json={"data": {"linkedin_url": linkedin_url}},
            timeout=timeout,
        )
    except requests.RequestException as err:
        raise RuntimeError(f"Prospeo request failed: {type(err).__name__}") from None

    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict) and body.get("error") is True and body.get("error_code") == "NO_MATCH":
        return None
    if not response.ok:
        raise RuntimeError(f"Prospeo enrich-person failed: HTTP {response.status_code}{_code(body)}")
    if not isinstance(body, dict):
        raise RuntimeError(f"Prospeo enrich-person failed: HTTP {response.status_code} malformed body")
    if body.get("error") is not False:
        raise RuntimeError(f"Prospeo enrich-person failed: HTTP {response.status_code}{_code(body)}")

    person = body.get("person")
    if not isinstance(person, dict):
        raise RuntimeError(f"Prospeo enrich-person failed: HTTP {response.status_code} malformed body")
    email_info = person.get("email")
    if email_info is None:
        return None
    if not isinstance(email_info, dict):
        raise RuntimeError(f"Prospeo enrich-person failed: HTTP {response.status_code} malformed body")

    email = (email_info.get("email") or "").strip()
    if not email or email_info.get("revealed") is False or "*" in email:
        return None

    company = body.get("company") if isinstance(body.get("company"), dict) else {}
    return {
        "name": person.get("full_name") or "",
        "company": company.get("name") or "",
        "title": person.get("current_job_title") or "",
        "email": email,
        "email_status": (email_info.get("status") or "").lower(),
    }
