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
    evidence_depth_label,
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


def test_non_elf_dump_forwards_header_graph_flags(tmp_path: Path) -> None:
    """--header-graph/--header-graph-includes reach dump_native_binary on the
    PE/Mach-O path — previously only perform_elf_dump forwarded them, so
    `dump --header-graph` silently no-opped on non-ELF input (Codex review)."""
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
        header_graph=True,
        header_graph_includes=True,
    )
    kwargs = calls["dump_kwargs"]
    assert kwargs["header_graph"] is True
    assert kwargs["header_graph_includes"] is True


def test_non_elf_dump_defaults_header_graph_off(tmp_path: Path) -> None:
    """Without --header-graph, dump_native_binary sees the flag as False (default),
    matching the ELF path's default-off behavior."""
    so = tmp_path / "lib.dll"
    snap = AbiSnapshot(library="lib.dll", version="1.0")

    calls: dict[str, object] = {}

    def _dump_native(*a, **k):  # noqa: ANN002, ANN003
        calls["dump_kwargs"] = k
        return snap

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
        _dump_native,
        _noop_stamp,
        _record_write,
    )
    kwargs = calls["dump_kwargs"]
    assert kwargs["header_graph"] is False
    assert kwargs["header_graph_includes"] is False


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


def test_perform_elf_dump_attaches_header_graph_when_requested(
    tmp_path: Path, monkeypatch
) -> None:
    """ADR-041 addendum: with header_graph=True, perform_elf_dump calls
    service._attach_header_graph (the same wrapper service.run_dump uses for
    `compare`'s implicit-dump path) with the raw headers, the L2-seeded
    includes (see test_perform_elf_dump_header_graph_receives_seeded_includes
    for the seeded-vs-raw distinction), lang/compile_context/public_headers/
    public_header_dirs it was given, and writes the wrapper's returned
    (possibly different) snapshot object. compile_context is passed through
    unmodified here because effective_gcc_options (None, no -p/--compile-db
    in this call) already matches compile_context.gcc_options — see
    test_perform_elf_dump_header_graph_gets_compile_db_flags for the case
    where they differ and a replacement context is built."""
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
        public_header_dirs,
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
        header_graph=True,
        header_graph_includes=True,
        compile_context=sentinel_cc,
    )

    assert captured["header_graph"] is True
    assert captured["header_graph_includes"] is True
    assert captured["headers"] == [hdr]
    assert captured["lang"] == "c++"
    assert captured["compile_context"] is sentinel_cc
    assert captured["snap"] is plain_snap
    # The wrapper's returned snapshot (not the original) is what gets written.
    assert captured["written_snap"] is graphed_snap


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
        "abicheck.buildsource.l2_seed.seed_l2_includes",
        lambda **_kw: ([seeded], []),
    )

    captured: dict[str, object] = {}

    def fake_attach(
        snap, header_graph, header_graph_includes, headers, includes,
        lang, compile_context, public_headers, public_header_dirs,
    ):  # noqa: ANN001
        captured["includes"] = includes
        return snap

    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c++", None, None, None, (), None, True, False, None,
        (), (), None, False, (), "", None, None, False, None, None, None, None,
        False, "build", _expand, _populate, _stamp, _write,
        header_graph=True, header_graph_includes=False,
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
        lang, compile_context, public_headers, public_header_dirs,
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
        header_graph=True, header_graph_includes=False,
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
        lang, compile_context, public_headers, public_header_dirs,
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
        header_graph=True, header_graph_includes=False,
        # compile_context defaults to None
    )

    got = captured["compile_context"]
    assert got is not None
    assert got.gcc_options == "-DFROM_COMPILE_DB"


