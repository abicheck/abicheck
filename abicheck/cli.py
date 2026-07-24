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

import dataclasses
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

from . import deadline
from .checker import DiffResult, LibraryMetadata
from .cli_audit import echo_filtered_surface, echo_reconciled
from .cli_dump_helpers import (
    _dump_will_attempt_hybrid_l4_extraction,
    check_requested_depth_satisfied,
    fold_dump_provenance_into_json,
    handle_non_elf_dump,
    has_other_l3_source,
    perform_elf_dump,
    resolve_compile_db_l3_reuse,
    resolve_dump_collect_context,
    resolve_dump_compile_context,
    resolve_dump_compile_db,
    resolve_dump_debug_format,
)
from .cli_help import compare_help_options, configure_rich_help
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
    apply_compare_profile,
    build_source_dump_options,
    compile_context_options,
    debug_resolution_options,
    env_matrix_option,
    evidence_options,
    header_graph_options,
    lang_option,
    normalize_sided_options,
    output_options,
    policy_options,
    profile_option,
    release_options,
    scope_options,
    set_input_options,
    severity_options,
    two_sided_input_options,
    verbose_option,
)
from .cli_params import (
    SIDED_EXISTING_PATH_PARAM,
    SIDED_PATH_PARAM,
    _load_suppression_and_policy as _load_suppression_and_policy,  # noqa: F401  — re-exported to keep cli import sites (test suite) stable
)
from .cli_resolve import (
    _apply_native_provenance,
    _detect_binary_format,
    _dump_native_binary,
    _expand_header_inputs,
    _is_supported_compare_input,
    _looks_like_application,
    _maybe_follow_linker_script,
    _normalize_binary_input,
    _populate_dependency_info,
    _resolve_compare_snapshots,
    _resolve_input,
    _resolve_linker_script,
    _sniff_text_format,
    classify_compare_operand,
)
from .compat.cli import compat_group
from .serialization import snapshot_to_json

if TYPE_CHECKING:
    from .buildsource.pack import BuildSourcePack
    from .checker_types import Change
    from .debug_resolver import DebugArtifact
    from .service_scan import CompileContext
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
    "_looks_like_application",
    "_maybe_follow_linker_script",
    "_normalize_binary_input",
    "_populate_dependency_info",
    "_resolve_compare_snapshots",
    "_resolve_input",
    "_resolve_linker_script",
    "_sniff_text_format",
    "classify_compare_operand",
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
                capture_output=True, text=True, timeout=5, check=False,
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


def _classify_missing_layers(
    pack: BuildSourcePack | None, missing: list[str]
) -> tuple[list[str], list[str]]:
    """Split *missing* layer values into (absent, ran_but_empty).

    ``absent`` — the layer never ran (no coverage row, or NOT_COLLECTED): the
    actionable fix is a compile DB / an installed frontend. ``ran_but_empty`` —
    a coverage row exists (PARTIAL/PRESENT) but the payload linked no facts: the
    fix is scoping/roots, not installing tools. Distinguishing the two stops the
    warning from telling users to install clang/castxml when those already ran.
    With no pack (or an unknown layer), default to ``absent`` so the legacy
    "not collected" wording still appears.
    """
    if pack is None:
        return list(missing), []
    from .buildsource.model import CoverageStatus, DataLayer

    by_value = {layer.value: layer for layer in DataLayer}
    absent: list[str] = []
    ran_empty: list[str] = []
    for value in missing:
        layer = by_value.get(value)
        cov = pack.manifest.coverage_for(layer) if layer is not None else None
        if cov is not None and cov.status != CoverageStatus.NOT_COLLECTED:
            ran_empty.append(value)
        else:
            absent.append(value)
    return absent, ran_empty


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
    extractor: str = "auto",
    inputs_pack: Path | None = None,
    depth: str | None = None,
) -> None:
    """Serialize snapshot and write to file or stdout.

    When *build_info* and/or *sources* are given, their normalized L3/L4/L5 facts
    are collected (inline from a source tree / build dir, or loaded from a pack
    directory) and embedded in the snapshot first (single-artifact UX) so a later
    ``compare old.json new.json`` needs no out-of-band packs. *collect_mode* (the
    ADR-033 D2 CI evidence mode) selects which layers and replay scope to collect:
    ``build`` captures L3 build context only, ``off`` collects nothing.
    *build_query* / *build_compile_db* are the CLI equivalents of the
    ``.abicheck.yml`` ``build.query`` / ``build.compile_db`` keys. *extractor* is
    the L4 source-ABI frontend — the same ``--ast-frontend`` knob that drives the
    L2 header AST (ADR-037 D8): one frontend choice across both pipeline stages.
    *depth* is the raw ``--depth`` CLI value (``None`` when not passed); when
    given, ``check_requested_depth_satisfied`` raises if the snapshot did not
    actually reach it.
    """
    if build_info is not None or sources is not None:
        from .cli_buildsource import embed_build_source
        embed_build_source(
            snap, build_info, sources,
            build_config=build_config, allow_build_query=allow_build_query,
            collect_mode=collect_mode,
            build_query=build_query, build_compile_db=build_compile_db,
            extractor=extractor,
        )
        # G21.7: fail loud — if a requested evidence layer came back empty, say so
        # prominently instead of leaving it buried in the coverage rows. Permissive
        # by design (a warning, not an error): --collection-mode strict on
        # `collect` remains the hard-fail path (ADR-028 D3).
        missing = _missing_requested_evidence_layers(snap.build_source, collect_mode)
        if missing:
            absent, ran_empty = _classify_missing_layers(snap.build_source, missing)
            parts: list[str] = []
            if absent:
                # Genuinely absent: no extractor / no compile DB / layer never ran.
                parts.append(
                    f"not collected: {', '.join(absent)} — supply "
                    "--build-info/--compile-db (a compile_commands.json, e.g. from "
                    "`bear -- make`), or install the clang/castxml source frontend"
                )
            if ran_empty:
                # Ran but produced/linked nothing — do NOT tell the user to install
                # tools they already have; point at the real cause in the coverage
                # rows (usually a public-header-roots or snapshot/source mismatch).
                parts.append(
                    f"collected but linked no facts: {', '.join(ran_empty)} — the "
                    "extractor ran but matched nothing; see the coverage rows for "
                    "the reason (commonly a public-header-roots mismatch, an "
                    "unseeded `--depth source` that selected 0 TUs — use "
                    "--changed-path/--since to seed a changed scope — or the "
                    "snapshot binary not matching --sources; a '0/N symbols "
                    "matched' means source decls did not link to the binary's "
                    "exports)"
                )
            click.echo(
                "Warning: requested evidence layer(s) " + "; ".join(parts) + ".",
                err=True,
            )
    # A build-emitted Flow-2 pack (--inputs) folds straight into the dump — the
    # plugin/wrapper flow in one command, no separate `merge` (after any inline
    # --sources/--build-info embed, so both fact sources combine).
    if inputs_pack is not None:
        from .cli_buildsource_merge import embed_inputs_pack
        embed_inputs_pack(snap, inputs_pack, output)
    # CLI-audit P1: an *explicitly* requested --depth that was not actually
    # reached is a hard failure, not a warning — see
    # check_requested_depth_satisfied's docstring. Checked last, after every
    # embed step above has had its chance to fill in build_source.
    check_requested_depth_satisfied(depth, snap)
    result = snapshot_to_json(snap)
    # Audit finding: dump/baseline provenance didn't record requested vs.
    # effective depth anywhere a later reader could inspect -- fold it into
    # the written JSON now that the strict gate above has had its say.
    result, resolved_depth_label = fold_dump_provenance_into_json(result, depth, snap)
    if output:
        _safe_write_output(output, result)
        click.echo(f"Snapshot written to {output}", err=True)
        # Self-describing output (CLI-audit P2): report the evidence depth
        # this snapshot actually reached -- computed from what it carries,
        # not the requested --depth, so an explicit --depth source that
        # collected nothing usable is never silently reported as if it had
        # succeeded. Only alongside the file-write notice above (never for
        # bare stdout output, which callers may pipe/parse as pure JSON).
        # Reuses fold_dump_provenance_into_json's own returned label (the
        # strict _gated_source_label, not the plain evidence_depth_label)
        # so this line can never disagree with the JSON's effective_depth
        # for the same dump -- they previously could, on the documented
        # zero-match-source-only case (external review).
        click.echo(f"Resolved evidence depth: {resolved_depth_label}", err=True)
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
    message="%(prog)s %(version)s (abicheck/abicheck)",
)
def main() -> None:
    """abicheck — ABI compatibility checker for C/C++ shared libraries."""
    # The plain CLI/CI path has no outer watchdog analogous to the MCP path's
    # service_scan._kill_process_tree; without this, an external SIGTERM
    # (job-scheduler cancellation, a CI step's own timeout) can orphan a
    # detached clang/castxml process group started by deadline.run_bounded
    # (Codex review, PR #591).
    deadline.install_sigterm_cleanup()


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
@lang_option
@click.option("-o", "--output", "output", type=click.Path(path_type=Path), default=None,
              help="Output JSON file. Defaults to stdout.")
