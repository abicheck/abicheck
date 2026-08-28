"""Targeted coverage for :mod:`abicheck.cli_dump_helpers`.

Exercises the error paths, formatting branches, and evidence-attachment
branches of the ``dump`` CLI helpers by calling the helper functions directly
with crafted arguments (rather than driving the whole CLI), so each assertion
pins a concrete return value, raised exception, or mutated-snapshot fact.

Covers the previously-uncovered lines:
- 145      ``resolve_dump_debug_format`` selector-supersedes branch (auto / explicit)
- ``compile_db_from_build_info`` derivation of the L2 database from --build-info
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
    _dump_manifest_header_roots,
    check_dump_debug_format_error,
    compile_db_from_build_info,
    perform_elf_dump,
    resolve_dump_collect_context,
    resolve_dump_compile_context,
    resolve_dump_debug_format,
)
from abicheck.cli_dump_non_elf import handle_non_elf_dump
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


# ── compile_db_from_build_info ──────────────────────────────────────────────
# The L2 compile database is derived from --build-info (the removed
# -p/--build-dir and --compile-db flags named the same operand a second time).


def test_compile_db_from_build_info_reads_a_file_operand(tmp_path: Path) -> None:
    """--build-info naming the database file itself is that database."""
    db = tmp_path / "compile_commands.json"
    db.write_text("[]", encoding="utf-8")
    hdr = tmp_path / "h.h"
    hdr.write_text("", encoding="utf-8")

    assert compile_db_from_build_info(db, (hdr,)) == db


def test_compile_db_from_build_info_reads_a_build_directory(tmp_path: Path) -> None:
    """--build-info naming a build directory finds its compile_commands.json."""
    build = tmp_path / "build"
    build.mkdir()
    db = build / "compile_commands.json"
    db.write_text("[]", encoding="utf-8")
    hdr = tmp_path / "h.h"
    hdr.write_text("", encoding="utf-8")

    assert compile_db_from_build_info(build, (hdr,)) == db


def test_compile_db_from_build_info_is_none_for_a_pack_directory(
    tmp_path: Path,
) -> None:
    """A pre-captured pack carries no compile database to parameterize L2 with."""
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "manifest.json").write_text("{}", encoding="utf-8")
    hdr = tmp_path / "h.h"
    hdr.write_text("", encoding="utf-8")

    assert compile_db_from_build_info(pack, (hdr,)) is None


def test_compile_db_from_build_info_is_none_without_headers(tmp_path: Path) -> None:
    """A compile database only parameterizes a header parse.

    Without -H/--header there is nothing for it to parameterize, so a
    headerless ``--build-info`` run is an ordinary L3-only dump -- not the
    "requires -H/--header" usage error the removed dedicated flag raised.
    """
    db = tmp_path / "compile_commands.json"
    db.write_text("[]", encoding="utf-8")

    assert compile_db_from_build_info(db, ()) is None
    assert compile_db_from_build_info(None, ()) is None


# ── check_dump_debug_format_error ──────────────────────────────────────────
# Pure predicate factored out of the debug-format BadParameter check so
# `dump --dry-run` reports the same condition instead of missing it entirely
# (it previously only ran in the real path, after the dry-run branch returned).


def test_check_debug_format_error_only_for_pe_macho() -> None:
    assert check_dump_debug_format_error("dwarf", "pe") == (
        "--dwarf is only supported for ELF binaries, not PE."
    )
    assert check_dump_debug_format_error("btf", "macho") == (
        "--btf is only supported for ELF binaries, not MACHO."
    )
    assert check_dump_debug_format_error("dwarf", "elf") is None
    assert check_dump_debug_format_error(None, "pe") is None
    assert check_dump_debug_format_error(None, None) is None


# ── resolve_dump_collect_context ────────────────────────────────────────────


def test_collect_context_warns_without_any_build_or_source_input(
    tmp_path: Path, capsys
) -> None:
    """An explicit deep --depth with no --sources/--build-info collects nothing,
    so it warns loudly rather than silently writing an L0-L2 snapshot."""
    hdr = tmp_path / "h.h"
    build = tmp_path / "build"

    resolve_dump_collect_context("build", None, None, build, (hdr,))
    assert "only L0-L2 data" not in capsys.readouterr().err

    resolve_dump_collect_context("build", None, None, None, (hdr,))
    assert "only L0-L2 data" in capsys.readouterr().err


def test_collect_context_binary_depth_drops_the_headers(tmp_path: Path) -> None:
    """--depth binary suppresses the L2 header AST, and with it the compile
    database the caller derives from those headers."""
    hdr = tmp_path / "h.h"

    mode, headers = resolve_dump_collect_context("binary", None, None, None, (hdr,))
    assert headers == ()
    assert compile_db_from_build_info(tmp_path, headers) is None
    assert mode is not None


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


def test_non_elf_dump_no_longer_threads_header_graph_kwargs(tmp_path: Path) -> None:
    """G29 Phase A: handle_non_elf_dump no longer takes/forwards
    header_graph/header_graph_includes at all — dump_native_binary
    (-> service.run_dump) always attaches the header-only graph uniformly
    across ELF/PE/Mach-O now, so there is nothing left to thread through
    this call. Regression for the pre-Phase-A bug where only the ELF path
    forwarded the opt-in flag and PE/Mach-O silently no-opped (Codex
    review); that class of bug can no longer occur."""
    so = tmp_path / "lib.dylib"
    snap = AbiSnapshot(library="lib.dylib", version="1.0")

    calls: dict[str, object] = {}

    def _dump_native(*a, **k):  # noqa: ANN002, ANN003
        calls["dump_kwargs"] = k
        return snap

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
        _dump_native,
        _noop_stamp,
        _record_write,
    )
    kwargs = calls["dump_kwargs"]
    assert "header_graph" not in kwargs
    assert "header_graph_includes" not in kwargs


def test_non_elf_dump_stamps_build_context_when_compile_db_matched(
    tmp_path: Path,
) -> None:
    """Codex review: a -p/--compile-db match was never threaded into the
    PE/Mach-O path at all -- snap.parsed_with_build_context stayed False
    even when cli.py's _resolve_build_context_flags found a real match, so
    `dump foo.dll -H api.h -p build --depth build` was wrongly rejected as
    only reaching "headers". Mirrors perform_elf_dump's identical stamp.
    from_headers=True here represents a genuine header-scoped dump (as
    opposed to service._try_header_scoped_dump()'s export-table fallback,
    covered separately below)."""
    so = tmp_path / "lib.dll"
    snap = AbiSnapshot(library="lib.dll", version="1.0", from_headers=True)
    header = tmp_path / "api.h"
    header.write_text("void f(void);\n", encoding="utf-8")

    def _dump_native(*a, **k):  # noqa: ANN002, ANN003
        return snap

    handle_non_elf_dump(
        so,
        "pe",
        (header,),
        (),
        "1.0",
        "c++",
        None,
        False,
        None,
        None,
        False,
        None,
        _dump_native,
        _noop_stamp,
        _record_write,
        compile_db_context_matched=True,
    )
    assert snap.parsed_with_build_context is True


def test_non_elf_dump_does_not_stamp_build_context_when_compile_db_unmatched(
    tmp_path: Path,
) -> None:
    so = tmp_path / "lib.dll"
    snap = AbiSnapshot(library="lib.dll", version="1.0")
    header = tmp_path / "api.h"
    header.write_text("void f(void);\n", encoding="utf-8")

    def _dump_native(*a, **k):  # noqa: ANN002, ANN003
        return snap

    handle_non_elf_dump(
        so,
        "pe",
        (header,),
        (),
        "1.0",
        "c++",
        None,
        False,
        None,
        None,
        False,
        None,
        _dump_native,
        _noop_stamp,
        _record_write,
        compile_db_context_matched=False,
    )
    assert snap.parsed_with_build_context is False


def test_non_elf_dump_does_not_stamp_build_context_on_mangling_fallback(
    tmp_path: Path,
) -> None:
    """Codex review: service._try_header_scoped_dump() can silently fall back
    to a fresh export-table-only snapshot (from_headers=False, scope_fallback
    set) when the parsed headers don't match any exported symbol -- e.g. an
    MSVC-mangled C++ DLL parsed with a mismatched compiler. The *request*
    still had headers and a genuinely matched compile DB, but the snapshot
    that was actually written never used either; stamping build-context
    evidence on it would let `--depth build` wrongly accept a plain
    export-table dump."""
    so = tmp_path / "lib.dll"
    snap = AbiSnapshot(
        library="lib.dll", version="1.0", from_headers=False, scope_fallback="mangling-fallback"
    )
    header = tmp_path / "api.h"
    header.write_text("void f(void);\n", encoding="utf-8")

    def _dump_native(*a, **k):  # noqa: ANN002, ANN003
        return snap

    handle_non_elf_dump(
        so,
        "pe",
        (header,),
        (),
        "1.0",
        "c++",
        None,
        False,
        None,
        None,
        False,
        None,
        _dump_native,
        _noop_stamp,
        _record_write,
        compile_db_context_matched=True,
    )
    assert snap.parsed_with_build_context is False


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
        gcc_options=None,
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


_CC_FIELDS = (
    "gcc_path", "gcc_prefix", "gcc_options", "gcc_option_tokens",
    "sysroot", "nostdinc", "frontend", "frontend_context",
)


def _fake_seed_and_fold(seeded_dirs, on_cleanup=None):  # noqa: ANN001, ANN202
    """Stand-in for ``seed_includes_and_fold_compile_context`` -- mirrors its
    ``(includes, l3_context_applied, context, dirs)`` return shape and pushes
    a pending cleanup, for tests that only care about the include-dir seed
    half (no real L3 fold); replaces the pre-merge ``seed_l2_includes`` fakes."""
    from abicheck.compile_context import CompileContext

    def _fake(**kwargs):  # noqa: ANN003
        if on_cleanup is not None:
            kwargs["pending_cleanups"].append(on_cleanup)
        ctx = CompileContext(**{k: kwargs[k] for k in _CC_FIELDS})
        return list(seeded_dirs), False, ctx, ()

    return _fake


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

    snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
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
        compile_db_context_matched=True,
    )

    assert snap.parsed_with_build_context is True
    # ADR-039 collector saw the DB's active -DKEEP and the guarded field.
    assert snap.build_context_defines == {"KEEP"}
    assert snap.conditional_fields["Config"]["legacy"]["guard"] == "KEEP"
    assert events.get("stamped") and events.get("written")
    assert "populated" not in events  # follow_deps was False


def test_perform_elf_dump_folds_l3_compile_context_into_header_parse(
    tmp_path: Path, monkeypatch
) -> None:
    """P0.3 L3->L2 fold (AGENTS.md's former "The native ELF `abicheck dump`
    path never applies L3 build context to its own L2 header parse" known
    gap, closed here): a --sources dump whose compile database resolves a
    real -std=/-D for these headers must fold those ABI-relevant flags into
    the *primary* header-AST parse (dump()'s own gcc_option_tokens), not
    just the L2 include-dir fallback seed_l2_includes already provides --
    and must stamp snapshot.parsed_with_build_context accordingly, the same
    stamp resolve_side_snapshot already applies for compare/scan's
    implicit-dump path. Without this fix a dump-produced baseline and a
    scan/compare candidate of the same project resolved under genuinely
    different extraction recipes (profile_fingerprint mismatch on
    include_sequence/language_standard) and `scan --against` refused the
    comparison as NOT_COMPARABLE for reasons neither command's own
    diagnostics named."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{
            "directory": str(tmp_path),
            "file": str(src),
            "arguments": ["c++", "-c", str(src), "-o", "out.o", "-std=c++20", "-DFOO=1"],
        }]),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def _dump_stub(**kw):  # noqa: ANN003
        captured["gcc_option_tokens"] = kw["gcc_option_tokens"]
        return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", _dump_stub)
    monkeypatch.setattr("abicheck.service._attach_header_graph", lambda snap, *_a, **_k: snap)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()
    snap_holder: dict[str, object] = {}

    def _write_and_capture(snap, *a, **k):  # noqa: ANN001, ANN002, ANN003
        snap_holder["snap"] = snap
        _write(snap, *a, **k)

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c++", None, None, None,
        (),  # gcc_path/prefix/options/option_tokens
        None, False,  # sysroot, nostdinc
        False, None,  # dwarf_only, effective_debug_format
        (), (),  # public_headers, public_header_dirs
        None,  # effective_compile_db -- the OLD -p/--compile-db mechanism, unused here
        False, (), "",  # follow_deps, search_paths, ld_library_path
        None, None, False,  # git_tag, build_id, no_git
        None, None,
        tmp_path,  # build_info=None, sources=tmp_path (auto-discovers compile_commands.json)
        None, False,
        "source-target",  # build_config, allow_build_query, collect_mode
        _expand, _populate, _stamp, _write_and_capture,
    )

    tokens = captured["gcc_option_tokens"]
    assert "-std=c++20" in tokens
    assert "-DFOO=1" in tokens
    written = snap_holder["snap"]
    assert written.parsed_with_build_context is True