def test_perform_elf_dump_skips_header_graph_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    """header_graph defaults to False: _attach_header_graph must not be called
    at all, and the plain snapshot from dump() is written unmodified."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")

    plain_snap = AbiSnapshot(library="lib.so", version="1.0")
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: plain_snap)

    called = {"attach": False}

    def fake_attach(*a, **k):  # noqa: ANN002, ANN003
        called["attach"] = True
        raise AssertionError("_attach_header_graph must not be called")

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

    assert called["attach"] is False
    assert written["snap"] is plain_snap


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

    def fake_seed(**kwargs):
        # collect_mode gates the inferred build query; assert it is threaded.
        captured["allow"] = kwargs["allow_inferred_build_query"]
        return [seeded], [lambda: events.append("cleanup")]

    def fake_dump(**kw):
        captured["extra_includes"] = kw.get("extra_includes")
        events.append("dump")
        return AbiSnapshot(library="lib.so", version="1.0")

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
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
    """With --header-graph, the seeded temp build dir must survive past the main
    dump() parse: _attach_header_graph reuses the same seeded include dirs for
    its own independent clang pass, so cleaning up right after dump() (the
    plain seeds_l2_includes_and_runs_cleanup ordering above) would hand that
    second pass a directory that is already gone, silently degrading the
    graph for inferred-build cases with generated/dependency headers (Codex
    review). Cleanup must instead run after the header-graph attach."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    events: list[str] = []
    plain_snap = AbiSnapshot(library="lib.so", version="1.0")

    def fake_seed(**kwargs):
        return [seeded], [lambda: events.append("cleanup")]

    def fake_dump(**kw):
        events.append("dump")
        return plain_snap

    def fake_attach(*a, **k):  # noqa: ANN002, ANN003
        events.append("attach")
        return plain_snap

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", fake_dump)
    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
        (), (), None, False, (), "", None, None, False, None, None, tmp_path, None,
        False, "build", _expand, _populate, _stamp, _write,
        header_graph=True, header_graph_includes=False,
    )

    assert events == ["dump", "attach", "cleanup"]


def test_perform_elf_dump_cleans_up_when_enrichment_raises_before_header_graph(
    tmp_path: Path, monkeypatch
) -> None:
    """An exception from a post-dump enrichment step (python_ext/python_api/
    numpy_capi/build-context) that runs BEFORE the --header-graph attach must
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

    def fake_seed(**kwargs):
        return [seeded], [lambda: events.append("cleanup")]

    def fake_dump(**kw):
        events.append("dump")
        return plain_snap

    def _raise_ext(_snap):
        events.append("python_ext")
        raise RuntimeError("boom in python_ext detection")

    def fake_attach(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("_attach_header_graph must not be reached")

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", fake_dump)
    monkeypatch.setattr("abicheck.python_ext.detect_python_extension", _raise_ext)
    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    with pytest.raises(RuntimeError, match="boom in python_ext detection"):
        perform_elf_dump(
            so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
            (), (), None, False, (), "", None, None, False, None, None, tmp_path, None,
            False, "build", _expand, _populate, _stamp, _write,
            header_graph=True, header_graph_includes=False,
        )

    assert events == ["dump", "python_ext", "cleanup"]


def test_perform_elf_dump_no_header_graph_cleans_up_right_after_dump(
    tmp_path: Path, monkeypatch
) -> None:
    """Without --header-graph there is no second pass to hold the seeded temp
    build dir open for, so cleanup must run immediately after dump() as
    before -- confirms the deferral above is conditional, not a blanket
    delay."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    events: list[str] = []

    def fake_seed(**kwargs):
        return [seeded], [lambda: events.append("cleanup")]

    def fake_dump(**kw):
        events.append("dump")
        return AbiSnapshot(library="lib.so", version="1.0")

    def fake_attach(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("_attach_header_graph must not be called")

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", fake_dump)
    monkeypatch.setattr("abicheck.service._attach_header_graph", fake_attach)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
        (), (), None, False, (), "", None, None, False, None, None, tmp_path, None,
        False, "build", _expand, _populate, _stamp, _write,
    )

    assert events == ["dump", "cleanup"]


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
        "abicheck.python_ext.detect_python_extension", lambda _s: ext_sentinel
    )
    monkeypatch.setattr(
        "abicheck.python_api.detect_python_api", lambda _s: api_sentinel
    )
    monkeypatch.setattr(
        "abicheck.numpy_capi.extract_numpy_capi_surface", lambda _p: numpy_sentinel
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

    monkeypatch.setattr("abicheck.python_ext.detect_python_extension", _boom)
    monkeypatch.setattr("abicheck.python_api.detect_python_api", _boom)
    monkeypatch.setattr("abicheck.numpy_capi.extract_numpy_capi_surface", _boom)

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

    def fake_seed(**kwargs):
        return [seeded], [lambda: events.append("cleanup")]

    def _raise(**_kw):
        events.append("dump")
        raise AbicheckError("castxml exploded")

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", _raise)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    with pytest.raises(click.ClickException, match="castxml exploded"):
        perform_elf_dump(
            so, (hdr,), (), "1.0", "c", None, None, None, (), None, True, False, None,
            (), (), None, False, (), "", None, None, False, None, None, None, None,
            False, "off", _expand, _populate, _stamp, _write,
            header_graph=True,  # even with a header-graph request, the failed
            # parse never reaches the attach, so cleanup must not be deferred.
        )

    assert events == ["dump", "cleanup"]


# ── evidence_depth_label (CLI-audit P2 self-describing output) ──────────────


def _pack(build_evidence=None, source_abi=None, source_graph=None):
    from abicheck.buildsource.pack import BuildSourcePack

    return BuildSourcePack(
        root=Path("/nonexistent"),
        build_evidence=build_evidence,
        source_abi=source_abi,
        source_graph=source_graph,
    )


def test_evidence_depth_label_binary_when_no_headers_no_build_source() -> None:
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    assert evidence_depth_label(snap) == "binary"


def test_evidence_depth_label_headers_when_from_headers_set() -> None:
    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    assert evidence_depth_label(snap) == "headers"


def test_evidence_depth_label_build_when_build_evidence_has_facts() -> None:
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")])
    )
    assert evidence_depth_label(snap) == "build"


