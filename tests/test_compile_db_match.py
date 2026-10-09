"""Unit tests for :mod:`abicheck.workflows.artifact.compile_db_match`.

The legacy ``-p``/``--build-info`` compile-database match used to live in
``cli_helpers_compare`` as a Click-raising resolver plus a silent dry-run
twin; ADR-063 Phase 1 gave it one owner in the typed dump pipeline. These are
the former CLI helper tests, ported onto that owner: ``_legacy`` is the strict
form the real run uses, ``_dry`` the non-raising form ``dump --dry-run`` uses,
and ``_announced`` the progress note ``execute_dump_request`` emits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.errors import AbicheckError
from abicheck.workflows.artifact.compile_db_match import (
    match_compile_db,
    try_match_compile_db,
)
from abicheck.workflows.dump.pipeline import _announce_compile_db_match


def _write_compile_db(directory, entries):
    db_path = directory / "compile_commands.json"
    db_path.write_text(json.dumps(entries), encoding="utf-8")
    return db_path


def _legacy(db, headers, source_filter):
    if db is None:
        return [], False
    match = match_compile_db(db, headers, source_filter)
    return list(match.tokens), match.matched


def _dry(db, headers, source_filter):
    match = try_match_compile_db(db, headers, source_filter)
    return None if match is None else match.matched


def _announced(db, headers) -> str:
    lines: list[str] = []
    _announce_compile_db_match(lines.append, db, match_compile_db(db, headers, None))
    return "\n".join(lines)


def test_match_compile_db_no_db_short_circuits():
    """With no compile db, resolver returns an empty list and matched=False
    without touching IO."""
    assert _legacy(None, (), None) == ([], False)


def test_match_compile_db_matched_header(tmp_path, capsys):
    """A header that a TU includes uses that TU's flags (build_context_for_header)."""
    src = tmp_path / "foo.cpp"
    src.write_text('#include "foo.h"\nint f() { return 0; }\n', encoding="utf-8")
    header = tmp_path / "foo.h"
    header.write_text("int f();\n", encoding="utf-8")
    inc_dir = tmp_path / "inc"
    inc_dir.mkdir()

    db = _write_compile_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": "foo.cpp",
                "arguments": [
                    "c++",
                    "-std=c++17",
                    "-DFOO=1",
                    "-I",
                    str(inc_dir),
                    "-c",
                    "foo.cpp",
                ],
            }
        ],
    )

    flags, matched = _legacy(db, (header,), None)

    # Flags derived from the matched TU: language standard, define, include path.
    assert "-std=c++17" in flags
    assert "-DFOO=1" in flags
    assert "-I" in flags
    assert matched is True
    err = _announced(db, (header,))
    assert "Build context:" in err
    assert "flags derived" in err
    # Single matched TU -> no conflict warning.
    assert "conflicting flags" not in err


def test_match_compile_db_matched_but_no_flags_still_reports_matched(
    tmp_path,
):
    """Codex review: a matched TU with no ABI-relevant flags to forward (a
    plain `cc -c src/foo.c` with no interesting defines/includes/standard)
    still derives an empty flags list, but matched must stay True -- it is
    real build-context evidence, not an absent one. This is the exact case
    dump lib.so -H api.h -p build --depth build must accept."""
    src = tmp_path / "foo.c"
    src.write_text('#include "foo.h"\nint f(void) { return 0; }\n', encoding="utf-8")
    header = tmp_path / "foo.h"
    header.write_text("int f(void);\n", encoding="utf-8")

    db = _write_compile_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": "foo.c",
                "arguments": ["cc", "-c", "foo.c"],
            }
        ],
    )

    flags, matched = _legacy(db, (header,), None)

    assert flags == []
    assert matched is True


def test_match_compile_db_union_fallback_with_conflicts(tmp_path, capsys):
    """No headers -> union fallback; conflicting defines trigger the conflict warning."""
    (tmp_path / "a.cpp").write_text("int a();\n", encoding="utf-8")
    (tmp_path / "b.cpp").write_text("int b();\n", encoding="utf-8")

    db = _write_compile_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": "a.cpp",
                "arguments": ["c++", "-std=c++17", "-DX=1", "-c", "a.cpp"],
            },
            {
                "directory": str(tmp_path),
                "file": "b.cpp",
                "arguments": ["c++", "-std=c++17", "-DX=2", "-c", "b.cpp"],
            },
        ],
    )

    # headers=() -> resolved_hdrs empty -> union fallback branch.
    flags, matched = _legacy(db, (), None)

    assert "-std=c++17" in flags
    assert matched is True
    err = _announced(db, ())
    assert "Build context:" in err
    # Conflicting -DX values across the two TUs -> has_conflicts True.
    assert "conflicting flags" in err


