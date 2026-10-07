"""Apollo people/bulk_match client (disabled provider; the free plan returns 403).

One request per call, no retries. Errors carry only the HTTP status and Apollo's error
code: response bodies can hold personal data and are never put in messages.
"""

import os
import re

import requests

from shared.linkedin_url import normalize_linkedin_url

_ENDPOINT = "https://api.apollo.io/api/v1/people/bulk_match"
_CODE = re.compile(r"[A-Z0-9_]{1,64}")


def _code(body) -> str:
    code = body.get("error_code") if isinstance(body, dict) else None
    return f" {code}" if isinstance(code, str) and _CODE.fullmatch(code) else ""


def _malformed(status: int) -> RuntimeError:
    return RuntimeError(f"Apollo bulk_match failed: HTTP {status} malformed body")


def bulk_match(linkedin_urls: list[str], timeout: float) -> dict[str, dict]:
    """Returned records keyed by normalized `linkedin_url`. A submitted URL with no record
    (or a record without an email) is absent, which means no-match."""
    api_key = os.environ.get("APOLLO_API_KEY")
    if not api_key:
        raise RuntimeError("APOLLO_API_KEY is not set")
    if not linkedin_urls:
        return {}

    try:
        response = requests.post(
            _ENDPOINT,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "details": [{"linkedin_url": url} for url in linkedin_urls],
                "reveal_personal_emails": False,
                "reveal_phone_number": False,
            },
            timeout=timeout,
        )
    except requests.RequestException as err:
        raise RuntimeError(f"Apollo request failed: {type(err).__name__}") from None

    try:
        body = response.json()
    except ValueError:
        body = None

    if not response.ok:
        raise RuntimeError(f"Apollo bulk_match failed: HTTP {response.status_code}{_code(body)}")
    matches = body.get("matches") if isinstance(body, dict) else None
    if not isinstance(matches, list):
        raise _malformed(response.status_code)

    records = {}
    for record in matches:
        if record is None:
            continue
        if not isinstance(record, dict):
            raise _malformed(response.status_code)
        url = record.get("linkedin_url")
        email = record.get("email")
        if (url is not None and not isinstance(url, str)) or (
            email is not None and not isinstance(email, str)
        ):
            raise _malformed(response.status_code)
        if not url or not url.strip() or not email or not email.strip():
            continue
        organization = record.get("organization") if isinstance(record.get("organization"), dict) else {}
        name = record.get("name") or " ".join(
            part for part in (record.get("first_name"), record.get("last_name")) if part
        )
        records[normalize_linkedin_url(url)] = {
            "name": name,
            "company": organization.get("name") or "",
            "title": record.get("title") or "",
            "email": email.strip(),
            "email_status": (record.get("email_status") or "").lower(),
        }
    return records
