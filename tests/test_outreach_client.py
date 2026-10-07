import json
import logging
from pathlib import Path
from unittest.mock import patch

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from email_finder import outreach_client
from email_finder.outreach_client import draft_outreach

FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATE_MD = "CANDIDATE-SENTINEL"


def _fixture() -> dict:
    return json.loads((FIXTURES / "outreach_response.json").read_text())


def _draft(raw: dict | None = None, side_effect=None, **overrides):
    kwargs = dict(
        name="Jane Doe",
        company="Example Corp",
        title="Engineering Manager",
        candidate_md=CANDIDATE_MD,
        model="claude-sonnet-5-5",
        max_tokens=300,
    )
    kwargs.update(overrides)
    response = Message.model_validate(raw if raw is not None else _fixture())
    with patch.object(outreach_client.anthropic, "Anthropic") as client_cls:
        create = client_cls.return_value.messages.create
        create.return_value = response
        create.side_effect = side_effect
        result = draft_outreach(**kwargs)
    return result, create


def test_returns_the_drafted_line_stripped():
    line, _ = _draft()
    assert line.startswith("Hi Jane, I'm a new-grad software engineer")
    assert line == line.strip()


def test_one_call_with_model_max_tokens_candidate_and_contact():
    _, create = _draft()
    create.assert_called_once()
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["max_tokens"] == 300
    assert CANDIDATE_MD in json.dumps(kwargs["system"])
    user = json.dumps(kwargs["messages"])
    for value in ("Jane Doe", "Example Corp", "Engineering Manager"):
        assert value in user


def test_blank_fields_are_not_sent_as_placeholders():
    _, create = _draft(title="", company="")
    user = json.dumps(create.call_args.kwargs["messages"])
    assert "None" not in user


def test_multi_line_text_is_collapsed_to_one_line():
    raw = _fixture()
    raw["content"] = [
        {"type": "text", "text": "Hi Jane,\n\nI build backend services."},
        {"type": "text", "text": " Would love to connect."},
    ]
    line, _ = _draft(raw)
    assert "\n" not in line
    assert line == "Hi Jane, I build backend services. Would love to connect."


def test_empty_text_raises():
    raw = _fixture()
    raw["content"] = [{"type": "text", "text": "  \n "}]
    with pytest.raises(ValueError, match="empty"):
        _draft(raw)


def test_api_errors_propagate():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.AuthenticationError(
        "denied", response=httpx2.Response(401, request=request), body=None
    )
    with pytest.raises(anthropic.AuthenticationError):
        _draft(side_effect=err)


def test_nothing_is_logged(caplog):
    caplog.set_level(logging.DEBUG)
    _draft()
    assert caplog.records == []
