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

"""``abicheck compare`` -- command input translation (ADR-061 Phase 4 item 1).

Covers the single-pair comparison, the directory/package release fan-out it
dispatches to, and the inline build-source embedding a live-binary operand
needs before the pair can be resolved.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ....cli_dump_helpers import (
    _dump_will_attempt_hybrid_l4_extraction,
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
    adr027_compare_options,
    app_usage_scope_options,
    apply_compare_profile,
    bundle_facts_manifest_options,
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
    reject_bundle_facts_manifest_without_old_bundle_facts,
    release_options,
    scope_options,
    secondary_output_options,
    set_input_options,
    severity_options,
    two_sided_input_options,
    verbose_option,
)
from ....cli_resolve import (
    _normalize_binary_input,
)
from ....frontends.cli import help as cli_help
from ..options.params import (
    SIDED_EXISTING_PATH_PARAM,
    SIDED_PATH_PARAM,
    _load_suppression_and_policy as _load_suppression_and_policy,  # noqa: F401  — re-exported to keep cli import sites (test suite) stable
)

if TYPE_CHECKING:
    pass


from ....cli import main
from ..runtime import (
    _validate_show_only,
)
from .dump import dump_cmd

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
    from ....cli_compare_release import compare_release_cmd

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
    from ....cli_buildsource_helpers import _is_inputs_pack_dir
    from ....workflows.extraction import is_pack_dir

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
    from ....cli_options import merge_compile_config

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
@cli_help.compare_help_options  # curated --help + full --help-all (G21.8 collapse M2)
@click.argument("old_input", type=click.Path(exists=True, path_type=Path))
@click.argument("new_input", type=click.Path(exists=True, path_type=Path))
# Set-input fan-out (ADR-037 D7): -j/--jobs, --dso-only, --output-dir only bite
# when the operands are directories/packages; a no-op-with-warning otherwise.
@set_input_options
# ── Release (directory/package) comparison knobs (ADR-037 D7) ────────────────
@release_options
# ── Stored bundle-facts OLD side (G38 Phase 13 follow-up) ────────────────────
# OLD_INPUT is automatically classified as a persisted BundleFacts document
# (produced by a prior `compare --bundle-facts-out`) rather than a live
# directory/package -- CLI cleanup phase two, PR I: the former
# `--old-bundle-facts` flag is gone; see
# workflows/bundle_compare_operand.py for the classifier and
# compare_bundle_facts.py for the dispatch it still routes to.
@click.option(
    "--max-json-object-nodes",
    "max_json_object_nodes",
    type=int,
    default=None,
    help="Override the JSON container-node budget "
    "(bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES, 1,000,000) "
    "when decoding OLD_INPUT, if it is a stored BundleFacts "
    "document (see workflows/bundle_compare_operand.py). A real "
    "per-library facts blob for a large, template-heavy "
    "library (e.g. SYCL/DPC++) can legitimately need well "
    "over the default to decode; this is the supported way "
    "to raise it, instead of patching the budget in code. "
    "Meaningless (and a no-op) otherwise.",
)
@bundle_facts_manifest_options  # G38 Phase 17
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
              help="Demangle C++ symbol names in markdown/review/html output "
                   "(default ON; use --no-demangle to turn off). json/sarif/junit "
                   "always keep raw mangled names for downstream tooling to match on.")
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
    from ....cli_compare_helpers import run_compare
    from ....cli_options import warn_deprecated_header_graph_flags

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

    # CLI cleanup phase two, PR I: OLD_INPUT/NEW_INPUT are classified
    # automatically for bundle-facts routing, replacing the removed
    # --old-bundle-facts flag -- see compare_bundle_operand_dispatch.py's
    # own docstring (stored NEW_INPUT + live OLD_INPUT is rejected there;
    # OLD_INPUT classified as stored -- alone, or with NEW_INPUT stored
    # too -- short-circuits the ordinary live-binary/directory dispatch
    # entirely, never reaching run_compare/_dispatch_release_compare -- see
    # compare_bundle_facts.py's own module docstring for why that lives
    # here rather than as a branch inside cli_compare_helpers.run_compare).
    from .compare_bundle_operand_dispatch import resolve_bundle_compare_dispatch

    _bundle_operands = resolve_bundle_compare_dispatch(kwargs["old_input"], kwargs["new_input"])
    if _bundle_operands.old_is_stored:
        from .compare_bundle_facts import (
            dispatch as dispatch_bundle_facts,
            resolve_dispatch_compile_context,
        )

        # Codex review: mirrors run_compare's own explicit-vs-default --lang
        # detection -- otherwise indistinguishable from Click's own default.
        _lang_src = ctx.get_parameter_source("lang")
        kwargs["lang_explicit"] = _lang_src == click.core.ParameterSource.COMMANDLINE
        _compile_context = resolve_dispatch_compile_context(
            ctx, kwargs, new_is_stored=_bundle_operands.new_is_stored
        )
        dispatch_bundle_facts(compile_context=_compile_context, new_is_stored=_bundle_operands.new_is_stored, **kwargs)
        return
    kwargs.pop("max_json_object_nodes", None)
    reject_bundle_facts_manifest_without_old_bundle_facts(kwargs)
    run_compare(ctx, **kwargs)
