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
    compile_db_filter_scope_error,
    compile_db_from_build_info,
    handle_non_elf_dump,
    perform_elf_dump,
    reject_snapshot_compression_conflict,
    resolve_dump_collect_context,
    resolve_dump_compile_context,
    resolve_dump_debug_format,
)
from .cli_help import compare_help_options, configure_rich_help, dump_help_options
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
    app_usage_scope_options,
    apply_compare_profile,
    build_source_dump_options,
    compile_context_options,
    contract_options,
    debug_resolution_options,
    env_matrix_option,
    evidence_options,
    header_graph_options,
    include_dependencies_option,
    lang_option,
    normalize_sided_options,
    output_options,
    pack_option,
    policy_options,
    profile_option,
    release_options,
    scope_options,
    secondary_output_options,
    set_input_options,
    severity_options,
    snapshot_compression_option,
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

if TYPE_CHECKING:
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

# Exit code for a single-library ``compare`` whose OLD/NEW snapshots failed
# ADR-050 D2's comparability gate (a ProfileMismatchError/ScopeMismatchError
# — the two snapshots were not extracted under a comparable contract, so no
# verdict was ever produced). Identical across the legacy (0/2/4) and
# severity-aware (0/1/2/4) single-library schemes, since the gate runs before
# either classification, and deliberately not ``8`` — that already means
# ``--fail-on-removed-library`` in the release/multi-library table. ``16``
# continues that table's own doubling pattern one step further and sits
# outside every existing compare exit code in all three tables.
_EXIT_NOT_COMPARABLE = 16


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


def _load_dump_manifest_or_reject(
    dump_manifest_path: Path | None,
    headers: tuple[Path, ...],
) -> Any:
    """Parse ``--dump-manifest``, rejecting the flag it is exclusive with.

    A manifest's own ``roots`` field and base profile declare the public
    surface, so ``-H``/``--header`` would be a second, conflicting
    declaration. Returns the parsed manifest, or ``None`` when no
    ``--dump-manifest`` was given.
    """
    if dump_manifest_path is None:
        return None
    if headers:
        raise click.UsageError(
            "--dump-manifest and -H/--header are mutually exclusive -- the "
            "manifest's own 'roots' field declares the public surface instead."
        )
    from .dump_manifest import load_manifest
    from .errors import ManifestValidationError

    try:
        parsed_dump_manifest = load_manifest(dump_manifest_path)
    except ManifestValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    return parsed_dump_manifest


def _resolve_and_check_dump_debug_format(
    so_path: Path | None,
    debug_format_opt: str | None,
    debug_format: str | None,
) -> str | None:
    """Resolve the effective debug format and reject the usage error it implies.

    A genuine usage error in the real run (exit 64), raised here -- before the
    ``--dry-run`` branch -- so the dry run and the real run agree on it rather
    than the dry run downgrading it into an evidence blocker. Returns ``None``
    for a source-only dump, which has no binary to resolve a debug format
    against.
    """
    if so_path is None:
        return None
    from .binary_utils import normalize_binary_input as _peek_binary_format
    from .cli_dump_helpers import check_dump_debug_format_error

    effective_debug_format = resolve_dump_debug_format(debug_format_opt, debug_format)
    _, dry_run_binary_fmt = _peek_binary_format(so_path)
    debug_format_error = check_dump_debug_format_error(
        effective_debug_format, dry_run_binary_fmt
    )
    if debug_format_error is not None:
        raise click.BadParameter(debug_format_error)
    return effective_debug_format


