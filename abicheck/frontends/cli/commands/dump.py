# Copyright 2026 Nikolay Petrov
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

"""``abicheck dump`` -- command input translation (ADR-061 Phase 4 item 1).

Translates the command's ~30 Click parameters into one ``DumpRequest``,
resolves it once, and hands the *resolved plan* to execution. Both the
``--dry-run`` preview and the real run read their inputs off that one resolved
object, which is what keeps a preview from describing a run nobody performed.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ....cli_dump_helpers import (
    _dump_will_attempt_hybrid_l4_extraction,
    compile_db_filter_scope_error,
    compile_db_for_filter_scope_check,
    compile_db_from_build_info,
    reject_snapshot_compression_conflict,
    resolve_dump_collect_context,
    resolve_dump_compile_context,
    resolve_dump_debug_format,
)
from ....cli_helpers_compare import (  # noqa: F401  — re-exported to keep cli import sites stable
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
from ....cli_options import (
    build_source_dump_options,
    compile_context_options,
    header_graph_options,
    include_dependencies_option,
    lang_option,
    snapshot_compression_option,
    verbose_option,
)
from ....cli_resolve import (
    _click_notify,
    _expand_header_inputs,
    _normalize_binary_input,
)
from ....frontends.cli import help as cli_help
from ..options.params import (
    _load_suppression_and_policy as _load_suppression_and_policy,  # noqa: F401  — re-exported to keep cli import sites (test suite) stable
)

if TYPE_CHECKING:
    from ....service_scan import CompileContext


# `main` is the Click group this command registers on; importing it here and
# importing this module from `cli.py`'s registration block is the same
# side-effect pattern every sibling `cli_*` command module already uses.
from ....cli import main
from ..runtime import (
    _resolve_debug_artifact,
    _setup_verbosity,
    _stamp_provenance,
)


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
    from ....errors import ManifestValidationError
    from ....workflows.extraction import load_manifest

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
    from ....cli_dump_helpers import check_dump_debug_format_error
    from ....workflows.extraction import normalize_binary_input as _peek_binary_format

    effective_debug_format = resolve_dump_debug_format(debug_format_opt, debug_format)
    _, dry_run_binary_fmt = _peek_binary_format(so_path)
    debug_format_error = check_dump_debug_format_error(
        effective_debug_format, dry_run_binary_fmt
    )
    if debug_format_error is not None:
        raise click.BadParameter(debug_format_error)
    return effective_debug_format


@main.command("dump")
@cli_help.dump_help_options  # curated --help + full --help-all (G21.8 collapse M2)
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
    from ....cli_buildsource import (
        _write_snapshot_output as _write_snapshot_output_fn,
        resolve_dump_request_for_cli,
    )
    from ....cli_dump_request import build_dump_request
    from ....cli_options import warn_deprecated_header_graph_flags
    from ....dry_run import emit_dry_run, reject_dry_run_with_output

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
    # Resolve the evidence-depth preset into the collect mode, apply --depth binary
    # suppression, and warn on an explicitly-requested deep depth without sources.
    collect_mode, headers = resolve_dump_collect_context(
        depth, _resolved_collect_mode, sources, build_info, headers,
    )
    # PR 3C prerequisite 3's own residual gap (CLI cleanup phase two plan,
    # "The `-H` directory gap"): `--dry-run` never validated a `-H`
    # directory, so it could report success for an invocation the real run
    # (`_expand_header_inputs`, called downstream at the real-execution
    # call sites below) would reject outright -- a missing path, an empty
    # header directory, or a path that is neither a file nor a directory.
    # Checked here, unconditionally and before the `--dry-run` branch,
    # exactly like the hybrid+depth and binary+no-SO_PATH `UsageError`
    # checks above and below: both paths must reject the same input the
    # same way. The result is discarded -- this call exists purely for its
    # validation side effect, matching `--dry-run`'s own documented
    # contract (`render_dump_dry_run`'s docstring: "no I/O beyond
    # stat()/PATH lookups"), which a directory walk satisfies (real
    # execution still calls `_expand_header_inputs` again downstream for
    # its own actual expanded list -- cheap, idempotent, and not worth
    # threading a resolved value through every intermediate call site for).
    #
    # Scoped to `so_path is not None` (Codex review, fresh evidence): a
    # source-only dump (no SO_PATH) deliberately treats `-H` as inert --
    # `dump_source_only()` below never receives `headers` at all, so a
    # useless/empty `-H` directory has no effect on the written snapshot
    # there and only warns, never rejects (see that branch's own comment
    # for why a hard rejection was tried and reverted: it broke 20
    # pre-existing tests that legitimately pass `-H` alongside `--sources`
    # with no binary). Hard-rejecting it here, before that branch is even
    # reached, would have reintroduced exactly that regression under a
    # different name.
    if headers and so_path is not None:
        _expand_header_inputs(list(headers))
    # The public-header/-dir split this command used to compute here is now
    # read off the resolved plan (`_resolved.public_headers` /
    # `.public_header_dirs`, ADR-061 Phase 3), so there is one derivation
    # rather than two that agreed. `resolve_dump_request` keeps the invariant
    # that made this tricky: derive from the *post-suppression* headers, since
    # `--depth binary` clears the header-AST inputs and a provenance root
    # split off beforehand would otherwise survive into the scope contract --
    # two binary-depth snapshots taken with different -H sets then carried
    # different scope fingerprints and `compare` rejected the pair with
    # ScopeMismatchError, at the one depth that is supposed to ignore headers
    # entirely (Codex review). See that function's own comment.
    # The L2 compile database is whatever --build-info names, read back after
    # --depth binary has had its say about the headers (a headerless dump has
    # no header AST for a database to parameterize).
    compile_db_path = compile_db_from_build_info(build_info, headers)
    # The scope check itself resolves the compile database more broadly than
    # `compile_db_path` above -- a `--sources` tree with no `--build-info` can
    # still auto-discover one, and the L3->L2 fold/L3 embed both resolve it
    # from `sources` alone (Codex review, fresh evidence). Deliberately a
    # *separate* variable: `compile_db_path` itself must stay `--build-info`-
    # only, since it also drives the legacy `-p` auto-match further down
    # (`effective_compile_db`) -- see `compile_db_for_filter_scope_check`'s
    # own docstring for why widening it here would widen that too.
    if (
        _filter_scope_error := compile_db_filter_scope_error(
            compile_db_filter,
            compile_db_for_filter_scope_check(build_info, sources, headers),
            collect_mode,
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
    _gcc_option_tokens, sysroot, nostdinc = _cc.gcc_option_tokens, _cc.sysroot, _cc.nostdinc
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
    # same way (external review). Compared case-insensitively (CodeRabbit
    # review): `depth` here is already Click-normalized for a real CLI
    # invocation, but this check's own logic is what `DumpRequest.
    # validation_errors()`'s `_source_only_binary_depth_errors()` mirrors --
    # keeping both case-insensitive avoids the two independently drifting.
    if so_path is None and (depth or "").lower() == "binary":
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
    # Built AND resolved here, before the branch, because both branches now
    # consume the same resolved plan -- which is ADR-061 Phase 3's acceptance
    # criterion "dry-run renders the same resolved plan normal execution
    # consumes", stated as a structural fact rather than a claim. A preview
    # computed by a second resolver looks authoritative while being connected
    # to nothing; that is worse than two implementations kept in sync by hand,
    # because nothing fails when they drift.
    #
    # CLI cleanup phase two, PR C: the real ELF run now executes through
    # `execute_dump_request` too, given the legacy `-p`/`--compile-db`
    # auto-match's own derived flags (`_resolve_build_context_flags`,
    # computed below, strictly after `_resolved` here) as an explicit
    # pass-through (ADR-063 Phase 1's `legacy_compile_db_tokens`/
    # `legacy_compile_db_matched` parameters) -- see the real-run call site
    # below, and `docs/contribute/known-gaps.md`'s "PR C" entry for the
    # precise mechanism this closes. PE/Mach-O now executes through the
    # identical `execute_dump_request` pipeline too (ADR-063 Phase 1's own
    # PE/Mach-O slice) -- see the PE/Mach-O branch below and
    # `handle_non_elf_dump`'s own module docstring (it is retired for this
    # call site, kept defined for its own direct unit tests only). Verified
    # only via mock-based CLI/unit tests, not a real PE/Mach-O toolchain --
    # none was available where this was done.
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
        compile_db_filter=compile_db_filter,
    )
    # `resolve_dump_request` runs no castxml/clang and writes nothing, so
    # hoisting it above the branch keeps `--dry-run` inside its own "cheap,
    # read-only resolution" contract while giving the real run the same
    # object. Its validations are ones this command already performs
    # independently a few lines above (`TestResolvedRequestAgreesWithTheCliLocals`
    # pins the two in agreement), so this raises no error the real path did
    # not already raise -- it raises the same ones from one place.
    _resolved = resolve_dump_request_for_cli(_dump_request)

    if dry_run:
        from ....cli_buildsource_helpers import _is_inputs_pack_dir
        from ....cli_dump_dry_run_build_query import add_build_query_dry_run_section
        from ....cli_dump_helpers import render_dump_dry_run
        from ....cli_helpers_compare import dry_run_compile_db_matched
        from ....workflows.extraction import is_pack_dir

        _dry_matched = dry_run_compile_db_matched(
            compile_db_path, None, headers, compile_db_filter,
        )
        _dry_result = render_dump_dry_run(
            _resolved,
            output=output,
            build_config=build_config,
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
        )
        # CLI cleanup phase two, PR 3C prerequisite 3: show whether/why
        # build.query would execute, without ever running it.
        add_build_query_dry_run_section(
            _dry_result, so_path=so_path,
            dump_manifest_given=parsed_dump_manifest is not None,
            sources=sources, headers=headers,
            collect_mode=collect_mode, build_info=build_info,
            build_config=build_config,
        )
        emit_dry_run(_dry_result)

    # Source-only dump (no binary) for the parallel-baseline flow.
    if so_path is None:
        # dump_source_only() below embeds only L3/L4/L5 build/source facts
        # into an otherwise-empty snapshot -- it has no L2 header-AST pass
        # and never receives `headers` at all, so -H/--header has no effect
        # on the WRITTEN snapshot here: `dump --sources tree -H api.h`
        # (no --depth, or --depth binary) exits 0 with an empty (0
        # functions/enums) snapshot and no visible sign -H was ignored.
        #
        # A hard usage error was tried first and reverted (Codex review,
        # fresh evidence): -H is NOT dead code for this invocation shape in
        # general -- the --dry-run branch above resolves a real
        # `DumpRequest` carrying the given headers (see
        # `test_dump_request_from_cli.py`'s own
        # `request.input.headers == (header,)` assertion) and
        # `add_build_query_dry_run_section` genuinely consults `headers`
        # when reporting whether `build.query` would run, both already
        # exercised by a wide, pre-existing test suite
        # (`test_dry_run_build_query_contract.py`) that legitimately passes
        # `-H` alongside `--sources` with no SO_PATH. A blanket rejection
        # here broke 20 of those tests. Only the WRITTEN snapshot -- this
        # code path specifically -- ignores headers; warn rather than
        # reject, and only once execution has actually committed to this
        # path (after the --dry-run branch, which already reports the
        # headers' real effect on the resolved request/build-query
        # instead).
        if headers:
            click.echo(
                "Warning: -H/--header has no effect on this source-only "
                "dump (--sources/--build-info with no binary/SO_PATH): "
                "this path embeds only L3/L4/L5 build/source facts, never "
                "a header-AST (L2) pass, so the header(s) given are not "
                "reflected in the written snapshot. Supply a binary "
                "(SO_PATH) for the headers to actually be parsed.",
                err=True,
            )
        from ....cli_buildsource import dump_source_only
        dump_source_only(sources, build_info, version, output, build_config, allow_build_query, git_tag, build_id, no_git, collect_mode, build_targets=build_targets, extractor=header_backend, depth=depth, include_dependencies=include_dependencies, gcc_path=gcc_path, gcc_prefix=gcc_prefix, snapshot_compression=snapshot_compression)
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

    # Auto-detect binary format — PE/Mach-O skip the ELF/castxml path. The
    # conventional ``libfoo.so`` dev symlink is often a GNU ld linker script;
    # follow it to the real shared library before dispatching.
    so_path, binary_fmt = _normalize_binary_input(so_path)
    if effective_debug_format is not None and binary_fmt in ("pe", "macho"):
        raise click.BadParameter(
            f"--{effective_debug_format} is only supported for ELF binaries, not {binary_fmt.upper()}."
        )

    # ADR-063 Phase 1: both binary formats now execute through the identical
    # `execute_dump_request` pipeline (`perform_elf_dump`/`handle_non_elf_dump`
    # are both retired for this call site, kept defined for their own direct
    # unit tests) via the shared tail `dump_execute.
    # execute_and_write_dump_cli_run` -- see that function's own docstring,
    # and `dump_execute.execute_dump_cli_run`'s docstring for the rest of
    # this call's design notes (pdb/depth/embed/legacy-token handling is
    # identical and format-generic; `execute_dump_request`/
    # `_resolve_side_snapshot_impl` took no format-specific branch to
    # support PE/Mach-O here). Split into a sibling module purely to keep
    # this file under the architecture gate's file-size cap, and it
    # deliberately does not import back into the CLI-registration family
    # this module itself sits in, which is why the re-resolution below
    # (re-pointing at the *normalized* `so_path`, nulling `requested_depth`
    # -- see that docstring for why) happens here rather than there.
    if binary_fmt in ("pe", "macho"):
        if parsed_dump_manifest is not None:
            raise click.UsageError(
                f"--dump-manifest is not yet supported for {binary_fmt.upper()} "
                "binaries (ADR-050 D3); use a single-header dump for this format."
            )
        if follow_deps:
            click.echo(
                "Warning: --follow-deps is only supported for ELF binaries.",
                err=True,
            )
    else:
        # Debug artifact resolution (ADR-021a): resolved here for the "Debug
        # info: ..." UX echo only. CLI cleanup phase two, PR C: the real run
        # below now reaches `execute_dump_request` -> `service.resolve_input`,
        # which resolves the identical artifact itself from the same
        # `debug_roots`/`debuginfod`/`debuginfod_url` already carried on
        # `_dump_request.input` -- so, unlike before this migration, the
        # resolved path is not threaded any further from here; it would only
        # ever reach the same result a second time. ELF only: PE/Mach-O never
        # resolved a detached debug artifact from here either, before this
        # migration.
        if debug_roots or debuginfod:
            artifact = _resolve_debug_artifact(
                so_path, debug_roots, debuginfod, debuginfod_url,
            )
            if artifact:
                click.echo(f"Debug info: {artifact.source}", err=True)

    _exec_request = dataclasses.replace(
        _dump_request,
        input=dataclasses.replace(_dump_request.input, path=so_path),
    )
    _exec_resolved = dataclasses.replace(
        resolve_dump_request_for_cli(_exec_request), requested_depth=None,
    )
    from ....workflows.extraction import (
        dump_manifest_header_roots,
        resolve_source_frontend_clang_bin,
    )
    from ..dump_execute import execute_and_write_dump_cli_run

    execute_and_write_dump_cli_run(
        _exec_resolved,
        notify=_click_notify,
        build_config=build_config,
        legacy_compile_db_tokens=tuple(build_context_flags),
        legacy_compile_db_matched=compile_db_matched,
        # Codex review, two real regressions on the original ELF migration:
        # `perform_elf_dump` always forwarded its own resolved collect mode
        # to the L2 seed (running a zero-config inferred build query for a
        # `--sources` tree with no compile database) and always replayed L4
        # source through the L3 fold's own compiler once it applied -- both
        # must be preserved here, exactly as `scan`'s own candidate
        # resolution already does. Applies identically to PE/Mach-O, which
        # shares this same tail.
        seed_collect_mode=_resolved.collect_mode,
        stamp_provenance=_stamp_provenance,
        write_snapshot_output=_write_snapshot_output_fn,
        git_tag=git_tag, build_id=build_id, no_git=no_git,
        output=output, build_info=build_info, sources=sources,
        allow_build_query=allow_build_query,
        collect_mode=_resolved.collect_mode,
        build_targets=build_targets,
        header_backend=_resolved.header_backend,
        requested_depth=_resolved.requested_depth,
        include_dependencies=include_dependencies,
        # `--dump-manifest` is ELF-only (rejected above for PE/Mach-O), so
        # `dump_manifest_header_roots(parsed_dump_manifest)` is always `()`
        # there -- included unconditionally rather than branched, since it's
        # a no-op for the format that doesn't support it.
        header_roots=tuple(headers)
        + dump_manifest_header_roots(parsed_dump_manifest)
        + tuple(_resolved.public_headers)
        + tuple(_resolved.public_header_dirs),
        clang_bin=resolve_source_frontend_clang_bin(
            gcc_path, gcc_prefix, exclude_cl_style=False,
        ),
        snapshot_compression=snapshot_compression,
        # L4 replay classifies declarations against these roots; with none it
        # classifies everything private and links nothing (measurement in
        # `_write_snapshot_output`'s own docstring).
        public_headers=tuple(_resolved.public_headers),
        public_header_dirs=tuple(_resolved.public_header_dirs),
    )

