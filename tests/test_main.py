import pytest

from job_scanner import main as main_module

ENV = dict(
    SHEET_ID="sheet-123",
    SERPER_API_KEY="serper",
    ANTHROPIC_API_KEY="anthropic",
    GOOGLE_APPLICATION_CREDENTIALS="service_account.json",
)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def _forbid_run(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not be called")

    monkeypatch.setattr(main_module.sheets_repo, "get_sheet_title", boom)
    monkeypatch.setattr(main_module.service, "run_pipeline", boom)


@pytest.mark.parametrize("missing", list(ENV))
def test_missing_env_var_fails_fast_and_names_it(env, caplog, missing):
    env.delenv(missing)
    _forbid_run(env)

    assert main_module.main([]) == 1
    assert "Missing required environment variable(s)" in caplog.text
    assert missing in caplog.text


def test_all_missing_env_vars_named_together(env, caplog):
    for name in ("SHEET_ID", "ANTHROPIC_API_KEY"):
        env.setenv(name, "")
    _forbid_run(env)

    assert main_module.main([]) == 1
    assert "SHEET_ID, ANTHROPIC_API_KEY" in caplog.text


def test_runs_pipeline_with_max_jobs_when_env_complete(env, capsys):
    calls = []
    env.setattr(main_module.sheets_repo, "get_sheet_title", lambda sheet_id: "Test Sheet")
    env.setattr(
        main_module.service, "run_pipeline",
        lambda max_jobs: calls.append(max_jobs) or dict(
            found=1, new=1, filtered=0, scored=1, failed=0, skipped_cap=0
        ),
    )

    assert main_module.main(["--max-jobs", "3"]) == 0
    assert calls == [3]
    out = capsys.readouterr().out
    assert "Writing to sheet: Test Sheet" in out
    assert "found 1, new 1, filtered 0, scored 1, failed 0, skipped (cap): 0" in out
