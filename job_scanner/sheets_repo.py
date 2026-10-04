"""Read and append the Jobs tab. The only job_scanner module that touches the sheet.

Columns are located by header name (row 1), so the sheet's column order is free to change.
"""

from job_scanner.url_utils import normalize_url
from shared import sheets

_TAB = "Jobs"
_HEADER_RANGE = "1:1"

JOBS_HEADERS = (
    "applied", "match_score", "url", "platform", "title", "company", "location",
    "visa_mention", "jd_source", "core_requirements", "reason", "posted_date",
    "date_found", "source_query",
)


class MissingHeadersError(RuntimeError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            f"Jobs tab header row is missing column(s): {', '.join(missing)}; nothing written"
        )


def header_positions(header_row: list) -> dict[str, int]:
    """Trimmed, lowercased header name -> 0-based column index (first occurrence wins)."""
    positions = {}
    for index, cell in enumerate(header_row):
        name = str(cell).strip().lower()
        if name and name not in positions:
            positions[name] = index
    return positions


def column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def get_columns(sheet_id: str) -> dict[str, int]:
    """Reads the Jobs header row; raises MissingHeadersError unless all JOBS_HEADERS exist."""
    rows = sheets.read_rows(sheet_id, _TAB, _HEADER_RANGE)
    positions = header_positions(rows[0] if rows else [])
    missing = [name for name in JOBS_HEADERS if name not in positions]
    if missing:
        raise MissingHeadersError(missing)
    return {name: positions[name] for name in JOBS_HEADERS}


def get_existing_urls(sheet_id: str, columns: dict[str, int]) -> set[str]:
    letter = column_letter(columns["url"])
    rows = sheets.read_rows(sheet_id, _TAB, f"{letter}2:{letter}")
    return {normalize_url(str(row[0])) for row in rows if row and str(row[0]).strip()}


def append_jobs(sheet_id: str, rows: list[dict], columns: dict[str, int]) -> None:
    width = max(columns.values()) + 1
    values = []
    for row in rows:
        cells = [""] * width
        for name, index in columns.items():
            cells[index] = row.get(name, "")
        values.append(cells)
    sheets.append_rows(sheet_id, _TAB, values)


def get_sheet_title(sheet_id: str) -> str:
    return sheets.get_sheet_title(sheet_id)
