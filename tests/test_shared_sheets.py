import pytest

from job_scanner import sheets_repo
from shared import sheets


def test_header_positions_trimmed_lowercased_first_occurrence_wins():
    header = ["  Name ", "", "EMAIL", "name", 7]
    assert sheets.header_positions(header) == {"name": 0, "email": 2, "7": 4}


def test_header_positions_empty_row():
    assert sheets.header_positions([]) == {}


@pytest.mark.parametrize("index, letter", [(0, "A"), (25, "Z"), (26, "AA"), (701, "ZZ"), (702, "AAA")])
def test_column_letter(index, letter):
    assert sheets.column_letter(index) == letter


def test_job_scanner_repo_reexports_the_shared_helpers():
    assert sheets_repo.header_positions is sheets.header_positions
    assert sheets_repo.column_letter is sheets.column_letter