# ── L2 compile context (shared with `scan` — ADR-037 D3 parity) ──────────────
# --ast-frontend / --gcc-path / --gcc-prefix / --gcc-options / --gcc-option /
# --sysroot / --nostdinc are defined once in cli_options.compile_context_options
# so `dump` and `scan` never drift; applied as a decorator below.
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
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Resolve and validate the invocation -- classify inputs, discover "
                   "config, show which evidence depths (binary/headers/build/source) "
                   "are available -- and print a report without producing a snapshot. "
                   "Writes nothing; incompatible with -o/--output.")
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
@verbose_option
# ── Provenance metadata ──────────────────────────────────────────────────────
@click.option("--git-tag", "git_tag", default=None,
              help="Git tag to embed in the snapshot (e.g. v2.0.0).")
@click.option("--build-id", "build_id", default=None,
              help="Opaque build identifier (CI run ID, build number, etc.).")
@click.option("--no-git", "no_git", is_flag=True, default=False,
              help="Do not auto-detect git commit SHA.")
@build_source_dump_options  # --build-info / --sources (embed inline)
@header_graph_options  # hidden deprecated no-op shim (shared with `compare`)
@compile_context_options  # --ast-frontend + cross-toolchain (shared with `scan`)
def dump_cmd(so_path: Path | None, headers: tuple[Path, ...], includes: tuple[Path, ...],
             public_headers: tuple[Path, ...], public_header_dirs: tuple[Path, ...],
             version: str, lang: str, header_backend: str, output: Path | None,
             gcc_path: str | None, gcc_prefix: str | None, gcc_options: str | None,
             gcc_option_tokens: tuple[str, ...],
             sysroot: Path | None, nostdinc: bool, pdb_path: Path | None,
             follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
             dwarf_only: bool, dry_run: bool,
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
             depth: str | None = None,
             header_graph_deprecated: bool = False,
             header_graph_includes_deprecated: bool = False,
             _resolved_compile_context: CompileContext | None = None,
             _resolved_collect_mode: str | None = None) -> None:
    """Dump ABI snapshot of a shared library to JSON.

    \b
    Example:
      abicheck dump libfoo.so.1 -H include/foo.h --version 1.2.3 -o snap.json
      abicheck dump --sources ./libfoo-src/ -o libfoo.src.json  # source-only (no binary)
    """
    from .cli_options import warn_deprecated_header_graph_flags
    from .dry_run import emit_dry_run, reject_dry_run_with_output

    warn_deprecated_header_graph_flags(
        header_graph_deprecated, header_graph_includes_deprecated
    )

    reject_dry_run_with_output(dry_run, output)
    _setup_verbosity(verbose)

    # Resolve the evidence-depth preset into the collect mode, apply --depth binary
    # suppression, and warn on an explicitly-requested deep depth without sources.
    collect_mode, headers, compile_db_path, compile_db_path_alt = resolve_dump_collect_context(
        depth, _resolved_collect_mode, sources, build_info,
        headers, compile_db_path, compile_db_path_alt,
    )

    # Fold the project's .abicheck.yml compile: block into the L2 compile context
    # (compare↔dump↔scan parity, ADR-037 D3): the same shared resolver scan uses,
    # so a dump honors `compile.std`/`defines`/`sysroot`/`frontend`/`include_dirs`
    # for its header AST the way scan does. CLI > config; an explicit --config or
    # the .abicheck.yml auto-discovered at the --sources root. Resolved before the
    # so_path-is-None dispatch (Codex review) -- resolve_dump_compile_context has
    # no so_path/binary_fmt dependency, and dump_source_only needs the
    # config-resolved frontend too: it drives the L4 source-ABI extractor (the
    # same --ast-frontend knob as the L2 header AST, ADR-037 D8), so a
    # .abicheck.yml `compile.frontend` must reach the source-only path exactly
    # like it already does the binary-dump path, not just this validation check.
    _cc, includes = resolve_dump_compile_context(
        _resolved_compile_context,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens, sysroot=sysroot, nostdinc=nostdinc,
        header_backend=header_backend, includes=includes,
        build_config=build_config, sources=sources,
    )
    gcc_path, gcc_prefix, gcc_options = _cc.gcc_path, _cc.gcc_prefix, _cc.gcc_options
    gcc_option_tokens, sysroot, nostdinc = _cc.gcc_option_tokens, _cc.sysroot, _cc.nostdinc
    header_backend = _cc.frontend

    # CLI-audit P1: --ast-frontend hybrid dual-runs castxml+clang for the L2
    # header AST, but L4 source-ABI replay has no such dual-backend merge —
    # an explicit --depth source would silently reach no further than
    # castxml/clang alone (or nothing) while still calling itself "hybrid".
    # Reject the combination outright rather than let it look like a
    # successful hybrid source analysis; the implicit default (no --depth)
    # is left alone since it is already allowed to honestly degrade. Checked
    # once here, after the CLI>config frontend resolution above and before
    # either dispatch branch, so a config-selected `compile.frontend: hybrid`
    # can't bypass it via either path (CodeRabbit + Codex review).
    #
    # Codex review: scoped to invocations that will actually attempt L4
    # extraction with the hybrid frontend -- see
    # _dump_will_attempt_hybrid_l4_extraction's docstring for the two cases
    # (prebuilt-pack --sources, and no --sources at all) where it must not
    # fire. --build-info never feeds L4 extraction (only L3 compile-DB
    # resolution), so it plays no part in this predicate (Codex review,
    # fourth finding).
    if (
        depth == "source"
        and header_backend == "hybrid"
        and _dump_will_attempt_hybrid_l4_extraction(sources)
    ):
        raise click.UsageError(
            "--depth source is incompatible with --ast-frontend hybrid: L4 "
            "source-ABI replay has no dual-backend hybrid extractor (unlike "
            "the L2 header-AST snapshot). Pass --ast-frontend castxml or "
            "--ast-frontend clang for a --depth source dump."
        )

    # A source-only dump (no SO_PATH) has no binary at all, so --depth binary
    # -- rank 0, the floor everything else must exceed -- is trivially
    # "satisfied" by check_requested_depth_satisfied even for a completely
    # empty snapshot (--depth binary resolves collect_mode to "off", which
    # skips L3-L5 embedding too): `dump --sources src --depth binary -o
    # out.json` would exit 0 and write a snapshot with no binary, header,
    # build, or source facts at all -- a baseline/CI consumer would read
    # that success as proof the requested rung is genuinely present. Checked
    # unconditionally, before the --dry-run branch, so both paths reject the
    # same way (external review).
    if so_path is None and depth == "binary":
        raise click.UsageError(
            "--depth binary requires a native artifact (SO_PATH); a "
            "source-only dump (--sources/--build-info with no SO_PATH) has "
            "no binary to report and needs at least --depth build or "
            "--depth source to produce any evidence."
        )

    # Resolve debug-format and binary-format identity once, shared between
    # the dry-run report and the real run, and raise the same UsageError/
    # BadParameter a real run would for either -- unconditionally, before the
    # --dry-run branch, exactly like the hybrid+depth UsageError check above.
    # These two validations (resolve_dump_compile_db's UsageError, and the
    # debug-format/PE-Mach-O BadParameter below) previously only ran in the
    # real path, after the dry-run branch, so `dump --dry-run` could report
    # success on an invocation the real run would immediately reject.
    # CodeRabbit review: an earlier version of this fix instead encoded both
    # as DryRunResult blockers (exit 1) -- silently downgrading what is a
    # genuine usage error (exit 64) into an evidence-blocker mistakenly, and
    # disagreeing with the real run's actual exit code for the identical
    # input. Raising directly here keeps dry-run and the real run on the
    # exact same code path for this check, not just the same message. Uses
    # the pure, side-effect-free binary_utils.normalize_binary_input (no
    # linker-script "Note:" echo) rather than _normalize_binary_input,
    # matching dry-run's own "cheap, read-only resolution only" contract;
    # the real path below still calls _normalize_binary_input itself for
    # that echo and the so_path reassignment (a no-op re-validation once
    # this has already passed).
    effective_debug_format: str | None = None
    if so_path is not None:
        from .binary_utils import normalize_binary_input as _peek_binary_format
        from .cli_dump_helpers import (
            check_dump_compile_db_error,
            check_dump_debug_format_error,
        )

        effective_debug_format = resolve_dump_debug_format(debug_format_opt, debug_format)
        compile_db_error = check_dump_compile_db_error(
            compile_db_path, compile_db_path_alt, headers
        )
        if compile_db_error is not None:
            raise click.UsageError(compile_db_error)
        _, dry_run_binary_fmt = _peek_binary_format(so_path)
        debug_format_error = check_dump_debug_format_error(
            effective_debug_format, dry_run_binary_fmt
        )
        if debug_format_error is not None:
            raise click.BadParameter(debug_format_error)

    if dry_run:
        from .buildsource.inline import is_pack_dir
        from .cli_buildsource_helpers import _is_inputs_pack_dir
        from .cli_dump_helpers import render_dump_dry_run
        from .cli_helpers_compare import dry_run_compile_db_matched

        _dry_matched = dry_run_compile_db_matched(
            compile_db_path, compile_db_path_alt, headers, compile_db_filter,
        )
        # AC-007 dry-run parity (Codex review): the real run below reuses a
        # matched -p/--compile-db as the L3 build source when no --build-info is
        # given, but that decision runs after this branch. Compute it here with
        # the same pure helper so the dry-run report describes the invocation it
        # is validating (its L3 source), instead of claiming "L0-L2 only".
        _dry_reused_bi, _ = resolve_compile_db_l3_reuse(
            depth, build_info, compile_db_path or compile_db_path_alt,
            matched=bool(_dry_matched), compile_db_filter=compile_db_filter,
            explicit_l3_selector=has_other_l3_source(
                build_query, build_compile_db, build_config, sources,
            ),
        )
        emit_dry_run(
            render_dump_dry_run(
                so_path=so_path, headers=headers, sources=sources,
                build_info=build_info, build_config=build_config,
                depth=depth, collect_mode=collect_mode,
                header_backend=header_backend, output=output,
                has_compile_db=bool(compile_db_path or compile_db_path_alt),
                # External review: dry-run previously only checked bare -p/
                # --compile-db presence; loading it and matching against the
                # resolved headers is cheap, deterministic, read-only
                # resolution, not "real work out of scope for a dry run".
                compile_db_matched=_dry_matched,
                compile_db_reused_as_l3=_dry_reused_bi is not build_info,
                # embed_build_source's own classification: a source-capable
                # --build-info is either a BuildSourcePack (is_pack_dir) or a
                # Flow-2 abicheck_inputs/ directory (_is_inputs_pack_dir) --
                # both can carry L4 source_abi facts, unlike a raw compile
                # DB/build dir (Codex review, second finding on this signal).
                build_info_is_pack=(
                    is_pack_dir(build_info) or _is_inputs_pack_dir(build_info)
                ),
            )
        )

    # Source-only dump (no binary) for the parallel-baseline flow.
    if so_path is None:
        from .cli_buildsource import dump_source_only
        dump_source_only(sources, build_info, version, output, build_config, allow_build_query, git_tag, build_id, no_git, collect_mode, build_query=build_query, build_compile_db=build_compile_db, extractor=header_backend, depth=depth)
        return

    effective_compile_db = resolve_dump_compile_db(compile_db_path, compile_db_path_alt, headers)

    # Resolved before the PE/Mach-O dispatch (Codex review): both binary-format
    # branches need the same -p/--compile-db -> castxml/clang flags and matched
    # signal -- the ELF path used to compute these only after the PE/Mach-O
    # early return, so a compile database's flags were silently dropped for
    # PE/Mach-O input, and --depth build backed only by -p was wrongly
    # rejected there (parsed_with_build_context was never stamped either).
    build_context_flags, compile_db_matched = _resolve_build_context_flags(
        effective_compile_db, headers, compile_db_filter,
    )
    effective_gcc_options = _merge_gcc_options(build_context_flags, gcc_options)

    # AC-007: reuse a `-p`/`--compile-db` database as the L3 build source for an
    # explicit `--depth build`/`source` with no dedicated `--build-info`. Gated on
    # the just-computed `compile_db_matched` (an unrelated/filtered DB must not
    # embed as L3), on `compile_db_filter` (which scopes L2 only, so the raw DB
    # can't be reused for L3 without pulling every entry), and on every other
    # dedicated L3 selector being absent — the CLI `--build-query`/
    # `--build-compile-db` flags AND an explicit `--config`, which may set
    # `build.query`/`build.compile_db`. Hijacking `build_info` would otherwise
    # override those lower-precedence selectors in `inline._resolve_compile_db`
    # (Codex review). All the decision logic is in the pure helper; only the echo
    # stays here.
    build_info, _l3_note = resolve_compile_db_l3_reuse(
        depth, build_info, effective_compile_db,
        matched=compile_db_matched, compile_db_filter=compile_db_filter,
        explicit_l3_selector=has_other_l3_source(
            build_query, build_compile_db, build_config, sources,
        ),
    )
    if _l3_note:
        click.echo(_l3_note, err=True)

    # Auto-detect binary format — PE/Mach-O skip the ELF/castxml path. The
    # conventional ``libfoo.so`` dev symlink is often a GNU ld linker script;
    # follow it to the real shared library before dispatching.
    so_path, binary_fmt = _normalize_binary_input(so_path)
    if effective_debug_format is not None and binary_fmt in ("pe", "macho"):
        raise click.BadParameter(
            f"--{effective_debug_format} is only supported for ELF binaries, not {binary_fmt.upper()}."
        )

    if binary_fmt in ("pe", "macho"):
        native_cc = (
            dataclasses.replace(_cc, gcc_options=effective_gcc_options)
            if effective_gcc_options != _cc.gcc_options
            else _cc
        )
        handle_non_elf_dump(
            so_path, binary_fmt, headers, includes, version, lang, pdb_path,
            follow_deps, git_tag, build_id, no_git, output,
            _dump_native_binary, _stamp_provenance, _write_snapshot_output,
            public_headers, public_header_dirs, build_info, sources, build_config,
            allow_build_query, collect_mode, build_query, build_compile_db,
            header_backend=header_backend, compile_context=native_cc,
            depth=depth, compile_db_context_matched=compile_db_matched,
        )
        return

    # Debug artifact resolution (ADR-021a): resolve before dump. P1.1: thread
    # a resolved detached debug file (build-id tree / path-mirror / debuginfod
    # — distinct from so_path itself) into the actual DWARF parse instead of
    # only logging it, so a stripped binary still gets DWARF-aware comparison.
    debug_info_path: Path | None = None
    if debug_roots or debuginfod:
        artifact = _resolve_debug_artifact(
            so_path, debug_roots, debuginfod, debuginfod_url,
        )
        if artifact:
            click.echo(f"Debug info: {artifact.source}", err=True)
            if artifact.dwarf_path and artifact.dwarf_path.resolve() != so_path.resolve():
                debug_info_path = artifact.dwarf_path

    perform_elf_dump(
        so_path=so_path,
        debug_info_path=debug_info_path,
        headers=headers,
        includes=includes,
        version=version,
        lang=lang,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        effective_gcc_options=effective_gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        user_gcc_options=gcc_options,
        compile_db_filter=compile_db_filter,
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
        compile_context=_cc,
        depth=depth,
        compile_db_context_matched=compile_db_matched,
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
            "--probe-matrix needs both sides: --probe-matrix old=… --probe-matrix new=…"
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
    severity_config: SeverityConfig | None = None,
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

    annotations = collect_annotations(
        result, annotate_additions=annotate_additions, severity_config=severity_config,
    )
    text = format_annotations(annotations)
    if text:
        click.echo(text, err=True)

    if write_step_summary:
        emit_github_step_summary(result, severity_config=severity_config)


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
    scheme: str,
    *, fmt: str = "markdown", stat: bool = False,
) -> None:
    """Announce (on stderr) which exit-code scheme the compare command uses.

    The scheme is now explicit (ADR-037 D12 / D4: ``--exit-code-scheme`` or the
    config's ``exit_code_scheme``, with ``auto`` already resolved to ``legacy`` or
    ``severity`` by the time we get here). Kept on stderr so it never pollutes the
    report on stdout, and only for the human-readable formats — machine formats
    (json/sarif/junit) and the one-line ``--stat`` summary are consumed by tooling
    that treats the whole captured stream as data, so the banner is suppressed.
    """
    if stat or fmt not in {"markdown", "html", "review"}:
        return
    if scheme == "severity":
        click.echo(
            "Exit-code scheme: severity-aware (per-category severity settings).",
            err=True,
        )
    else:
        click.echo(
            "Exit-code scheme: legacy verdict (0=compatible, 2=API break, 4=ABI break). "
            "Pass --exit-code-scheme severity (or a --severity-* setting) for the "
            "severity-aware scheme.",
            err=True,
        )


