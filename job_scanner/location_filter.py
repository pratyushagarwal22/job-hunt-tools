"""Drop jobs whose location is clearly outside the US."""

from urllib.parse import unquote_plus

from job_scanner.title_filter import first_match


def location_drop_reason(
    location: str | None,
    title: str,
    url: str,
    exclude_keywords: list[str],
    is_ats: bool,
    us_signals: list[str] = (),
) -> tuple[str, str] | None:
    """None if the job passes; else (matched keyword, where: location/title/url).

    ATS jobs check the location field; search jobs check the title and raw URL (snippets
    mention offices and teams too loosely to drop on). A US signal in any checked field keeps
    the job even when an exclude keyword matches (e.g. "New York or Zurich").
    """
    if is_ats:
        fields = [("location", location or "")]
    else:
        fields = [("title", title or ""), ("url", unquote_plus(url or ""))]
    if any(first_match(text, us_signals) is not None for _, text in fields):
        return None
    for where, text in fields:
        keyword = first_match(text, exclude_keywords)
        if keyword is not None:
            return keyword, where
    return None
