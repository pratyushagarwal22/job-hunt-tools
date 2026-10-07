import pytest

from email_finder import sheets_repo
from email_finder.sheets_repo import CONTACTS_HEADERS, MissingHeadersError


class FakeSheets:
    def __init__(self, monkeypatch, header, columns=None):
        self.header = header
        self.columns = columns or {}
        self.reads = []
        self.appends = []
        monkeypatch.setattr(sheets_repo.sheets, "read_rows", self._read)
        monkeypatch.setattr(sheets_repo.sheets, "append_rows", self._append)
        monkeypatch.setattr(sheets_repo.sheets, "get_sheet_title", lambda sheet_id: "Contacts Test")

    def _read(self, sheet_id, tab, a1_range):
        assert tab == "Contacts"
        self.reads.append(a1_range)
        if a1_range == "1:1":
            return [self.header] if self.header else []
        return [[value] if value != "" else [] for value in self.columns.get(a1_range, [])]

    def _append(self, sheet_id, tab, rows):
        assert tab == "Contacts"
        self.appends.append(rows)


def _row(**overrides) -> dict:
    row = {name: f"<{name}>" for name in CONTACTS_HEADERS}
    row.update(overrides)
    return row


def test_header_order_is_the_11_contacts_columns():
    assert CONTACTS_HEADERS == (
        "contacted", "name", "company", "title", "email", "email_status", "email_source",
        "linkedin_url", "notes", "outreach_line", "date_found",
    )


def test_columns_mapped_by_trimmed_case_insensitive_header_in_any_order(monkeypatch):
    shuffled = list(reversed(CONTACTS_HEADERS))
    FakeSheets(monkeypatch, [f" {name.upper()}  " for name in shuffled])

    columns = sheets_repo.get_columns("sheet")

    assert columns == {name: shuffled.index(name) for name in CONTACTS_HEADERS}


def test_missing_headers_fatal_and_nothing_appended(monkeypatch):
    header = [name for name in CONTACTS_HEADERS if name not in ("email_source", "notes")]
    fake = FakeSheets(monkeypatch, header)

    with pytest.raises(MissingHeadersError, match="email_source, notes") as err:
        sheets_repo.get_columns("sheet")

    assert err.value.missing == ["email_source", "notes"]
    assert "Contacts" in str(err.value)
    assert fake.appends == []


def test_empty_header_row_names_every_header(monkeypatch):
    FakeSheets(monkeypatch, [])
    with pytest.raises(MissingHeadersError) as err:
        sheets_repo.get_columns("sheet")
    assert err.value.missing == list(CONTACTS_HEADERS)


def test_append_writes_each_value_at_its_header_position_with_contacted_blank(monkeypatch):
    header = ["extra"] + list(reversed(CONTACTS_HEADERS)) + ["trailing"]
    fake = FakeSheets(monkeypatch, header)
    columns = sheets_repo.get_columns("sheet")

    sheets_repo.append_contacts("sheet", [_row(contacted=True), _row(email="")], columns)

    (rows,) = fake.appends
    assert len(rows) == 2
    for cells in rows:
        assert len(cells) == len(header) - 1
        assert cells[0] == ""
        assert cells[header.index("contacted")] == ""
        assert cells[header.index("name")] == "<name>"
        assert cells[header.index("date_found")] == "<date_found>"
    assert rows[1][header.index("email")] == ""


def test_append_missing_keys_are_blank(monkeypatch):
    fake = FakeSheets(monkeypatch, list(CONTACTS_HEADERS))
    columns = sheets_repo.get_columns("sheet")

    sheets_repo.append_contacts("sheet", [{"linkedin_url": "https://linkedin.com/in/a"}], columns)

    (cells,) = fake.appends[0]
    assert cells[CONTACTS_HEADERS.index("linkedin_url")] == "https://linkedin.com/in/a"
    assert cells.count("") == len(CONTACTS_HEADERS) - 1


def test_append_with_no_rows_makes_no_call(monkeypatch):
    fake = FakeSheets(monkeypatch, list(CONTACTS_HEADERS))
    columns = sheets_repo.get_columns("sheet")
    sheets_repo.append_contacts("sheet", [], columns)
    assert fake.appends == []


def test_existing_keys_normalized_urls_and_lowercase_emails_blanks_skipped(monkeypatch):
    # linkedin_url is column H (index 7), email is column E (index 4)
    fake = FakeSheets(
        monkeypatch,
        list(CONTACTS_HEADERS),
        columns={
            "H2:H": ["https://www.LinkedIn.com/in/Jane-Doe/?x=1", "", "  ", "https://uk.linkedin.com/in/b"],
            "E2:E": [" Jane.Doe@Example.COM ", "", "b@example.org"],
        },
    )
    columns = sheets_repo.get_columns("sheet")

    urls, emails = sheets_repo.get_existing_keys("sheet", columns)

    assert urls == {"https://linkedin.com/in/jane-doe", "https://linkedin.com/in/b"}
    assert emails == {"jane.doe@example.com", "b@example.org"}
    assert set(fake.reads) == {"1:1", "H2:H", "E2:E"}


def test_get_sheet_title_passthrough(monkeypatch):
    FakeSheets(monkeypatch, list(CONTACTS_HEADERS))
    assert sheets_repo.get_sheet_title("sheet") == "Contacts Test"