def test_perform_elf_dump_hashes_derived_include_dirs_into_ast_cache_key(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review, PR #782: a derived -I/-isystem dir reaches the header
    parse only as an opaque gcc_option_tokens string, which extra_includes'
    own directory-mtime hashing never inspects -- without folding it into
    extra_hash_dirs too, editing a header under that dir would reuse a
    stale cached AST."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    build_inc = tmp_path / "buildinc"
    build_inc.mkdir()
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{
            "directory": str(tmp_path),
            "file": str(src),
            "arguments": [
                "c++", "-c", str(src), "-o", "out.o", "-I", str(build_inc),
            ],
        }]),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def _dump_stub(**kw):  # noqa: ANN003
        captured["extra_hash_dirs"] = kw["extra_hash_dirs"]
        return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", _dump_stub)
    monkeypatch.setattr("abicheck.service._attach_header_graph", lambda snap, *_a, **_k: snap)
    # The seed and fold now share one collect_inline_pack() call (Codex
    # review, PR #782) and can no longer be disabled independently -- this
    # test still asserts build_inc lands in extra_hash_dirs specifically
    # (not just extra_includes), carried there via the derived compile-
    # context's own include-operand extraction.

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c++", None, None, None,
        (),  # gcc_path/prefix/options/option_tokens
        None, False,  # sysroot, nostdinc
        False, None,  # dwarf_only, effective_debug_format
        (), (),  # public_headers, public_header_dirs
        None,  # effective_compile_db
        False, (), "",  # follow_deps, search_paths, ld_library_path
        None, None, False,  # git_tag, build_id, no_git
        None, None,
        tmp_path,  # build_info=None, sources=tmp_path
        None, False,
        "source-target",  # build_config, allow_build_query, collect_mode
        _expand, _populate, _stamp, _write,
    )

    assert build_inc in captured["extra_hash_dirs"]


