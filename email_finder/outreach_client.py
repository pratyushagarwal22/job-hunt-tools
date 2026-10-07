"""Draft one LinkedIn outreach line with a single Claude call.

Never logs: the prompt holds candidate.md and the contact's details, and the drafted line is
personal. Errors propagate; the service decides what to do with them.
"""

import anthropic

SYSTEM_PROMPT = """\
You write one short, friendly LinkedIn outreach message from a job-seeking candidate to a \
contact at a company. Use only facts from the candidate document below; never invent \
experience, mutual connections or details about the contact.

Rules:
- One or two sentences, under 300 characters, plain text, no emoji, no hashtags.
- Greet the contact by first name when a name is given.
- Mention one concrete, relevant thing from the candidate's background and ask for a short \
chat or advice about roles on their team. Do not ask for a referral outright.
- Reply with the message only: no quotes, no subject line, no placeholders.

<candidate>
{candidate_md}
</candidate>
"""


def _user_message(name: str, company: str, title: str) -> str:
    details = [
        f"{label}: {value}"
        for label, value in (("Name", name), ("Title", title), ("Company", company))
        if value
    ]
    return "Write the outreach message for this contact.\n" + "\n".join(details)


def draft_outreach(
    name: str, company: str, title: str, candidate_md: str, model: str, max_tokens: int
) -> str:
    response = anthropic.Anthropic().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT.format(candidate_md=candidate_md),
        messages=[{"role": "user", "content": _user_message(name, company, title)}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    line = " ".join(text.split())
    if not line:
        raise ValueError(f"Outreach draft was empty (stop_reason={response.stop_reason})")
    return line
