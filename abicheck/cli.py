# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI — abicheck dump | compare | compat (dump | check)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

# rich-click renders the (large) option lists in named panels for progressive
# disclosure (G21.8 / collapse M1). We keep the plain ``click`` API (so the
# module type-checks against click's stubs) and only base the root group on
# ``RichGroup`` — that alone makes ``cls=_AbicheckGroup`` render the rich panels
# (and RichGroup.command produces RichCommand subcommands). Fall back to plain
# click.Group if rich-click is somehow unavailable so the CLI never hard-fails.
try:
    from rich_click import RichGroup as _RootGroupBase
except ImportError:  # pragma: no cover - rich-click is a declared dependency
    _RootGroupBase = click.Group  # type: ignore[assignment,misc]

from .checker import DiffResult, LibraryMetadata, compare
from .cli_audit import echo_filtered_surface, echo_pattern_modulations
from .cli_datasources import print_data_sources as _print_data_sources
from .cli_dump_helpers import (
    perform_elf_dump,
    resolve_dump_compile_db,
    resolve_dump_debug_format,
    resolve_dump_depth,
)
from .cli_help import configure_rich_help
from .cli_helpers_compare import (  # noqa: F401  — re-exported to keep cli import sites stable
    _build_match_map as _build_match_map,
    _canonical_library_key as _canonical_library_key,
    _collect_additions as _collect_additions,
    _collect_force_public_symbols as _collect_force_public_symbols,
    _collect_release_inputs as _collect_release_inputs,
    _merge_gcc_options as _merge_gcc_options,
    _merge_redundant_changes as _merge_redundant_changes,
    _provenance_timestamp as _provenance_timestamp,
    _resolve_build_context_flags as _resolve_build_context_flags,
    _resolve_per_side_options as _resolve_per_side_options,
    _resolve_severity as _resolve_severity,
    _version_sort_key as _version_sort_key,
    _warn_ignored_flags as _warn_ignored_flags,
)
from .cli_options import (
    adr027_compare_options,
    build_source_compare_options,
    build_source_dump_options,
)
from .cli_params import POLICY_FILE_PARAM, _load_suppression_and_policy
from .cli_resolve import (
    _apply_native_provenance,
    _detect_binary_format,
    _dump_native_binary,
    _expand_header_inputs,
    _is_supported_compare_input,
    _maybe_follow_linker_script,
    _normalize_binary_input,
    _populate_dependency_info,
    _resolve_compare_snapshots,
    _resolve_input,
    _resolve_linker_script,
    _sniff_text_format,
)
from .compat.cli import compat_group
from .errors import AbicheckError
from .serialization import snapshot_to_json

if TYPE_CHECKING:
    from .buildsource.pack import BuildSourcePack
    from .checker_types import Change, DiffResult
    from .debug_resolver import DebugArtifact
    from .severity import SeverityConfig

from . import __version__ as _abicheck_version
from .model import AbiSnapshot

# Input-resolution & native-dump dispatch helpers now live in the cli_resolve
# leaf module. They are re-exported here (declared in __all__ so the re-export
# is explicit for mypy's no-implicit-reexport and for ruff) to keep existing
# ``from abicheck.cli import _resolve_input`` call sites — sibling cli_* modules,
# mcp_server, and the test-suite — working unchanged. New code should import
# these from ``abicheck.cli_resolve`` directly.
__all__ = [
    "_apply_native_provenance",
    "_detect_binary_format",
    "_dump_native_binary",
    "_expand_header_inputs",
    "_is_supported_compare_input",
    "_maybe_follow_linker_script",
    "_normalize_binary_input",
    "_populate_dependency_info",
    "_resolve_compare_snapshots",
    "_resolve_input",
    "_resolve_linker_script",
    "_sniff_text_format",
]

_logger = logging.getLogger("abicheck")


