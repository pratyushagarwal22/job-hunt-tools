"""Input line parsing: one URL per line, `#` comment lines and blanks skipped,
optional trailing `# note` (the `#` must follow whitespace, so URL fragments stay in the URL)."""

import re
from dataclasses import dataclass
from pathlib import Path

_TRAILING_NOTE = re.compile(r"^(.*?)(?:\s+#(.*))?$")


@dataclass(frozen=True)
class InputEntry:
    url: str
    note: str


def parse_line(line: str) -> InputEntry | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    match = _TRAILING_NOTE.match(line)
    return InputEntry(match[1].strip(), (match[2] or "").strip())


def read_file(path: str | Path) -> list[InputEntry]:
    lines = Path(path).read_text().splitlines()
    return [entry for entry in map(parse_line, lines) if entry is not None]
