"""Targeted coverage for :mod:`abicheck.cli_dump_helpers`.

Exercises the error paths, formatting branches, and evidence-attachment
branches of the ``dump`` CLI helpers by calling the helper functions directly
with crafted arguments (rather than driving the whole CLI), so each assertion
pins a concrete return value, raised exception, or mutated-snapshot fact.

Covers the previously-uncovered lines:
- 145      ``resolve_dump_debug_format`` selector-supersedes branch (auto / explicit)
- 196-197  ``resolve_dump_compile_db`` header-requirement UsageError
- 254-257  ``handle_non_elf_dump`` ClickException passthrough vs. wrap
- 350-357  ``resolve_dump_compile_context`` pre-resolved-context verbatim return
- 465-466  ``perform_elf_dump`` parsed_with_build_context stamp
- 482      ``perform_elf_dump`` ADR-039 _attach_build_context call
- 496-510  ``perform_elf_dump`` python_ext / python_api / follow_deps branches
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from abicheck.cli_dump_helpers import (
    handle_non_elf_dump,
    perform_elf_dump,
    resolve_dump_compile_context,
    resolve_dump_compile_db,
    resolve_dump_debug_format,
)
from abicheck.errors import AbicheckError
from abicheck.model import AbiSnapshot

# ── resolve_dump_debug_format ───────────────────────────────────────────────


def test_debug_format_selector_auto_returns_none_overriding_legacy() -> None:
    """An explicit --debug-format auto returns to auto-detection (None) even when
    a legacy --btf/--ctf/--dwarf value is also present (line 145)."""
    assert resolve_dump_debug_format("auto", "btf") is None
    # Case-insensitive: uppercase AUTO also normalizes to None.
    assert resolve_dump_debug_format("AUTO", "dwarf") is None


def test_debug_format_selector_explicit_supersedes_legacy() -> None:
    """A non-auto selector value is returned verbatim, superseding the legacy flag."""
    assert resolve_dump_debug_format("dwarf", "btf") == "dwarf"
    assert resolve_dump_debug_format("ctf", None) == "ctf"


def test_debug_format_absent_selector_falls_back_to_legacy() -> None:
    """When the selector is absent the legacy flag value is used (else branch)."""
    assert resolve_dump_debug_format(None, "btf") == "btf"
    assert resolve_dump_debug_format(None, None) is None


# ── resolve_dump_compile_db ─────────────────────────────────────────────────


def test_compile_db_without_headers_raises_usage_error(tmp_path: Path) -> None:
    """A compile DB with no -H/--header is a usage error — CastXML has nothing to
    parse (lines 196-197)."""
    db = tmp_path / "compile_commands.json"
    db.write_text("[]", encoding="utf-8")
    with pytest.raises(click.UsageError, match="requires -H/--header"):
        resolve_dump_compile_db(db, None, ())


def test_compile_db_alias_resolves_and_requires_headers(tmp_path: Path) -> None:
    """The -p alias (second arg) is honored and also gated on headers."""
    db = tmp_path / "compile_commands.json"
    db.write_text("[]", encoding="utf-8")
    with pytest.raises(click.UsageError):
        resolve_dump_compile_db(None, db, ())


def test_compile_db_with_headers_returns_effective_path(tmp_path: Path) -> None:
    """With headers present the effective (primary-preferred) DB path is returned."""
    primary = tmp_path / "primary.json"
    alt = tmp_path / "alt.json"
    hdr = tmp_path / "h.h"
    for p in (primary, alt, hdr):
        p.write_text("", encoding="utf-8")
    # Primary wins over the alias.
    assert resolve_dump_compile_db(primary, alt, (hdr,)) == primary
    # Alias is used when primary is absent.
    assert resolve_dump_compile_db(None, alt, (hdr,)) == alt
    # No DB at all → None (and no header requirement to enforce).
    assert resolve_dump_compile_db(None, None, ()) is None


# ── handle_non_elf_dump error handling ──────────────────────────────────────


def _noop_stamp(snap, *, git_tag, build_id, no_git):  # noqa: ANN001, ANN202
    return None


def _record_write(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
    return None


def test_non_elf_dump_click_exception_passes_through(tmp_path: Path) -> None:
    """A click.ClickException raised by the native dumper propagates unchanged —
    it is not re-wrapped (lines 254-255)."""
    so = tmp_path / "lib.dylib"

    sentinel = click.UsageError("bad flag for native dumper")

    def _raise_click(*a, **k):  # noqa: ANN002, ANN003
        raise sentinel

    with pytest.raises(click.UsageError) as excinfo:
        handle_non_elf_dump(
            so,
            "macho",
            (),
            (),
            "1.0",
            "c++",
            None,
            False,
            None,
            None,
            False,
            None,
            _raise_click,
            _noop_stamp,
            _record_write,
        )
    assert excinfo.value is sentinel


@pytest.mark.parametrize(
    "exc",
    [AbicheckError("boom"), RuntimeError("rt"), OSError("io"), ValueError("val")],
)
def test_non_elf_dump_wraps_domain_errors(tmp_path: Path, exc: Exception) -> None:
    """AbicheckError/RuntimeError/OSError/ValueError from the native dumper are
    wrapped in a ClickException carrying the message (lines 256-257)."""
    so = tmp_path / "lib.dll"

    def _raise(*a, **k):  # noqa: ANN002, ANN003
        raise exc

    with pytest.raises(click.ClickException) as excinfo:
        handle_non_elf_dump(
            so,
            "pe",
            (),
            (),
            "1.0",
            "c++",
            None,
            False,
            None,
            None,
            False,
            None,
            _raise,
            _noop_stamp,
            _record_write,
        )
    # ClickException but NOT a UsageError (which would be the passthrough path).
    assert not isinstance(excinfo.value, click.UsageError)
    assert str(exc) in str(excinfo.value)


def test_non_elf_dump_success_stamps_and_writes(tmp_path: Path) -> None:
    """The happy path forwards the snapshot to stamp_provenance and
    write_snapshot_output with the header-backend extractor."""
    so = tmp_path / "lib.dylib"
    snap = AbiSnapshot(library="lib.dylib", version="9.9")

    calls: dict[str, object] = {}

    def _dump_native(*a, **k):  # noqa: ANN002, ANN003
        calls["dump_kwargs"] = k
        return snap

    def _stamp(s, *, git_tag, build_id, no_git):  # noqa: ANN001
        calls["stamped"] = (s, git_tag, build_id, no_git)

    def _write(s, output, build_info, sources, build_config, allow, mode, **kw):  # noqa: ANN001
        calls["written"] = (s, output, mode, kw.get("extractor"))

    handle_non_elf_dump(
        so,
        "macho",
        (),
        (),
        "3.0",
        "c++",
        None,
        False,
        "v3",
        "bid",
        True,
        tmp_path / "out.json",
        _dump_native,
        _stamp,
        _write,
        header_backend="clang",
    )
    assert calls["stamped"] == (snap, "v3", "bid", True)
    written = calls["written"]
    assert written[0] is snap
    assert written[3] == "clang"  # extractor threaded through


def test_non_elf_dump_follow_deps_warns(tmp_path: Path, capsys) -> None:
    """--follow-deps is ELF-only; the native path emits a stderr warning (line 244)."""
    so = tmp_path / "lib.dylib"
    snap = AbiSnapshot(library="lib.dylib", version="1")

    handle_non_elf_dump(
        so,
        "macho",
        (),
        (),
        "1",
        "c++",
        None,
        True,
        None,
        None,
        False,
        None,
        lambda *a, **k: snap,
        _noop_stamp,
        _record_write,
    )
    assert "--follow-deps is only supported for ELF" in capsys.readouterr().err


# ── resolve_dump_compile_context pre-resolved passthrough ───────────────────


def test_compile_context_preresolved_returned_verbatim() -> None:
    """When the caller already resolved the compile context it is returned as-is,
    with no re-discovery/re-merge of the tree's .abicheck.yml (lines 350-357)."""
    sentinel_ctx = object()
    includes = (Path("/inc/a"), Path("/inc/b"))

    ctx, out_includes = resolve_dump_compile_context(
        sentinel_ctx,  # type: ignore[arg-type]
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=True,
        header_backend="auto",
        includes=includes,
        build_config=None,
        sources=None,
    )
    assert ctx is sentinel_ctx
    assert out_includes == includes