def test_perform_elf_dump_scopes_primary_dump_for_ast_memo_reuse(
    tmp_path: Path, monkeypatch
) -> None:
    """G31 Phase C / Codex review: perform_elf_dump's own primary ``dump()``
    call must run inside ``dumper_cache.ast_memoize_scope()`` so a clang
    primary parse is handed off in-process to the later
    ``_attach_header_graph`` pass -- this ELF ``dump`` CLI path reaches
    ``dumper.dump`` directly, not ``service.run_dump``, so without its own
    scope here the memo is never populated and the graph pass re-reads/
    re-parses the disk cache instead."""
    from abicheck import dumper_cache

    so = tmp_path / "lib.so"
    hdr = tmp_path / "config.h"
    hdr.write_text("struct Config { int v; };", encoding="utf-8")

    seen_active: dict[str, bool] = {}

    def _dump_stub(**_kw):  # noqa: ANN003
        seen_active["active"] = dumper_cache.ast_memoize_active()
        return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", _dump_stub)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

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
        None,  # effective_compile_db
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
        compile_db_context_matched=False,
    )

    assert seen_active["active"] is True
    assert dumper_cache.ast_memoize_active() is False


def test_perform_elf_dump_folds_header_dir_into_scope_header_dirs(
    tmp_path: Path, monkeypatch
) -> None:
    """G30 pilot validation regression: a directory passed via -H/--header must
    be threaded through to dump()'s scope_header_dirs (via split_public_
    header_inputs, the same helper `compare` uses) -- not just its expanded
    per-file listing -- so the resulting contract's scope_fingerprint agrees
    with a live compare-side extraction of the identical header set."""
    so = tmp_path / "lib.so"
    include_dir = tmp_path / "include"
    include_dir.mkdir()
    hdr = include_dir / "api.h"
    hdr.write_text("int api(void);", encoding="utf-8")

    captured: dict[str, object] = {}

    def _fake_dump(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", _fake_dump)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so,
        (include_dir,),
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
        None,  # effective_compile_db
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
        compile_db_context_matched=False,
    )

    assert captured["scope_header_dirs"] == [include_dir]
    # No --public-header-dir was given -- provenance-tagging inputs stay
    # untouched (ADR-015 opt-in unaffected).
    assert captured["public_header_dirs"] == []


