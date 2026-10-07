"""LinkedIn URL normalizer shared by email_finder and the legacy contacts import."""

from urllib.parse import urlsplit


def normalize_linkedin_url(url: str) -> str:
    """Lowercase, https, no `www.`, no query/fragment, no trailing slash.

    `https://www.LinkedIn.com/in/Jane-Doe/?utm_source=foo` -> `https://linkedin.com/in/jane-doe`
    """
    url = url.strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url.lower())
    host = parts.netloc.removeprefix("www.")
    return f"https://{host}{parts.path.rstrip('/')}"
