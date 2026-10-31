"""Find the minimum years of experience a job posting requires."""

import html
import re

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUM = r"(?:\d{1,2}|" + "|".join(_WORDS) + r")"
# "three (3) years" — the parenthesised digit repeats the word.
_ECHO = r"(?:\s*\(\d{1,2}\))?"
_YEARS_RE = re.compile(
    rf"(?P<prefix>\b(?:at\s+least|minimum(?:\s+of)?)\s+)?"
    rf"(?<![\w.])(?P<n>{_NUM}){_ECHO}\s*(?P<plus>\+)?"
    rf"(?P<range>\s*(?:-|–|—|to)\s*{_NUM}{_ECHO}\s*\+?)?"
    r"\s*(?:years?|yrs?)\b"
    # Years that describe something other than the candidate.
    r"(?!\s+(?:ago|old|of\s+age)\b)"
    r"(?!\s+(?:in|of)\s+(?:business|operation|operations|existence)\b)"
    # Rest of the sentence, as a lookahead so it doesn't swallow later matches.
    r"(?=(?P<tail>[^.!?;\n]{0,100}))",
    re.IGNORECASE,
)
_SKILL_TAIL = re.compile(r"^['’]?\s*(?:in|with|of|using)\s+\S", re.IGNORECASE)
_EXPERIENCE = re.compile(r"\bexperience\b", re.IGNORECASE)
_DEGREE_BEFORE = re.compile(
    r"\b(?:bachelor|master|ph\.?\s?d|degree|bs|ms)['’]?s?\W*(?:\+|and|plus|with)\s*$",
    re.IGNORECASE,
)
_EXPERIENCE_BEFORE = re.compile(r"\bexperience[^.!?;\n]{0,15}$", re.IGNORECASE)
_AGED_BEFORE = re.compile(r"\baged\s+$", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
# No early-career posting asks for 15+ years; those numbers are company history.
_MAX_EARLY_CAREER_YEARS = 15


def required_years(text: str) -> tuple[int | None, str | None]:
    """Smallest experience requirement in the text and the phrase it came from, or (None, None)."""
    clean = _clean(text or "")
    best: tuple[int, str] | None = None
    for match in _YEARS_RE.finditer(clean):
        if not _is_experience(match, clean):
            continue
        years = _to_int(match.group("n"))
        if years >= _MAX_EARLY_CAREER_YEARS:
            continue
        if best is None or years < best[0]:
            best = (years, _phrase(match))
    return best if best else (None, None)


def _clean(text: str) -> str:
    # Twice, for double-escaped markup like "&amp;nbsp;".
    text = html.unescape(html.unescape(text))
    text = _TAG.sub(" ", text)
    return " ".join(text.split())


def _is_experience(match: re.Match, text: str) -> bool:
    tail = match.group("tail")
    before = text[max(0, match.start() - 40): match.start()]
    if _AGED_BEFORE.search(before):
        return False
    return bool(
        match.group("plus")
        or match.group("range")
        or _EXPERIENCE.search(tail)
        or _SKILL_TAIL.match(tail)
        or _DEGREE_BEFORE.search(before)
        or _EXPERIENCE_BEFORE.search(before)
    )


def _to_int(value: str) -> int:
    return int(value) if value.isdigit() else _WORDS[value.lower()]


def _phrase(match: re.Match) -> str:
    tail = match.group("tail")
    experience = _EXPERIENCE.search(tail)
    tail = tail[: experience.end()] if experience else tail[:30]
    return f"{match.group(0)}{tail}".strip()
