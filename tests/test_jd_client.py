import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from job_scanner import jd_client
from job_scanner.jd_client import fetch_jd

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://example.com/jobs/123"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _response(
    text: str, status: int = 200, content_type: str = "text/html; charset=utf-8"
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.text = text
    resp.headers = {"Content-Type": content_type}
    return resp


def _fetch(response=None, side_effect=None, min_chars: int = 500):
    with patch.object(
        jd_client.requests, "get", return_value=response, side_effect=side_effect
    ) as get:
        result = fetch_jd(URL, timeout=15, min_chars=min_chars)
    return result, get


def test_full_page_returns_text_and_full():
    (text, source), get = _fetch(_response(_fixture("jd_fetch_success.html")))

    assert source == "full"
    assert len(text) >= 500
    assert "Full-Stack Software Engineer, Reinforcement Learning" in text
    assert "About the Role" in text
    assert "\n\n" not in text


def test_single_get_with_browser_user_agent_and_timeout():
    _, get = _fetch(_response(_fixture("jd_fetch_success.html")))

    get.assert_called_once()
    args, kwargs = get.call_args
    assert args[0] == URL
    assert kwargs["timeout"] == 15
    assert kwargs["headers"]["User-Agent"].startswith("Mozilla/5.0")


def test_script_and_style_removed():
    (text, source), _ = _fetch(_response(_fixture("jd_fetch_success.html")))

    assert source == "full"
    assert "__remixContext" not in text
    assert "window.ENV" not in text
    assert "remix-css" not in text
    assert "@font-face" not in text


def test_inline_script_style_noscript_svg_template_removed():
    body = "Visible job description line. " * 30
    markup = (
        "<html><head><style>.x{color:red}</style>"
        "<script>var secretScript = 1;</script></head>"
        f"<body><p>{body}</p>"
        "<noscript>Enable JavaScript please</noscript>"
        "<svg><text>svg label text</text></svg>"
        "<template><p>template text</p></template>"
        "</body></html>"
    )
    (text, source), _ = _fetch(_response(markup))

    assert source == "full"
    assert "Visible job description line." in text
    for leaked in ("color:red", "secretScript", "Enable JavaScript", "svg label", "template text"):
        assert leaked not in text


def test_blank_lines_collapsed():
    markup = "<div>Line one</div>\n\n\n<div>   </div><p>\n\nLine two</p>" + (
        "<p>filler text here</p>" * 40
    )
    (text, _), _ = _fetch(_response(markup))

    assert text.startswith("Line one\nLine two\n")
    assert "\n\n" not in text
    assert all(line == line.strip() and line for line in text.splitlines())


def test_js_only_page_returns_snippet(caplog):
    with caplog.at_level(logging.WARNING):
        result, _ = _fetch(_response(_fixture("jd_fetch_empty.html")))

    assert result == (None, "snippet")
    assert URL in caplog.text


def test_below_threshold_returns_snippet(caplog):
    markup = "<html><body><p>" + "x" * 499 + "</p></body></html>"
    with caplog.at_level(logging.WARNING):
        result, _ = _fetch(_response(markup), min_chars=500)

    assert result == (None, "snippet")
    assert URL in caplog.text
    assert "499" in caplog.text


def test_at_threshold_returns_full():
    markup = "<html><body><p>" + "x" * 500 + "</p></body></html>"
    (text, source), _ = _fetch(_response(markup), min_chars=500)

    assert source == "full"
    assert len(text) == 500


def test_non_2xx_returns_snippet(caplog):
    with caplog.at_level(logging.WARNING):
        result, _ = _fetch(_response(_fixture("jd_fetch_success.html"), status=403))

    assert result == (None, "snippet")
    assert URL in caplog.text
    assert "403" in caplog.text


def test_timeout_returns_snippet(caplog):
    with caplog.at_level(logging.WARNING):
        result, _ = _fetch(side_effect=requests.Timeout("read timed out"))

    assert result == (None, "snippet")
    assert URL in caplog.text
    assert "read timed out" in caplog.text


def test_connection_error_returns_snippet():
    result, _ = _fetch(side_effect=requests.ConnectionError("refused"))

    assert result == (None, "snippet")


def test_non_html_content_type_returns_snippet(caplog):
    with caplog.at_level(logging.WARNING):
        result, _ = _fetch(
            _response('{"description": "' + "x" * 1000 + '"}', content_type="application/json")
        )

    assert result == (None, "snippet")
    assert URL in caplog.text
    assert "application/json" in caplog.text


def test_missing_content_type_returns_snippet():
    resp = _response(_fixture("jd_fetch_success.html"))
    resp.headers = {}
    result, _ = _fetch(resp)

    assert result == (None, "snippet")


def test_xhtml_content_type_accepted():
    (_, source), _ = _fetch(
        _response(_fixture("jd_fetch_success.html"), content_type="application/xhtml+xml")
    )

    assert source == "full"