@main.command("dump")
@dump_help_options  # curated --help + full --help-all (G21.8 collapse M2)
@click.argument("so_path", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("-H", "--header", "headers", multiple=True, type=click.Path(exists=True, path_type=Path),
              help="Public header file or directory (repeat for multiple).")
@click.option("-I", "--include", "includes", multiple=True, type=click.Path(path_type=Path),
              help="Extra include directory for castxml.")
# Declaration provenance (ADR-015) comes from -H/--header itself: a file
# entry tags that header public, a directory entry tags everything under it
# (split by header_utils.split_public_header_inputs, the same partition
# `compare` has always applied to its own -H list). The separate
# --public-header/--public-header-dir pair said the same thing a second way.
@include_dependencies_option
@click.option("--version", "version", default="unknown", show_default=True,
              help="Library version string to embed in snapshot.")
@lang_option
@click.option("-o", "--output", "output", type=click.Path(path_type=Path), default=None,
              help="Output JSON file. Defaults to stdout.")
@snapshot_compression_option
# ── L2 compile context (shared with `scan` — ADR-037 D3 parity) ──────────────
# --ast-frontend / --compiler / --compiler-prefix / --compiler-option /
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
# The L2 compile database comes from --build-info, whose operand is already
# "a build dir, a compile_commands.json, or a pre-captured pack" -- the same
# thing -p/--build-dir and its --compile-db alias took.
@click.option("--compile-db-filter", "compile_db_filter", default=None,
              help="Glob pattern to filter compile_commands.json entries by source file "
                   "(e.g. 'src/libfoo/**'). Useful for large databases.")
# ── Debug artifact resolution (ADR-021a) ──────────────────────────────────────
@click.option("--debug-root", "debug_roots", multiple=True, type=click.Path(path_type=Path),
              help="Directory containing separate debug files (build-id trees, "
                   "path-mirror debug files, or dSYM bundles). This option can be repeated.")
@click.option("--debuginfod", is_flag=True, default=False,
              help="Enable debuginfod network resolution for debug info (opt-in). "
                   "Uses DEBUGINFOD_URLS environment variable or --debuginfod-url.")
@click.option("--debuginfod-url", "debuginfod_url", default=None,
              help="debuginfod server URL (overrides DEBUGINFOD_URLS env var).")
# ── Multi-TU manifest (ADR-050 D3) ────────────────────────────────────────────
@click.option("--dump-manifest", "dump_manifest_path",
              type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="A strict YAML document describing multiple translation units to compile "
                   "and merge into one snapshot, instead of a single -H/--header list. "
                   "Mutually exclusive with -H/--header (declare the public surface in "
                   "the manifest's own roots field and base profile instead). ELF only "
                   "so far.")
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
@compile_context_options()  # --ast-frontend + cross-toolchain (shared with `scan`)
def dump_cmd(so_path: Path | None, headers: tuple[Path, ...], includes: tuple[Path, ...],
             include_dependencies: bool,
             version: str, lang: str, header_backend: str, output: Path | None,
             snapshot_compression: str,
             compiler_path: str | None, compiler_prefix: str | None,
             compiler_option_tokens: tuple[str, ...],
             sysroot: Path | None, nostdinc: bool, pdb_path: Path | None,
             follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
             dwarf_only: bool, dry_run: bool,
             debug_format_opt: str | None,
             debug_format: str | None,
             compile_db_filter: str | None,
             debug_roots: tuple[Path, ...],
             debuginfod: bool, debuginfod_url: str | None,
             dump_manifest_path: Path | None,
             verbose: bool,
             git_tag: str | None, build_id: str | None, no_git: bool,
             build_info: Path | None = None, sources: Path | None = None,
             build_config: Path | None = None, allow_build_query: bool = False,
             build_query: str | None = None, build_compile_db: str | None = None,
             build_targets: tuple[str, ...] = (),
             depth: str | None = None,
             header_graph_deprecated: bool = False,
             header_graph_includes_deprecated: bool = False,
             frontend_context: str = "host",
             # --gcc-options removed as a CLI flag (CLI audit PR 5/5); this
             # defaulted-None parameter stays only so the internal composition
             # below (_merge_gcc_options et al.) doesn't need to change --
             # it's never populated from the CLI anymore, only ever None here.
             gcc_options: str | None = None,
             _resolved_compile_context: CompileContext | None = None,
             _resolved_collect_mode: str | None = None,
             _resolved_include_labels: dict[Path, str] | None = None,
             _resolved_lang_explicit: bool | None = None) -> None:
    """Dump ABI snapshot of a shared library to JSON.

    \b
    Example:
      abicheck dump libfoo.so.1 -H include/foo.h --version 1.2.3 -o snap.json
      abicheck dump --sources ./libfoo-src/ -o libfoo.src.json  # source-only (no binary)
    """
    # Imported here, not at module level: the snapshot write path lives in
    # `cli_buildsource`, which reaches back into this module, so a static
    # top-level import would be the very edge the lazy `__getattr__` shim at
    # the tail of this file exists to avoid (AGENTS.md, "Moving helpers out of
    # a module that re-exports them"). That shim only serves *attribute*
    # access on the module (`cli._write_snapshot_output`); a bare name inside
    # this module needs a real import.
    from .cli_buildsource import (
        _write_snapshot_output as _write_snapshot_output_fn,
        resolve_dump_request_for_cli,
    )
    from .cli_dump_request import build_dump_request
    from .cli_options import warn_deprecated_header_graph_flags
    from .dry_run import emit_dry_run, reject_dry_run_with_output

    warn_deprecated_header_graph_flags(
        header_graph_deprecated, header_graph_includes_deprecated
    )

    reject_dry_run_with_output(dry_run, output)
    if output is None and snapshot_compression not in ("auto", "none"):
        raise click.UsageError(
            f"--compression {snapshot_compression} requires -o/--output -- "
            "stdout is always plain JSON (auto resolves to 'none' without "
            "-o/--output; pass an explicit output file to write compressed "
            "output)."
        )
    reject_snapshot_compression_conflict(output, snapshot_compression)
    _setup_verbosity(verbose)

    # G31 Phase C follow-up (AGENTS.md "dump --lang c++ is silently discarded
    # ..." known gap): --lang carries a Click default of "c++" (LANG_DEFAULT),
    # so the resolved `lang` string alone can never distinguish a genuinely
    # explicit `--lang c++` request from the unspecified default -- both
    # produce the identical value. `perform_elf_dump` (and the ELF header-AST
    # passes it drives) normalizes a non-"c" `lang` to `None` for the common
    # default case, to preserve auto-detection -- but that squash previously
    # discarded an explicit request too, since there was nothing here to tell
    # the two apart. Resolved once, from Click's own parameter-source
    # bookkeeping, and threaded through so the primary snapshot pass and the
    # header-graph pass agree on the same explicit-vs-auto-detected decision.
    #
    # `_resolved_lang_explicit` (Codex review): the private hook
    # `_embed_inline_source_side`'s nested `ctx.invoke(dump_cmd, ...)` uses to
    # hand in an explicitness already computed against *compare*'s own real
    # ctx -- mirrors `_resolved_compile_context`/`_resolved_collect_mode`/
    # `_resolved_include_labels` immediately above. Without it, this
    # ctx.invoke sub-context has no COMMANDLINE parameter source for `lang`
    # (the same loss those other hooks already exist to work around), so a
    # `compare --lang c++ --old-sources tree/` side would silently resolve
    # `lang_explicit=False` here regardless of what the user actually typed.
    lang_explicit = (
        _resolved_lang_explicit
        if _resolved_lang_explicit is not None
        else click.get_current_context().get_parameter_source("lang")
        == click.core.ParameterSource.COMMANDLINE
    )

    # ADR-050 D3: parsed before the collect/compile-context resolution below so
    # a bad manifest fails fast, and validated against the *raw* CLI values
    # (headers hasn't been reassigned yet).
    parsed_dump_manifest = None
    parsed_dump_manifest = _load_dump_manifest_or_reject(dump_manifest_path, headers)

    # Declaration provenance (ADR-015) is derived from -H/--header itself:
    # file entries tag those headers public, directory entries tag everything
    # under them. Same partition `compare` applies to its own -H list, so a
    # `dump -H include/` and the equivalent `compare -H include/` describe one
    # public surface rather than two.
    from .header_utils import split_public_header_inputs

    # Resolve the evidence-depth preset into the collect mode, apply --depth binary
    # suppression, and warn on an explicitly-requested deep depth without sources.
    collect_mode, headers = resolve_dump_collect_context(
        depth, _resolved_collect_mode, sources, build_info, headers,
    )

    # Derived from the *post-suppression* headers, not the raw ones: at
    # --depth binary `resolve_dump_collect_context` clears the header-AST
    # inputs, and provenance roots split off beforehand survived that and were
    # still stamped into the snapshot's scope contract. Two binary-depth
    # snapshots taken with different -H sets then carried different scope
    # fingerprints and `compare` rejected the pair with ScopeMismatchError --
    # at the one depth that is supposed to ignore headers entirely (Codex
    # review). Deriving after the clear makes them empty exactly when the
    # headers they describe are.
    _public_header_files, _public_header_dirs = split_public_header_inputs(headers)
    public_headers = tuple(_public_header_files)
    public_header_dirs = tuple(_public_header_dirs)
    # The L2 compile database is whatever --build-info names, read back after
    # --depth binary has had its say about the headers (a headerless dump has
    # no header AST for a database to parameterize).
    compile_db_path = compile_db_from_build_info(build_info, headers)
    if (
        _filter_scope_error := compile_db_filter_scope_error(
            compile_db_filter, compile_db_path, collect_mode
        )
    ) is not None:
        raise click.UsageError(_filter_scope_error)

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
        gcc_options=gcc_options, sysroot=sysroot, nostdinc=nostdinc,
        header_backend=header_backend, includes=includes,
        build_config=build_config, sources=sources,
        frontend_context=frontend_context,
        compiler_path=compiler_path, compiler_prefix=compiler_prefix,
        compiler_option_tokens=compiler_option_tokens,
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
    # the dry-run report and the real run, and raise the same BadParameter a
    # real run would -- unconditionally, before the --dry-run branch, exactly
    # like the hybrid+depth UsageError check above.
    # This validation (the debug-format/PE-Mach-O BadParameter below)
    # previously only ran in the real path, after the dry-run branch, so
    # `dump --dry-run` could report success on an invocation the real run
    # would immediately reject.
    # CodeRabbit review: an earlier version of this fix instead encoded it
    # as a DryRunResult blocker (exit 1) -- silently downgrading what is a
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
    effective_debug_format = _resolve_and_check_dump_debug_format(
        so_path, debug_format_opt, debug_format,
    )

    # CLI cleanup phase two, PR 3A blocker 5: one `DumpRequest` describing this
    # invocation, built from the CLI's *already-resolved* values (the
    # `CompileContext` above, the frontend, the explicit-language decision) so
    # the request records the run rather than forming a second opinion about
    # it. See `cli_dump_request.py`'s own module docstring for why that
    # direction matters.
    #
    # Built here, before the branch, rather than inside `if dry_run:` -- and
    # only `--dry-run` consumes it today. That is deliberate and worth being
    # plain about: the real ELF/PE/Mach-O run still executes through
    # `perform_elf_dump`/`handle_non_elf_dump` below (three obstacles remain,
    # recorded in the plan's PR 3A section), so this is the object that
    # migration will build from, positioned where both branches can reach it
    # rather than tucked inside the one that currently does.
    _dump_request = build_dump_request(
        so_path=so_path, headers=headers, includes=includes,
        version=version, lang=lang, lang_explicit=lang_explicit,
        header_backend=header_backend, compile_context=_cc,
        frontend_context=frontend_context, depth=depth,
        dwarf_only=dwarf_only, debug_format=effective_debug_format,
        pdb_path=pdb_path, debug_roots=debug_roots,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
        dump_manifest=parsed_dump_manifest,
        sources=sources, build_info=build_info, build_targets=build_targets,
        include_dependencies=include_dependencies,
        follow_deps=follow_deps, search_paths=search_paths,
        ld_library_path=ld_library_path,
        include_labels=_resolved_include_labels,
        resolved_collect_mode=_resolved_collect_mode,
    )

    if dry_run:
        from .buildsource.inline import is_pack_dir
        from .cli_buildsource_helpers import _is_inputs_pack_dir
        from .cli_dump_dry_run_build_query import add_build_query_dry_run_section
        from .cli_dump_helpers import render_dump_dry_run
        from .cli_helpers_compare import dry_run_compile_db_matched

        # The dry-run report is now rendered from a real `ResolvedDumpRequest`
        # -- the resolve-only half of the same pipeline `run_dump_request`
        # executes -- instead of from `dump_cmd`'s own hand-derived locals.
        # `resolve_dump_request` runs no castxml/clang and writes nothing, so
        # this stays inside `render_dump_dry_run`'s own "cheap, read-only
        # resolution" contract.
        _resolved = resolve_dump_request_for_cli(_dump_request)
        _dry_matched = dry_run_compile_db_matched(
            compile_db_path, None, headers, compile_db_filter,
        )
        _dry_result = render_dump_dry_run(
            so_path=so_path, headers=_resolved.headers, sources=sources,
            build_info=build_info, build_config=build_config,
            depth=_resolved.requested_depth,
            collect_mode=_resolved.collect_mode,
            header_backend=_resolved.header_backend, output=output,
            snapshot_compression=snapshot_compression,
            has_compile_db=compile_db_path is not None,
            # External review: dry-run previously only checked bare -p/
            # --compile-db presence; loading it and matching against the
            # resolved headers is cheap, deterministic, read-only
            # resolution, not "real work out of scope for a dry run".
            compile_db_matched=_dry_matched,
            # embed_build_source's own classification: a source-capable
            # --build-info is either a BuildSourcePack (is_pack_dir) or a
            # Flow-2 abicheck_inputs/ directory (_is_inputs_pack_dir) --
            # both can carry L4 source_abi facts, unlike a raw compile
            # DB/build dir (Codex review, second finding on this signal).
            build_info_is_pack=(
                is_pack_dir(build_info) or _is_inputs_pack_dir(build_info)
            ),
            dump_manifest=parsed_dump_manifest,
        )
        # CLI cleanup phase two, PR 3C prerequisite 3: show whether/why
        # build.query would execute, without ever running it.
        add_build_query_dry_run_section(
            _dry_result, so_path=so_path,
            dump_manifest_given=parsed_dump_manifest is not None,
            sources=sources, headers=headers,
            collect_mode=collect_mode, build_info=build_info,
            build_config=build_config,
            build_query=build_query, build_compile_db=build_compile_db,
        )
        emit_dry_run(_dry_result)

    # Source-only dump (no binary) for the parallel-baseline flow.
    if so_path is None:
        from .cli_buildsource import dump_source_only
        dump_source_only(sources, build_info, version, output, build_config, allow_build_query, git_tag, build_id, no_git, collect_mode, build_query=build_query, build_compile_db=build_compile_db, build_targets=build_targets, extractor=header_backend, depth=depth, include_dependencies=include_dependencies, gcc_path=gcc_path, gcc_prefix=gcc_prefix, snapshot_compression=snapshot_compression)
        return

    effective_compile_db = compile_db_path

    # Resolved before the PE/Mach-O dispatch (Codex review): both binary-format
    # branches need the same --build-info -> castxml/clang flags and matched
    # signal -- the ELF path used to compute these only after the PE/Mach-O
    # early return, so a compile database's flags were silently dropped for
    # PE/Mach-O input (parsed_with_build_context was never stamped either).
    build_context_flags, compile_db_matched = _resolve_build_context_flags(
        effective_compile_db, headers, compile_db_filter,
    )
    effective_gcc_options = _merge_gcc_options(build_context_flags, gcc_options)

    # Auto-detect binary format — PE/Mach-O skip the ELF/castxml path. The
    # conventional ``libfoo.so`` dev symlink is often a GNU ld linker script;
    # follow it to the real shared library before dispatching.
    so_path, binary_fmt = _normalize_binary_input(so_path)
    if effective_debug_format is not None and binary_fmt in ("pe", "macho"):
        raise click.BadParameter(
            f"--{effective_debug_format} is only supported for ELF binaries, not {binary_fmt.upper()}."
        )

    if binary_fmt in ("pe", "macho"):
        if parsed_dump_manifest is not None:
            raise click.UsageError(
                f"--dump-manifest is not yet supported for {binary_fmt.upper()} "
                "binaries (ADR-050 D3); use a single-header dump for this format."
            )
        native_cc = (
            dataclasses.replace(_cc, gcc_options=effective_gcc_options)
            if effective_gcc_options != _cc.gcc_options
            else _cc
        )
        handle_non_elf_dump(
            so_path, binary_fmt, headers, includes, version, lang, pdb_path,
            follow_deps, git_tag, build_id, no_git, output,
            _dump_native_binary, _stamp_provenance, _write_snapshot_output_fn,
            public_headers, public_header_dirs, build_info, sources, build_config,
            allow_build_query, collect_mode, build_query, build_compile_db,
            build_targets=build_targets,
            header_backend=header_backend, compile_context=native_cc,
            depth=depth, compile_db_context_matched=compile_db_matched,
            include_dependencies=include_dependencies,
            snapshot_compression=snapshot_compression,
            lang_explicit=lang_explicit,
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
        lang_explicit=lang_explicit,
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
        write_snapshot_output=_write_snapshot_output_fn,
        build_query=build_query,
        build_compile_db=build_compile_db,
        build_targets=build_targets,
        compile_context=_cc,
        depth=depth,
        compile_db_context_matched=compile_db_matched,
        dump_manifest=parsed_dump_manifest,
        include_labels=_resolved_include_labels,
        include_dependencies=include_dependencies,
        snapshot_compression=snapshot_compression,
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
    severity_config: SeverityConfig | None = None,
    demangle: bool = False,
    contract_evaluation: bool = False,
    require_complete_analysis: bool = False,
) -> str:
    """Render comparison result in the requested output format.

    No ``stat``/``show_recommendation`` parameters (CLI cleanup phase two,
    PR 1): the one-line summary is reached only via ``fmt ==
    service_render.ONELINE_FORMAT`` (the built-in ``quick`` --profile's own
    injection). The release recommendation is unconditional for every CLI
    invocation -- achieved by explicitly passing ``show_recommendation=True``
    below, not by changing :func:`service.render_output`'s own default
    (which stays ``False``, the pre-removal Tier-2 Python API default, per
    Codex review, fresh evidence -- a direct caller that omits the keyword
    must keep getting the behaviour it always got).
    """
    from .service import render_output
    return render_output(
        fmt, result, old, new,
        follow_deps=follow_deps, show_only=show_only,
        report_mode=report_mode, show_impact=show_impact,
        severity_config=severity_config,
        demangle=demangle,
        contract_evaluation=contract_evaluation,
        show_recommendation=True,
        require_complete_analysis=require_complete_analysis,
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






def _write_or_echo(output: Path | None, text: str) -> None:
    """Write text to file or echo to stdout."""
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)


def _announce_exit_scheme(
    scheme: str,
    *, fmt: str = "markdown",
) -> None:
    """Announce (on stderr) which exit-code scheme the compare command uses.

    The scheme is now explicit (ADR-037 D12 / D4: ``--exit-code-scheme`` or the
    config's ``exit_code_scheme``, with ``auto`` already resolved to ``legacy`` or
    ``severity`` by the time we get here). Kept on stderr so it never pollutes the
    report on stdout, and only for the human-readable formats — machine formats
    (json/sarif/junit) and the internal one-line format (``service_render.
    ONELINE_FORMAT``, the built-in ``quick`` --profile's sole surviving use of
    ``--stat``'s old one-line output) are consumed by tooling that treats the
    whole captured stream as data, so the banner is suppressed for those too;
    the ``fmt not in {...}`` check below already covers it without a separate
    boolean, since it isn't one of the three human-readable format names.
    """
    if fmt not in {"markdown", "html", "review"}:
        return
    if scheme == "severity":
        click.echo(
            "Exit-code scheme: severity-aware (per-category severity settings).",
            err=True,
        )
    else:
        click.echo(
            "Exit-code scheme: legacy verdict (0=compatible, 2=API break, 4=ABI break; "
            "with --contract, 1=incomplete contract coverage; with "
            "--require-complete-analysis, 1=incomplete analysis assurance -- both "
            "orthogonal axes that never lower a 2/4). "
            "Pass --exit-code-scheme severity (or a severity setting) for the "
            "severity-aware scheme.",
            err=True,
        )


def _exit_with_severity_or_verdict(
    result: DiffResult, sev_config: SeverityConfig | None, scheme: str,
    fmt: str | None = None, secondary_fmt: str | None = None,
    *, require_complete_analysis: bool = False,
) -> None:
    """Exit with the appropriate code for the resolved exit-code scheme.

    ADR-049 Phase 7 / P0.4: the contract-coverage and analysis-assurance axes
    are folded in here rather than at each call site, so a command cannot
    acquire a compatibility exit and forget an orthogonal one. Since CLI
    cleanup phase two PR G1, the fold itself is
    `exit_decision.resolve_compare_exit_decision` -- the one canonical
    resolver, rather than three separately-called `max` folds -- so a
    caller building the report's `exit` block (`reporter_contract_blocks.
    add_contract_context`) and this function's own process exit read the
    identical decision. The diagnostics below still read each axis's own
    contribution straight off the resolved `ExitDecision`, so their wording
    (and the final exit code) are unchanged from before this delegation.
    """
    from .analysis_assurance import assurance_floor_diagnostic
    from .contract_coverage_exit import announce_coverage_floor
    from .exit_decision import resolve_compare_exit_decision

    decision = resolve_compare_exit_decision(
        result, sev_config, scheme,
        require_complete_analysis=require_complete_analysis,
    )
    announce_coverage_floor(
        result, base_exit=decision.compatibility_contribution,
        fmt=fmt, secondary_fmt=secondary_fmt,
    )
    # The pre-assurance exit is what the diagnostic's own wording describes
    # ("floored to"/"contributes, below the compatibility axis's own exit"),
    # so it excludes the assurance contribution itself rather than reading
    # `decision.code` (which, when assurance is the winning axis, would be
    # self-referential).
    pre_assurance_exit = max(
        decision.compatibility_contribution, decision.contract_coverage_contribution,
    )
    diagnostic = assurance_floor_diagnostic(
        result, require_complete=require_complete_analysis, base_exit=pre_assurance_exit
    )
    if diagnostic is not None:
        click.echo(diagnostic, err=True)
    if decision.code != 0:
        sys.exit(decision.code)


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
    severity_config: SeverityConfig | None = None,
    contract_evaluation: bool = False,
) -> None:
    """Attach metadata and emit redundancy/filter/suppression output."""
    result.old_metadata = _collect_metadata(old_input)
    result.new_metadata = _collect_metadata(new_input)

    if show_redundant and result.redundant_changes:
        _merge_redundant_changes(result)
    if show_filtered and result.out_of_surface_changes:
        echo_filtered_surface(result, contract_evaluation=contract_evaluation)
    if show_filtered and result.reconciled_changes:
        echo_reconciled(result, contract_evaluation=contract_evaluation)

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
    # CLI cleanup phase two, PR E removed --annotate/--annotate-additions,
    # the flag that used to gate a $GITHUB_STEP_SUMMARY write here as a side
    # effect. Making that write unconditional-in-CI instead (an earlier
    # revision of this comment) was itself a real regression (Codex review,
    # fresh evidence): when this command runs through the composite Action,
    # the subprocess inherits GITHUB_ACTIONS=true/GITHUB_STEP_SUMMARY from
    # the Action's own job, so an unconditional write here double-writes
    # against action/run.sh's own, richer, INPUT_ADD_JOB_SUMMARY-gated job
    # summary (or writes one even when a caller explicitly set
    # add-job-summary: false). The CLI no longer writes a step summary on
    # its own at all -- annotations_step_summary.emit_github_step_summary
    # stays available as a public primitive for a caller invoking the CLI
    # directly outside the composite Action to call itself.


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
            "packages: sarif/html/review require a single-pair (non-directory, "
            "non-package) comparison. Choose one of: "
            f"{', '.join(sorted(_RELEASE_FORMATS))}, or compare one library at "
            f"a time (a single old/new .so pair) to use --format {fmt}."
        )
    # CLI cleanup phase two, PR E: --write now works for a release operand
    # (compare_release_cmd's own secondary_output_options only declares
    # json/markdown/junit, matching _RELEASE_FORMATS) -- but `compare`'s own
    # --write accepts sarif/html/review too, parsed by its own Click
    # callback *before* this dispatch ever runs, so an incompatible
    # secondary format must be rejected here explicitly. Without this,
    # compare_release_cmd's own callback is reached directly (not through
    # Click's arg parsing, so its own decorator-level validation never
    # runs) and _format_release_summary's fallback branch would silently
    # render markdown to the requested sarif/html/review path instead of
    # erroring.
    secondary_fmt = kwargs.get("secondary_fmt")
    if secondary_fmt is not None and secondary_fmt not in _RELEASE_FORMATS:
        raise click.UsageError(
            f"--write {secondary_fmt}=... is not available when comparing "
            "directories or packages: sarif/html/review require a "
            "single-pair (non-directory, non-package) comparison. Choose "
            f"one of: {', '.join(sorted(_RELEASE_FORMATS))}, or compare one "
            "library at a time (a single old/new .so pair) to use --write "
            f"{secondary_fmt}=..."
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
    lang_explicit: bool = False,
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
    include_labels: dict[Path, str] | None = None,
    include_dependencies: bool = False,
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

    ``lang_explicit`` (G31 Phase C follow-up, Codex review): whether ``lang``
    is a genuinely explicit ``--lang`` on *compare*'s own real ``ctx``, mirroring
    ``frontend_explicit``/``nostdinc_explicit`` immediately below — the nested
    ``ctx.invoke(dump_cmd, ...)`` below has no ``COMMANDLINE`` parameter
    source of its own for ``lang``, so without this a `compare --lang c++
    --old-sources tree/` side would silently auto-detect instead of honoring
    the explicit request on a language-ambiguous header. Forwarded via
    ``dump_cmd``'s private ``_resolved_lang_explicit`` hook, the same shape
    ``_resolved_compile_context``/``_resolved_collect_mode``/
    ``_resolved_include_labels`` already use.

    ``include_labels`` (ADR-050 D1, CodeRabbit review): this side's already-
    resolved ``path -> label`` map from a labeled ``--include
    old:LABEL=PATH``/``new:LABEL=PATH`` compare entry, forwarded to the
    inline ``dump`` invocation's private ``_resolved_include_labels`` hook —
    without this, a raw ``--old/new-sources`` tree's inline-dumped temporary
    snapshot silently lost its label, leaving that side's extraction contract
    fingerprinted as if the support root were unlabeled/external even though
    the non-inline path already threads the same label correctly.

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
            ignored.append(f"--sources {label}= source tree")
        if build_info_raw:
            ignored.append(f"raw --build-info {label}=")
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
            f"Warning: --sources {label}=/--build-info {label}= was given but the "
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
            f"the --sources {label}= tree: L4 source-ABI replay has no "
            "dual-backend hybrid extractor (unlike the L2 header-AST "
            f"snapshot). Pass --ast-frontend {label}=castxml or "
            f"--ast-frontend {label}=clang (or an unsided --ast-frontend) "
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
        _resolved_lang_explicit=lang_explicit,
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
        _resolved_include_labels=include_labels,
        # Thread compare's own --include-system-declarations flag through, rather
        # than hardcoding it, so this inline `--old/new-sources` embed path
        # scopes the same way the sibling path (a side reaching
        # service.run_dump directly, with no raw source tree) now does --
        # merely adding deeper L3-L5 evidence to an otherwise-identical
        # compare must not silently change this side's dependency scope
        # depending only on which evidence flags happened to be passed.
        include_dependencies=include_dependencies,
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
# (ADR-037 D3), with --ast-frontend side-aware here; --lang stays inline.
@two_sided_input_options
@compile_context_options(sided_frontend=True)  # --ast-frontend (side-aware) + cross-toolchain
@lang_option
# ── Compare options (unchanged) ──────────────────────────────────────────────
@output_options(
    ["json", "markdown", "sarif", "html", "junit", "review"],
    format_help="Output format. 'review' emits a compact GitHub-facing digest "
                "(verdict + counts + release recommendation + manual-review banner) "
                "suitable for a job summary or PR comment.",
)
@secondary_output_options(
    ["json", "markdown", "sarif", "html", "junit", "review"],
    format_help="Emit a second output format from this same comparison run, to "
                "its own file, without re-running the comparison (e.g. "
                "--format markdown for a human alongside --write json=abi.json "
                "for tooling). FORMAT is one of {formats}; PATH must differ from "
                "--output/-o. Always renders the full, unfiltered report "
                "(ignores --show-only). For a directory/package (release) "
                "comparison, only json/markdown/junit are available.",
)
@click.option("--demangle/--no-demangle", default=None,
              help="Demangle C++ symbol names in markdown/review output (default "
                   "ON; use --no-demangle to turn off). json/sarif always keep raw "
                   "mangled names, and HTML is rendered structurally and is never "
                   "demangled regardless of this flag.")
# Policy + suppression family (ADR-037 D3). The strict/justification pair
# lives only in .abicheck.yml's suppression: block now (ADR-037 D4).
@policy_options
@click.option("--pdb-path", "pdb", multiple=True, type=SIDED_PATH_PARAM,
              help="Explicit PDB file path for Windows PE debug info. Applies to both "
                   "sides; scope to one with an 'old='/'new=' prefix, repeating the flag "
                   "per side (e.g. --pdb-path old=a.pdb --pdb-path new=b.pdb). Overrides "
                   "automatic PDB discovery (ADR-040).")
# ── Scoped comparison (ADR-043): app-usage and required-symbol contracts ─────
@app_usage_scope_options
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
                   "explicitly here so passing --severity-preset no longer silently "
                   "changes the scheme. Default: config's exit_code_scheme, else auto. "
                   "Deliberately kept visible (unlike the removed per-category "
                   "--severity-* family) -- ADR-040 D4 keeps it a coarse override; "
                   "see test_config_rebalance.py's test_coarse_overrides_stay_visible.")
