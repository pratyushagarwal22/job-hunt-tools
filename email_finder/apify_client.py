"""Apify linkedin-email-finder actor client. One synchronous actor run per call, no retries.

Errors carry only the HTTP status and Apify's error type: response bodies can hold
personal data and are never put in messages.
"""

import os
import re

import requests

_ENDPOINT = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
_NO_MATCH_ERROR = "No email found in response"
_CODE = re.compile(r"[a-z0-9-]{1,64}")


def _code(body) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("type") if isinstance(error, dict) else None
    return f" {code}" if isinstance(code, str) and _CODE.fullmatch(code) else ""


def _parse_item(item) -> tuple[str, dict | None]:
    if not isinstance(item, dict):
        raise RuntimeError("Apify actor run failed: malformed item")
    url = item.get("linkedin_url")
    found = item.get("found")
    if not isinstance(url, str) or not url.strip() or not isinstance(found, bool):
        raise RuntimeError("Apify actor run failed: malformed item")
    if not found:
        if item.get("error") == _NO_MATCH_ERROR:
            return url, None
        raise RuntimeError("Apify actor run failed: item has an unexpected error")
    email = item.get("email")
    if not isinstance(email, str) or not email.strip():
        raise RuntimeError("Apify actor run failed: found item has no email")
    return url, {
        "name": item.get("name") or "",
        "company": item.get("company") or "",
        "title": "",
        "email": email.strip(),
        "email_status": "unverified",
    }


def find_emails(urls: list[str], actor_id: str, timeout: float) -> dict[str, dict | None]:
    """One entry per returned item, keyed by the item's `linkedin_url`: a match dict or None.

    Submitted URLs with no returned item are absent; the caller treats that as an error.
    """
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN is not set")
    if not urls:
        return {}

    try:
        response = requests.post(
            _ENDPOINT.format(actor=actor_id.replace("/", "~")),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"urls": list(urls)},
            timeout=timeout,
        )
    except requests.RequestException as err:
        raise RuntimeError(f"Apify request failed: {type(err).__name__}") from None

    try:
        body = response.json()
    except ValueError:
        body = None

    if not response.ok:
        raise RuntimeError(f"Apify actor run failed: HTTP {response.status_code}{_code(body)}")
    if not isinstance(body, list):
        raise RuntimeError(f"Apify actor run failed: HTTP {response.status_code} malformed body")

    return dict(_parse_item(item) for item in body)