def _exit_with_severity_or_verdict(
    result: DiffResult, sev_config: SeverityConfig | None, scheme: str,
) -> None:
    """Exit with the appropriate code for the resolved exit-code scheme."""
    from .severity import compute_exit_code, legacy_exit_code
    if scheme == "severity":
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
    severity_config: SeverityConfig | None = None,
) -> None:
    """Attach metadata and emit redundancy/filter/suppression/annotation output."""
    result.old_metadata = _collect_metadata(old_input)
    result.new_metadata = _collect_metadata(new_input)

    if show_redundant and result.redundant_changes:
        _merge_redundant_changes(result)
    if show_filtered and result.out_of_surface_changes:
        echo_filtered_surface(result)
    if show_filtered and result.reconciled_changes:
        echo_reconciled(result)

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
        result, annotate=annotate, annotate_additions=annotate_additions,
        severity_config=severity_config,
    )


# ── ADR-037 D7: input-type dispatch for `compare` ────────────────────────────
# `compare` accepts a single .so / snapshot, a directory, or a package. Set
# inputs (directory/package) fan out to a per-library comparison (the former
# `compare-release`); an application/PIE operand is rejected with a hint at
# `appcompat`. The set-only fan-out flags are a no-op-with-warning on single
# inputs.

_RELEASE_FORMATS = frozenset({"json", "markdown", "junit"})


