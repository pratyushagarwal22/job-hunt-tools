"""Drop jobs whose location is clearly outside the US."""

from urllib.parse import unquote_plus

from job_scanner.title_filter import matches_any


def passes_location_filter(
    location: str | None,
    title: str,
    snippet: str,
    url: str,
    exclude_keywords: list[str],
    is_ats: bool,
) -> bool:
    if is_ats:
        text = location or ""
    else:
        text = " ".join([title or "", snippet or "", unquote_plus(url or "")])
    return not matches_any(text, exclude_keywords)