def test_perform_elf_dump_does_not_stamp_build_context_for_dwarf_only(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review: --dwarf-only explicitly ignores -H headers
    (dumper._try_dwarf_snapshot warns "ignoring provided headers" and returns
    a DWARF-built snapshot with from_headers left False) -- a -p compile
    database matching the originally *requested* headers must not be
    recorded as real build-context evidence for a snapshot that never
    actually parsed them, even though compile_db_context_matched is True
    (mirrors handle_non_elf_dump's identical from_headers gate)."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "config.h"
    hdr.write_text("struct Config {\n int v;\n};", encoding="utf-8")
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([{"command": "cc -DKEEP -c config.c"}]), encoding="utf-8")

    snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=False)
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
        True,  # dwarf_only
        None,  # effective_debug_format
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
        compile_db_context_matched=True,
    )

    assert snap.parsed_with_build_context is False


def test_perform_elf_dump_does_not_stamp_build_context_when_db_unmatched(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review: a -p/--compile-db path that is present (effective_compile_db
    is not None) but derived no usable castxml flags (compile_db_context_matched
    is False -- e.g. an empty or non-matching compile_commands.json) must NOT
    stamp parsed_with_build_context, since evidence_depth_label/
    check_requested_depth_satisfied read that flag as genuine "build" evidence
    for the strict --depth build gate. Otherwise a --compile-db pointing at an
    unusable DB would silently satisfy --depth build with zero real facts."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "config.h"
    hdr.write_text("struct Config {\n int v;\n};", encoding="utf-8")
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([]), encoding="utf-8")  # syntactically valid, empty

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
        compile_db_context_matched=False,
    )

    assert snap.parsed_with_build_context is False


def test_perform_elf_dump_attaches_header_graph_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    """ADR-041 addendum / G29 Phase A: perform_elf_dump always calls
    service._attach_header_graph (the same wrapper service.run_dump uses for
    `compare`'s implicit-dump path) with the raw headers, the L2-seeded
    includes (see test_perform_elf_dump_header_graph_receives_seeded_includes
    for the seeded-vs-raw distinction), compile_context/public_headers/
    public_header_dirs it was given, and writes the wrapper's returned
    (possibly different) snapshot object. The context this call receives is
    the fully-merged ``l3_effective_ctx`` (P0.3 fold, AGENTS.md's former
    "dump path never applies L3 build context" known gap) built from the
    actual resolved parse parameters -- gcc_path/gcc_prefix/gcc_options/
    gcc_option_tokens/sysroot/nostdinc/frontend, plus any L3-derived
    context when --sources/--build-info is given (not here: no sources) --
    not the caller-supplied ``compile_context`` object forwarded unmodified.
    That is a deliberate correction, not a regression: the old
    gcc_options-string-only comparison could silently forward a
    ``compile_context`` whose own nostdinc/sysroot disagreed with what the
    primary dump() call actually used (this test's own ``nostdinc=True``
    positional argument against ``sentinel_cc``'s default ``nostdinc=False``
    is exactly that latent inconsistency) — see
    test_perform_elf_dump_header_graph_gets_compile_db_flags for the
    -p/--compile-db case.

    ``lang`` here is "c++" with ``lang_explicit`` left at its default
    (``False``) — i.e. Click's own ``LANG_DEFAULT``, not a genuine
    ``--lang c++`` from the user (G31 Phase C follow-up). The header-graph
    attach must therefore receive the same squashed-to-``None`` value the
    primary snapshot pass uses (auto-detection), not the raw "c++" string —
    see test_perform_elf_dump_explicit_lang_reaches_header_graph for the
    explicit-request counterpart."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")

    plain_snap = AbiSnapshot(library="lib.so", version="1.0")
    graphed_snap = AbiSnapshot(library="lib.so", version="1.0")
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: plain_snap)

    captured: dict[str, object] = {}

    def fake_attach(
        snap,
        header_graph,
        header_graph_includes,
        headers,
        includes,
        lang,
        compile_context,
        public_headers,
        public_header_dirs, include_search_dirs=None,
    ):
        captured["snap"] = snap
        captured["header_graph"] = header_graph
        captured["header_graph_includes"] = header_graph_includes
        captured["headers"] = headers
        captured["includes"] = includes
        captured["lang"] = lang
        captured["compile_context"] = compile_context
        captured["public_headers"] = public_headers
        captured["public_header_dirs"] = public_header_dirs
        return graphed_snap

    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    def _write_and_capture(snap, *a, **k):  # noqa: ANN001, ANN002, ANN003
        captured["written_snap"] = snap
        _write(snap, *a, **k)

    from abicheck.service_scan import CompileContext

    sentinel_cc = CompileContext()

    perform_elf_dump(
        so,
        (hdr,),
        (),
        "1.0",
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
        _write_and_capture,
        compile_context=sentinel_cc,
    )

    assert captured["header_graph"] is True
    assert captured["header_graph_includes"] is True
    assert captured["headers"] == [hdr]
    assert captured["lang"] is None
    # Not `is sentinel_cc` -- the effective context is freshly built from the
    # actual resolved parse parameters (P0.3 fold), which is what fixes the
    # latent nostdinc inconsistency: this call's own positional nostdinc=True
    # must reach the header-graph pass even though sentinel_cc itself defaults
    # nostdinc=False.
    from abicheck.compile_context import CompileContext as _CC

    assert captured["compile_context"] == _CC(nostdinc=True)
    assert captured["snap"] is plain_snap
    # The wrapper's returned snapshot (not the original) is what gets written.
    assert captured["written_snap"] is graphed_snap


def test_perform_elf_dump_explicit_lang_reaches_header_graph(
    tmp_path: Path, monkeypatch
) -> None:
    """G31 Phase C follow-up (AGENTS.md "dump --lang c++ is silently
    discarded ..." known gap): a genuinely explicit ``--lang c++``
    (``lang_explicit=True``) must reach BOTH the primary snapshot pass and
    the header-graph attach as the real "c++" value, not get squashed to
    ``None`` on the primary pass while only the header-graph pass sees it
    (the exact divergence the gap named) or vice versa."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")

    plain_snap = AbiSnapshot(library="lib.so", version="1.0")
    captured_dump: dict[str, object] = {}

    def fake_dump(**kwargs):  # noqa: ANN001, ANN003
        captured_dump["lang"] = kwargs.get("lang")
        return plain_snap

    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", fake_dump)

    captured_graph: dict[str, object] = {}

    def fake_attach(
        snap, header_graph, header_graph_includes, headers, includes,
        lang, compile_context, public_headers, public_header_dirs, include_search_dirs=None,
    ):
        captured_graph["lang"] = lang
        return snap

    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    from abicheck.service_scan import CompileContext

    perform_elf_dump(
        so,
        (hdr,),
        (),
        "1.0",
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
        compile_context=CompileContext(),
        lang_explicit=True,
    )

    assert captured_dump["lang"] == "c++"
    assert captured_graph["lang"] == "c++"