def _reject_application_operand(
    old_input: Path, new_input: Path, old_kind: str, new_kind: str
) -> None:
    """Error when a `compare` operand is an application/executable, not a library."""
    which = old_input if old_kind == "app" else new_input
    raise click.UsageError(
        f"'{which}' looks like an application/executable, not a shared library, "
        "so `compare` cannot pair it as a library ABI. To check whether an "
        "application is still satisfied by a library, use "
        "`abicheck compare <old-lib> <new-lib> --used-by <app>`. If this file "
        "really is a shared library with an unusual ET_DYN/PIE layout, dump it "
        "first with `abicheck dump` and compare the resulting snapshots."
    )


def _warn_unused_set_flags(
    *, jobs_explicit: bool, dso_only: bool, output_dir: Path | None
) -> None:
    """Warn that the set-input fan-out flags do not apply to single-file inputs."""
    used = []
    if jobs_explicit:
        used.append("-j/--jobs")
    if dso_only:
        used.append("--dso-only")
    if output_dir is not None:
        used.append("--output-dir")
    if used:
        click.echo(
            "Warning: " + ", ".join(used) + " only apply to directory/package "
            "(set) inputs; ignoring them for this single-file comparison.",
            err=True,
        )


def _dispatch_release_compare(ctx: click.Context, **kwargs: Any) -> None:
    """Fan a directory/package `compare` out to the per-library release engine.

    Routes through the same release engine (the unregistered `compare_release_cmd`,
    which fans out per library through the single Tier-2 `service.run_compare`
    chokepoint and writes the two-level summary/per-library output), so a library
    compared here gets the identical verdict it would from a single-pair `compare`
    (ADR-037 D1/D7). The standalone `compare-release` command was removed; this is
    now its only entry point.

    Calls ``compare_release_cmd.callback`` directly rather than
    ``ctx.invoke(compare_release_cmd, ...)`` (CLI-audit P2: "business logic
    depends on Click-to-Click orchestration") -- ``compare_release_cmd`` is
    itself never registered on `main` and exists solely to be called this way
    (see its own module comment), and every one of its ~44 parameters is
    already supplied explicitly by the caller below, so there is no Click
    default-filling for ``ctx.invoke`` to usefully do here; it was only ever
    creating a throwaway sub-``Context`` to call the same plain function.
    ``UsageError``/``BadParameter`` normally get ``e.ctx`` backfilled by
    ``ctx.invoke``'s ``augment_usage_errors`` wrapper for display purposes
    (a "Usage: ..." header on the formatted error) -- replicated by hand here
    so a validation error raised inside the release engine still formats
    identically to before.
    """
    fmt = kwargs.get("fmt", "markdown")
    if fmt not in _RELEASE_FORMATS:
        raise click.UsageError(
            f"--format {fmt} is not available when comparing directories or "
            f"packages; choose one of: {', '.join(sorted(_RELEASE_FORMATS))}."
        )
    from .cli_compare_release import compare_release_cmd

    assert compare_release_cmd.callback is not None
    try:
        compare_release_cmd.callback(**kwargs)
    except click.UsageError as exc:
        if exc.ctx is None:
            exc.ctx = ctx
        raise


