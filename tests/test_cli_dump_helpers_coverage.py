"""Targeted coverage for :mod:`abicheck.cli_dump_helpers`.

Exercises the error paths, formatting branches, and resolution branches of the
``dump`` CLI helpers by calling the helper functions directly with crafted
arguments (rather than driving the whole CLI), so each assertion pins a
concrete return value or raised exception:

- ``resolve_dump_debug_format``'s selector-supersedes branch (auto / explicit)
- ``compile_db_from_build_info``'s derivation of the L2 database from
  ``--build-info``
- ``check_dump_debug_format_error``'s PE/Mach-O rejection
- ``resolve_dump_collect_context``'s warning and ``--depth binary`` branches
- ``resolve_dump_compile_context``'s pre-resolved-context verbatim return
- ``dumper_scoping.dump_manifest_header_roots``' ownership set

The ``perform_elf_dump``/``handle_non_elf_dump`` execution tests this file
used to hold went away with those functions (ADR-063 Track 1 -- they had no
production caller once ``dump_cmd``'s real run moved onto the shared typed
executor). The behaviours whose only home was one of them are now pinned
against the live path in ``tests/test_dump_cli_execution_behaviors.py``;
everything else they asserted is already owned at the shared pipeline's own
seams (that module's docstring names each).
"""

from __future__ import annotations

from pathlib import Path

from abicheck.cli_dump_helpers import (
    check_dump_debug_format_error,
    compile_db_from_build_info,
    resolve_dump_collect_context,
    resolve_dump_compile_context,
    resolve_dump_debug_format,
)

# Canonical home (``dumper_scoping``), not ``cli_dump_helpers``'s former
# private alias of it: that alias existed only for ``perform_elf_dump``'s own
# call and went away with it. ``frontends/cli/commands/dump.py`` -- the real
# ``dump`` CLI -- imports the same canonical name.
from abicheck.workflows.extraction import dump_manifest_header_roots

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


_CC_FIELDS = (
    "gcc_path",
    "gcc_prefix",
    "gcc_options",
    "gcc_option_tokens",
    "sysroot",
    "nostdinc",
    "frontend",
    "frontend_context",
)


# ── evidence_depth_label (CLI-audit P2 self-describing output) ──────────────


# ── --dump-manifest header_roots (Codex review P1) ──────────────────────────


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

    roots = dump_manifest_header_roots(manifest)

    assert root in roots
    assert pub_path in roots
    assert pub_dir in roots
    assert owned_include in roots
    assert forced_private in roots
    assert unowned_include not in roots


def test_dump_manifest_header_roots_empty_without_manifest() -> None:
    assert dump_manifest_header_roots(None) == ()
