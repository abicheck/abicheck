"""The native-GCC row fixtures load with any header path, as the OS spells it.

Bug class: a path pasted into JSON *text* breaks on a character JSON escapes.
The Windows leg failed every ``test_native_gcc_protocol`` case on
``C:\\...\\cpp.h`` (``Invalid \\escape``). The oracle here is the placeholder
count in the raw fixture text and the path string itself, not the loader.
"""

from __future__ import annotations

import json

import pytest

from tests._native_gcc_fixtures import (
    FIXTURES,
    HEADER_PLACEHOLDER,
    fixture_rows,
    substitute_header,
)

# Paths a host can hand the loader: POSIX, Windows (drive, UNC, a backslash
# before a letter JSON would read as an escape: \n, \t, \u), and characters
# JSON must escape in a string.
PATHS = [
    "/tmp/pytest-of-runner/test0/cpp.h",
    r"C:\Users\runneradmin\AppData\Local\Temp\pytest\new\tmp\cpp.h",
    r"\\server\share\unit\cpp.h",
    r"D:\a\_temp\x\u0041\cpp.h",
    '/tmp/with "quotes" and \\ backslash/cpp.h',
]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


@pytest.mark.parametrize("name", ["c", "cpp"])
@pytest.mark.parametrize("path", PATHS)
def test_every_placeholder_becomes_the_exact_path(name: str, path: str) -> None:
    raw = (FIXTURES / f"rows_{name}.json").read_text(encoding="utf-8")
    placeholders = raw.count(f'"{HEADER_PLACEHOLDER}"')
    assert placeholders, "the fixture must carry the placeholder"
    strings = _strings(fixture_rows(name, path))
    assert strings.count(path) == placeholders
    assert not [s for s in strings if HEADER_PLACEHOLDER in s]


def _restore(value: object, path: str) -> object:
    if isinstance(value, str):
        return HEADER_PLACEHOLDER if value == path else value
    if isinstance(value, list):
        return [_restore(v, path) for v in value]
    if isinstance(value, dict):
        return {k: _restore(v, path) for k, v in value.items()}
    return value


@pytest.mark.parametrize("path", PATHS)
def test_the_rows_are_otherwise_unchanged(path: str) -> None:
    raw = json.loads((FIXTURES / "rows_cpp.json").read_text(encoding="utf-8"))
    # Mapping the path back to the placeholder restores the committed rows.
    assert _restore(fixture_rows("cpp", path), path) == raw
    assert substitute_header(raw, path) != raw