def test_evidence_depth_label_source_when_source_abi_has_reachable_entities() -> None:
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(),
        source_abi=SourceAbiSurface(
            reachable_declarations=[SourceEntity(id="foo", kind="function")]
        ),
    )
    assert evidence_depth_label(snap) == "source"


def test_evidence_depth_label_source_when_source_graph_has_nodes() -> None:
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        source_graph=SourceGraphSummary(nodes=[GraphNode(id="n1", kind="function")])
    )
    assert evidence_depth_label(snap) == "source"


def test_evidence_depth_label_does_not_overstate_empty_source_abi() -> None:
    # Regression (CodeRabbit review): source_abi/source_graph/build_evidence
    # can be present (non-None) but carry no real facts -- e.g.
    # _run_inline_source_abi returns an empty SourceAbiSurface() when clang is
    # unavailable after L3 was found. Presence alone must not overstate
    # "source"/"build" for a layer that ran but linked nothing.
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.buildsource.source_graph import SourceGraphSummary

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(),  # no targets/compile_units
        source_abi=SourceAbiSurface(),  # no reachable entities
        source_graph=SourceGraphSummary(),  # no nodes
    )
    # All three layers are present but empty -- falls all the way back to
    # "headers" (from_headers=True), not "source" or "build".
    assert evidence_depth_label(snap) == "headers"


# ── check_requested_depth_satisfied (CLI-audit P1 strict depth contract) ────


def test_check_requested_depth_satisfied_noop_when_depth_not_requested() -> None:
    """The bare default (no --depth) never hard-fails -- degrading silently
    is the whole point of leaving --depth unspecified."""
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    check_requested_depth_satisfied(None, snap)  # must not raise


def test_check_requested_depth_satisfied_binary_always_passes() -> None:
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    check_requested_depth_satisfied("binary", snap)  # must not raise


def test_check_requested_depth_satisfied_unknown_depth_is_noop() -> None:
    """Defensive branch: a depth value outside _DEPTH_RANK (should never
    happen past CLI parsing, which restricts --depth to the four known
    rungs) is treated as unconstrained rather than raising."""
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    check_requested_depth_satisfied("not-a-real-depth", snap)  # must not raise


def test_gated_source_label_without_a_pack_falls_back_to_headers_or_binary() -> None:
    from abicheck.cli_dump_helpers import _gated_source_label

    assert _gated_source_label(None, AbiSnapshot(library="libfoo.so", version="1.0")) == "binary"
    headers_snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    assert _gated_source_label(None, headers_snap) == "headers"


def test_dump_will_attempt_hybrid_l4_extraction_false_for_prebuilt_pack(tmp_path) -> None:
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli_dump_helpers import _dump_will_attempt_hybrid_l4_extraction

    pack_dir = tmp_path / "pack"
    BuildSourcePack.empty(pack_dir).write()
    assert _dump_will_attempt_hybrid_l4_extraction(pack_dir) is False


def test_dump_will_attempt_hybrid_l4_extraction_true_for_raw_source_tree(tmp_path) -> None:
    from abicheck.cli_dump_helpers import _dump_will_attempt_hybrid_l4_extraction

    tree = tmp_path / "src"
    tree.mkdir()
    assert _dump_will_attempt_hybrid_l4_extraction(tree) is True


