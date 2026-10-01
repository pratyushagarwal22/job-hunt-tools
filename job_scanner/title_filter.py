"""Cheap include/exclude keyword filter on job titles."""

import re


def passes_title_filter(title: str, include: list[str], exclude: list[str]) -> bool:
    return matches_any(title, include) and not matches_any(title, exclude)


def matches_any(text: str, keywords: list[str]) -> bool:
    """Case-insensitive whole-word/phrase match of any keyword in text."""
    if not text:
        return False
    return any(_keyword_pattern(k).search(text) for k in keywords if k and k.strip())


def _keyword_pattern(keyword: str) -> re.Pattern:
    # \b fails next to symbols ("C++", "AI/ML"), so use explicit non-word lookarounds.
    phrase = r"\s+".join(re.escape(word) for word in keyword.split())
    return re.compile(rf"(?<!\w){phrase}(?!\w)", re.IGNORECASE)