def _source_is_pack(path: Path) -> bool:
    """True if *path* is a pack directory rather than a raw source checkout —
    lets ``compare``'s --sources/--build-info accept either.

    Validates the manifest *content*, not just its presence: a raw checkout that
    happens to contain a top-level ``manifest.json`` (which ``BuildSourcePack.load``
    would otherwise accept with sparse defaults) must still be collected from, so
    we require the ``BuildSourcePack`` marker (``build_source_pack_version`` /
    legacy ``evidence_pack_version``) — or a build-emitted Flow-2 ``abicheck_inputs/``
    pack. Both pack kinds are auto-detected and routed to the out-of-band pack
    loader (``_load_side_pack_input``/``prepare_embedded_build_source``), which
    handles either kind; only a genuinely raw tree/build dir falls through to the
    inline-collection path below (ADR-043: there is no separate ``merge`` command
    to route an inputs pack through anymore).
    """
    # Single source of truth: the dump/collect side validates the same way via
    # inline.is_pack_dir (content, not filename), so the two never disagree.
    from .buildsource.inline import is_pack_dir
    from .cli_buildsource_helpers import _is_inputs_pack_dir

    return is_pack_dir(path) or _is_inputs_pack_dir(path)


def _embed_inline_source_side(
    ctx: click.Context,
    *,
    input_path: Path,
    sources: Path | None,
    headers: tuple[Path, ...] | list[Path],
    includes: tuple[Path, ...] | list[Path],
    version: str,
    lang: str,
    header_backend: str,
    compile_context: object,
    frontend_explicit: bool,
    nostdinc_explicit: bool,
    build_info: Path | None,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
    dwarf_only: bool,
    debug_format: str | None,
    pdb_path: Path | None,
    collect_mode: str,
    out_dir: Path,
    label: str,
    depth: str | None = None,
    debug_roots: tuple[Path, ...] = (),
    debuginfod: bool = False,
    debuginfod_url: str | None = None,
) -> tuple[Path, Path | None, Path | None]:
    """Resolve one side's ``--sources`` into the input ``compare`` should read.

    A raw source *tree* (no manifest.json) on a native-binary side is dumped
    inline at *collect_mode* (the deep-compare workflow, folded into ``compare``)
    so the L3-L5 facts ride embedded in the snapshot. Returns
    ``(input_to_read, sources_to_keep, build_info_to_keep)``: a pre-built
    ``collect`` pack passes through untouched; an embedded tree consumes both its
    sources and ``--build-info`` (-> ``None``, so the later
    ``prepare_embedded_build_source`` won't re-process them); a snapshot input
    can't be re-dumped, so a tree on it is reported ignored.

    *compile_context* is compare's already-resolved
    :class:`~abicheck.service_scan.CompileContext` (the merged per-side context).
    The caller passes the *resolved* values plus the toolchain/dependency/native
    knobs (``follow_deps``/``--gcc-*``/``--dwarf-only``/…) so the inline dump
    parses this side exactly as a native ``compare``/``dump`` would.

    ``debug_roots``/``debuginfod``/``debuginfod_url`` (P1.1, Codex review):
    this side's resolved detached-debug-artifact inputs, forwarded verbatim to
    the inline ``dump`` invocation below — without this, a raw
    ``--old/new-sources`` tree bypassed ``--debug-root`` entirely (the inline
    dump used its own unset defaults), so a stripped binary on this side still
    lost its DWARF even though the sibling non-inline path was fixed.

    ``depth`` is ``compare``'s own (unmodified) ``--depth`` string, used only
    to reproduce ``dump_cmd``'s ``--depth source`` + ``--ast-frontend hybrid``
    rejection for this side (Codex review): the ``ctx.invoke(dump_cmd, ...)``
    call below never passes ``depth=``, so without this explicit check
    ``dump_cmd``'s own guard silently never fires for a raw
    ``--old/new-sources`` tree here even when ``compare --depth source
    --ast-frontend hybrid`` would reject the identical tree via a plain
    ``dump --sources <tree> --depth source --ast-frontend hybrid`` — an
    inconsistent, silently-degrading escape hatch from the same command-line
    surface the check was written to close. Deliberately narrower than
    threading ``depth`` into the nested ``dump_cmd`` invocation itself, which
    would also activate that call's ``check_requested_depth_satisfied`` hard
    gate on this one side's snapshot in isolation — a larger behavior change
    than this finding asked for, and not needed here since ``compare``'s own
    ``--depth`` semantics (missing-evidence-layer warnings, not a hard
    per-side gate) are unaffected by this narrowly-scoped check.
    """
    sources_raw = sources is not None and not _source_is_pack(sources)
    build_info_raw = build_info is not None and not _source_is_pack(build_info)
    if not sources_raw and not build_info_raw:
        # Nothing raw to collect inline; any pack-shaped sources/build-info fall
        # through to prepare_embedded_build_source unchanged.
        return input_path, sources, build_info
    # A *raw* --build-info (build dir / compile_commands.json) is collected by the
    # inline dump below — it must never reach prepare_embedded_build_source, which
    # treats a leftover --build-info as an out-of-band *pack* (_resolve_side_pack →
    # _load_pack_or_raise) and aborts with "Invalid evidence pack". A pack-shaped
    # one passes through for that out-of-band path. Likewise raw sources are
    # consumed here; pack sources pass through (Codex review).
    kept_build_info = None if build_info_raw else build_info
    kept_sources = None if sources_raw else sources
    norm, fmt = _normalize_binary_input(input_path)
    if fmt is None:
        ignored = []
        if sources_raw:
            ignored.append(f"--{label}-sources source tree")
        if build_info_raw:
            ignored.append(f"raw --{label}-build-info")
        click.echo(
            f"Warning: {label} input {input_path} is a snapshot, not a native "
            f"binary; the {' and '.join(ignored)} is ignored (dump the binary "
            "from its tree to embed deeper evidence).",
            err=True,
        )
        return input_path, kept_sources, kept_build_info
    # The --depth dial governs how deep to collect. When it resolves to "off"
    # (--depth binary/headers) there is no source collection to do, so a raw tree
    # / build-info can't contribute at this depth — ignore it with a note rather
    # than silently deepening the run (matches the old deep-compare, which never
    # auto-bumped the depth).
    if collect_mode == "off":
        click.echo(
            f"Warning: --{label}-sources/--{label}-build-info was given but the "
            "selected --depth collects no evidence; ignoring it. Use --depth "
            "build or --depth source to collect from it.",
            err=True,
        )
        return input_path, kept_sources, kept_build_info
    # Only the raw inputs are consumed by the inline dump; pack-shaped sources /
    # build-info ride through to the out-of-band path.
    dump_sources = sources if sources_raw else None
    dump_build_info = build_info if build_info_raw else None
    out = out_dir / f"{label}.abi.json"
    # Merge the side's source-root .abicheck.yml `compile:` block into compare's
    # resolved context — exactly what `dump --sources` / the old deep-compare did —
    # but compute the CLI-over-config explicitness HERE (compare's real ctx, where
    # --ast-frontend/--nostdinc are genuine COMMANDLINE params) and freeze the
    # result, handing it to dump via the private _resolved_compile_context hook so
    # dump does not re-resolve under ctx.invoke (which would lose that explicitness).
    # This honors the tree's include_dirs/sysroot/frontend while keeping explicit
    # CLI overrides winning (Codex review).
    import dataclasses

    from .cli_options import merge_compile_config

    side_cli = dataclasses.replace(compile_context, frontend=header_backend)  # type: ignore[type-var]
    frozen_cc, merged_includes = merge_compile_config(
        side_cli,  # type: ignore[arg-type]
        tuple(includes),
        None,
        sources=dump_sources,
        frontend_explicit=frontend_explicit,
        nostdinc_explicit=nostdinc_explicit,
    )
    # Reproduce dump_cmd's --depth source + --ast-frontend hybrid rejection for
    # this side -- see this function's own docstring for why the nested
    # ctx.invoke(dump_cmd, ...) below does not surface that check itself
    # (Codex review).
    if (
        depth == "source"
        and frozen_cc.frontend == "hybrid"
        and _dump_will_attempt_hybrid_l4_extraction(dump_sources)
    ):
        raise click.UsageError(
            f"--depth source is incompatible with --ast-frontend hybrid for "
            f"the --{label}-sources tree: L4 source-ABI replay has no "
            "dual-backend hybrid extractor (unlike the L2 header-AST "
            f"snapshot). Pass --{label}-ast-frontend castxml or "
            f"--{label}-ast-frontend clang (or the unscoped --ast-frontend) "
            "for a --depth source compare."
        )
    # CLI-audit P2 ("business logic depends on Click-to-Click orchestration"):
    # this ctx.invoke was investigated for removal alongside the
    # compare_release_cmd one above (_dispatch_release_compare now calls its
    # .callback directly) and deliberately kept. dump_cmd has 44 parameters;
    # only ~19 are supplied here, so removing ctx.invoke would mean either
    # hand-duplicating Click's own ~25 remaining @click.option defaults here
    # (silently drifts the moment one of them changes) or reaching into
    # Click's private Context._make_sub_context/get_default/type_cast_value
    # machinery to resolve them correctly -- i.e. reimplementing ctx.invoke
    # by hand for no behavioral gain, since dump_cmd's own
    # resolve_dump_compile_context() genuinely needs a real, correctly-scoped
    # click.get_current_context() on the path this caller doesn't take
    # (resolved_compile_context is always non-None here, but that is an
    # invariant of THIS call site, not something dump_cmd's general callback
    # contract guarantees for a future caller). ctx.invoke is the public,
    # documented Click API for exactly this "call another command with most
    # params pre-resolved, let Click fill in the rest" case. The genuine fix
    # for the architectural concern is extracting dump_cmd's resolve/dispatch
    # body into a shared Tier-2-style function both the CLI wrapper and this
    # embed path call directly -- a real refactor of a heavily-hardened,
    # already-2000-line-adjacent file, out of scope for a contained change.
    ctx.invoke(
        dump_cmd,
        so_path=norm,
        headers=tuple(headers),
        includes=merged_includes,
        version=version,
        lang=lang,
        _resolved_compile_context=frozen_cc,
        follow_deps=follow_deps,
        search_paths=search_paths,
        ld_library_path=ld_library_path,
        dwarf_only=dwarf_only,
        debug_format_opt=debug_format,
        pdb_path=pdb_path,
        sources=dump_sources,
        build_info=dump_build_info,
        _resolved_collect_mode=collect_mode,
        output=out,
        debug_roots=debug_roots,
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url,
    )
    # The raw sources/build-info are now embedded in the snapshot; pack-shaped
    # inputs (kept_*) ride through to the later prepare_embedded_build_source so
    # it does not re-process the consumed raws as bogus packs — Codex review.
    return out, kept_sources, kept_build_info