def _setup_verbosity(verbose: bool) -> None:
    """Configure logging verbosity for native commands."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG if verbose else logging.WARNING)


def _safe_write_output(output: Path, text: str) -> None:
    """Write *text* to *output*, creating parent directories as needed."""
    try:
        parent = output.parent
        if not parent.exists():
            click.echo(f"Creating output directory: {parent}", err=True)
            parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Cannot write to {output}: {exc}") from exc


def _stamp_provenance(
    snap: AbiSnapshot,
    *,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
) -> None:
    """Fill provenance metadata on a snapshot (mutates in place).

    ``created_at`` honours ``SOURCE_DATE_EPOCH`` (the reproducible-builds
    standard): when set to a Unix timestamp, that fixed time is used instead of
    the wall clock, so two dumps of an identical library are byte-identical —
    enabling content-addressable caching and reproducible-build verification.
    An unset or malformed value falls back to the current time.
    """
    import os
    import subprocess

    snap.created_at = _provenance_timestamp(os.environ.get("SOURCE_DATE_EPOCH"))
    snap.git_tag = git_tag
    snap.build_id = build_id

    if not no_git:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                snap.git_commit = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # git not available or not a repo — leave as None


def _layer_payload_empty(pack: BuildSourcePack, key: str) -> bool:
    """True when *key*'s embedded payload carries no facts.

    A coverage row can read ``PARTIAL``/``PRESENT`` while the payload is empty —
    e.g. ``_run_inline_source_abi`` returns an empty ``SourceAbiSurface()`` when
    clang is unavailable after L3 was found. The status alone then hides the
    miss, so we inspect the actual payload (Codex review, PR #422).
    """
    if key == "L3":
        be = pack.build_evidence
        return be is None or (not be.targets and not be.compile_units)
    if key == "L4":
        sa = pack.source_abi
        return sa is None or not any(sa.reachable_buckets().values())
    if key == "L5":
        sg = pack.source_graph
        return sg is None or not sg.nodes
    return False


def _missing_requested_evidence_layers(
    pack: BuildSourcePack | None, collect_mode: str
) -> list[str]:
    """Layers the *collect_mode* asked for but that came back empty.

    Maps the ADR-033 evidence mode to its expected L3/L4/L5 layers and checks the
    embedded pack. A layer is reported missing when its coverage row is
    ``NOT_COLLECTED`` (or absent) **or** when its embedded payload carries no
    facts despite a ``PARTIAL``/``PRESENT`` status — the latter catches a
    requested extractor that ran but produced nothing (e.g. clang unavailable).
    Returns [] when nothing was requested or every requested layer has facts.
    """
    if pack is None:
        return []
    from .buildsource.model import CoverageStatus, DataLayer
    from .buildsource.source_replay import collection_for_ci_mode

    _layer_for = {
        "L3": DataLayer.L3_BUILD,
        "L4": DataLayer.L4_SOURCE_ABI,
        "L5": DataLayer.L5_SOURCE_GRAPH,
    }
    _, layers = collection_for_ci_mode(collect_mode)
    missing: list[str] = []
    for key in layers:
        layer = _layer_for.get(key)
        if layer is None:
            continue
        cov = pack.manifest.coverage_for(layer)
        if (
            cov is None
            or cov.status == CoverageStatus.NOT_COLLECTED
            or _layer_payload_empty(pack, key)
        ):
            missing.append(layer.value)
    return missing


def _write_snapshot_output(
    snap: AbiSnapshot,
    output: Path | None,
    build_info: Path | None = None,
    sources: Path | None = None,
    build_config: Path | None = None,
    allow_build_query: bool = False,
    collect_mode: str = "source-target",
    build_query: str | None = None,
    build_compile_db: str | None = None,
) -> None:
    """Serialize snapshot and write to file or stdout.

    When *build_info* and/or *sources* are given, their normalized L3/L4/L5 facts
    are collected (inline from a source tree / build dir, or loaded from a pack
    directory) and embedded in the snapshot first (single-artifact UX) so a later
    ``compare old.json new.json`` needs no out-of-band packs. *collect_mode* (the
    ADR-033 D2 CI evidence mode) selects which layers and replay scope to collect:
    ``build`` captures L3 build context only, ``off`` collects nothing.
    *build_query* / *build_compile_db* are the CLI equivalents of the
    ``.abicheck.yml`` ``build.query`` / ``build.compile_db`` keys.
    """
    if build_info is not None or sources is not None:
        from .cli_buildsource import embed_build_source
        embed_build_source(
            snap, build_info, sources,
            build_config=build_config, allow_build_query=allow_build_query,
            collect_mode=collect_mode,
            build_query=build_query, build_compile_db=build_compile_db,
        )
        # G21.7: fail loud — if a requested evidence layer came back empty, say so
        # prominently instead of leaving it buried in the coverage rows. Permissive
        # by design (a warning, not an error): --collection-mode strict on
        # `collect` remains the hard-fail path (ADR-028 D3).
        missing = _missing_requested_evidence_layers(snap.build_source, collect_mode)
        if missing:
            click.echo(
                f"Warning: requested evidence layer(s) not collected: "
                f"{', '.join(missing)}. The snapshot embeds no facts for them — "
                "supply --build-info/--compile-db or install clang/castxml, and "
                "see the coverage rows for details.",
                err=True,
            )
    result = snapshot_to_json(snap)
    if output:
        _safe_write_output(output, result)
        click.echo(f"Snapshot written to {output}", err=True)
    else:
        click.echo(result)


def _collect_metadata(path: Path) -> LibraryMetadata | None:
    """Compute SHA-256 and file size for a library artifact.

    Returns *None* when *path* is a text-based snapshot (JSON or Perl dump)
    so that reports don't display misleading metadata for the serialised file.
    """
    text_fmt = _sniff_text_format(path)
    if text_fmt in ("json", "perl"):
        return None

    import hashlib

    data = path.read_bytes()
    return LibraryMetadata(
        path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


# Exit code for an invalid invocation (bad arguments, unknown option, invalid
# option value, unreadable/unrecognised input path). Chosen as sysexits.h
# ``EX_USAGE`` so it sits *outside* the compare/compat result space
# {0, 1, 2, 4} — a CI script can therefore tell "you called me wrong" apart
# from a real ABI verdict. Click defaults ``UsageError`` to exit 2, which
# collides with ``compare``'s documented "2 = source break"; this remaps it.
_EXIT_USAGE_ERROR = 64


class _AbicheckGroup(_RootGroupBase):
    """Root group that maps Click *usage* errors to a dedicated exit code.

    Click exits 2 for ``UsageError`` / ``BadParameter`` (bad arguments, unknown
    options, invalid option values, missing/unreadable input paths), which
    collides with ``compare``'s documented ``2 = source break`` result. Remap
    just that code to ``_EXIT_USAGE_ERROR`` so an invalid invocation is never
    mistaken for an ABI verdict. Other ``ClickException``s (exit 1, used for
    operational failures such as malformed input or an expired strict waiver),
    verdict exits (``SystemExit`` 2/4), and the ``compat`` error scheme (3–11)
    are deliberately left untouched.
    """

    def main(self, *args: Any, standalone_mode: bool = True, **kwargs: Any) -> Any:  # type: ignore[override]
        # Call plain click's main (not rich-click's RichGroup.main, our direct
        # super), because rich-click's main renders and exits on a ClickException
        # itself — which would bypass the usage-error→64 remap below. Help still
        # renders richly: that goes through RichCommand.format_help, invoked by
        # click's main during --help handling regardless of which main runs.
        if not standalone_mode:
            return click.Group.main(self, *args, standalone_mode=False, **kwargs)  # type: ignore[call-overload]
        try:
            click.Group.main(self, *args, standalone_mode=False, **kwargs)  # type: ignore[call-overload]
        except click.exceptions.Abort:
            click.echo("Aborted!", err=True)
            sys.exit(1)
        except click.exceptions.ClickException as exc:
            exc.show()
            # Only Click's usage-error code (2) collides with a compare verdict.
            sys.exit(_EXIT_USAGE_ERROR if exc.exit_code == 2 else exc.exit_code)
        else:
            sys.exit(0)


configure_rich_help()  # register --help option-group panels (G21.8 / M1)


@click.group(cls=_AbicheckGroup)
@click.version_option(
    version=_abicheck_version,
    prog_name="abicheck",
    message="%(prog)s %(version)s (napetrov/abicheck)",
)
def main() -> None:
    """abicheck — ABI compatibility checker for C/C++ shared libraries."""


@main.command("dump")
@click.argument("so_path", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("-H", "--header", "headers", multiple=True, type=click.Path(exists=True, path_type=Path),
              help="Public header file or directory (repeat for multiple).")
@click.option("-I", "--include", "includes", multiple=True, type=click.Path(path_type=Path),
              help="Extra include directory for castxml.")
# ── Declaration provenance (ADR-015) ─────────────────────────────────────────
@click.option("--public-header", "public_headers", multiple=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Header treated as public for provenance classification (repeat for "
                   "multiple). Declarations are tagged public/private/system in the snapshot. "
                   "Opt-in: omitting this leaves every origin UNKNOWN.")
@click.option("--public-header-dir", "public_header_dirs", multiple=True,
              type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Directory whose headers are treated as public for provenance "
                   "classification (repeat for multiple).")
@click.option("--version", "version", default="unknown", show_default=True,
              help="Library version string to embed in snapshot.")
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False),
              help="Language mode for the header backend.")
@click.option("--header-backend", "header_backend", default="auto", show_default=True,
              type=click.Choice(["auto", "castxml", "clang"], case_sensitive=False),
              help="L2 header-AST frontend: castxml (default schema reference) or "
                   "clang (-ast-dump=json; for hosts where castxml is absent or its "
                   "bundled frontend chokes). auto = castxml if present, else clang. "
                   "Env: ABICHECK_HEADER_BACKEND.")
@click.option("-o", "--output", "output", type=click.Path(path_type=Path), default=None,
              help="Output JSON file. Defaults to stdout.")
# ── Cross-compilation flags ───────────────────────────────────────────────────
@click.option("--gcc-path", default=None,
              help="Path to GCC/G++ cross-compiler binary.")
@click.option("--gcc-prefix", default=None,
              help="Cross-toolchain prefix (e.g. aarch64-linux-gnu-).")
@click.option("--gcc-options", default=None,
              help="Extra compiler flags passed through to castxml (split on "
                   "whitespace). For a flag whose value contains spaces, use the "
                   "repeatable --gcc-option instead.")
@click.option("--gcc-option", "gcc_option_tokens", multiple=True,
              help="A single extra compiler flag passed through to castxml "
                   "verbatim (repeatable; not whitespace-split). Use two flags for "
                   "a flag + spaced value, e.g. --gcc-option=-include "
                   "--gcc-option='some header.h'.")
@click.option("--sysroot", type=click.Path(path_type=Path), default=None,
              help="Alternative system root directory.")
@click.option("--nostdinc", is_flag=True, default=False,
              help="Do not search standard system include paths.")
@click.option("--pdb-path", "pdb_path", type=click.Path(path_type=Path), default=None,
              help="Explicit path to PDB file for Windows PE debug info. "
                   "Overrides automatic PDB discovery from the PE debug directory.")
@click.option("--follow-deps", is_flag=True, default=False,
              help="Resolve transitive DT_NEEDED dependencies and include the full "
                   "dependency graph and symbol binding status in the snapshot. "
                   "ELF only.")
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries (with --follow-deps).")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (with --follow-deps).")
@click.option("--dwarf-only", is_flag=True, default=False,
              help="Force DWARF-only mode: use DWARF debug info as the primary "
                   "data source even when headers are available. Enables type-aware "
                   "artifact checks without requiring castxml.")
@click.option("--show-data-sources", is_flag=True, default=False,
              help="Preview only: print which data layers (L0-L5) are available "
                   "for the binary and exit. No snapshot is written and no "
                   "L3/L4/L5 facts are embedded — re-run without this flag "
                   "(optionally with --build-info/--sources) to produce a snapshot.")
@click.option("--debug-format", "debug_format_opt",
              type=click.Choice(["auto", "dwarf", "btf", "ctf"], case_sensitive=False), default=None,
              help="Force the ELF debug format (auto=pick best available). "
                   "Supersedes the individual --btf/--ctf/--dwarf flags.")
@click.option("--btf", "debug_format", flag_value="btf", default=None, hidden=True,
              help="Force BTF debug format (ELF only).")
@click.option("--ctf", "debug_format", flag_value="ctf", hidden=True,
              help="Force CTF debug format (ELF only).")
@click.option("--dwarf", "debug_format", flag_value="dwarf", hidden=True,
              help="Force DWARF debug format (ELF only).")
# ── Build context capture (ADR-020a) ──────────────────────────────────────────
@click.option("-p", "--build-dir", "compile_db_path", type=click.Path(path_type=Path), default=None,
              help="Build directory containing compile_commands.json, or path to the "
                   "file itself. Enables deterministic header parsing with exact build "
                   "flags. Requires -H/--header.")
@click.option("--compile-db", "compile_db_path_alt", type=click.Path(path_type=Path), default=None,
              hidden=True,
              help="Explicit path to compile_commands.json (alias for -p).")
@click.option("--compile-db-filter", "compile_db_filter", default=None,
              help="Glob pattern to filter compile_commands.json entries by source file "
                   "(e.g. 'src/libfoo/**'). Useful for large databases.")
# ── Debug artifact resolution (ADR-021a) ──────────────────────────────────────
@click.option("--debug-root", "debug_roots", multiple=True, type=click.Path(path_type=Path),
              help="Directory containing separate debug files (build-id trees, "
                   "path-mirror debug files, or dSYM bundles). Can be repeated.")
@click.option("--debuginfod", is_flag=True, default=False,
              help="Enable debuginfod network resolution for debug info (opt-in). "
                   "Uses DEBUGINFOD_URLS environment variable or --debuginfod-url.")
@click.option("--debuginfod-url", "debuginfod_url", default=None,
              help="debuginfod server URL (overrides DEBUGINFOD_URLS env var).")
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="Enable verbose/debug output.")
# ── Provenance metadata ──────────────────────────────────────────────────────
@click.option("--git-tag", "git_tag", default=None,
              help="Git tag to embed in the snapshot (e.g. v2.0.0).")
@click.option("--build-id", "build_id", default=None,
              help="Opaque build identifier (CI run ID, build number, etc.).")
@click.option("--no-git", "no_git", is_flag=True, default=False,
              help="Do not auto-detect git commit SHA.")
@build_source_dump_options  # --build-info / --sources (embed inline)
def dump_cmd(so_path: Path | None, headers: tuple[Path, ...], includes: tuple[Path, ...],
             public_headers: tuple[Path, ...], public_header_dirs: tuple[Path, ...],
             version: str, lang: str, header_backend: str, output: Path | None,
             gcc_path: str | None, gcc_prefix: str | None, gcc_options: str | None,
             gcc_option_tokens: tuple[str, ...],
             sysroot: Path | None, nostdinc: bool, pdb_path: Path | None,
             follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
             dwarf_only: bool, show_data_sources: bool,
             debug_format_opt: str | None,
             debug_format: str | None,
             compile_db_path: Path | None, compile_db_path_alt: Path | None,
             compile_db_filter: str | None,
             debug_roots: tuple[Path, ...],
             debuginfod: bool, debuginfod_url: str | None,
             verbose: bool,
             git_tag: str | None, build_id: str | None, no_git: bool,
             build_info: Path | None = None, sources: Path | None = None,
             build_config: Path | None = None, allow_build_query: bool = False,
             build_query: str | None = None, build_compile_db: str | None = None,
             collect_mode: str = "source-target",
             depth: str | None = None, max_depth: bool = False) -> None:
    """Dump ABI snapshot of a shared library to JSON.

    \b
    Example:
      abicheck dump libfoo.so.1 -H include/foo.h --version 1.2.3 -o snap.json
      abicheck dump --sources ./libfoo-src/ -o libfoo.src.json  # source-only (no binary)
    """
    _setup_verbosity(verbose)

    # Resolve the --depth/--max preset into the underlying --collect-mode before
    # any dump path runs, so every branch (source-only / PE-Mach-O / ELF) embeds
    # the same evidence depth (G21.1).
    collect_mode = resolve_dump_depth(
        depth, max_depth, collect_mode,
        click.get_current_context().get_parameter_source("collect_mode")
        == click.core.ParameterSource.COMMANDLINE,
    )

    # Source-only dump (no binary) for the parallel-baseline / merge flow.
    if so_path is None:
        if show_data_sources:
            raise click.UsageError(
                "--show-data-sources requires SO_PATH; source-only dump cannot "
                "produce binary data-source diagnostics."
            )
        from .cli_buildsource import dump_source_only
        dump_source_only(sources, build_info, version, output, build_config, allow_build_query, git_tag, build_id, no_git, collect_mode, build_query=build_query, build_compile_db=build_compile_db)
        return

    effective_debug_format = resolve_dump_debug_format(debug_format_opt, debug_format)
    effective_compile_db = resolve_dump_compile_db(compile_db_path, compile_db_path_alt, headers)

    # --show-data-sources: diagnostic output and exit
    if show_data_sources:
        _print_data_sources(
            so_path,
            bool(headers),
            build_source_path=build_info,
            sources_path=sources,
        )
        return

    # Auto-detect binary format — PE/Mach-O skip the ELF/castxml path. The
    # conventional ``libfoo.so`` dev symlink is often a GNU ld linker script;
    # follow it to the real shared library before dispatching.
    so_path, binary_fmt = _normalize_binary_input(so_path)
    if effective_debug_format is not None and binary_fmt in ("pe", "macho"):
        raise click.BadParameter(
            f"--{effective_debug_format} is only supported for ELF binaries, not {binary_fmt.upper()}."
        )
    if binary_fmt in ("pe", "macho"):
        if gcc_option_tokens or gcc_options:
            click.echo(
                "Warning: --gcc-option/--gcc-options are not applied on the "
                f"native {binary_fmt.upper()} dump path and will be ignored.",
                err=True,
            )
        _handle_non_elf_dump(
            so_path, binary_fmt, headers, includes, version, lang, pdb_path,
            follow_deps, git_tag, build_id, no_git, output, public_headers,
            public_header_dirs, build_info, sources, build_config,
            allow_build_query, collect_mode, build_query, build_compile_db,
            header_backend=header_backend,
        )
        return

    build_context_flags = _resolve_build_context_flags(
        effective_compile_db, headers, compile_db_filter,
    )
    effective_gcc_options = _merge_gcc_options(build_context_flags, gcc_options)

    # Debug artifact resolution (ADR-021a): resolve before dump
    if debug_roots or debuginfod:
        artifact = _resolve_debug_artifact(
            so_path, debug_roots, debuginfod, debuginfod_url,
        )
        if artifact:
            click.echo(f"Debug info: {artifact.source}", err=True)

    perform_elf_dump(
        so_path=so_path,
        headers=headers,
        includes=includes,
        version=version,
        lang=lang,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        effective_gcc_options=effective_gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        dwarf_only=dwarf_only,
        effective_debug_format=effective_debug_format,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        header_backend=header_backend,
        effective_compile_db=effective_compile_db,
        follow_deps=follow_deps,
        search_paths=search_paths,
        ld_library_path=ld_library_path,
        git_tag=git_tag,
        build_id=build_id,
        no_git=no_git,
        output=output,
        build_info=build_info,
        sources=sources,
        build_config=build_config,
        allow_build_query=allow_build_query,
        collect_mode=collect_mode,
        expand_header_inputs=_expand_header_inputs,
        populate_dependency_info=_populate_dependency_info,
        stamp_provenance=_stamp_provenance,
        write_snapshot_output=_write_snapshot_output,
        build_query=build_query,
        build_compile_db=build_compile_db,
    )


def _handle_non_elf_dump(
    so_path: Path,
    binary_fmt: str,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    version: str,
    lang: str,
    pdb_path: Path | None,
    follow_deps: bool,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
    output: Path | None,
    public_headers: tuple[Path, ...] = (),
    public_header_dirs: tuple[Path, ...] = (),
    build_info: Path | None = None,
    sources: Path | None = None,
    build_config: Path | None = None,
    allow_build_query: bool = False,
    collect_mode: str = "source-target",
    build_query: str | None = None,
    build_compile_db: str | None = None,
    header_backend: str = "auto",
) -> None:
    """Handle PE/Mach-O native dump path and output writing."""
    if follow_deps:
        click.echo("Warning: --follow-deps is only supported for ELF binaries.", err=True)
    try:
        snap = _dump_native_binary(
            so_path, binary_fmt, list(headers), list(includes), version, lang,
            pdb_path=pdb_path,
            public_headers=list(public_headers),
            public_header_dirs=list(public_header_dirs),
            header_backend=header_backend,
        )
    except click.ClickException:
        raise
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    _write_snapshot_output(
        snap, output, build_info, sources, build_config, allow_build_query,
        collect_mode, build_query=build_query, build_compile_db=build_compile_db,
    )


def _resolve_debug_artifact(
    so_path: Path,
    debug_roots: tuple[Path, ...],
    debuginfod: bool,
    debuginfod_url: str | None,
) -> DebugArtifact | None:
    """Resolve optional separate debug artifacts for dump."""
    from .debug_resolver import resolve_debug_info

    return resolve_debug_info(
        so_path,
        debug_roots=list(debug_roots) or None,
        enable_debuginfod=debuginfod,
        debuginfod_urls=[debuginfod_url] if debuginfod_url else None,
    )


def _validate_show_only(
    ctx: click.Context, param: click.Parameter, value: str | None,
) -> str | None:
    """Eagerly validate --show-only tokens so invalid ones surface early."""
    if value is None:
        return None
    from .reporter import ShowOnlyFilter
    try:
        ShowOnlyFilter.parse(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    return value


def _render_output(
    fmt: str,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None = None,
    *,
    follow_deps: bool = False,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    stat: bool = False,
    severity_config: SeverityConfig | None = None,
    show_recommendation: bool = False,
    demangle: bool = False,
) -> str:
    """Render comparison result in the requested output format."""
    from .service import render_output
    return render_output(
        fmt, result, old, new,
        follow_deps=follow_deps, show_only=show_only,
        report_mode=report_mode, show_impact=show_impact,
        stat=stat, severity_config=severity_config,
        show_recommendation=show_recommendation,
        demangle=demangle,
    )


def _load_probe_matrix_changes(
    probe_matrix_old: Path | None, probe_matrix_new: Path | None,
) -> list[Change] | None:
    """Load build-config matrix snapshots and return diff_matrix() findings.

    These findings (CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV,
    BEHAVIOURAL_DEFAULT_CHANGED) need multi-configuration inputs the plain
    compare() does not have, so they are computed here and merged in (G2).
    """
    if probe_matrix_old is None and probe_matrix_new is None:
        return None
    if probe_matrix_old is None or probe_matrix_new is None:
        raise click.UsageError(
            "--probe-matrix-old and --probe-matrix-new must be given together."
        )
    from .diff_build_config import diff_matrix
    from .probe_harness import load_matrix_snapshot

    old_matrix = load_matrix_snapshot(probe_matrix_old)
    new_matrix = load_matrix_snapshot(probe_matrix_new)
    return list(diff_matrix(old_matrix, new_matrix))


# ---------------------------------------------------------------------------
# Shared helpers for CLI commands
# ---------------------------------------------------------------------------


def _warn_all_suppressed(result: DiffResult) -> None:
    """Warn if a suppression file swallowed all changes."""
    total_changes = len(result.changes) + result.suppressed_count
    if result.suppression_file_provided and total_changes > 0 and len(result.changes) == 0:
        click.echo(
            "Warning: all ABI changes were suppressed by the suppression file. "
            "Verify your suppression rules are not too broad.",
            err=True,
        )


def _maybe_emit_annotations(
    result: DiffResult,
    *,
    annotate: bool,
    annotate_additions: bool,
    write_step_summary: bool = True,
) -> None:
    """Emit GitHub annotations to stderr if --annotate is set and running in CI."""
    if not annotate:
        return

    from .annotations import (
        collect_annotations,
        emit_github_step_summary,
        format_annotations,
        is_github_actions,
    )

    if not is_github_actions():
        return

    annotations = collect_annotations(result, annotate_additions=annotate_additions)
    text = format_annotations(annotations)
    if text:
        click.echo(text, err=True)

    if write_step_summary:
        emit_github_step_summary(result)


def _write_release_step_summary(text: str, fmt: str) -> None:
    """Write a single step summary for compare-release when running in CI."""
    import os as _os

    summary_path = _os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    from .annotations import is_github_actions

    if not is_github_actions():
        return

    # For markdown output, write the summary directly.
    # For JSON, wrap it in a code block.
    if fmt == "json":
        content = f"```json\n{text}\n```\n"
    else:
        content = text + "\n"

    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(content)


def _write_or_echo(output: Path | None, text: str) -> None:
    """Write text to file or echo to stdout."""
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)


def _announce_exit_scheme(
    severity_explicitly_set: bool, sev_config: SeverityConfig | None,
    *, fmt: str = "markdown", stat: bool = False,
) -> None:
    """Announce (on stderr) which exit-code scheme the compare command uses.

    Kept on stderr so it never pollutes the report on stdout. Emitted only by
    the ``compare`` command (not compare-release / appcompat), and only for the
    human-readable formats — machine formats (json/sarif/junit) and the
    one-line ``--stat`` summary are consumed by tooling that treats the whole
    captured stream as data, so the banner is suppressed there.
    """
    if stat or fmt not in {"markdown", "html", "review"}:
        return
    if severity_explicitly_set:
        click.echo(
            "Exit-code scheme: severity-aware (per-category --severity-* settings).",
            err=True,
        )
    else:
        click.echo(
            "Exit-code scheme: legacy verdict (0=compatible, 2=API break, 4=ABI break). "
            "Pass --severity-preset/--severity-* for the severity-aware scheme.",
            err=True,
        )


def _exit_with_severity_or_verdict(
    result: DiffResult, sev_config: SeverityConfig | None, severity_explicitly_set: bool,
) -> None:
    """Exit with appropriate code based on severity config or legacy verdict."""
    from .severity import compute_exit_code, legacy_exit_code
    if severity_explicitly_set:
        assert sev_config is not None
        eff_sets = result._effective_kind_sets()
        exit_code = compute_exit_code(
            result.changes,
            sev_config,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
        if exit_code != 0:
            sys.exit(exit_code)
    else:
        code = legacy_exit_code(result.verdict)
        if code != 0:
            sys.exit(code)


def _log_one_side_debug(
    label: str, binary: Path, droots: list[Path],
    *,
    debuginfod: bool, debuginfod_url: str | None,
) -> None:
    """Resolve and log debug info for a single binary side, if applicable."""
    if _detect_binary_format(binary) is None or not (droots or debuginfod):
        return
    from .debug_resolver import resolve_debug_info

    artifact = resolve_debug_info(
        binary,
        debug_roots=droots or None,
        enable_debuginfod=debuginfod,
        debuginfod_urls=[debuginfod_url] if debuginfod_url else None,
    )
    if artifact:
        click.echo(f"Debug info ({label}): {artifact.source}", err=True)


def _log_debug_resolution(
    old_input: Path, new_input: Path,
    resolved_old_debug: list[Path], resolved_new_debug: list[Path],
    *,
    debuginfod: bool, debuginfod_url: str | None,
) -> None:
    """Resolve and log per-side debug info (debug roots / debuginfod), if any."""
    if not (resolved_old_debug or resolved_new_debug or debuginfod):
        return
    _log_one_side_debug(
        "old", old_input, resolved_old_debug,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
    )
    _log_one_side_debug(
        "new", new_input, resolved_new_debug,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
    )


def _finalize_compare_result(
    result: DiffResult, old_input: Path, new_input: Path,
    *,
    show_redundant: bool, show_filtered: bool,
    annotate: bool, annotate_additions: bool,
) -> None:
    """Attach metadata and emit redundancy/filter/suppression/annotation output."""
    result.old_metadata = _collect_metadata(old_input)
    result.new_metadata = _collect_metadata(new_input)

    if show_redundant and result.redundant_changes:
        _merge_redundant_changes(result)
    if show_filtered and result.out_of_surface_changes:
        echo_filtered_surface(result)

    # The scoping fallback warning goes to stderr so it never corrupts the
    # machine-readable payload on stdout (which carries scope_resolved /
    # manual_review_required for programmatic consumers).
    if result.scope_to_public_surface and not result.scope_resolved:
        click.echo(
            "Warning: --scope-public-headers could not resolve the public "
            "surface (no header-derived public symbols); fell back to the full "
            "export table. Compatibility is UNCONFIRMED — treat this result as "
            "manual-review-required, not a clean public surface.",
            err=True,
        )

    _warn_all_suppressed(result)
    _maybe_emit_annotations(
        result, annotate=annotate, annotate_additions=annotate_additions
    )


@main.command("compare")
@click.argument("old_input", type=click.Path(exists=True, path_type=Path))
@click.argument("new_input", type=click.Path(exists=True, path_type=Path))
# ── Dump options (used when input is an ELF binary) ──────────────────────────
@click.option("-H", "--header", "headers", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header file or directory applied to both sides (repeat for multiple). "
                   "Recommended for full ABI analysis; without headers, native binaries fall back to symbols-only mode. "
                   "Scopes the ABI surface to declarations in these headers for ELF; on PE/Mach-O scoping is "
                   "best-effort and falls back to the export table when castxml is unavailable or names don't match "
                   "(e.g. MSVC C++ mangling). Validated for native binaries; ignored for snapshots.")
@click.option("-I", "--include", "includes", multiple=True,
              type=click.Path(path_type=Path),
              help="Extra include directory for castxml (applied to both sides).")
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False),
              help="Language mode for the header backend.")
@click.option("--header-backend", "header_backend", default="auto", show_default=True,
              type=click.Choice(["auto", "castxml", "clang"], case_sensitive=False),
              help="L2 header-AST frontend for native-binary inputs: castxml "
                   "(default) or clang (-ast-dump=json; for clang-only hosts). "
                   "auto = castxml if present, else clang. Env: ABICHECK_HEADER_BACKEND.")
@click.option("--old-header", "old_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for old side only (overrides -H for old). "
                   "Validated for native binaries; ignored for snapshots.")
@click.option("--new-header", "new_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for new side only (overrides -H for new). "
                   "Validated for native binaries; ignored for snapshots.")
@click.option("--old-include", "old_includes_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Include dir for old side only (overrides -I for old).")
@click.option("--new-include", "new_includes_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Include dir for new side only (overrides -I for new).")
@click.option("--old-version", "old_version", default="old", show_default=True,
              help="Version label for old side (used when input is a .so file).")
@click.option("--new-version", "new_version", default="new", show_default=True,
              help="Version label for new side (used when input is a .so file).")
# ── Compare options (unchanged) ──────────────────────────────────────────────
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "sarif", "html", "junit", "review"]),
              default="markdown", show_default=True,
              help="Output format. 'review' emits a compact GitHub-facing digest "
                   "(verdict + counts + release recommendation + manual-review banner) "
                   "suitable for a job summary or PR comment.")
@click.option("--demangle/--no-demangle", default=None,
              help="Demangle C++ symbol names in markdown/review output (default "
                   "ON; use --no-demangle to turn off). json/sarif always keep raw "
                   "mangled names, and HTML is rendered structurally and is never "
                   "demangled regardless of this flag.")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.option("--suppress", type=click.Path(exists=True, path_type=Path), default=None,
              help="Suppression file (YAML) to filter known/intentional changes.")
@click.option("--strict-suppressions", is_flag=True, default=False,
              help="Fail with exit code 1 if any suppression rule has expired.")
@click.option("--require-justification", is_flag=True, default=False,
              help="Require every suppression rule to have a non-empty 'reason' field.")
@click.option("--policy", "policy",
              type=click.Choice(["strict_abi", "sdk_vendor", "plugin_abi"], case_sensitive=True),
              default="strict_abi", show_default=True,
              help="Built-in policy profile for verdict classification. Ignored when --policy-file is given.")
@click.option("--policy-file", "policy_file_path",
              type=POLICY_FILE_PARAM, default=None,
              help="YAML policy file with per-kind verdict overrides, or a built-in name (e.g. 'security'). Overrides --policy.")
@click.option("--pdb-path", "pdb_path", type=click.Path(path_type=Path), default=None,
              help="Explicit PDB file path for Windows PE debug info (applied to both sides). "
                   "Overrides automatic PDB discovery.")
@click.option("--old-pdb-path", "old_pdb_path", type=click.Path(path_type=Path), default=None,
              help="PDB file path for old side only (overrides --pdb-path for old).")
@click.option("--new-pdb-path", "new_pdb_path", type=click.Path(path_type=Path), default=None,
              help="PDB file path for new side only (overrides --pdb-path for new).")
@click.option("--dwarf-only", is_flag=True, default=False,
              help="Force DWARF-only mode for both sides: use DWARF debug info "
                   "as primary data source even when headers are available.")
@click.option("--severity-preset", "severity_preset",
              type=click.Choice(["default", "strict", "info-only"], case_sensitive=True),
              default=None,
              help="Severity preset: 'default', 'strict', or 'info-only'. "
                   "Controls exit codes and report labels. Per-category "
                   "--severity-* options override the chosen preset.")
@click.option("--severity-abi-breaking", "severity_abi_breaking",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for clear ABI/API incompatibilities (overrides preset).")
@click.option("--severity-potential-breaking", "severity_potential_breaking",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for potential incompatibilities needing review (overrides preset).")
@click.option("--severity-quality-issues", "severity_quality_issues",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for problematic behaviors like std symbol leaks (overrides preset).")
@click.option("--severity-addition", "severity_addition",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for new public API additions (overrides preset).")
@click.option("--follow-deps", is_flag=True, default=False,
              help="Resolve transitive dependencies for both old and new, compute symbol "
                   "bindings, and include a dependency-change section in the report. ELF only.")
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries (with --follow-deps).")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (with --follow-deps).")
@click.option("--show-redundant", is_flag=True, default=False,
              help="Disable redundancy filtering and show all changes including those "
                   "derived from root type changes.")
@click.option("--scope-public-headers/--no-scope-public-headers", "scope_public_headers",
              default=True, show_default=True,
              help="Restrict findings to the public-header ABI surface (ADR-024): "
                   "changes to symbols/types not reachable from public-header-declared "
                   "exported API are recorded as filtered, not reported. Internal-type "
                   "leaks are never hidden. On by default; use --no-scope-public-headers "
                   "to report every finding regardless of surface.")
@click.option("--collapse-versioned-symbols", "collapse_versioned_symbols", is_flag=True, default=False,
              help="Opt-in (G15): when a versioned-symbol scheme is detected (most removed "
                   "symbols reappear differing only by a version token, e.g. ICU u_*_NN), "
                   "reclassify those version-rename pairs as compatible so the verdict "
                   "reflects the real delta, not the rename churn. A real SONAME bump and "
                   "non-versioned removals still drive the verdict.")
@click.option("--show-filtered", "show_filtered", is_flag=True, default=False,
              help="List findings excluded by --scope-public-headers (audit trail).")
@click.option("--public-symbol", "public_symbols", multiple=True,
              help="Widening overlay (ADR-024 §D6): force a symbol (mangled or demangled "
                   "name) into the public surface even when header provenance can't see it "
                   "(asm stubs, .def exports, extern \"C\" shims, MSVC-mangling gaps). "
                   "Repeatable. Only meaningful with --scope-public-headers.")
@click.option("--public-symbols-list", "public_symbols_list",
              type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="File of symbols to force public (one per line; '#' comments and blank "
                   "lines ignored), à la abi-compliance-checker -symbols-list. "
                   "Merged with --public-symbol.")
@click.option("--probe-matrix-old", "probe_matrix_old", type=click.Path(exists=True, path_type=Path),
              default=None,
              help="Old build-configuration matrix snapshot (from 'abicheck probe run'). "
                   "When given with --probe-matrix-new, build-config findings "
                   "(CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV, "
                   "BEHAVIOURAL_DEFAULT_CHANGED) are folded into this comparison's "
                   "verdict and report (G2: probe -> compare).")
@click.option("--probe-matrix-new", "probe_matrix_new", type=click.Path(exists=True, path_type=Path),
              default=None,
              help="New build-configuration matrix snapshot (pairs with --probe-matrix-old).")
@click.option("--show-only", "show_only", default=None,
              callback=_validate_show_only, expose_value=True, is_eager=False,
              help="Comma-separated filter tokens to limit displayed changes. "
                   "Severity: breaking, api-break, risk, compatible. "
                   "Element: functions, variables, types, enums, elf. "
                   "Action: added, removed, changed. "
                   "AND across dimensions, OR within. Does not affect exit codes.")
@click.option("--stat", is_flag=True, default=False,
              help="One-line summary output for CI gates. "
                   "With --format json, emits only the summary object.")
@click.option("--report-mode", "report_mode",
              type=click.Choice(["full", "leaf", "impact"], case_sensitive=True),
              default="full", show_default=True,
              help="Report mode: 'full' lists all changes individually (default), "
                   "'leaf' groups by root type changes with impact lists, "
                   "'impact' behaves as 'full' with the impact summary table enabled "
                   "(equivalent to --report-mode full --show-impact).")
@click.option("--show-impact", is_flag=True, default=False,
              help="Append an impact summary table showing root changes and affected interfaces.")
@click.option("--recommend", is_flag=True, default=False,
              help="Append a release recommendation (semver bump + SONAME action) to the "
                   "report. Always present in --format json under 'release_recommendation'.")
@click.option("--debug-format", "debug_format_opt",
              type=click.Choice(["auto", "dwarf", "btf", "ctf"], case_sensitive=False), default=None,
              help="Force the ELF debug format for both sides (auto=pick best available). "
                   "Supersedes the individual --btf/--ctf/--dwarf flags.")
@click.option("--btf", "debug_format", flag_value="btf", default=None, hidden=True,
              help="Force BTF debug format for both sides (ELF only).")
@click.option("--ctf", "debug_format", flag_value="ctf", hidden=True,
              help="Force CTF debug format for both sides (ELF only).")
@click.option("--dwarf", "debug_format", flag_value="dwarf", hidden=True,
              help="Force DWARF debug format for both sides (ELF only).")
@click.option("--annotate", is_flag=True, default=False,
              help="Emit GitHub Actions workflow command annotations to stderr. "
                   "Annotations appear as inline comments on PR diffs. "
                   "Only effective when GITHUB_ACTIONS=true.")
@click.option("--annotate-additions", is_flag=True, default=False,
              help="Include additions/compatible changes as ::notice annotations "
                   "(requires --annotate).")
# ── Debug artifact resolution (ADR-021a) ──────────────────────────────────────
@click.option("--debug-root", "debug_roots", multiple=True, type=click.Path(path_type=Path),
              help="Directory containing separate debug files (build-id trees, "
                   "path-mirror, dSYM bundles). Applied to both sides. Can be repeated.")
@click.option("--debug-root1", "debug_roots_old", multiple=True, type=click.Path(path_type=Path),
              help="Debug root for old side only (overrides --debug-root for old).")
@click.option("--debug-root2", "debug_roots_new", multiple=True, type=click.Path(path_type=Path),
              help="Debug root for new side only (overrides --debug-root for new).")
@click.option("--debuginfod", is_flag=True, default=False,
              help="Enable debuginfod network resolution for debug info (opt-in).")
@click.option("--debuginfod-url", "debuginfod_url", default=None,
              help="debuginfod server URL (overrides DEBUGINFOD_URLS env var).")
@build_source_compare_options  # --old/new-build-info, --old/new-sources, --collect-mode
@adr027_compare_options  # ADR-027: --pattern-verdicts/--explain-patterns/--surface-metrics
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="Enable verbose/debug output.")
def compare_cmd(
    old_input: Path, new_input: Path,
    headers: tuple[Path, ...], includes: tuple[Path, ...], lang: str,
    header_backend: str,
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
    old_version: str, new_version: str,
    fmt: str, demangle: bool | None, output: Path | None,
    suppress: Path | None, strict_suppressions: bool, require_justification: bool,
    policy: str, policy_file_path: Path | None,
    pdb_path: Path | None, old_pdb_path: Path | None, new_pdb_path: Path | None,
    dwarf_only: bool,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
    follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
    show_redundant: bool, show_only: str | None, stat: bool,
    scope_public_headers: bool, collapse_versioned_symbols: bool, show_filtered: bool,
    public_symbols: tuple[str, ...], public_symbols_list: Path | None,
    report_mode: str, show_impact: bool,
    recommend: bool,
    debug_format_opt: str | None,
    debug_format: str | None,
    annotate: bool,
    annotate_additions: bool,
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
    debuginfod: bool,
    debuginfod_url: str | None,
    pattern_verdicts: bool,
    explain_patterns: bool,
    surface_metrics: bool,
    verbose: bool,
    old_build_info: Path | None = None, new_build_info: Path | None = None,
    old_sources: Path | None = None, new_sources: Path | None = None,
    collect_mode: str = "off",
    probe_matrix_old: Path | None = None,
    probe_matrix_new: Path | None = None,
) -> None:
    """Compare two ABI surfaces and report changes.

    Each input (OLD, NEW) can be a .so shared library, a JSON snapshot from
    'abicheck dump', or an ABICC Perl dump file. The format is auto-detected.

    When a .so file is given, headers (-H) are recommended for full ABI
    extraction. If headers are absent for ELF, abicheck falls back to
    DWARF-only mode (if DWARF available) or symbols-only analysis.

    \b
    Exit codes (legacy, without --severity-* flags):
      0  NO_CHANGE, COMPATIBLE, or COMPATIBLE_WITH_RISK — no binary ABI break
         (COMPATIBLE_WITH_RISK: deployment risk present; check the report)
      2  API_BREAK — source-level API break — recompilation required
      4  BREAKING — binary ABI break detected
    \b
    Exit codes (severity-aware, with any --severity-* flag):
      0  No error-level findings
      1  Error-level findings in addition or quality_issues only
      2  Error-level findings in potential_breaking (but not abi_breaking)
      4  Error-level findings in abi_breaking
    \b
    Invalid invocation (bad arguments/options, unreadable or unrecognised
    input) exits 64, outside the result space above, so it is never mistaken
    for an ABI verdict.

    \b
    Examples:
    \b
      # One-liner: each version has its own header (primary flow)
      abicheck compare libfoo.so.1 libfoo.so.2 \\
        --old-header include/v1/foo.h --new-header include/v2/foo.h
    \b
      # Shorthand: -H when the same header applies to both versions
      abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h
    \b
      # With version labels and SARIF output
      abicheck compare libfoo.so.1 libfoo.so.2 \\
        --old-header v1/foo.h --new-header v2/foo.h \\
        --old-version 1.0 --new-version 2.0 --format sarif -o abi.sarif
    \b
      # Compare saved snapshot vs current build (mixed mode)
      abicheck compare baseline.json ./build/libfoo.so --new-header include/foo.h
    \b
      # Compare two pre-dumped snapshots (existing workflow)
      abicheck compare libfoo-1.0.json libfoo-2.0.json
    \b
      # Policy and suppression
      abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h --policy sdk_vendor
      abicheck compare old.json new.json --suppress suppressions.yaml
    """
    _setup_verbosity(verbose)

    if annotate_additions and not annotate:
        raise click.UsageError("--annotate-additions requires --annotate")

    # Reconcile the --debug-format selector with the legacy --btf/--ctf/--dwarf
    # flags. The selector supersedes the legacy flags whenever it is given:
    # an explicit "auto" returns to auto-detection (None) even if a legacy flag
    # is also present; only when the selector is absent do the legacy flags apply.
    if debug_format_opt is not None:
        effective_debug_format = None if debug_format_opt.lower() == "auto" else debug_format_opt
    else:
        effective_debug_format = debug_format

    # Tri-state --demangle: default ON for the text formats whose renderer
    # post-processes symbols through demangle_text (markdown/review), OFF for
    # machine formats (json/sarif/junit) and HTML — the HTML renderer emits
    # symbols structurally and demangling its string would inject unescaped
    # '<'/'>'/'&' from C++ names and corrupt the markup. Explicit flag wins.
    if demangle is None:
        demangle = fmt in {"markdown", "review"}

    # --report-mode impact is sugar for "full" report with the impact table on.
    if report_mode == "impact":
        report_mode = "full"
        show_impact = True

    sev_config, severity_explicitly_set = _resolve_severity(
        severity_preset, severity_abi_breaking,
        severity_potential_breaking, severity_quality_issues, severity_addition,
    )

    old_h, new_h, old_inc, new_inc = _resolve_per_side_options(
        headers, includes, old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
    )

    # Follow GNU ld linker scripts up front so the resolved DSO (not the text
    # script) drives format detection, metadata, and dependency analysis.
    old_input, old_fmt = _normalize_binary_input(old_input)
    new_input, new_fmt = _normalize_binary_input(new_input)
    # --debug-format / legacy --btf/--ctf/--dwarf force an ELF debug format and
    # are silently ignored by the PE/Mach-O dump paths. Reject them up front for
    # non-ELF binary inputs (mirrors dump_cmd) so the flag is never accepted but
    # ignored. JSON-snapshot / dump inputs have *_fmt == None and are unaffected.
    if effective_debug_format is not None:
        for side, bfmt in (("old", old_fmt), ("new", new_fmt)):
            if bfmt in ("pe", "macho"):
                raise click.BadParameter(
                    f"--debug-format {effective_debug_format} is only supported "
                    f"for ELF binaries, but the {side} input is {bfmt.upper()}."
                )
    _warn_ignored_flags(
        old_fmt is not None, new_fmt is not None,
        headers, includes,
        old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
    )

    # Resolve per-side debug roots: --debug-root1 overrides --debug-root for old, etc.
    resolved_old_debug = list(debug_roots_old) if debug_roots_old else list(debug_roots)
    resolved_new_debug = list(debug_roots_new) if debug_roots_new else list(debug_roots)
    _log_debug_resolution(
        old_input, new_input,
        resolved_old_debug, resolved_new_debug,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
    )

    old, new = _resolve_compare_snapshots(
        old_input, new_input, old_fmt, new_fmt,
        old_h, new_h, old_inc, new_inc,
        old_version, new_version, lang,
        pdb_path, old_pdb_path, new_pdb_path,
        dwarf_only, effective_debug_format,
        follow_deps, search_paths, ld_library_path,
        header_backend=header_backend,
    )

    suppression, pf = _load_suppression_and_policy(
        suppress, policy, policy_file_path,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
    )

    force_public = _collect_force_public_symbols(public_symbols, public_symbols_list)
    if force_public and not scope_public_headers:
        click.echo(
            "Warning: --public-symbol/--public-symbols-list only take effect with "
            "--scope-public-headers; ignoring the widening overlay.",
            err=True,
        )

    extra_changes = _load_probe_matrix_changes(probe_matrix_old, probe_matrix_new)

    # Build-info + source facts (ADR-028/033): the helper times inline diffing
    # for the D6/D9 metrics and returns coverage/metrics to attach post-compare.
    from .cli_buildsource import attach_evidence_metrics, prepare_embedded_build_source
    extra_changes, layer_coverage_rows, evidence_metrics, _ev_changes = (
        prepare_embedded_build_source(
            old, new, collect_mode, extra_changes,
            old_build_info, new_build_info, old_sources, new_sources,
            policy_file=pf,
        )
    )

    apply_patterns = pattern_verdicts or explain_patterns  # --explain implies on
    result = compare(
        old, new, suppression=suppression, policy=policy, policy_file=pf,
        scope_to_public_surface=scope_public_headers,
        force_public_symbols=force_public,
        extra_changes=extra_changes,
        pattern_verdicts=apply_patterns,
        surface_metrics=surface_metrics,
        collapse_versioned_symbols=collapse_versioned_symbols,
    )
    if layer_coverage_rows:
        result.layer_coverage = layer_coverage_rows
    # Pass all injected findings (probe-matrix + evidence) so artifact-backed
    # excludes them — none come from L0-L2 diffing.
    attach_evidence_metrics(result, evidence_metrics, extra_changes or [])

    if explain_patterns:
        echo_pattern_modulations(result)

    _finalize_compare_result(
        result, old_input, new_input,
        show_redundant=show_redundant, show_filtered=show_filtered,
        annotate=annotate, annotate_additions=annotate_additions,
    )

    text = _render_output(
        fmt, result, old, new,
        follow_deps=follow_deps,
        show_only=show_only, report_mode=report_mode,
        show_impact=show_impact, stat=stat,
        severity_config=sev_config if severity_explicitly_set else None,
        show_recommendation=recommend,
        demangle=demangle,
    )

    _write_or_echo(output, text)

    _announce_exit_scheme(severity_explicitly_set, sev_config, fmt=fmt, stat=stat)
    _exit_with_severity_or_verdict(result, sev_config, severity_explicitly_set)


@main.command("recommend-collect-mode")
@click.argument("paths", nargs=-1)
def recommend_collect_mode_cmd(paths: tuple[str, ...]) -> None:
    """Recommend a `--collect-mode` from a PR's changed paths (ADR-033 D3).

    Prints `build` for build-system-only changes, `source-changed` when sources
    or headers changed, else `off`. The artifact compare stays authoritative —
    this only scopes which optional evidence a CI job should collect.
    """
    from .buildsource.source_replay import recommend_collect_mode
    click.echo(recommend_collect_mode(paths))


# ── ABICC compat subcommands (implementation in abicheck.compat) ─────────────
# NOTE: eagerly loads abicheck.compat.cli at import time — intentional so all
# consumers get compat commands registered. Private helpers re-exported for
# backward compatibility with code importing from abicheck.cli directly.
from .compat.cli import (  # noqa: E402,F401
    _API_BREAK_KINDS,
    _BINARY_ONLY_KINDS,
    _NEW_SYMBOL_KINDS,
    _P2_STUB_FLAGS,
    _apply_strict,
    _apply_warn_newsym,
    _build_internal_suppression,
    _build_skip_suppression,
    _build_whitelist_suppression,
    _classify_compat_error_exit_code,
    _compat_fail,
    _detect_compiler_version,
    _do_echo,
    _filter_binary_only,
    _filter_source_only,
    _limit_affected_changes,
    _load_descriptor_or_dump,
    _load_skip_headers,
    _merge_suppression,
    _resolve_headers_from_list,
    _safe_path,
    _setup_logging,
    _warn_stub_flags,
    _write_affected_list,
)

# fmt: on

main.add_command(compat_group)


# ---------------------------------------------------------------------------
# Sub-command modules. Imported for side-effect so their @main.command(...)
# decorators register the commands on the Click group above. They sit in
# sibling files to keep this module under the AI-readiness file-size limit.
# ---------------------------------------------------------------------------
from . import (  # noqa: E402  — must run after `main` and helpers are defined
    cli_appcompat,  # noqa: F401  — registers appcompat
    cli_baseline,  # noqa: F401  — registers baseline
    cli_buildsource,  # noqa: F401  — registers collect
    cli_compare_release,  # noqa: F401  — registers compare-release
    cli_debian_symbols,  # noqa: F401  — registers debian-symbols
    cli_plugin,  # noqa: F401  — registers plugin-check
    cli_pr_comment,  # noqa: F401  — registers pr-comment
    cli_probe,  # noqa: F401  — registers probe (run, compare)
    cli_scan,  # noqa: F401  — registers scan
    cli_stack,  # noqa: F401  — registers deps, stack-check
    cli_suggest,  # noqa: F401  — registers suggest-suppressions
    cli_surface,  # noqa: F401  — registers surface-report
)

if __name__ == "__main__":
    main()