def test_match_compile_db_empty_db_not_matched(tmp_path):
    """A syntactically valid but empty compile_commands.json matches nothing --
    matched must be False (Codex review, the original finding this signal
    exists to fix: an empty/unusable -p database must not satisfy --depth
    build)."""
    db = _write_compile_db(tmp_path, [])
    header = tmp_path / "foo.h"
    header.write_text("int f(void);\n", encoding="utf-8")

    flags, matched = _legacy(db, (header,), None)

    assert flags == []
    assert matched is False


def test_match_compile_db_missing_db_raises(tmp_path):
    """A non-existent compile db surfaces as a ClickException (AbicheckError path)."""
    missing = tmp_path / "nope" / "compile_commands.json"
    with pytest.raises((AbicheckError, OSError)):
        _legacy(missing, (), None)


def test_match_compile_db_invalid_json_raises(tmp_path):
    """Malformed JSON in the compile db is wrapped as a ClickException."""
    db = tmp_path / "compile_commands.json"
    db.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises((AbicheckError, OSError)):
        _legacy(db, (), None)


def test_try_match_compile_db_none_without_a_db():
    """No -p/--compile-db given at all -> None, distinct from a definite
    True/False verdict, and no I/O attempted."""
    assert _dry(None, (), None) is None


def test_try_match_compile_db_true_for_matching_header(tmp_path, capsys):
    """Mirrors _resolve_build_context_flags's own matched=True case, but
    silently -- no 'Build context: ...' stderr echo (that announcement
    belongs to the real run, not a dry run)."""
    src = tmp_path / "foo.cpp"
    src.write_text('#include "foo.h"\nint f() { return 0; }\n', encoding="utf-8")
    header = tmp_path / "foo.h"
    header.write_text("int f();\n", encoding="utf-8")
    db = _write_compile_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": "foo.cpp",
                "arguments": ["c++", "-c", "foo.cpp"],
            }
        ],
    )

    assert _dry(db, (header,), None) is True
    assert capsys.readouterr().err == ""


def test_try_match_compile_db_alias_path_used_when_primary_absent(tmp_path):
    """The -p alias (second positional arg) is honored, same as
    _resolve_build_context_flags's effective_compile_db resolution."""
    header = tmp_path / "foo.h"
    header.write_text("int f(void);\n", encoding="utf-8")
    db = _write_compile_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": "foo.c",
                "arguments": ["cc", "-c", "foo.c"],
            }
        ],
    )
    assert _dry(db, (header,), None) is True


def test_try_match_compile_db_union_fallback_without_headers(tmp_path):
    """No headers -> build_context_union_fallback branch (mirrors
    _resolve_build_context_flags's identical no-headers path)."""
    db = _write_compile_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": "a.cpp",
                "arguments": ["c++", "-c", "a.cpp"],
            }
        ],
    )
    assert _dry(db, (), None) is True


def test_try_match_compile_db_false_for_empty_db(tmp_path):
    """A syntactically valid but empty compile_commands.json -- definite
    False, not raised, not None."""
    db = _write_compile_db(tmp_path, [])
    header = tmp_path / "foo.h"
    header.write_text("int f(void);\n", encoding="utf-8")
    assert _dry(db, (header,), None) is False


def test_try_match_compile_db_false_for_missing_db():
    """A missing compile database file: _resolve_build_context_flags raises
    (AbicheckError, OSError) for this, but a dry run must never raise -- it's a
    definite False (the invocation cannot succeed), not a crash."""
    assert _dry(Path("/nonexistent/compile_commands.json"), (), None) is False


def test_try_match_compile_db_false_for_malformed_json(tmp_path):
    """Malformed JSON in the compile db: folded into False, not raised."""
    db = tmp_path / "compile_commands.json"
    db.write_text("{ this is not json", encoding="utf-8")
    assert _dry(db, (), None) is False


@pytest.mark.parametrize("content", ["{ not json", "{}", '"a string"'], ids=repr)
def test_a_bad_database_is_an_operational_failure_not_a_usage_error(tmp_path, content):
    """`execute_dump_request` turns any unusable compile database into a
    `SnapshotError` (exit 1), never a bare `ValidationError` (usage, 64) --
    the exit the CLI-side resolver gave before the match moved into the
    pipeline. A missing file is covered too."""
    from unittest import mock

    from abicheck.errors import SnapshotError, ValidationError
    from abicheck.workflows.dump.pipeline import (
        DumpExecutionOptions,
        execute_dump_request,
    )

    db = tmp_path / "compile_commands.json"
    db.write_text(content, encoding="utf-8")
    resolved = mock.MagicMock()
    resolved.request.input.headers = ()
    resolved.execution_options = None
    for path in (db, tmp_path / "missing.json"):
        with pytest.raises(SnapshotError) as info:
            execute_dump_request(
                resolved, options=DumpExecutionOptions(compile_db=path)
            )
        assert not isinstance(info.value, ValidationError)