def test_dump_will_attempt_hybrid_l4_extraction_false_without_any_input() -> None:
    """Codex review (third finding): no --sources at all means
    collect_inline_pack never runs regardless of frontend -- rejecting here
    would point the user at a fix (switch frontends) that would not
    actually satisfy --depth source; the real problem is reported
    downstream by check_requested_depth_satisfied instead."""
    from abicheck.cli_dump_helpers import _dump_will_attempt_hybrid_l4_extraction

    assert _dump_will_attempt_hybrid_l4_extraction(None) is False


def test_dump_will_attempt_hybrid_l4_extraction_ignores_build_info(tmp_path) -> None:
    """Codex review (fourth finding): a raw --build-info tree never feeds L4
    extraction -- embed_build_source passes raw_sources (never raw_build_info)
    as _run_inline_source_abi's one 'sources' argument, so --build-info plays
    no part in whether an extractor (hybrid or otherwise) runs. Only a raw
    (non-pack) --sources tree does. The predicate takes a single 'sources'
    argument for exactly this reason -- there is no build_info parameter to
    pass a raw tree to."""
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli_dump_helpers import _dump_will_attempt_hybrid_l4_extraction

    pack_dir = tmp_path / "pack"
    BuildSourcePack.empty(pack_dir).write()
    assert _dump_will_attempt_hybrid_l4_extraction(pack_dir) is False


def test_check_requested_depth_satisfied_headers_without_header_ast_fails() -> None:
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0")  # from_headers=False
    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth headers"):
        check_requested_depth_satisfied("headers", snap)


def test_check_requested_depth_satisfied_headers_with_header_ast_passes() -> None:
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    check_requested_depth_satisfied("headers", snap)  # must not raise


def test_check_requested_depth_satisfied_build_without_build_facts_fails() -> None:
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth build"):
        check_requested_depth_satisfied("build", snap)


def test_check_requested_depth_satisfied_source_with_source_facts_passes() -> None:
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(),
        source_abi=SourceAbiSurface(
            reachable_declarations=[SourceEntity(id="foo", kind="function")]
        ),
    )
    check_requested_depth_satisfied("source", snap)  # must not raise


def test_check_requested_depth_satisfied_source_with_empty_payload_fails() -> None:
    """Mirrors test_evidence_depth_label_does_not_overstate_empty_source_abi:
    a coverage row can be present while the payload links no facts -- the
    strict check must see through that the same way evidence_depth_label does."""
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.buildsource.source_graph import SourceGraphSummary
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(),
        source_abi=SourceAbiSurface(),
        source_graph=SourceGraphSummary(),
    )
    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth source"):
        check_requested_depth_satisfied("source", snap)


def test_check_requested_depth_satisfied_header_graph_only_does_not_satisfy_source() -> None:
    """Codex review: service._attach_header_graph (--header-graph without
    --sources/--build-info) builds a pack whose L5 source_graph is genuinely
    non-empty while L3/L4 coverage rows are explicitly NOT_COLLECTED -- no
    build or L4 source-ABI replay ever ran. evidence_depth_label's honest L5
    check alone would read this as "source", letting --header-graph silently
    satisfy an explicit --depth source with zero real source/build evidence.
    The strict check must see through that and still fail."""
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    pack = BuildSourcePack(
        root=Path(""),
        source_graph=SourceGraphSummary(nodes=[GraphNode(id="n1", kind="function")]),
    )
    pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED),
        LayerCoverage(layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.PRESENT),
    ]
    snap.build_source = pack

    # Sanity check: evidence_depth_label alone (the honesty-reporting
    # function) does read this as "source" -- that's the exact gap the
    # strict check must independently close, not a bug in that function.
    assert evidence_depth_label(snap) == "source"

    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth source"):
        check_requested_depth_satisfied("source", snap)


