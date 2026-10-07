import pytest

from email_finder.input_file import InputEntry, parse_line, read_file


@pytest.mark.parametrize("line", ["", "   ", "\n", "# a comment", "   # indented comment"])
def test_blank_and_comment_lines_are_skipped(line):
    assert parse_line(line) is None


def test_plain_url():
    assert parse_line("https://www.linkedin.com/in/jane-doe/\n") == InputEntry(
        "https://www.linkedin.com/in/jane-doe/", ""
    )


def test_trailing_note():
    assert parse_line("  https://linkedin.com/in/jane-doe   #  recruiter, Example Corp ") == InputEntry(
        "https://linkedin.com/in/jane-doe", "recruiter, Example Corp"
    )


def test_hash_without_space_stays_in_the_url():
    assert parse_line("https://linkedin.com/in/jane-doe/#about") == InputEntry(
        "https://linkedin.com/in/jane-doe/#about", ""
    )


def test_text_without_hash_stays_in_the_url_part():
    assert parse_line("https://linkedin.com/in/jane-doe recruiter") == InputEntry(
        "https://linkedin.com/in/jane-doe recruiter", ""
    )


def test_read_file_keeps_order_and_skips_comments(tmp_path):
    path = tmp_path / "profiles.txt"
    path.write_text(
        "# header comment\n"
        "\n"
        "https://linkedin.com/in/a\n"
        "https://linkedin.com/in/b  # note b\n"
        "   \n"
        "https://linkedin.com/company/x\n"
    )
    assert read_file(path) == [
        InputEntry("https://linkedin.com/in/a", ""),
        InputEntry("https://linkedin.com/in/b", "note b"),
        InputEntry("https://linkedin.com/company/x", ""),
    ]


def test_read_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_file(tmp_path / "nope.txt")