@main.command("compare")
@compare_help_options  # curated --help + full --help-all (G21.8 collapse M2)
@click.argument("old_input", type=click.Path(exists=True, path_type=Path))
@click.argument("new_input", type=click.Path(exists=True, path_type=Path))
# Set-input fan-out (ADR-037 D7): -j/--jobs, --dso-only, --output-dir only bite
# when the operands are directories/packages; a no-op-with-warning otherwise.
@set_input_options
# ── Release (directory/package) comparison knobs (ADR-037 D7) ────────────────
@release_options
# ── Dump options (used when input is an ELF binary) ──────────────────────────
# Two-sided header/include/version family (ADR-037 D3). The L2 compile-context
# family (--ast-frontend + cross-toolchain --gcc-*/--sysroot/--nostdinc) comes from
# the shared @compile_context_options decorator so compare/dump/scan never drift
# (ADR-037 D3); --lang and the per-side --old/new-ast-frontend overrides stay inline.
@two_sided_input_options
@compile_context_options  # --ast-frontend + cross-toolchain (shared with dump/scan)
@lang_option
@click.option("--old-ast-frontend", "old_header_backend",
              default=None,
              type=click.Choice(["auto", "castxml", "clang", "hybrid"], case_sensitive=False),
              help="C/C++ AST frontend for the old side only (overrides "
                   "--ast-frontend for old). Use when the old release parses on "
                   "castxml but the new one needs clang (or vice versa).")
@click.option("--new-ast-frontend", "new_header_backend",
              default=None,
              type=click.Choice(["auto", "castxml", "clang", "hybrid"], case_sensitive=False),
              help="C/C++ AST frontend for the new side only (overrides "
                   "--ast-frontend for new).")
