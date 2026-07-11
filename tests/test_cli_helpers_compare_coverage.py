"""Coverage-focused unit tests for :mod:`abicheck.cli_helpers_compare`.

Exercises the compile-db → castxml flag resolver (``_resolve_build_context_flags``),
the severity resolver (``_resolve_severity``), and project-config discovery
(``discover_project_config``) with real inputs and meaningful assertions.
"""

from __future__ import annotations

import json

import click
import pytest

from abicheck.cli_helpers_compare import (
    _resolve_build_context_flags,
    _resolve_severity,
    discover_project_config,
)


def _write_compile_db(directory, entries):
    """Write a compile_commands.json into *directory* and return its path."""
    db_path = directory / "compile_commands.json"
    db_path.write_text(json.dumps(entries), encoding="utf-8")
    return db_path


def test_resolve_build_context_flags_no_db_short_circuits():
    """With no compile db, resolver returns an empty list without touching IO."""
    assert _resolve_build_context_flags(None, (), None) == []


def test_resolve_build_context_flags_matched_header(tmp_path, capsys):
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

    flags = _resolve_build_context_flags(db, (header,), None)

    # Flags derived from the matched TU: language standard, define, include path.
    assert "-std=c++17" in flags
    assert "-DFOO=1" in flags
    assert "-I" in flags
    # The "Build context: ... flags derived" note is emitted on stderr.
    err = capsys.readouterr().err
    assert "Build context:" in err
    assert "flags derived" in err
    # Single matched TU -> no conflict warning.
    assert "conflicting flags" not in err


def test_resolve_build_context_flags_union_fallback_with_conflicts(tmp_path, capsys):
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
    flags = _resolve_build_context_flags(db, (), None)

    assert "-std=c++17" in flags
    err = capsys.readouterr().err
    assert "Build context:" in err
    # Conflicting -DX values across the two TUs -> has_conflicts True.
    assert "conflicting flags" in err


def test_resolve_build_context_flags_missing_db_raises_click_exception(tmp_path):
    """A non-existent compile db surfaces as a ClickException (AbicheckError path)."""
    missing = tmp_path / "nope" / "compile_commands.json"
    with pytest.raises(click.ClickException):
        _resolve_build_context_flags(missing, (), None)


def test_resolve_build_context_flags_invalid_json_raises_click_exception(tmp_path):
    """Malformed JSON in the compile db is wrapped as a ClickException."""
    db = tmp_path / "compile_commands.json"
    db.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(click.ClickException):
        _resolve_build_context_flags(db, (), None)


def test_resolve_severity_not_explicit_when_all_none():
    """When no severity input is given, explicitly_set is False."""
    config, explicitly_set = _resolve_severity(None, None, None, None, None)
    assert explicitly_set is False
    assert config is not None


def test_resolve_severity_explicit_from_preset():
    """A preset marks severity as explicitly set."""
    config, explicitly_set = _resolve_severity("strict", None, None, None, None)
    assert explicitly_set is True
    assert config is not None


def test_resolve_severity_explicit_from_single_category():
    """A single per-category override alone marks severity as explicitly set."""
    config, explicitly_set = _resolve_severity(None, "error", None, None, None)
    assert explicitly_set is True
    assert config is not None


def test_discover_project_config_finds_in_start_dir(tmp_path):
    """A .abicheck.yml directly in the start dir is discovered (return candidate)."""
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("scope_public: true\n", encoding="utf-8")
    found = discover_project_config(start=tmp_path)
    assert found == cfg.resolve()


def test_discover_project_config_walks_up_to_parent(tmp_path):
    """Discovery walks up parents until it finds the enclosing project config."""
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("scope_public: true\n", encoding="utf-8")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    found = discover_project_config(start=nested)
    assert found == cfg.resolve()


def test_discover_project_config_returns_none_when_absent(tmp_path):
    """No config anywhere up the tree -> None."""
    nested = tmp_path / "x" / "y"
    nested.mkdir(parents=True)
    # tmp_path itself has no .abicheck.yml; guard against a real one higher up
    # by asserting the return is either None or a path outside tmp_path.
    found = discover_project_config(start=nested)
    assert found is None or not str(found).startswith(str(tmp_path.resolve()))
