"""Read and append the Jobs tab. The only job_scanner module that touches the sheet."""

from job_scanner.url_utils import normalize_url
from shared import sheets

_TAB = "Jobs"
_URL_RANGE = "J2:J"


def get_existing_urls(sheet_id: str) -> set[str]:
    rows = sheets.read_rows(sheet_id, _TAB, _URL_RANGE)
    return {normalize_url(str(row[0])) for row in rows if row and str(row[0]).strip()}


def append_jobs(sheet_id: str, rows: list[list]) -> None:
    sheets.append_rows(sheet_id, _TAB, rows)


def get_sheet_title(sheet_id: str) -> str:
    return sheets.get_sheet_title(sheet_id)