def test_check_requested_depth_satisfied_l3_plus_backfilled_graph_does_not_satisfy_source() -> None:
    """Codex review (second finding): cli_buildsource.embed_build_source's
    header-only-graph backfill can graft a --header-graph L5 pack onto an
    otherwise-real, L3-only --build-info pack -- so "L3 present, L4 absent,
    L5 present" is NOT a reliable "genuine source-tier" signature either; it
    is exactly what a real `--build-info <L3-pack> --header-graph` run
    produces with zero L4/L5 source-tier replay. The strict gate must
    require real L4 facts, not just a non-empty L3."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    backfilled_pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_graph=SourceGraphSummary(nodes=[GraphNode(id="n1", kind="function")]),
    )
    backfilled_pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED),
        LayerCoverage(layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.PRESENT),
    ]
    snap.build_source = backfilled_pack

    assert evidence_depth_label(snap) == "source"
    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth source"):
        check_requested_depth_satisfied("source", snap)
    # The failure message should honestly name "build" (real L3), not
    # "binary" or "headers", since L3 genuinely is present.
    try:
        check_requested_depth_satisfied("source", snap)
    except DumpDepthNotSatisfiedError as exc:
        assert "reached 'build'" in str(exc)

    # A --depth build request against the same pack is genuinely satisfied
    # -- L3 is real here, only the "source" rung is gated on L4.
    check_requested_depth_satisfied("build", snap)  # must not raise


def test_check_requested_depth_satisfied_source_with_real_l4_facts_passes() -> None:
    """The gate is satisfied whenever L4 (source_abi) genuinely has facts,
    regardless of whether L3/L5 are also present -- this is what a real
    --sources/--build-info replay with L4 reachable declarations looks like."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    real_pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=SourceAbiSurface(
            reachable_declarations=[SourceEntity(id="foo", kind="function")]
        ),
        source_graph=SourceGraphSummary(nodes=[GraphNode(id="n1", kind="function")]),
    )
    snap.build_source = real_pack
    check_requested_depth_satisfied("source", snap)  # must not raise


def test_check_requested_depth_satisfied_source_zero_match_no_graph_passes() -> None:
    """CodeRabbit review: evidence_depth_label's own L4-or-L5 payload-emptiness
    rule requires *either* L4 *or* L5 to be non-empty -- a zero-match
    source-only dump that parsed TUs but linked no declarations (no binary to
    link against) AND folded no L5 graph leaves *both* empty, so
    evidence_depth_label reports "build" directly. The old code only called
    _gated_source_label when evidence_depth_label already said "source",
    so this genuinely-attempted, zero-match case skipped the gated recompute
    entirely and was wrongly rejected -- exactly the "unseeded --depth source
    that selected 0 TUs" scenario _write_snapshot_output's own G21.7 warning
    describes as expected, warn-only behavior, not a hard failure. Unlike
    test_check_requested_depth_satisfied_source_with_real_l4_facts_passes
    above, this pack deliberately carries NO source_graph, so it only passes
    once _gated_source_label runs unconditionally."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    surface = SourceAbiSurface()
    surface.coverage["compile_units_selected"] = 1
    surface.coverage["compile_units_parsed"] = 1
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=surface,
    )
    snap.build_source = pack

    assert evidence_depth_label(snap) == "build"
    check_requested_depth_satisfied("source", snap)  # must not raise


def test_l4_source_abi_was_attempted_false_for_unavailable_extractor() -> None:
    """Codex review (fifth finding): _run_inline_source_abi returns the same
    empty-surface, PARTIAL-coverage shape both when a source-only dump
    legitimately links zero declarations (no binary to match against) AND
    when the selected extractor is missing from PATH -- coverage *status*
    alone cannot tell them apart. The gate must key off
    SourceAbiSurface.coverage['compile_units_parsed'] (set only when replay
    actually executed) rather than the coverage row's PRESENT/PARTIAL status,
    so a categorically-failed extraction does not silently satisfy an
    explicit --depth source."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import _l4_source_abi_was_attempted

    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        # The exact shape _run_inline_source_abi returns when impl.available()
        # is False: a bare SourceAbiSurface() with no coverage dict populated
        # (replay never ran to set compile_units_parsed).
        source_abi=SourceAbiSurface(),
    )
    pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.PARTIAL),
        LayerCoverage(layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED),
    ]

    assert _l4_source_abi_was_attempted(pack) is False


def test_l4_source_abi_was_attempted_true_for_zero_linked_but_parsed_tus() -> None:
    """The counterpart to the unavailable-extractor case above: replay
    genuinely ran and parsed TUs (compile_units_parsed > 0) but linked zero
    declarations -- the expected, warn-only outcome for a source-only `dump
    --sources` with no binary to link against. This must still count as
    "attempted", so it can still satisfy an explicit --depth source (matching
    the existing G21.7 warn-not-error behavior for the same scenario)."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import _l4_source_abi_was_attempted

    surface = SourceAbiSurface()
    surface.coverage["compile_units_selected"] = 1
    surface.coverage["compile_units_parsed"] = 1
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=surface,
    )
    pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.PARTIAL),
        LayerCoverage(layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED),
    ]

    assert _l4_source_abi_was_attempted(pack) is True