# ── Compare options (unchanged) ──────────────────────────────────────────────
@output_options(
    ["json", "markdown", "sarif", "html", "junit", "review"],
    format_help="Output format. 'review' emits a compact GitHub-facing digest "
                "(verdict + counts + release recommendation + manual-review banner) "
                "suitable for a job summary or PR comment.",
)
@click.option("--secondary-format", "secondary_fmt",
              type=click.Choice(["json", "markdown", "sarif", "html", "junit", "review"]),
              default=None,
              help="Emit a second output format from this same comparison run, "
                   "without re-running the comparison a second time (e.g. a human "
                   "--format markdown report alongside a --secondary-format json "
                   "artifact for tooling). Requires --secondary-output (writing two "
                   "formats to the same stream would be ambiguous). Always renders "
                   "the full, unfiltered report (ignores --show-only/--stat). Not "
                   "supported for directory/package (release) comparisons.")
@click.option("--secondary-output", "secondary_output",
              type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="File path to write --secondary-format's output to. Must "
                   "differ from --output/-o, or the secondary render would "
                   "silently overwrite the primary report.")
@click.option("--demangle/--no-demangle", default=None,
              help="Demangle C++ symbol names in markdown/review output (default "
                   "ON; use --no-demangle to turn off). json/sarif always keep raw "
                   "mangled names, and HTML is rendered structurally and is never "
                   "demangled regardless of this flag.")
# Policy + suppression family (ADR-037 D3); strict/justification stay inline.
@policy_options
@click.option("--strict-suppressions", is_flag=True, default=False, hidden=True,
              help="Fail with exit code 1 if any suppression rule has expired "
                   "(config: suppression.strict). Demoted to config (ADR-037 D4).")
@click.option("--require-justification", is_flag=True, default=False, hidden=True,
              help="Require every suppression rule to have a non-empty 'reason' "
                   "field (config: suppression.require_justification). Demoted to "
                   "config (ADR-037 D4).")
@click.option("--pdb-path", "pdb", multiple=True, type=SIDED_PATH_PARAM,
              help="Explicit PDB file path for Windows PE debug info. Applies to both "
                   "sides; scope to one with an 'old='/'new=' prefix, repeating the flag "
                   "per side (e.g. --pdb-path old=a.pdb --pdb-path new=b.pdb). Overrides "
                   "automatic PDB discovery (ADR-040).")
# ── Scoped comparison (ADR-043): app-usage and required-symbol contracts ─────
@click.option("--used-by", "used_by_apps", multiple=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Application binary whose actual imports/required symbol versions "
                   "scope the comparison (repeatable; folds `appcompat`). The full "
                   "library comparison still runs once; the worst app-scoped result "
                   "becomes the primary verdict/exit code, with the full verdict and "
                   "unrelated changes kept as informational context. OLD/NEW may be "
                   "real library binaries or JSON snapshots carrying binary evidence "
                   "(a `dump` of a real library, not headers-only). Mutually "
                   "exclusive with --required-symbol/--required-symbols.")
@click.option("--verify-runtime", "verify_runtime", is_flag=True, default=False,
              help="With --used-by: actually run each consumer binary once against "
                   "the OLD library and once against the NEW one (LD_BIND_NOW=1), "
                   "recording a consumer_runtime_load_failed RISK finding when the "
                   "dynamic linker itself reports an undefined symbol against the "
                   "new library after loading cleanly against the old one (ADR-044 "
                   "P2 item 2). A dynamic corroborating signal alongside the static "
                   "scanner, never a replacement for it. Requires OLD/NEW to be real "
                   "library binaries (not JSON snapshots) and is Linux-only; a "
                   "no-op elsewhere. Ignored without --used-by.")
@click.option("--required-symbol", "required_symbols_opt", multiple=True,
              help="An exported linker symbol a plugin host resolves via dlopen/dlsym "
                   "and requires (repeatable; folds `plugin-check`). Scopes the "
                   "comparison to this explicit entrypoint contract instead of the "
                   "full diff. Mutually exclusive with --used-by.")
@click.option("--required-symbols", "required_symbols_file",
              type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="File of required symbols, one per line (blank lines and '#' "
                   "comments ignored). Combined with any --required-symbol values.")
# Severity preset + per-category overrides (ADR-037 D3 / D4).
@severity_options
# ── Project config & exit-code scheme (ADR-037 D4 / D12) ──────────────────────
@click.option("--config", "config", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None,
              help="Path to the project .abicheck.yml (ADR-037 D4). Default: the "
                   "nearest .abicheck.yml found from the current directory upward. "
                   "Supplies stable project settings (severity map, scope/FP "
                   "tuning, suppression policy, exit-code scheme); CLI flags "
                   "override it.")
@click.option("--exit-code-scheme", "exit_code_scheme",
              type=click.Choice(["auto", "legacy", "severity"], case_sensitive=True),
              default=None,
              help="Exit-code scheme (ADR-037 D12): 'legacy' (0/2/4 verdict), "
                   "'severity' (per-category error levels), or 'auto' (severity "
                   "when a severity setting is in effect, else legacy). Declared "
                   "explicitly here so passing --severity-* no longer silently "
                   "changes the scheme. Default: config's exit_code_scheme, else auto.")
@click.option("--follow-deps", is_flag=True, default=False,
              help="Resolve transitive dependencies for both old and new, compute symbol "
                   "bindings, and include a dependency-change section in the report. ELF only.")
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries (with --follow-deps).")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (with --follow-deps).")
@header_graph_options  # hidden deprecated no-op shim (shared with `dump`)
@click.option("--show-redundant/--no-show-redundant", "show_redundant", default=False,
              hidden=True,
              help="Disable redundancy filtering and show all changes including those "
                   "derived from root type changes. Demoted to config "
                   "(scope.show_redundant, ADR-040); --show-redundant/--no-show-redundant "
                   "still overrides it either way.")
@scope_options  # --scope-public-headers/--no- (ADR-037 D3); --show-filtered stays inline
@click.option("--collapse-versioned-symbols", "collapse_versioned_symbols", is_flag=True, default=False,
              hidden=True,
              help="Opt-in (G15): when a versioned-symbol scheme is detected (most removed "
                   "symbols reappear differing only by a version token, e.g. ICU u_*_NN), "
                   "reclassify those version-rename pairs as compatible so the verdict "
                   "reflects the real delta, not the rename churn. A real SONAME bump and "
                   "non-versioned removals still drive the verdict. Demoted to config "
                   "(scope.collapse_versioned_symbols, ADR-037 D4).")
@click.option("--show-filtered", "show_filtered", is_flag=True, default=False,
              help="List findings excluded by --scope-public-headers (audit trail).")
@click.option("--public-symbol", "public_symbols", multiple=True, hidden=True,
              help="Widening overlay (ADR-024 §D6): force a symbol (mangled or demangled "
                   "name) into the public surface even when header provenance can't see it "
                   "(asm stubs, .def exports, extern \"C\" shims, MSVC-mangling gaps). "
                   "Repeatable. Only meaningful with --scope-public-headers. Demoted to "
                   "config (scope.public_symbols, ADR-037 D4).")
@click.option("--public-symbols-list", "public_symbols_list",
              type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              hidden=True,
              help="File of symbols to force public (one per line; '#' comments and blank "
                   "lines ignored), à la abi-compliance-checker -symbols-list. "
                   "Merged with --public-symbol and scope.public_symbols (ADR-037 D4).")
