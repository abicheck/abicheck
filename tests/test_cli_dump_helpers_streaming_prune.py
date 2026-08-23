"""Streaming-prune scope-suppression coverage for :mod:`abicheck.cli_dump_helpers`.

Split out of ``test_cli_dump_helpers_coverage.py`` (already at the AI-readiness
file-size hard cap) rather than grown there, per this repo's established
sibling-test-file pattern for a large existing test module.

Both tests pin the same fix: ``perform_elf_dump``/``handle_non_elf_dump``
must suppress the opt-in streaming pruner (``dumper_clang_streaming.py``)
for their primary header-AST-parsing call whenever ``include_dependencies=
True`` (``dump --include-system-declarations``) was requested — otherwise the
pruner, which has no visibility into that flag on its own, could silently
drop dependency-header functions/variables the flag explicitly asked to keep
(Codex review, PR #840).
"""

from __future__ import annotations

from pathlib import Path

from abicheck import dumper_cache
from abicheck.cli_dump_helpers import handle_non_elf_dump, perform_elf_dump
from abicheck.dumper_clang_streaming import streaming_prune_suppressed
from abicheck.model import AbiSnapshot
from tests.test_cli_dump_helpers_coverage import _elf_dump_callables


def test_perform_elf_dump_suppresses_streaming_prune_for_full_scope_request(
    tmp_path: Path, monkeypatch
) -> None:
    """``include_dependencies=True`` must suppress the pruner for the
    primary ``dump()`` call."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "config.h"
    hdr.write_text("struct Config { int v; };", encoding="utf-8")

    seen_suppressed: dict[str, bool] = {}

    def _dump_stub(**_kw):  # noqa: ANN003
        seen_suppressed["suppressed"] = streaming_prune_suppressed()
        return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", _dump_stub)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    assert not streaming_prune_suppressed()  # not leaked before the call
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
        include_dependencies=True,
    )

    assert seen_suppressed["suppressed"] is True
    assert not streaming_prune_suppressed()  # not leaked after the call


def test_perform_elf_dump_does_not_open_ast_memoize_scope_for_empty_headers(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review, PR #840: `--dump-manifest` invocations reach this
    function with an empty `headers` tuple (mutually exclusive with `-H`).
    `_attach_header_graph` no-ops on empty headers, so wrapping the primary
    `dump()` call in `dumper_cache.ast_memoize_scope()` unconditionally
    would protect a memo nothing will ever consume -- while silently
    disabling the opt-in streaming pruner (gated on `ast_memoize_active()`)
    for a manifest dump's own TU parses whenever they share this thread
    (a single TU, or `ABICHECK_TU_JOBS=1`). This pins that the scope is
    NOT opened when `headers` is empty."""
    so = tmp_path / "lib.so"
    seen_active: dict[str, bool] = {}

    def _dump_stub(**_kw):  # noqa: ANN003
        seen_active["active"] = dumper_cache.ast_memoize_active()
        return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", _dump_stub)

    _events, _stamp, _write, _expand, _populate = _elf_dump_callables()

    assert not dumper_cache.ast_memoize_active()  # not leaked before the call
    perform_elf_dump(
        so,
        (),  # headers -- empty, as a real --dump-manifest invocation has
        (),
        "1.0",
        "c",
        None,
        None,
        None,
        (),  # gcc_path/prefix/options/option_tokens
        None,
        False,  # sysroot, nostdinc
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

    assert seen_active["active"] is False
    assert not dumper_cache.ast_memoize_active()  # not leaked after the call


def test_non_elf_dump_suppresses_streaming_prune_for_full_scope_request(
    tmp_path: Path,
) -> None:
    """Same contract as above, for ``handle_non_elf_dump``'s
    ``dump_native_binary`` call (PE/Mach-O path)."""
    so = tmp_path / "lib.dylib"
    snap = AbiSnapshot(library="lib.dylib", version="9.9")
    seen_suppressed: dict[str, bool] = {}

    def _dump_native(*a, **k):  # noqa: ANN002, ANN003
        seen_suppressed["suppressed"] = streaming_prune_suppressed()
        return snap

    assert not streaming_prune_suppressed()  # not leaked before the call
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
        lambda *a, **k: None,
        lambda *a, **k: None,
        header_backend="clang",
        include_dependencies=True,
    )
    assert seen_suppressed["suppressed"] is True
    assert not streaming_prune_suppressed()  # not leaked after the call