@click.option("--follow-deps", is_flag=True, default=False,
              help="Resolve transitive dependencies for both old and new, compute symbol "
                   "bindings, and include a dependency-change section in the report. ELF only.")
@include_dependencies_option
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries (with --follow-deps).")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (with --follow-deps).")
@header_graph_options  # hidden deprecated no-op shim (shared with `dump`)
@scope_options  # --scope-public-headers/--no- (ADR-037 D3); --show-filtered stays inline
@click.option("--show-filtered", "show_filtered", is_flag=True, default=False,
              help="List findings excluded by --scope-public-headers (audit trail).")
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
@click.option("--report-mode", "report_mode",
              type=click.Choice(["full", "leaf", "impact", "root-cause"], case_sensitive=True),
              default="full", show_default=True,
              help="Report mode: 'full' lists all changes individually (default), "
                   "'leaf' groups by root type changes with impact lists, "
                   "'impact' behaves as 'full' plus an impact summary table "
                   "listing root changes and the interfaces they affect, "
                   "'root-cause' groups findings sharing a root cause "
                   "(Change.caused_by_type) under one entry for "
                   "--format json/markdown (the default rendered text output); "
                   "--format sarif keeps its normal one-result-per-finding "
                   "shape but adds properties.rootCauseId/rootCause to each "
                   "result; --format junit still renders as 'full'.")
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
@click.option("--diagnostic-comparison", "diagnostic_comparison", is_flag=True, default=False,
              help="ADR-050 D2's sanctioned escape hatch: when OLD and NEW were "
                   "extracted under a genuinely incomparable profile/scope "
                   "(ExtractionContract mismatch), downgrade the default hard "
                   "failure (exit 16, no verdict) into a tentative diff instead, "
                   "stamped assurance: \"none\" everywhere in the report so a "
                   "reader knows not to trust it the way an ordinary comparable "
                   "diff is trusted. Not needed, and does nothing, on a "
                   "comparable pair.")
