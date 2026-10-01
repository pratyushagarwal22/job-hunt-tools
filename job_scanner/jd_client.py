"""Plain HTTP fetch of a job description page. One GET, no retries."""

import logging

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_STRIP_TAGS = ("script", "style", "noscript", "svg", "template")


def fetch_jd(url: str, timeout: float, min_chars: int) -> tuple[str | None, str]:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("JD fetch %s: request failed: %s", url, exc)
        return None, "snippet"

    if not response.ok:
        logger.warning("JD fetch %s: HTTP %s", url, response.status_code)
        return None, "snippet"

    content_type = (response.headers.get("Content-Type") or "").lower()
    if not any(t in content_type for t in _HTML_TYPES):
        logger.warning("JD fetch %s: non-HTML content type %r", url, content_type)
        return None, "snippet"

    text = _visible_text(response.text)
    if len(text) < min_chars:
        logger.warning(
            "JD fetch %s: only %d chars of visible text (< %d)", url, len(text), min_chars
        )
        return None, "snippet"
    return text, "full"


def _visible_text(markup: str) -> str:
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)
