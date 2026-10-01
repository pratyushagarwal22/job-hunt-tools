"""URL normalization used as the dedup key."""

from urllib.parse import urlsplit, urlunsplit

# gh_jid is deliberately absent: it is the job ID on company-hosted Greenhouse pages.
_TRACKING_PARAMS = {"ref", "source", "gh_src"}


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    kept = [pair for pair in parts.query.split("&") if pair and not _is_tracking(pair)]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            "&".join(kept),
            "",
        )
    )


def _is_tracking(pair: str) -> bool:
    key = pair.split("=", 1)[0].lower()
    return key.startswith("utm_") or key in _TRACKING_PARAMS
