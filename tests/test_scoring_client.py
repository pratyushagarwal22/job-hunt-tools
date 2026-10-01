import json
import logging
from pathlib import Path
from unittest.mock import patch

import anthropic
import pytest
from anthropic.types import Message

from job_scanner import scoring_client
from job_scanner.scoring_client import ScoreResult, score_job

FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATE_MD = "CANDIDATE-SENTINEL"
PROFILE_YAML = "PROFILE-SENTINEL"
JD = "Software Engineer, New Grad. Build backend services in Python."


def _fixture() -> dict:
    return json.loads((FIXTURES / "haiku_score_response.json").read_text())


def _tool_input(raw: dict) -> dict:
    return next(b for b in raw["content"] if b["type"] == "tool_use")["input"]


def _score(raw: dict | None = None, side_effect=None, **overrides):
    kwargs = dict(
        jd_text=JD,
        jd_source="full",
        candidate_md=CANDIDATE_MD,
        profile_yaml=PROFILE_YAML,
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        max_jd_chars=15000,
    )
    kwargs.update(overrides)
    response = Message.model_validate(raw if raw is not None else _fixture())
    with patch.object(scoring_client.anthropic, "Anthropic") as client_cls:
        create = client_cls.return_value.messages.create
        create.return_value = response
        create.side_effect = side_effect
        try:
            result = score_job(**kwargs)
        except Exception as exc:
            result = exc
    return result, create


def _with_input(**changes) -> dict:
    raw = _fixture()
    _tool_input(raw).update(changes)
    return raw


def test_valid_result_matches_fixture():
    result, _ = _score()

    expected = _tool_input(_fixture())
    assert isinstance(result, ScoreResult)
    assert result.model_dump().keys() == expected.keys()
    assert result.match_score == round(expected["match_score"], 1)
    assert result.reason == expected["reason"]
    assert all(isinstance(v, str) and v for k, v in result.model_dump().items() if k != "match_score")


def test_single_call_with_forced_tool_and_settings():
    _, create = _score()

    create.assert_called_once()
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5-20251001"
    assert kwargs["max_tokens"] == 1024
    (tool,) = kwargs["tools"]
    assert kwargs["tool_choice"] == {"type": "tool", "name": tool["name"]}
    assert tool["input_schema"] == ScoreResult.model_json_schema()


def test_candidate_and_profile_in_system_prompt():
    _, create = _score()

    (block,) = create.call_args.kwargs["system"]
    assert CANDIDATE_MD in block["text"]
    assert PROFILE_YAML in block["text"]
    assert "ai_evaluation" in block["text"]


def test_system_prompt_has_no_personal_details():
    for personal in ("F-1", "OPT", "Seattle", "Bellevue"):
        assert personal not in scoring_client.SYSTEM_PROMPT
    assert "Location within the US is never a penalty." in scoring_client.SYSTEM_PROMPT


def test_system_prompt_has_calibration_deferring_to_profile():
    prompt = scoring_client.SYSTEM_PROMPT

    assert "Score calibration" in prompt
    assert "profile rules take precedence" in prompt
    for band in ("9-10", "7-8", "- 6:", "3-5", "1-2"):
        assert band in prompt


@pytest.mark.parametrize("jd_source", ["full", "snippet"])
def test_calibration_in_cached_system_block_not_user_message(jd_source):
    _, create = _score(jd_source=jd_source)

    (block,) = create.call_args.kwargs["system"]
    content = create.call_args.kwargs["messages"][0]["content"]
    for marker in ("Score calibration", "Example jobs per band", "Snippet-only inputs"):
        assert marker in block["text"]
        assert marker not in content
    assert block["cache_control"] == {"type": "ephemeral"}


def test_system_prompt_cached():
    _, create = _score()

    (block,) = create.call_args.kwargs["system"]
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}


def test_cache_tokens_logged(caplog):
    raw = _fixture()
    raw["usage"].update(cache_creation_input_tokens=1234, cache_read_input_tokens=5678)
    with caplog.at_level(logging.INFO, logger="job_scanner"):
        _score(raw)

    assert "cache_creation_input_tokens=1234" in caplog.text
    assert "cache_read_input_tokens=5678" in caplog.text


