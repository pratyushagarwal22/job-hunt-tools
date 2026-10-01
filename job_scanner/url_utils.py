"""URL normalization used as the dedup key."""

from urllib.parse import urlsplit, urlunsplit

# gh_jid and id are deliberately absent: they are job IDs on some career sites.
_TRACKING_PARAMS = {"ref", "source", "gh_src", "team", "page", "location", "action"}
_APPLY_SEGMENT = "/apply"


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