@click.option("--post-manifest", "post_manifest_path",
              type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="Scope the comparison to a POST Python export manifest's committed ABI "
                   "surface. Only changes to the manifest's pp_*/ufunc-loop symbols count; "
                   "private __pp_* kernel churn and other non-committed exports are demoted "
                   "to the filtered ledger (see --show-filtered).")
@click.option("--probe-matrix", "probe_matrix", multiple=True, type=SIDED_EXISTING_PATH_PARAM,
              help="Build-configuration matrix snapshot, "
                   "scoped per side with an 'old='/'new=' prefix (e.g. --probe-matrix "
                   "old=m1 --probe-matrix new=m2). With both sides given, build-config "
                   "findings (CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV, "
                   "BEHAVIOURAL_DEFAULT_CHANGED) are folded into this comparison's "
                   "verdict and report (G2: probe -> compare; ADR-040).")
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
              type=click.Choice(["full", "leaf", "impact", "root-cause"], case_sensitive=True),
              default="full", show_default=True,
              help="Report mode: 'full' lists all changes individually (default), "
                   "'leaf' groups by root type changes with impact lists, "
                   "'impact' behaves as 'full' with the impact summary table enabled "
                   "(equivalent to --report-mode full --show-impact), "
                   "'root-cause' groups findings sharing a root cause "
                   "(Change.caused_by_type) under one entry for "
                   "--format json/markdown (the default rendered text output); "
                   "--format sarif keeps its normal one-result-per-finding "
                   "shape but adds properties.rootCauseId/rootCause to each "
                   "result; --format junit still renders as 'full'.")
@click.option("--show-impact", is_flag=True, default=False,
              help="Append an impact summary table showing root changes and affected interfaces.")
@click.option("--recommend", is_flag=True, default=False,
              help="Append a release recommendation (semver bump + SONAME action) to the "
                   "report. Always present in --format json under 'release_recommendation'.")
@click.option("--annotate", is_flag=True, default=False,
              help="Emit GitHub Actions workflow command annotations to stderr. "
                   "Annotations appear as inline comments on PR diffs. "
                   "Only effective when GITHUB_ACTIONS=true.")
@click.option("--annotate-additions", is_flag=True, default=False,
              help="Include additions/compatible changes as ::notice annotations "
                   "(requires --annotate).")
# ── Debug artifact resolution (ADR-021a + ADR-037 D3) ─────────────────────────
# --dwarf-only, --debug-root{,1,2}, --debuginfod[-url], --debug-format (+hidden
# --btf/--ctf/--dwarf): the shared local-ELF debug-resolution family.
@debug_resolution_options
@evidence_options  # --depth, --sources, --build-info
@adr027_compare_options  # ADR-027: --pattern-verdicts/--explain-patterns/--surface-metrics
@env_matrix_option  # ADR-020b: --env-matrix (runtime_floors contract)
@profile_option  # ADR-040 Lever 3: --profile (workflow-default bundles)
@click.option("--reconcile-build-context", is_flag=True, default=False,
              help="Clear context-free header-parse false positives using the build's "
                   "active preprocessor defines (ADR-039): a conditional field's phantom "
                   "add/remove/size change the build proves never happened is moved to an "
                   "audit bucket instead of the verdict. No-op unless snapshots carry "
                   "build_context_defines + per-field guards.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Resolve and validate the invocation -- classify inputs, resolve "
                   "depth/scope, show tool/config resolution -- and print a report "
                   "without running the diff. Writes nothing; incompatible with "
                   "-o/--output.")
@verbose_option
@click.pass_context
def compare_cmd(ctx: click.Context, /, **kwargs: Any) -> None:
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
        --header old=include/v1/foo.h --header new=include/v2/foo.h
    \b
      # Shorthand: -H when the same header applies to both versions
      abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h
    \b
      # With version labels and SARIF output
      abicheck compare libfoo.so.1 libfoo.so.2 \\
        --header old=v1/foo.h --header new=v2/foo.h \\
        --version old=1.0 --version new=2.0 --format sarif -o abi.sarif
    \b
      # Compare saved snapshot vs current build (mixed mode)
      abicheck compare baseline.json ./build/libfoo.so --header new=include/foo.h
    \b
      # Compare two pre-dumped snapshots (existing workflow)
      abicheck compare libfoo-1.0.json libfoo-2.0.json
    \b
      # Policy and suppression
      abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h --policy sdk_vendor
      abicheck compare old.json new.json --suppress suppressions.yaml
    """
    # Options are parsed by the click wrapper above; the full compare flow lives
    # in cli_compare_helpers.run_compare (size-split from cli.py to keep this
    # module under the AI-readiness file-size cap). Click collects every declared
    # option/argument into **kwargs, so forwarding it verbatim keeps behaviour —
    # and the exit-code matrix — identical while the single typed signature lives
    # only on run_compare (no duplicated 56-line parameter list; CodeFactor).
    from .cli_compare_helpers import run_compare
    from .cli_options import warn_deprecated_header_graph_flags

    # G29 Phase A: --header-graph/--header-graph-includes are hidden, inert
    # no-op shims (header_graph_options) — pop them out of kwargs before
    # forwarding to run_compare (whose typed signature no longer carries
    # them; the graph is now unconditional) and just emit the deprecation
    # note if either was passed.
    warn_deprecated_header_graph_flags(
        kwargs.pop("header_graph_deprecated", False),
        kwargs.pop("header_graph_includes_deprecated", False),
    )

    # ADR-040 Lever 1: translate the side-aware --header/--include/--sources/
    # --build-info tuples back into the per-side kwargs run_compare consumes.
    normalize_sided_options(kwargs)
    # ADR-040 Lever 3: fold the selected --profile's workflow defaults into the
    # forwarded options (explicit flags always win) and drop the CLI-only
    # ``profile`` key before delegating to the typed run_compare signature.
    apply_compare_profile(ctx, kwargs)

    run_compare(ctx, **kwargs)


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
#
# When this file is run directly (``python -m abicheck.cli``, distinct from
# the documented ``python -m abicheck`` entry point in __main__.py but still
# a common thing to type), Python executes it as the ``__main__`` module —
# under a DIFFERENT sys.modules key than ``abicheck.cli``. Every sibling
# module below does ``from .cli import main``, a fresh relative import that
# would otherwise re-execute this file a second time under the real
# ``abicheck.cli`` key, producing a second, empty ``main`` Click group; every
# ``@main.command(...)`` decorator then attaches to that second group, not
# the one actually running, so `python -m abicheck.cli --help` silently
# listed only the handful of commands defined directly in this file (dump/
# compare/compat) and omitted every sibling-registered one (scan, deps,
# ...). Alias the already-running module under its real
# package name first, so the relative import below reuses it instead
# (Codex review).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    sys.modules.setdefault("abicheck.cli", sys.modules[__name__])

from . import (  # noqa: E402  — must run after `main` and helpers are defined
    cli_aggregate,  # noqa: F401  — registers aggregate
    cli_build_output,  # noqa: F401  — registers build-output (validate)
    cli_buildsource,  # noqa: F401  — buildsource internals (no command of its own)
    cli_project_targets,  # noqa: F401  — registers project-targets (validate)
    cli_scan,  # noqa: F401  — registers scan
    cli_stack,  # noqa: F401  — registers deps (tree, compare)
)

if __name__ == "__main__":
    main()