@contract_options  # ADR-049: --contract/--audit-suppressions
@pack_option  # ADR-049 D8: --pack
@click.option("--use-cases", "use_cases_manifest",
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None,
              help="An impact-use-cases.yaml manifest (G29 Phase 4, ADR-057 "
                   "amendment) whose declared use cases this comparison's own "
                   "findings are attributed to: for each use case, which changes "
                   "its resolved entrypoints can be shown to reach. Needs a "
                   "source graph on at least one side (dump --sources/"
                   "--build-info, or the always-on header-only graph). Read-only "
                   "-- an unattributed finding is an absence of proof, not proof "
                   "the finding is harmless, so this never moves a verdict or an "
                   "exit code. Validate a manifest on its own with "
                   "`abicheck project validate-use-cases`.")
@click.option("--require-complete-analysis", "require_complete_analysis",
              is_flag=True, default=False,
              help="P0.4: fail the build when analysis_assurance.status is not "
                   "'complete', independent of the compatibility verdict. "
                   "Contributes exit 1, folded with max the same way "
                   "--contract's coverage axis is (ADR-049 Phase 7): "
                   "it raises a clean 0 to 1 and never lowers a 2/4. Single-pair "
                   "compares only, not the directory/package release fan-out. "
                   "See docs/reference/exit-codes.md.")
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
    Exit codes (legacy, with no severity setting in effect):
      0  NO_CHANGE, COMPATIBLE, or COMPATIBLE_WITH_RISK — no binary ABI break
         (COMPATIBLE_WITH_RISK: deployment risk present; check the report)
      2  API_BREAK — source-level API break — recompilation required
      4  BREAKING — binary ABI break detected
    \b
    Exit codes (severity-aware, with --severity-preset or a config severity: block):
      0  No error-level findings
      1  Error-level findings in addition or quality_issues only
      2  Error-level findings in potential_breaking (but not abi_breaking)
      4  Error-level findings in abi_breaking
    \b
    Orthogonal to both tables (ADR-049 Phase 7): with --contract,
    incomplete contract coverage of the selected --contract domain
    contributes exit 1. It is folded with max, so it raises a clean 0 to 1
    and never lowers a 2/4 — under the legacy scheme, 1 can only mean this.
    Without --contract there is no domain to be short of evidence
    for and the tables above are exhaustive. Set contract.unresolved=warn
    (via a `kind: contract` --pack) to accept incomplete coverage.
    \b
    A second, independent orthogonal axis (P0.4): with
    --require-complete-analysis, an analysis_assurance.status other than
    "complete" (how complete/trustworthy the evidence itself was — depth,
    TU/export accounting, fact-set comparability, header-context drift,
    source-graph completeness — independent of what the verdict says)
    contributes exit 1 the same way, folded with the same max discipline.
    Without the flag, analysis_assurance is still always computed and
    reported in --format json, it just never affects the exit code.
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