def test_missing_cache_usage_logged_as_zero(caplog):
    raw = _fixture()
    raw["usage"].update(cache_creation_input_tokens=None, cache_read_input_tokens=None)
    with caplog.at_level(logging.INFO, logger="job_scanner"):
        _score(raw)

    assert "cache_creation_input_tokens=0" in caplog.text
    assert "cache_read_input_tokens=0" in caplog.text


@pytest.mark.parametrize("creation, read", [(0, 0), (None, None), (0, None)])
def test_warns_when_cache_not_used(caplog, creation, read):
    raw = _fixture()
    raw["usage"].update(cache_creation_input_tokens=creation, cache_read_input_tokens=read)
    with caplog.at_level(logging.WARNING, logger="job_scanner"):
        _score(raw)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "cache" in warnings[0].getMessage()


@pytest.mark.parametrize("creation, read", [(4600, 0), (0, 4600)])
def test_no_warning_when_cache_used(caplog, creation, read):
    raw = _fixture()
    raw["usage"].update(cache_creation_input_tokens=creation, cache_read_input_tokens=read)
    with caplog.at_level(logging.WARNING, logger="job_scanner"):
        _score(raw)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("score", [10.04, 7.46, 1.0, 10.0])
def test_score_rounded_to_one_decimal(score):
    result, _ = _score(_with_input(match_score=score))

    assert result.match_score == round(score, 1)


@pytest.mark.parametrize("score", [0.9, 10.5, -3, 11])
def test_score_out_of_range_raises(score):
    result, _ = _score(_with_input(match_score=score))

    assert isinstance(result, ValueError)
    assert "match_score" in str(result)


def test_missing_field_raises():
    raw = _fixture()
    del _tool_input(raw)["reason"]
    result, _ = _score(raw)

    assert isinstance(result, ValueError)
    assert "reason" in str(result)


def test_validation_error_does_not_echo_model_output():
    result, _ = _score(_with_input(match_score="SECRET-VALUE"))

    assert isinstance(result, ValueError)
    assert "SECRET-VALUE" not in str(result)


def test_missing_tool_call_raises():
    raw = _fixture()
    raw["content"] = [{"type": "text", "text": "I think this is a 7."}]
    raw["stop_reason"] = "end_turn"
    result, _ = _score(raw)

    assert isinstance(result, ValueError)
    assert "end_turn" in str(result)


def test_jd_truncated_to_max_chars():
    jd = "A" * 100 + "B" * 50
    _, create = _score(jd_text=jd, max_jd_chars=100)

    content = create.call_args.kwargs["messages"][0]["content"]
    assert "A" * 100 in content
    assert "B" not in content


def test_short_jd_not_truncated():
    _, create = _score(max_jd_chars=15000)

    assert JD in create.call_args.kwargs["messages"][0]["content"]


def test_hints_passed_through():
    _, create = _score(title_hint="Backend Engineer, New Grad", company_hint="Acme Robotics")

    content = create.call_args.kwargs["messages"][0]["content"]
    assert "Backend Engineer, New Grad" in content
    assert "Acme Robotics" in content


def test_no_hints_no_known_lines():
    _, create = _score()

    assert "Known" not in create.call_args.kwargs["messages"][0]["content"]


def test_snippet_mode_adds_note():
    _, create = _score(jd_source="snippet")

    content = create.call_args.kwargs["messages"][0]["content"]
    assert scoring_client.SNIPPET_NOTE in content
    assert "do not invent requirements" in content


def test_full_mode_has_no_snippet_note():
    _, create = _score(jd_source="full")

    assert scoring_client.SNIPPET_NOTE not in create.call_args.kwargs["messages"][0]["content"]


def test_sdk_errors_propagate():
    err = anthropic.APIConnectionError(request=None)
    result, _ = _score(side_effect=err)

    assert result is err


def test_logs_do_not_contain_prompt_contents(caplog):
    with caplog.at_level(logging.DEBUG, logger="job_scanner"):
        _score(title_hint="HINT-SENTINEL")

    assert "tokens" in caplog.text
    for secret in (CANDIDATE_MD, PROFILE_YAML, JD, "HINT-SENTINEL"):
        assert secret not in caplog.text
