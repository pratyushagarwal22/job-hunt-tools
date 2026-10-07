"""Read and append the Contacts tab. The only email_finder module that touches the sheet.

Columns are located by header name (row 1), so the sheet's column order is free to change.
"""

from shared import sheets
from shared.linkedin_url import normalize_linkedin_url
from shared.sheets import column_letter, header_positions

_TAB = "Contacts"
_HEADER_RANGE = "1:1"

CONTACTS_HEADERS = (
    "contacted", "name", "company", "title", "email", "email_status", "email_source",
    "linkedin_url", "notes", "outreach_line", "date_found",
)


class MissingHeadersError(RuntimeError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            f"Contacts tab header row is missing column(s): {', '.join(missing)}; nothing written"
        )


def get_columns(sheet_id: str) -> dict[str, int]:
    """Reads the Contacts header row; raises MissingHeadersError unless all CONTACTS_HEADERS exist."""
    rows = sheets.read_rows(sheet_id, _TAB, _HEADER_RANGE)
    positions = header_positions(rows[0] if rows else [])
    missing = [name for name in CONTACTS_HEADERS if name not in positions]
    if missing:
        raise MissingHeadersError(missing)
    return {name: positions[name] for name in CONTACTS_HEADERS}


def _column_values(sheet_id: str, index: int) -> list[str]:
    letter = column_letter(index)
    rows = sheets.read_rows(sheet_id, _TAB, f"{letter}2:{letter}")
    return [str(row[0]).strip() for row in rows if row and str(row[0]).strip()]


def get_existing_keys(sheet_id: str, columns: dict[str, int]) -> tuple[set[str], set[str]]:
    """(normalized linkedin_url values, lowercase email values), blanks skipped."""
    urls = {normalize_linkedin_url(value) for value in _column_values(sheet_id, columns["linkedin_url"])}
    emails = {value.lower() for value in _column_values(sheet_id, columns["email"])}
    return urls, emails


def append_contacts(sheet_id: str, rows: list[dict], columns: dict[str, int]) -> None:
    if not rows:
        return
    width = max(columns.values()) + 1
    values = []
    for row in rows:
        cells = [""] * width
        for name, index in columns.items():
            cells[index] = row.get(name, "")
        cells[columns["contacted"]] = ""
        values.append(cells)
    sheets.append_rows(sheet_id, _TAB, values)


def get_sheet_title(sheet_id: str) -> str:
    return sheets.get_sheet_title(sheet_id)
