"""Google Sheets auth and low-level read/append primitives."""

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _service():
    # Credentials file path is read from GOOGLE_APPLICATION_CREDENTIALS.
    credentials, _ = google.auth.default(scopes=_SCOPES)
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _http_error(action: str, err: HttpError) -> RuntimeError:
    return RuntimeError(f"Google Sheets {action} failed: HTTP {err.resp.status}")


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


def read_rows(sheet_id: str, tab: str, a1_range: str) -> list[list]:
    try:
        result = (
            _service()
            .spreadsheets()
            .values()
            .get(
                spreadsheetId=sheet_id,
                range=f"'{tab}'!{a1_range}",
                valueRenderOption="UNFORMATTED_VALUE",
            )
            .execute()
        )
    except HttpError as err:
        raise _http_error("read", err) from err
    return result.get("values", [])


def append_rows(sheet_id: str, tab: str, rows: list[list]) -> None:
    try:
        (
            _service()
            .spreadsheets()
            .values()
            .append(
                spreadsheetId=sheet_id,
                range=f"'{tab}'!A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
            .execute()
        )
    except HttpError as err:
        raise _http_error("append", err) from err


def get_sheet_title(sheet_id: str) -> str:
    try:
        result = (
            _service()
            .spreadsheets()
            .get(spreadsheetId=sheet_id, fields="properties.title")
            .execute()
        )
    except HttpError as err:
        raise _http_error("get title", err) from err
    return result["properties"]["title"]