# ── perform_elf_dump evidence-attachment branches ───────────────────────────


def _elf_dump_callables():  # noqa: ANN202
    """Return (recorder, stamp, write, expand, populate) stub callables."""
    events: dict[str, object] = {}

    def _stamp(snap, *, git_tag, build_id, no_git):  # noqa: ANN001
        events["stamped"] = True

    def _write(*a, **k):  # noqa: ANN002, ANN003
        events["written"] = True

    def _expand(inputs):  # noqa: ANN001
        return list(inputs)

    def _populate(snap, so_path, search_paths, sysroot, ld_library_path):  # noqa: ANN001
        events["populated"] = (so_path, tuple(search_paths))

    return events, _stamp, _write, _expand, _populate


def test_perform_elf_dump_stamps_build_context_and_attaches(
    tmp_path: Path, monkeypatch
) -> None:
    """With a compile DB and resolved headers, perform_elf_dump marks the snapshot
    parsed_with_build_context and runs the ADR-039 harvest (lines 465-466, 473, 482)."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "config.h"
    hdr.write_text(
        "struct Config {\n int v;\n#ifdef KEEP\n int legacy;\n#endif\n};",
        encoding="utf-8",
    )
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([{"command": "cc -DKEEP -c config.c"}]), encoding="utf-8")

    snap = AbiSnapshot(library="lib.so", version="1.0")
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: snap)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so,
        (hdr,),
        (),
        "1.0",
        "c",
        None,
        None,
        None,
        (),  # gcc_path/prefix/options/option_tokens
        None,
        True,  # sysroot, nostdinc
        False,
        None,  # dwarf_only, effective_debug_format
        (),
        (),  # public_headers, public_header_dirs
        db,  # effective_compile_db
        False,
        (),
        "",  # follow_deps, search_paths, ld_library_path
        None,
        None,
        False,  # git_tag, build_id, no_git
        None,
        None,
        None,
        None,
        False,
        "off",  # output..collect_mode
        _expand,
        _populate,
        _stamp,
        _write,
    )

    assert snap.parsed_with_build_context is True
    # ADR-039 collector saw the DB's active -DKEEP and the guarded field.
    assert snap.build_context_defines == {"KEEP"}
    assert snap.conditional_fields["Config"]["legacy"]["guard"] == "KEEP"
    assert events.get("stamped") and events.get("written")
    assert "populated" not in events  # follow_deps was False


def test_perform_elf_dump_detects_python_surfaces_and_follow_deps(
    tmp_path: Path, monkeypatch
) -> None:
    """Without a compile DB, perform_elf_dump skips the build-context stamp but
    still runs python_ext / python_api detection and, with follow_deps, the
    dependency populate callback (lines 496-510)."""
    so = tmp_path / "lib.so"

    snap = AbiSnapshot(library="lib.so", version="2.0")
    assert snap.python_ext is None and snap.python_api is None
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: snap)

    ext_sentinel = object()
    api_sentinel = object()
    monkeypatch.setattr(
        "abicheck.python_ext.detect_python_extension", lambda _s: ext_sentinel
    )
    monkeypatch.setattr(
        "abicheck.python_api.detect_python_api", lambda _s: api_sentinel
    )

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so,
        (),
        (),
        "2.0",
        "c++",
        None,
        None,
        None,
        (),
        None,
        True,
        False,
        None,
        (),
        (),
        None,  # effective_compile_db → build-context branches skipped
        True,
        (tmp_path,),
        "/lib",  # follow_deps True
        None,
        None,
        False,
        None,
        None,
        None,
        None,
        False,
        "off",
        _expand,
        _populate,
        _stamp,
        _write,
    )

    # Build-context branch skipped (no DB), python surfaces detected via helpers.
    assert snap.parsed_with_build_context is False
    assert snap.python_ext is ext_sentinel
    assert snap.python_api is api_sentinel
    # follow_deps path invoked populate_dependency_info with the search paths.
    assert events["populated"] == (so, (tmp_path,))


def test_perform_elf_dump_preserves_existing_python_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    """When the snapshot already carries python_ext/python_api, detection is not
    re-run (the ``is None`` guards on lines 496 and 505 stay false)."""
    so = tmp_path / "lib.so"

    snap = AbiSnapshot(library="lib.so", version="1")
    preexisting_ext = object()
    preexisting_api = object()
    snap.python_ext = preexisting_ext  # type: ignore[assignment]
    snap.python_api = preexisting_api  # type: ignore[assignment]
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: snap)

    def _boom(_s):  # noqa: ANN001, ANN202
        raise AssertionError("detection must not run when metadata is present")

    monkeypatch.setattr("abicheck.python_ext.detect_python_extension", _boom)
    monkeypatch.setattr("abicheck.python_api.detect_python_api", _boom)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so,
        (),
        (),
        "1",
        "c++",
        None,
        None,
        None,
        (),
        None,
        True,
        False,
        None,
        (),
        (),
        None,
        False,
        (),
        "",
        None,
        None,
        False,
        None,
        None,
        None,
        None,
        False,
        "off",
        _expand,
        _populate,
        _stamp,
        _write,
    )

    assert snap.python_ext is preexisting_ext
    assert snap.python_api is preexisting_api


def test_perform_elf_dump_wraps_dump_errors(tmp_path: Path, monkeypatch) -> None:
    """A domain error from dumper.dump is wrapped in a ClickException (line 462)."""
    so = tmp_path / "lib.so"

    def _raise(**_kw):
        raise AbicheckError("castxml exploded")

    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", _raise)
    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    with pytest.raises(click.ClickException, match="castxml exploded"):
        perform_elf_dump(
            so,
            (),
            (),
            "1",
            "c++",
            None,
            None,
            None,
            (),
            None,
            True,
            False,
            None,
            (),
            (),
            None,
            False,
            (),
            "",
            None,
            None,
            False,
            None,
            None,
            None,
            None,
            False,
            "off",
            _expand,
            _populate,
            _stamp,
            _write,
        )
    assert "written" not in events
