"""Draft one LinkedIn connection note with a single Claude call.

Never logs: the prompt holds candidate.md, the examples and the contact's details, and the
drafted note is personal. Errors propagate (ValueError messages never carry the drafted text);
the service decides what to do with them.
"""

import re

import anthropic

SYSTEM_PROMPT = """\
You write one LinkedIn connection note from a job-seeking candidate to a person at a company. \
It is sent with a connection request, so it must be short.

Rules:
- At most {max_chars} characters. Plain text on one line: no line breaks, no emoji, no hashtags.
- Start with "Hi <first name>," on the same line when a first name is given. No sign-off and \
no signature.
- Use only facts from the candidate document below. Never invent facts about the person or \
the company beyond the details given, and never invent mutual connections.
- Never mention visa, sponsorship, phone numbers, email addresses or salary.
- Mention one concrete, relevant thing from the candidate's background and end by asking to \
connect. Do not ask for a referral.
- Reply with the note only: no quotes, no placeholders.

<candidate>
{candidate_md}
</candidate>
"""

EXAMPLES_SECTION = """
Example notes follow. They show the style and length only: do not copy their wording, and \
never use a fact from them that is not in the candidate document.

<examples>
{examples_md}
</examples>
"""

# A sentence ends at . ! or ? followed by whitespace or the end of the text.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def fit_to_length(line: str, max_chars: int) -> str | None:
    """The line if it fits, else its longest prefix ending a sentence within max_chars,
    else None."""
    if len(line) <= max_chars:
        return line
    ends = [m.end() for m in _SENTENCE_END.finditer(line) if m.end() <= max_chars]
    return line[:ends[-1]] if ends else None


def _system_prompt(candidate_md: str, examples_md: str, max_chars: int) -> str:
    prompt = SYSTEM_PROMPT.format(max_chars=max_chars, candidate_md=candidate_md)
    if examples_md.strip():
        prompt += EXAMPLES_SECTION.format(examples_md=examples_md.strip())
    return prompt


def _user_message(first_name: str, company: str, title: str, notes: str) -> str:
    details = [
        f"{label}: {value}"
        for label, value in (
            ("First name", first_name), ("Title", title), ("Company", company), ("Notes", notes),
        )
        if value
    ]
    if notes:
        ask = (
            "Write the connection note. If the notes mention a role, reference that role; "
            "otherwise make it a networking note."
        )
    else:
        ask = "Write a networking connection note (no specific role)."
    return ask + "\n" + "\n".join(details)


def draft_outreach(
    first_name: str,
    company: str,
    title: str,
    notes: str,
    candidate_md: str,
    examples_md: str,
    model: str,
    max_tokens: int,
    max_chars: int,
) -> str:
    response = anthropic.Anthropic().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_system_prompt(candidate_md, examples_md, max_chars),
        messages=[{"role": "user", "content": _user_message(first_name, company, title, notes)}],
    )
    if response.stop_reason == "max_tokens":
        raise ValueError("Outreach draft hit max_tokens")
    text = "".join(block.text for block in response.content if block.type == "text")
    line = " ".join(text.split())
    if not line:
        raise ValueError(f"Outreach draft was empty (stop_reason={response.stop_reason})")
    fitted = fit_to_length(line, max_chars)
    if fitted is None:
        raise ValueError(f"Outreach draft over max_chars {max_chars} with no sentence end within it")
    return fitted
