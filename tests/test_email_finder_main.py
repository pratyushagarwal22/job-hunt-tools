import logging

import pytest
import yaml

from email_finder import main as main_module
from email_finder import service, sheets_repo

ENV = dict(
    SHEET_ID="sheet-123",
    GOOGLE_APPLICATION_CREDENTIALS="service_account.json",
    PROSPEO_API_KEY="prospeo-secret",
    APIFY_TOKEN="apify-secret",
)
SETTINGS = {"providers": ["prospeo", "apify"]}
RESULT = {
    "lines": ["https://linkedin.com/in/a found via prospeo verified", "duplicate https://linkedin.com/in/a"],
    "summary": {
        "input": 2, "ignored": 0, "duplicate": 1, "existing": 0,
        "found": {"prospeo": 1, "apify": 0}, "not_found": 0, "pending": 0,
    },
    "exit_code": 0,
}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module.service, "load_settings", lambda: SETTINGS)
    for name in ("APOLLO_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def _forbid_run(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not be called")

    monkeypatch.setattr(main_module.sheets_repo, "get_sheet_title", boom)
    monkeypatch.setattr(main_module.service, "run", boom)


def _fake_run(monkeypatch, result=RESULT, calls=None):
    monkeypatch.setattr(main_module.sheets_repo, "get_sheet_title", lambda sheet_id: "Job Tracker TEST")

    def run(urls, file_path, draft):
        if calls is not None:
            calls.append((urls, file_path, draft))
        return result

    monkeypatch.setattr(main_module.service, "run", run)


@pytest.mark.parametrize("missing", list(ENV))
def test_missing_env_var_fails_fast_and_names_it(env, caplog, missing):
    env.delenv(missing)
    _forbid_run(env)
    assert main_module.main([]) == 1
    assert "Missing required environment variable(s)" in caplog.text
    assert missing in caplog.text


def test_all_missing_named_together_and_anthropic_only_with_draft(env, caplog):
    env.setenv("SHEET_ID", "")
    env.delenv("APIFY_TOKEN")
    _forbid_run(env)
    assert main_module.main(["--draft"]) == 1
    assert "SHEET_ID, APIFY_TOKEN, ANTHROPIC_API_KEY" in caplog.text


def test_apollo_key_not_needed_when_apollo_disabled(env, capsys):
    _fake_run(env)
    assert main_module.main([]) == 0


def test_prints_sheet_title_first_then_lines_then_summary(env, capsys):
    env.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    calls = []
    _fake_run(env, calls=calls)

    assert main_module.main(["https://linkedin.com/in/a", "--file", "x.txt", "--draft"]) == 0
    out = capsys.readouterr().out.splitlines()

    assert out[0] == "Writing to sheet: Job Tracker TEST"
    assert out[1:3] == RESULT["lines"]
    assert out[3] == (
        "input 2, ignored 0, duplicate 1, skipped existing 0, found prospeo 1, "
        "found apify 0, not found 0, pending 0"
    )
    assert calls == [(["https://linkedin.com/in/a"], "x.txt", True)]


def test_draft_requires_anthropic_key_and_passes_when_set(env):
    env.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    _fake_run(env)
    assert main_module.main(["--draft"]) == 0


def test_no_args_means_default_file(env):
    calls = []
    _fake_run(env, calls=calls)
    main_module.main([])
    assert calls == [([], None, False)]


def test_service_exit_code_is_returned(env, capsys):
    _fake_run(env, result={**RESULT, "exit_code": 1})
    assert main_module.main([]) == 1
    assert "pending 0" in capsys.readouterr().out


def _assert_one_plain_error(caplog, expected):
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert expected in errors[0].getMessage()
    assert errors[0].exc_info is None
    assert "Traceback" not in caplog.text


def test_unexpected_exception_logged_with_traceback_exit_1(env, caplog):
    env.setattr(main_module.sheets_repo, "get_sheet_title", lambda sheet_id: "T")

    def run(*args):
        raise RuntimeError("boom")

    env.setattr(main_module.service, "run", run)
    assert main_module.main([]) == 1
    assert "Fatal error, run aborted" in caplog.text
    assert any(r.exc_info for r in caplog.records)


@pytest.mark.parametrize(
    "error, expected",
    [
        (service.TooManyUrlsError("21 URLs to look up exceeds max_urls_per_run 20; nothing looked up"),
         "exceeds max_urls_per_run 20"),
        (sheets_repo.MissingHeadersError(["notes"]), "missing column(s): notes"),
        (service.RunError("Could not read input file x.txt: FileNotFoundError"), "x.txt"),
        (service.RunError("Invalid providers in settings.yaml: ['hunter']"), "hunter"),
    ],
)
def test_expected_errors_are_one_plain_line_exit_1(env, caplog, error, expected):
    env.setattr(main_module.sheets_repo, "get_sheet_title", lambda sheet_id: "T")

    def run(*args):
        raise error

    env.setattr(main_module.service, "run", run)
    assert main_module.main([]) == 1
    _assert_one_plain_error(caplog, expected)


def test_unreadable_settings_is_one_plain_line_exit_1(env, caplog):
    def load_settings():
        raise yaml.YAMLError("bad yaml")

    env.setattr(main_module.service, "load_settings", load_settings)
    _forbid_run(env)
    assert main_module.main([]) == 1
    _assert_one_plain_error(caplog, "email_finder/settings.yaml")


def _real_run_without_sheet_or_providers(env):
    env.setattr(main_module.sheets_repo, "get_sheet_title", lambda sheet_id: "T")

    def boom(*args, **kwargs):
        raise AssertionError("should not be called")

    for target, name in (
        (service.sheets_repo, "get_columns"), (service.sheets_repo, "get_existing_keys"),
        (service.prospeo_client, "enrich_person"), (service.apify_client, "find_emails"),
    ):
        env.setattr(target, name, boom)


def test_draft_with_missing_candidate_file_is_one_plain_line_no_lookups(env, caplog, tmp_path):
    env.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    env.setattr(service, "_CANDIDATE_PATH", tmp_path / "candidate.md")
    _real_run_without_sheet_or_providers(env)

    assert main_module.main(["https://linkedin.com/in/a", "--draft"]) == 1
    _assert_one_plain_error(caplog, "candidate.md")


def test_missing_input_file_is_one_plain_line_no_lookups(env, caplog, tmp_path):
    _real_run_without_sheet_or_providers(env)
    assert main_module.main(["--file", str(tmp_path / "nope.txt")]) == 1
    _assert_one_plain_error(caplog, "nope.txt")


def test_draft_help_says_connection_note(env, capsys):
    with pytest.raises(SystemExit):
        main_module.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "connection note" in out and "outreach line" not in out


def test_keys_never_printed(env, capsys, caplog):
    _fake_run(env)
    main_module.main([])
    text = capsys.readouterr().out + caplog.text
    assert "prospeo-secret" not in text and "apify-secret" not in text