def test_perform_elf_dump_dwarf_only_does_not_attach_header_graph(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review: dwarf_only=True means "ignore headers entirely" -- dump()
    already returns a DWARF-only snapshot without parsing headers for that
    case, so this direct-ELF-dump CLI path (which calls _attach_header_graph
    itself rather than going through service.run_dump) must not silently
    re-parse the same headers via clang and embed L2 build_source evidence
    the caller explicitly asked not to have."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")

    plain_snap = AbiSnapshot(library="lib.so", version="1.0")
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: plain_snap)

    captured: dict[str, object] = {}

    def fake_attach(
        snap,
        header_graph,
        header_graph_includes,
        headers,
        includes,
        lang,
        compile_context,
        public_headers,
        public_header_dirs,
        include_search_dirs=None,
    ):
        captured["header_graph"] = header_graph
        captured["header_graph_includes"] = header_graph_includes
        return snap

    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    from abicheck.service_scan import CompileContext

    sentinel_cc = CompileContext()

    perform_elf_dump(
        so,
        (hdr,),
        (),
        "1.0",
        "c++",
        None,
        None,
        None,
        (),
        None,
        True,
        True,  # dwarf_only
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
        compile_context=sentinel_cc,
    )

    assert captured["header_graph"] is False
    assert captured["header_graph_includes"] is False


