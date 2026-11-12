"""Committed native-GCC plugin rows (``tests/fixtures/native_gcc/``), loaded for a test.

The rows were captured with the header path replaced by the placeholder
``@HEADER@``. A test writes the header into its own temporary directory and
loads the rows with that path put back. The substitution is done on the
*parsed* rows, never on the JSON text: pasting a path into JSON text breaks
the moment the path holds a character JSON escapes (every Windows path has
backslashes), which is how ``test_native_gcc_protocol`` failed on
windows-latest while its sibling, which escaped the path by hand, passed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "native_gcc"
HEADER_PLACEHOLDER = "@HEADER@"


def _substitute(value: Any, header: str) -> Any:
    if isinstance(value, str):
        return value.replace(HEADER_PLACEHOLDER, header)
    if isinstance(value, list):
        return [_substitute(v, header) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, header) for k, v in value.items()}
    return value


def substitute_header(rows: Any, header: str | Path) -> Any:
    """*rows* with every ``@HEADER@`` in a string value replaced by *header*."""
    return _substitute(rows, str(header))


def fixture_rows(name: str, header: str | Path) -> list[dict[str, Any]]:
    """The ``rows_<name>.json`` fixture, with its header placeholder set to *header*."""
    text = (FIXTURES / f"rows_{name}.json").read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = substitute_header(json.loads(text), header)
    return rows


def fixture_header(tmp_path: Path, name: str) -> Path:
    """Write the fixture's header (``c.h`` or ``cpp.h``) into *tmp_path*."""
    header = tmp_path / ("c.h" if name == "c" else "cpp.h")
    header.write_text(
        (FIXTURES / header.name).read_text(encoding="utf-8"), encoding="utf-8"
    )
    return header
