"""LinkedIn URL normalizer shared by email_finder and the legacy contacts import."""

from urllib.parse import urlsplit


def normalize_linkedin_url(url: str) -> str:
    """Lowercase, https, any linkedin.com subdomain -> linkedin.com, no query/fragment,
    no trailing slash; on linkedin.com, extra path after /in/<slug> is dropped.

    `https://www.LinkedIn.com/in/Jane-Doe/?utm_source=foo` -> `https://linkedin.com/in/jane-doe`
    `https://linkedin.com/in/jane-doe/details/experience/` -> `https://linkedin.com/in/jane-doe`
    """
    url = url.strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url.lower())
    host = parts.netloc
    if host.endswith(".linkedin.com"):
        host = "linkedin.com"
    path = parts.path.rstrip("/")
    if host == "linkedin.com":
        segments = path.split("/")
        if len(segments) > 3 and segments[1] == "in" and segments[2]:
            path = "/".join(segments[:3])
    return f"https://{host}{path}"