def test_perform_elf_dump_header_graph_receives_seeded_includes(
    tmp_path: Path, monkeypatch
) -> None:
    """When --sources/--build-info seeds build-derived L2 include dirs (no
    explicit -I given), the header-graph attach must see those seeded dirs
    too, not just the raw --include argument. The main dump() call already
    gets `eff_includes + inc_extra`; previously `_attach_header_graph` only
    received the raw `includes` tuple, so its independent second clang pass
    could silently degrade to a declaration-only graph on a header that
    needs a build-seeded -I (e.g. a dependency SDK), even though the main
    snapshot parsed cleanly (Codex review)."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    plain_snap = AbiSnapshot(library="lib.so", version="1.0")
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: plain_snap)
    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        _fake_seed_and_fold([seeded]),
    )

    captured: dict[str, object] = {}

    def fake_attach(
        snap, header_graph, header_graph_includes, headers, includes,
        lang, compile_context, public_headers, public_header_dirs, include_search_dirs=None,
    ):  # noqa: ANN001
        captured["includes"] = includes
        return snap

    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c++", None, None, None, (), None, True, False, None,
        (), (), None, False, (), "", None, None, False, None, None, None, None,
        False, "build", _expand, _populate, _stamp, _write,
    )

    assert seeded in captured["includes"]


def test_perform_elf_dump_header_graph_gets_compile_db_flags(
    tmp_path: Path, monkeypatch
) -> None:
    """When -p/--compile-db derives extra -D/-I/-std flags, effective_gcc_options
    (folded from those flags, above the main dump() call) must reach the
    header-graph attach's compile_context too. compile_context itself was
    resolved earlier from the plain --gcc-options CLI value only, so without
    this fix a header that only parses with the compile-DB flags would produce
    a valid main snapshot while the second, independent clang pass building
    the header graph parsed without them and silently degraded to a
    declaration-only graph (Codex review)."""
    from abicheck.service_scan import CompileContext

    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")

    plain_snap = AbiSnapshot(library="lib.so", version="1.0")
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: plain_snap)

    captured: dict[str, object] = {}

    def fake_attach(
        snap, header_graph, header_graph_includes, headers, includes,
        lang, compile_context, public_headers, public_header_dirs, include_search_dirs=None,
    ):  # noqa: ANN001
        captured["compile_context"] = compile_context
        return snap

    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()
    original_cc = CompileContext(gcc_options="-DFOO")

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c++", None, None,
        "-DFROM_COMPILE_DB -DFOO",  # effective_gcc_options (compile-db-merged)
        (), None, True, False, None, (), (), None, False, (), "", None, None,
        False, None, None, None, None, False, "off", _expand, _populate,
        _stamp, _write,
        compile_context=original_cc,
    )

    got = captured["compile_context"]
    assert got is not original_cc  # a new context was built, not mutated in place
    assert got.gcc_options == "-DFROM_COMPILE_DB -DFOO"
    # Every other field carries over from the original context unchanged.
    assert got.gcc_path == original_cc.gcc_path
    assert got.frontend == original_cc.frontend
    # The original passed-in context itself must stay untouched (frozen dataclass).
    assert original_cc.gcc_options == "-DFOO"


def test_perform_elf_dump_header_graph_builds_context_when_none_given(
    tmp_path: Path, monkeypatch
) -> None:
    """Same compile-DB-flags scenario as
    test_perform_elf_dump_header_graph_gets_compile_db_flags, but with no
    compile_context at all (None) -- e.g. a caller that never resolved one.
    effective_gcc_options must still reach the header-graph attach by
    constructing a fresh CompileContext, not silently dropping the flags
    because there was nothing to dataclasses.replace()."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")

    plain_snap = AbiSnapshot(library="lib.so", version="1.0")
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: plain_snap)

    captured: dict[str, object] = {}

    def fake_attach(
        snap, header_graph, header_graph_includes, headers, includes,
        lang, compile_context, public_headers, public_header_dirs, include_search_dirs=None,
    ):  # noqa: ANN001
        captured["compile_context"] = compile_context
        return snap

    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c++", None, None,
        "-DFROM_COMPILE_DB",  # effective_gcc_options, no compile_context given
        (), None, True, False, None, (), (), None, False, (), "", None, None,
        False, None, None, None, None, False, "off", _expand, _populate,
        _stamp, _write,
        # compile_context defaults to None
    )

    got = captured["compile_context"]
    assert got is not None
    assert got.gcc_options == "-DFROM_COMPILE_DB"


def test_perform_elf_dump_attaches_header_graph_by_default_no_flags(
    tmp_path: Path, monkeypatch
) -> None:
    """G29 Phase A: _attach_header_graph is now always called (no flag
    controls it any more), so the plain snapshot from dump() is replaced by
    the wrapper's returned snapshot even with no header-graph-related
    argument passed at all."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")

    plain_snap = AbiSnapshot(library="lib.so", version="1.0")
    graphed_snap = AbiSnapshot(library="lib.so", version="1.0")
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: plain_snap)

    called = {"attach": False}

    def fake_attach(*a, **k):  # noqa: ANN002, ANN003
        called["attach"] = True
        return graphed_snap

    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()
    written: dict[str, object] = {}

    def _write_and_capture(snap, *a, **k):  # noqa: ANN001, ANN002, ANN003
        written["snap"] = snap
        _write(snap, *a, **k)

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
        (), (), None, False, (), "", None, None, False, None, None, None, None,
        False, "off", _expand, _populate, _stamp, _write_and_capture,
    )

    assert called["attach"] is True
    assert written["snap"] is graphed_snap


def test_perform_elf_dump_seeds_l2_includes_and_runs_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    """perform_elf_dump seeds build-derived L2 include dirs into the header parse and
    drains the pending temp-build-dir cleanup in the finally (the ELF-side parity of
    the non-ELF seed path)."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    captured: dict = {}
    events: list[str] = []

    def fake_seed_and_fold(**kwargs):
        # collect_mode gates the inferred build query; assert it is threaded.
        captured["allow"] = kwargs["collect_mode"] != "off"
        kwargs["pending_cleanups"].append(lambda: events.append("cleanup"))
        from abicheck.compile_context import CompileContext

        ctx = CompileContext(**{k: kwargs[k] for k in _CC_FIELDS})
        return [seeded], False, ctx, ()

    def fake_dump(**kw):
        captured["extra_includes"] = kw.get("extra_includes")
        events.append("dump")
        return AbiSnapshot(library="lib.so", version="1.0")

    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", fake_dump)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
        (), (), None,  # effective_compile_db None
        False, (), "", None, None, False, None, None, tmp_path, None, False,
        "build",  # collect_mode (non-"off" → inferred query allowed)
        _expand, _populate, _stamp, _write,
    )

    assert captured["allow"] is True  # collect_mode "build" → inferred query allowed
    assert seeded in captured["extra_includes"]  # build dir reached the header parse
    assert events == ["dump", "cleanup"]  # cleanup drained after the parse


