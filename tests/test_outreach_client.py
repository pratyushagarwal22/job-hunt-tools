import json
import logging
from pathlib import Path
from unittest.mock import patch

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from email_finder import outreach_client
from email_finder.outreach_client import draft_outreach, fit_to_length

FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATE_MD = "CANDIDATE-SENTINEL"
EXAMPLES_MD = "EXAMPLES-SENTINEL"


def _fixture() -> dict:
    return json.loads((FIXTURES / "outreach_response.json").read_text())


def _with_text(text: str, stop_reason: str = "end_turn") -> dict:
    raw = _fixture()
    raw["content"] = [{"type": "text", "text": text}]
    raw["stop_reason"] = stop_reason
    return raw


def _draft(raw: dict | None = None, side_effect=None, **overrides):
    kwargs = dict(
        first_name="Jane",
        company="Example Corp",
        title="Engineering Manager",
        notes="",
        candidate_md=CANDIDATE_MD,
        examples_md="",
        model="claude-sonnet-5-5",
        max_tokens=4096,
        max_chars=200,
    )
    kwargs.update(overrides)
    response = Message.model_validate(raw if raw is not None else _fixture())
    with patch.object(outreach_client.anthropic, "Anthropic") as client_cls:
        create = client_cls.return_value.messages.create
        create.return_value = response
        create.side_effect = side_effect
        result = draft_outreach(**kwargs)
    return result, create


def _system(create) -> str:
    return create.call_args.kwargs["system"]


def _user(create) -> str:
    return json.dumps(create.call_args.kwargs["messages"])


def test_returns_the_drafted_note_stripped():
    line, _ = _draft()
    assert line.startswith("Hi Jane, new-grad software engineer")
    assert line == line.strip()
    assert len(line) <= 200


def test_one_call_with_model_max_tokens_candidate_and_contact():
    _, create = _draft()
    create.assert_called_once()
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["max_tokens"] == 4096
    assert CANDIDATE_MD in _system(create)
    for value in ("Jane", "Example Corp", "Engineering Manager"):
        assert value in _user(create)


def test_prompt_states_the_purpose_rules_and_length():
    _, create = _draft(max_chars=180)
    system = _system(create)
    assert "LinkedIn connection note" in system
    assert "180 characters" in system
    for banned in ("visa", "sponsorship", "phone numbers", "email addresses", "salary"):
        assert banned in system
    assert "sign-off" in system
    assert "only facts from the candidate document" in system


def test_notes_are_passed_through_and_ask_for_the_role():
    _, create = _draft(notes="applied to Backend Engineer, Payments")
    user = _user(create)
    assert "applied to Backend Engineer, Payments" in user
    assert "role" in user.lower()


def test_empty_notes_ask_for_a_networking_note():
    _, create = _draft(notes="")
    user = _user(create)
    assert "Notes" not in user
    assert "networking" in user.lower()


def test_examples_are_included_for_style_when_given():
    _, create = _draft(examples_md=EXAMPLES_MD)
    system = _system(create)
    assert EXAMPLES_MD in system
    assert "style" in system.lower()


def test_no_examples_section_when_examples_are_empty():
    _, create = _draft(examples_md="")
    assert "<examples>" not in _system(create)


def test_blank_fields_are_not_sent_as_placeholders():
    _, create = _draft(title="", company="", first_name="")
    user = _user(create)
    assert "None" not in user
    assert "Title:" not in user and "Company:" not in user and "First name:" not in user


def test_multi_line_text_is_collapsed_to_one_line():
    raw = _fixture()
    raw["content"] = [
        {"type": "text", "text": "Hi Jane,\n\nI build backend services."},
        {"type": "text", "text": " Would love to connect."},
    ]
    line, _ = _draft(raw)
    assert "\n" not in line
    assert line == "Hi Jane, I build backend services. Would love to connect."


def test_too_long_reply_is_trimmed_at_the_last_sentence_end():
    text = "Hi Jane, I build backend services. I shipped a solo app! Would love to connect and hear more."
    line, _ = _draft(_with_text(text), max_chars=60)
    assert line == "Hi Jane, I build backend services. I shipped a solo app!"


def test_too_long_reply_with_no_sentence_end_in_limit_raises():
    text = "Hi Jane, I build backend services and shipped a solo app and would love to connect."
    with pytest.raises(ValueError, match="max_chars"):
        _draft(_with_text(text), max_chars=40)


def test_max_tokens_stop_raises_even_with_text():
    with pytest.raises(ValueError, match="max_tokens"):
        _draft(_with_text("Hi Jane, I build backend serv", stop_reason="max_tokens"))


def test_thinking_blocks_are_ignored():
    raw = _fixture()
    raw["content"] = [
        {"type": "thinking", "thinking": "THINKING-SENTINEL", "signature": "sig"},
        {"type": "text", "text": "Hi Jane, would love to connect."},
    ]
    line, _ = _draft(raw)
    assert line == "Hi Jane, would love to connect."


def test_empty_text_raises():
    with pytest.raises(ValueError, match="empty"):
        _draft(_with_text("  \n "))


def test_api_errors_propagate():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.AuthenticationError(
        "denied", response=httpx2.Response(401, request=request), body=None
    )
    with pytest.raises(anthropic.AuthenticationError):
        _draft(side_effect=err)


def test_error_messages_do_not_carry_the_drafted_text():
    text = "SECRET-DRAFT and no sentence end at all in this long reply"
    with pytest.raises(ValueError) as excinfo:
        _draft(_with_text(text), max_chars=20)
    assert "SECRET-DRAFT" not in str(excinfo.value)


def test_nothing_is_logged(caplog):
    caplog.set_level(logging.DEBUG)
    _draft()
    assert caplog.records == []


@pytest.mark.parametrize(
    "line, max_chars, expected",
    [
        ("Short note.", 200, "Short note."),
        ("One. Two. Three.", 10, "One. Two."),
        ("One. Two. Three.", 9, "One. Two."),
        ("One. Two. Three.", 8, "One."),
        ("Ask? Then more words here", 12, "Ask?"),
        ("Version 3.5 is out", 10, None),
        ("No sentence end here at all", 10, None),
    ],
)
def test_fit_to_length(line, max_chars, expected):
    assert fit_to_length(line, max_chars) == expected
