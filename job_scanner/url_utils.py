"""URL normalization used as the dedup key, and the job platform for a URL."""

from urllib.parse import parse_qsl, urlsplit, urlunsplit

# gh_jid and id are deliberately absent: they are job IDs on some career sites.
_TRACKING_PARAMS = {"ref", "source", "gh_src", "team", "page", "location", "action"}
_APPLY_SEGMENT = "/apply"

# Host (or parent domain) -> platform. The longest matching suffix wins, so a specific
# company host like nvidia.wd5.myworkdayjobs.com beats the generic myworkdayjobs.com.
_PLATFORM_HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "workable.com": "workable",
    "wellfound.com": "wellfound",
    "ycombinator.com": "workatastartup",
    "workatastartup.com": "workatastartup",
    "welcometothejungle.com": "welcometothejungle",
    "smartrecruiters.com": "smartrecruiters",
    "jobvite.com": "jobvite",
    "breezy.hr": "breezy",
    "jobs.a16z.com": "a16z",
    "myworkdayjobs.com": "workday",
    "amazon.jobs": "company_site",
    "google.com": "company_site",
    "jobs.apple.com": "company_site",
    "careers.microsoft.com": "company_site",
    "nvidia.com": "company_site",
    "nvidia.wd5.myworkdayjobs.com": "company_site",
    "tesla.com": "company_site",
    "metacareers.com": "company_site",
    "jobs.netflix.net": "company_site",
    "careers.snap.com": "company_site",
}
_GREENHOUSE_JOB_PARAM = "gh_jid"


def platform_for_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if any(key.lower() == _GREENHOUSE_JOB_PARAM for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return "greenhouse"
    host = (parts.hostname or "").lower()
    matches = [
        suffix for suffix in _PLATFORM_HOSTS
        if host == suffix or host.endswith("." + suffix)
    ]
    if not matches:
        return "other"
    return _PLATFORM_HOSTS[max(matches, key=len)]


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    kept = [pair for pair in parts.query.split("&") if pair and not _is_tracking(pair)]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            _strip_path(parts.path),
            "&".join(kept),
            "",
        )
    )


def _strip_path(path: str) -> str:
    path = path.rstrip("/")
    while path.lower().endswith(_APPLY_SEGMENT):
        path = path[: -len(_APPLY_SEGMENT)].rstrip("/")
    return path


def _is_tracking(pair: str) -> bool:
    key = pair.split("=", 1)[0].lower()
    return key.startswith("utm_") or key in _TRACKING_PARAMS