def test_perform_elf_dump_defers_l2_cleanup_until_after_header_graph(
    tmp_path: Path, monkeypatch
) -> None:
    """The seeded temp build dir must survive past the main dump() parse:
    _attach_header_graph (always run now, G29 Phase A) reuses the same
    seeded include dirs for its own independent clang pass, so cleaning up
    right after dump() (the plain seeds_l2_includes_and_runs_cleanup
    ordering above) would hand that second pass a directory that is already
    gone, silently degrading the graph for inferred-build cases with
    generated/dependency headers (Codex review). Cleanup must instead run
    after the header-graph attach."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    events: list[str] = []
    plain_snap = AbiSnapshot(library="lib.so", version="1.0")

    fake_seed_and_fold = _fake_seed_and_fold(
        [seeded], on_cleanup=lambda: events.append("cleanup")
    )

    def fake_dump(**kw):
        events.append("dump")
        return plain_snap

    def fake_attach(*a, **k):  # noqa: ANN002, ANN003
        events.append("attach")
        return plain_snap

    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", fake_dump)
    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
        (), (), None, False, (), "", None, None, False, None, None, tmp_path, None,
        False, "build", _expand, _populate, _stamp, _write,
    )

    assert events == ["dump", "attach", "cleanup"]


def test_perform_elf_dump_cleans_up_when_enrichment_raises_before_header_graph(
    tmp_path: Path, monkeypatch
) -> None:
    """An exception from a post-dump enrichment step (python_ext/python_api/
    numpy_capi/build-context) that runs BEFORE the header-graph attach must
    still release the seeded temp build dir -- deferring cleanup only until
    "right after dump()" isn't enough; nothing between dump() and the
    header-graph attach may leak it either (Codex review)."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    events: list[str] = []
    plain_snap = AbiSnapshot(library="lib.so", version="1.0")

    fake_seed_and_fold = _fake_seed_and_fold(
        [seeded], on_cleanup=lambda: events.append("cleanup")
    )

    def fake_dump(**kw):
        events.append("dump")
        return plain_snap

    def _raise_ext(_snap):
        events.append("python_ext")
        raise RuntimeError("boom in python_ext detection")

    def fake_attach(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("_attach_header_graph must not be reached")

    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", fake_dump)
    monkeypatch.setattr("abicheck.workflows.extraction.detect_python_extension", _raise_ext)
    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    with pytest.raises(RuntimeError, match="boom in python_ext detection"):
        perform_elf_dump(
            so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
            (), (), None, False, (), "", None, None, False, None, None, tmp_path, None,
            False, "build", _expand, _populate, _stamp, _write,
        )

    assert events == ["dump", "python_ext", "cleanup"]


def test_perform_elf_dump_cleanup_still_runs_after_header_graph_with_no_flags(
    tmp_path: Path, monkeypatch
) -> None:
    """G29 Phase A: there is no flag left to omit — the header-graph attach
    always runs, so cleanup always waits for it (superseding the old "without
    --header-graph, cleanup runs right after dump()" behavior; see
    test_perform_elf_dump_defers_l2_cleanup_until_after_header_graph for the
    ordering guarantee itself)."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    events: list[str] = []
    plain_snap = AbiSnapshot(library="lib.so", version="1.0")

    fake_seed_and_fold = _fake_seed_and_fold(
        [seeded], on_cleanup=lambda: events.append("cleanup")
    )

    def fake_dump(**kw):
        events.append("dump")
        return plain_snap

    def fake_attach(*a, **k):  # noqa: ANN002, ANN003
        events.append("attach")
        return plain_snap

    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", fake_dump)
    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
        (), (), None, False, (), "", None, None, False, None, None, tmp_path, None,
        False, "build", _expand, _populate, _stamp, _write,
    )

    assert events == ["dump", "attach", "cleanup"]


def test_perform_elf_dump_detects_python_surfaces_and_follow_deps(
    tmp_path: Path, monkeypatch
) -> None:
    """Without a compile DB, perform_elf_dump skips the build-context stamp but
    still runs python_ext / python_api detection and, with follow_deps, the
    dependency populate callback (lines 496-510)."""
    so = tmp_path / "lib.so"

    snap = AbiSnapshot(library="lib.so", version="2.0")
    assert (
        snap.python_ext is None
        and snap.python_api is None
        and snap.numpy_capi is None
    )
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: snap)

    ext_sentinel = object()
    api_sentinel = object()
    numpy_sentinel = object()
    monkeypatch.setattr(
        "abicheck.workflows.extraction.detect_python_extension", lambda _s: ext_sentinel
    )
    monkeypatch.setattr(
        "abicheck.workflows.extraction.detect_python_api", lambda _s: api_sentinel
    )
    monkeypatch.setattr(
        "abicheck.workflows.extraction.extract_numpy_capi_surface", lambda _p: numpy_sentinel
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
    # G26 (Codex review): numpy_capi must also be attached on this ELF `dump`
    # CLI path, since it bypasses service.run_dump's own attach point.
    assert snap.numpy_capi is numpy_sentinel
    # follow_deps path invoked populate_dependency_info with the search paths.
    assert events["populated"] == (so, (tmp_path,))


def test_perform_elf_dump_preserves_existing_python_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    """When the snapshot already carries python_ext/python_api/numpy_capi,
    detection is not re-run (the ``is None`` guards stay false)."""
    so = tmp_path / "lib.so"

    snap = AbiSnapshot(library="lib.so", version="1")
    preexisting_ext = object()
    preexisting_api = object()
    preexisting_numpy = object()
    snap.python_ext = preexisting_ext  # type: ignore[assignment]
    snap.python_api = preexisting_api  # type: ignore[assignment]
    snap.numpy_capi = preexisting_numpy  # type: ignore[assignment]
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: snap)

    def _boom(_s):  # noqa: ANN001, ANN202
        raise AssertionError("detection must not run when metadata is present")

    monkeypatch.setattr("abicheck.workflows.extraction.detect_python_extension", _boom)
    monkeypatch.setattr("abicheck.workflows.extraction.detect_python_api", _boom)
    monkeypatch.setattr("abicheck.workflows.extraction.extract_numpy_capi_surface", _boom)

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
    assert snap.numpy_capi is preexisting_numpy


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


def test_perform_elf_dump_wraps_dump_errors_still_cleans_up_seeded_dirs(
    tmp_path: Path, monkeypatch
) -> None:
    """When dump() itself raises, any L2-seeded temp build dir must still be
    released immediately in the except path -- the header-graph attach that
    would otherwise justify deferring cleanup is never reached on a failed
    parse, so holding the directory open would leak it."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    events: list[str] = []

    fake_seed_and_fold = _fake_seed_and_fold(
        [seeded], on_cleanup=lambda: events.append("cleanup")
    )

    def _raise(**_kw):
        events.append("dump")
        raise AbicheckError("castxml exploded")

    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", _raise)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    with pytest.raises(click.ClickException, match="castxml exploded"):
        perform_elf_dump(
            so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
            (), (), None, False, (), "", None, None, False, None, None, None, None,
            False, "off", _expand, _populate, _stamp, _write,
            # Even though the always-on header-graph attach (G29 Phase A)
            # would otherwise run, the failed parse never reaches it, so
            # cleanup must not be deferred.
        )

    assert events == ["dump", "cleanup"]


# ── evidence_depth_label (CLI-audit P2 self-describing output) ──────────────




# ── perform_elf_dump / --dump-manifest header_roots (Codex review P1) ───────


def test_perform_elf_dump_forwards_dump_manifest_roots_as_header_roots(
    tmp_path: Path, monkeypatch
) -> None:
    """``--dump-manifest`` is mutually exclusive with ``-H``, so ``headers``
    is empty on a manifest-driven dump even though the manifest's own
    ``roots`` are the library's real header roots. Without forwarding those
    roots into ``write_snapshot_output``'s ``header_roots``, the default
    dependency-exclusion pass (``dumper_scoping.scope_snapshot_excluding_
    dependencies``) falls back to the bare system-header path heuristic and
    can misclassify an installed library's own headers under a system
    prefix as a dependency, silently emptying the snapshot (Codex review)."""
    from abicheck.dump_manifest import DumpManifest

    so = tmp_path / "lib.so"
    snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: snap)

    captured: dict[str, object] = {}

    def _write(*_a, header_roots=(), **_k):  # noqa: ANN002, ANN003
        captured["header_roots"] = header_roots

    _events, _stamp, _write_unused, _expand, _populate = _elf_dump_callables()
    manifest_root = Path("/usr/include/mylib/api.h")
    manifest = DumpManifest(base_dir=tmp_path, roots=(manifest_root,))

    perform_elf_dump(
        so, (), (), "1.0", "c", None, None, None, (), None, True, False, None,
        (), (), None, False, (), "", None, None, False, None, None, None, None,
        False, "off", _expand, _populate, _stamp, _write,
        dump_manifest=manifest,
    )

    assert manifest_root in captured["header_roots"]


