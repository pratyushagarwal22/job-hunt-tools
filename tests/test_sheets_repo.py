import pytest

from job_scanner import sheets_repo
from job_scanner.sheets_repo import JOBS_HEADERS, MissingHeadersError


class FakeSheets:
    def __init__(self, monkeypatch, header, url_values=()):
        self.header = header
        self.url_values = [[value] for value in url_values]
        self.reads = []
        self.appends = []
        monkeypatch.setattr(sheets_repo.sheets, "read_rows", self._read)
        monkeypatch.setattr(sheets_repo.sheets, "append_rows", self._append)

    def _read(self, sheet_id, tab, a1_range):
        assert tab == "Jobs"
        self.reads.append(a1_range)
        if a1_range == "1:1":
            return [self.header] if self.header else []
        return self.url_values

    def _append(self, sheet_id, tab, rows):
        assert tab == "Jobs"
        self.appends.append(rows)


def _row(**overrides) -> dict:
    row = {name: f"<{name}>" for name in JOBS_HEADERS}
    row.update(overrides)
    return row


def test_header_order_is_the_14_jobs_columns():
    assert JOBS_HEADERS == (
        "applied", "match_score", "url", "platform", "title", "company", "location",
        "visa_mention", "jd_source", "core_requirements", "reason", "posted_date",
        "date_found", "source_query",
    )


def test_columns_mapped_by_trimmed_case_insensitive_header_in_any_order(monkeypatch):
    shuffled = list(reversed(JOBS_HEADERS))
    header = [f"  {name.upper()} " for name in shuffled]
    FakeSheets(monkeypatch, header)

    columns = sheets_repo.get_columns("sheet")

    assert columns == {name: shuffled.index(name) for name in JOBS_HEADERS}


def test_append_writes_each_value_at_its_header_position(monkeypatch):
    header = ["notes"] + list(reversed(JOBS_HEADERS)) + ["extra"]
    fake = FakeSheets(monkeypatch, header)
    columns = sheets_repo.get_columns("sheet")

    sheets_repo.append_jobs("sheet", [_row(applied="", match_score=7.5)], columns)

    (rows,) = fake.appends
    (cells,) = rows
    assert len(cells) == len(header) - 1
    assert cells[0] == ""
    for name in JOBS_HEADERS:
        expected = {"applied": "", "match_score": 7.5}.get(name, f"<{name}>")
        assert cells[header.index(name)] == expected


def test_missing_headers_fatal_and_nothing_appended(monkeypatch):
    header = [name for name in JOBS_HEADERS if name not in ("platform", "reason")]
    fake = FakeSheets(monkeypatch, header)

    with pytest.raises(MissingHeadersError, match="platform, reason") as err:
        sheets_repo.get_columns("sheet")

    assert err.value.missing == ["platform", "reason"]
    assert fake.appends == []


def test_empty_header_row_names_every_header(monkeypatch):
    FakeSheets(monkeypatch, [])

    with pytest.raises(MissingHeadersError) as err:
        sheets_repo.get_columns("sheet")

    assert err.value.missing == list(JOBS_HEADERS)


def test_existing_urls_read_from_url_column_c(monkeypatch):
    fake = FakeSheets(
        monkeypatch, list(JOBS_HEADERS),
        url_values=["https://Example.com/a/?utm_source=x", "", "  ", "https://example.com/b"],
    )
    columns = sheets_repo.get_columns("sheet")

    urls = sheets_repo.get_existing_urls("sheet", columns)

    assert fake.reads == ["1:1", "C2:C"]
    assert urls == {"https://example.com/a", "https://example.com/b"}


def test_existing_urls_follow_moved_url_column(monkeypatch):
    header = [f"x{i}" for i in range(27)] + list(JOBS_HEADERS)
    fake = FakeSheets(monkeypatch, header)
    columns = sheets_repo.get_columns("sheet")

    sheets_repo.get_existing_urls("sheet", columns)

    assert fake.reads[-1] == "AD2:AD"


@pytest.mark.parametrize("index, letter", [(0, "A"), (2, "C"), (25, "Z"), (26, "AA"), (701, "ZZ"), (702, "AAA")])
def test_column_letter(index, letter):
    assert sheets_repo.column_letter(index) == letter
