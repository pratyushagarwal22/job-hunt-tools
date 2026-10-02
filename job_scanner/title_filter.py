"""Cheap include/exclude keyword filter on job titles."""

import re

NO_INCLUDE_MATCH = "no include match"


def title_drop_reason(
    title: str, include: list[str], exclude: list[str], always_allow: list[str] = ()
) -> str | None:
    """None if the title passes; else the matched exclude keyword or NO_INCLUDE_MATCH.

    always_allow phrases count as an include match and are removed before the exclude check,
    so "Member of Technical Staff" passes but "Senior Member of Technical Staff" still drops.
    """
    title = title or ""
    allowed = False
    for phrase in always_allow:
        if phrase and phrase.strip():
            title, removed = _keyword_pattern(phrase).subn(" ", title)
            allowed = allowed or removed > 0
    excluded = first_match(title, exclude)
    if excluded is not None:
        return excluded
    if not allowed and first_match(title, include) is None:
        return NO_INCLUDE_MATCH
    return None


def first_match(text: str, keywords: list[str]) -> str | None:
    """First keyword found in text (case-insensitive whole-word/phrase match), else None."""
    if not text:
        return None
    for keyword in keywords:
        if keyword and keyword.strip() and _keyword_pattern(keyword).search(text):
            return keyword
    return None


def _keyword_pattern(keyword: str) -> re.Pattern:
    # \b fails next to symbols ("C++", "AI/ML"), so use explicit non-word lookarounds.
    phrase = r"\s+".join(re.escape(word) for word in keyword.split())
    return re.compile(rf"(?<!\w){phrase}(?!\w)", re.IGNORECASE)