def test_perform_elf_dump_header_roots_is_headers_only_without_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    """No ``--dump-manifest``: ``header_roots`` is exactly the ``-H`` tuple,
    unchanged from before (no manifest roots to fold in)."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: snap)

    captured: dict[str, object] = {}

    def _write(*_a, header_roots=(), **_k):  # noqa: ANN002, ANN003
        captured["header_roots"] = header_roots

    _events, _stamp, _write_unused, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
        (), (), None, False, (), "", None, None, False, None, None, None, None,
        False, "off", _expand, _populate, _stamp, _write,
    )

    assert captured["header_roots"] == (hdr,)


def test_dump_manifest_header_roots_includes_public_headers_and_project_owned_includes(
    tmp_path: Path,
) -> None:
    """Regression coverage for a Codex-review P1 finding: ``roots`` alone
    is not the manifest's full ownership set -- ``public_header_paths``/
    ``public_header_dirs`` (the manifest's own ADR-015 provenance-input
    equivalent of ``--public-header``/``--public-header-dir``) and any
    per-translation-unit include directory explicitly marked
    ``project_owned: true`` are just as much "the dump's actual root set"
    and must reach dependency scoping too, or their declarations can be
    misclassified as dependencies and silently dropped."""
    from abicheck.dump_manifest import DumpManifest, IncludeEntry, TranslationUnit

    root = Path("/usr/include/mylib/api.h")
    pub_path = Path("/usr/include/mylib/extra_public.h")
    pub_dir = Path("/usr/include/mylib/public_dir")
    owned_include = Path("/usr/include/mylib/owned_include")
    unowned_include = Path("/usr/include/other/not_owned")
    # Codex review, second round: a TU may force-include a private support
    # header alongside a public one (dump_manifest.py's own docstring) --
    # not required to already be in roots or a project_owned include, so a
    # forced-include header under a system-prefixed path was otherwise
    # misclassified as a toolchain dependency and filtered out.
    forced_private = Path("/usr/include/mylib/internal_support.h")
    manifest = DumpManifest(
        base_dir=tmp_path,
        roots=(root,),
        public_header_paths=(pub_path,),
        public_header_dirs=(pub_dir,),
        translation_units=(
            TranslationUnit(
                name="tu1",
                forced_includes=(forced_private,),
                includes=(
                    IncludeEntry(path=owned_include, project_owned=True),
                    IncludeEntry(path=unowned_include, project_owned=False),
                ),
            ),
        ),
    )

    roots = _dump_manifest_header_roots(manifest)

    assert root in roots
    assert pub_path in roots
    assert pub_dir in roots
    assert owned_include in roots
    assert forced_private in roots
    assert unowned_include not in roots


def test_dump_manifest_header_roots_empty_without_manifest() -> None:
    assert _dump_manifest_header_roots(None) == ()
