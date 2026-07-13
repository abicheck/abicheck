"""Coverage-focused unit tests for :mod:`abicheck.cli_helpers_compare`.

Exercises the compile-db → castxml flag resolver (``_resolve_build_context_flags``),
the severity resolver (``_resolve_severity``), and project-config discovery
(``discover_project_config``) with real inputs and meaningful assertions.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.cli_helpers_compare import (
    _resolve_build_context_flags,
    _resolve_severity,
    discover_project_config,
    fold_l0_hard_removals,
)
from abicheck.errors import AbicheckError
from abicheck.model import AbiSnapshot


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


# ── fold_l0_hard_removals (case97 fix) ───────────────────────────────────────


def _snap(source_path=None):
    """Build an AbiSnapshot whose source_mtime/source_size match the file on
    disk.

    Touches source_path into existence if needed, so fold_l0_hard_removals'
    identity check (recorded mtime/size == current on-disk mtime/size)
    passes by default — tests that want to exercise a *mismatch* stat the
    file themselves after construction and mutate it.
    """
    snap = AbiSnapshot(library="lib.so", version="1.0")
    snap.source_path = source_path
    if source_path is not None:
        p = Path(source_path)
        if not p.exists():
            p.touch()
        st = p.stat()
        snap.source_mtime = st.st_mtime
        snap.source_size = st.st_size
    return snap


def test_fold_l0_hard_removals_no_source_path_is_noop(monkeypatch):
    """Neither snapshot remembers a real binary — nothing to re-probe, returned as-is."""
    monkeypatch.setattr(
        "abicheck.service.resolve_input",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(_snap(), _snap(), "c++", extra)
    assert result is extra


def test_fold_l0_hard_removals_one_sided_source_path_is_noop(monkeypatch, tmp_path):
    """Only one side has a source_path — still can't do a meaningful re-probe."""
    monkeypatch.setattr(
        "abicheck.service.resolve_input",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    extra = []
    result = fold_l0_hard_removals(
        _snap(str(tmp_path / "old.so")), _snap(None), "c++", extra
    )
    assert result == []


def test_fold_l0_hard_removals_stat_failure_after_recorded_mtime_is_noop(
    monkeypatch, tmp_path
):
    """The binary existed at snapshot-dump time (source_mtime recorded) but
    is gone by compare time (deleted, moved) — the re-stat itself raises,
    which must be swallowed the same as any other unresolvable probe."""
    monkeypatch.setattr(
        "abicheck.service.resolve_input",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    old_snap = _snap(str(tmp_path / "old.so"))
    new_snap = _snap(str(tmp_path / "new.so"))
    (tmp_path / "old.so").unlink()
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(old_snap, new_snap, "c++", extra)
    assert result is extra


def test_fold_l0_hard_removals_mtime_mismatch_is_noop(monkeypatch, tmp_path):
    """The binary at source_path was modified since the snapshot was dumped
    (rebuilt in place) — folding in a probe of *that* binary would make a
    pre-dumped-snapshot compare non-reproducible, so it's declined."""
    monkeypatch.setattr(
        "abicheck.service.resolve_input",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    old_snap = _snap(str(tmp_path / "old.so"))
    new_snap = _snap(str(tmp_path / "new.so"))
    # Simulate a rebuild: the snapshot still claims the mtime it was dumped
    # at, but the file on disk has since moved on.
    old_snap.source_mtime -= 1000
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(old_snap, new_snap, "c++", extra)
    assert result is extra


def test_fold_l0_hard_removals_missing_recorded_mtime_is_noop(monkeypatch, tmp_path):
    """A snapshot predating the source_mtime field (or one hand-authored
    without it) can't be identity-checked — decline rather than trust it."""
    monkeypatch.setattr(
        "abicheck.service.resolve_input",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    old_snap = _snap(str(tmp_path / "old.so"))
    new_snap = _snap(str(tmp_path / "new.so"))
    old_snap.source_mtime = None
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(old_snap, new_snap, "c++", extra)
    assert result is extra


def test_fold_l0_hard_removals_size_mismatch_is_noop(monkeypatch, tmp_path):
    """The binary's size differs from what was recorded — mtime alone can't
    catch every rebuild (e.g. a mtime-preserving copy), so size is checked
    too."""
    monkeypatch.setattr(
        "abicheck.service.resolve_input",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    old_snap = _snap(str(tmp_path / "old.so"))
    new_snap = _snap(str(tmp_path / "new.so"))
    old_snap.source_size += 1
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(old_snap, new_snap, "c++", extra)
    assert result is extra


def test_fold_l0_hard_removals_missing_recorded_size_is_noop(monkeypatch, tmp_path):
    """A snapshot predating the source_size field can't be identity-checked
    — decline rather than trust it."""
    monkeypatch.setattr(
        "abicheck.service.resolve_input",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    old_snap = _snap(str(tmp_path / "old.so"))
    new_snap = _snap(str(tmp_path / "new.so"))
    old_snap.source_size = None
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(old_snap, new_snap, "c++", extra)
    assert result is extra


def test_fold_l0_hard_removals_mtime_mismatch_ignored_when_dump_time_epoch_recorded(
    monkeypatch, tmp_path
):
    """A snapshot dumped under SOURCE_DATE_EPOCH persists source_mtime_epoch
    so the substitution is known regardless of the *compare*-time
    environment — a dump-time epoch (CI) followed by a compare with no
    SOURCE_DATE_EPOCH at all (interactive) must not re-enable a check that
    can never pass for that permanently-substituted mtime (Codex review,
    second round). No SOURCE_DATE_EPOCH is set here at all — only the
    persisted per-snapshot flag drives the carve-out. Size still applies."""
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **kw: object())
    old_snap = _snap(str(tmp_path / "old.so"))
    new_snap = _snap(str(tmp_path / "new.so"))
    # Simulate the epoch substitution recorded at dump time: the snapshot
    # remembers both the fixed epoch value and that it *was* a substitution.
    old_snap.source_mtime = 1609459200.0
    old_snap.source_mtime_epoch = True
    new_snap.source_mtime = 1609459200.0
    new_snap.source_mtime_epoch = True
    removal = Change(
        kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
        symbol="_ZN3lib8extendedEv",
        description="ELF-only function removed",
    )
    diff = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="lib.so",
        changes=[removal],
        verdict=Verdict.BREAKING,
    )
    monkeypatch.setattr("abicheck.service.compare_snapshots", lambda *a, **kw: diff)
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(old_snap, new_snap, "c++", extra)
    assert result == extra + [removal]


def test_fold_l0_hard_removals_size_mismatch_still_noop_with_dump_time_epoch(
    monkeypatch, tmp_path
):
    """The source_mtime_epoch carve-out only relaxes the mtime side — a
    genuine size mismatch (content actually changed) must still block the
    fold-in."""
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setattr(
        "abicheck.service.resolve_input",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    old_snap = _snap(str(tmp_path / "old.so"))
    new_snap = _snap(str(tmp_path / "new.so"))
    old_snap.source_mtime = 1609459200.0
    old_snap.source_mtime_epoch = True
    new_snap.source_mtime = 1609459200.0
    new_snap.source_mtime_epoch = True
    old_snap.source_size += 1
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(old_snap, new_snap, "c++", extra)
    assert result is extra


def test_fold_l0_hard_removals_mtime_mismatch_still_noop_without_epoch_flag(
    monkeypatch, tmp_path
):
    """SOURCE_DATE_EPOCH set at *compare* time alone must not relax the
    mtime check for a snapshot that was NOT dumped under an epoch — only the
    persisted source_mtime_epoch flag drives the carve-out, not the
    compare-time environment (Codex review, second round)."""
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1609459200")
    monkeypatch.setattr(
        "abicheck.service.resolve_input",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    old_snap = _snap(str(tmp_path / "old.so"))
    new_snap = _snap(str(tmp_path / "new.so"))
    old_snap.source_mtime -= 1000
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(old_snap, new_snap, "c++", extra)
    assert result is extra


def test_fold_l0_hard_removals_resolve_failure_returns_unchanged(monkeypatch, tmp_path):
    """A source_path that can no longer be resolved (moved/missing binary) is
    swallowed — the real compare must not fail because this best-effort probe
    couldn't run."""

    def _raise(*_a, **_kw):
        raise AbicheckError("no such file")

    monkeypatch.setattr("abicheck.service.resolve_input", _raise)
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(
        _snap(str(tmp_path / "old.so")), _snap(str(tmp_path / "new.so")), "c++", extra
    )
    assert result == extra


def test_fold_l0_hard_removals_folds_elf_only_removal(monkeypatch, tmp_path):
    """The symbols-only re-probe finds a hard ELF-only removal (case97's exact
    shape) — it's folded into extra_changes."""
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **kw: object())
    removal = Change(
        kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
        symbol="_ZN3lib8extendedEv",
        description="ELF-only function removed",
    )
    unrelated = Change(
        kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="other", description=""
    )
    diff = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="lib.so",
        changes=[removal, unrelated],
        verdict=Verdict.BREAKING,
    )
    monkeypatch.setattr("abicheck.service.compare_snapshots", lambda *a, **kw: diff)
    extra = [Change(kind=ChangeKind.FUNC_ADDED, symbol="x", description="")]
    result = fold_l0_hard_removals(
        _snap(str(tmp_path / "old.so")), _snap(str(tmp_path / "new.so")), "c++", extra
    )
    assert result is not extra
    assert extra + [removal] == result
    assert unrelated not in result


def test_fold_l0_hard_removals_ignores_non_elf_only_findings(monkeypatch, tmp_path):
    """A breaking finding that isn't func_removed_elf_only is never folded in —
    this probe restores exactly one specific fact, never a general advisory dump."""
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **kw: object())
    diff = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="lib.so",
        changes=[Change(kind=ChangeKind.FUNC_REMOVED, symbol="other", description="")],
        verdict=Verdict.BREAKING,
    )
    monkeypatch.setattr("abicheck.service.compare_snapshots", lambda *a, **kw: diff)
    result = fold_l0_hard_removals(
        _snap(str(tmp_path / "old.so")), _snap(str(tmp_path / "new.so")), "c++", None
    )
    assert result == []


def test_fold_l0_hard_removals_none_extra_changes_defaults_to_empty(
    monkeypatch, tmp_path
):
    """extra_changes=None (compare's default) is treated as an empty list, not a crash."""
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **kw: object())
    removal = Change(
        kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
        symbol="gone",
        description="",
    )
    diff = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="lib.so",
        changes=[removal],
        verdict=Verdict.BREAKING,
    )
    monkeypatch.setattr("abicheck.service.compare_snapshots", lambda *a, **kw: diff)
    result = fold_l0_hard_removals(
        _snap(str(tmp_path / "old.so")), _snap(str(tmp_path / "new.so")), "c++", None
    )
    assert result == [removal]