# The snapshot write path moved to `cli_buildsource` (this module sits near the
# AI-readiness file-size cap; see that module's own "Snapshot output" section).
# These names are kept resolvable at their historical `abicheck.cli` path for
# tests and for `cli_dump_helpers`' `cli._layer_payload_empty` /
# `cli._missing_requested_evidence_layers` lookups. A module-level `__getattr__`
# (PEP 562) resolves them lazily via `importlib.import_module` -- a runtime
# call, not a static import edge -- so `cli` never grows a top-level dependency
# on `cli_buildsource`, which imports back into `cli` (AGENTS.md, "Moving
# helpers out of a module that re-exports them"). New code should import from
# `cli_buildsource` directly.
_SNAPSHOT_OUTPUT_REEXPORTS = frozenset({
    "_classify_missing_layers",
    "_layer_payload_empty",
    "_missing_requested_evidence_layers",
    "_write_snapshot_output",
})


def __getattr__(name: str) -> Any:
    if name in _SNAPSHOT_OUTPUT_REEXPORTS:
        import importlib

        return getattr(importlib.import_module("abicheck.cli_buildsource"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    sys.modules.setdefault("abicheck.cli", sys.modules[__name__])

from . import (  # noqa: E402  — must run after `main` and helpers are defined
    cli_aggregate,  # noqa: F401  — registers aggregate
    cli_buildsource,  # noqa: F401  — buildsource internals (no command of its own)
    cli_project,  # noqa: F401  — registers project (validate, validate-build, plan)
    cli_scan,  # noqa: F401  — registers scan
    cli_stack,  # noqa: F401  — registers deps (tree, compare)
)

if __name__ == "__main__":
    main()
