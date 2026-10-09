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

"""``abicheck compare``'s operand routing, shared by the command and its engine.

The directory/package release fan-out dispatch and the inline build-source
embedding a live-binary operand needs before the pair can be resolved. Kept
apart from :mod:`.compare` (which registers the command on the root group) so
the scalar engine in ``cli_compare_helpers`` can call these without importing
the command module back -- that import closed the ``cli`` ->
``commands.compare`` -> ``cli_compare_helpers`` -> ``commands.compare`` cycle.
The nested ``dump`` is looked up on the running command tree for the same
reason, rather than imported.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import click

from ....cli_dump_helpers import (
    _dump_will_attempt_hybrid_l4_extraction,
)
from ....cli_resolve import (
    _normalize_binary_input,
)
from ....report.report_modes import normalize_report_mode
from ..dump_debug_config import DumpDebugConfig
from ..inline_evidence_routing import inline_dump_evidence_routing

_RELEASE_FORMATS = frozenset({"json", "markdown", "junit", "oneline", "html"})


def reject_release_incompatible_view_mode(report_mode: str) -> None:
    """Reject a ``--view`` mode/token a directory/package release fan-out
    can't honor: ``leaf``/``root-cause`` restructure a single comparison's
    own root-cause graph, and the release summary is an aggregate report
    across every library with no single such graph to restructure. ``full``
    and ``impact`` (each library already has its own ``DiffResult`` to
    compute an impact table from) are the only accepted report modes here.

    Plan slice 7o removed this predicate's second half. ``--view filtered``
    used to be rejected here too, because the release engine never threaded
    ``show_filtered`` into its own renderer and so would have produced
    output identical to an invocation without the token. The token is gone
    -- the scope/reconciliation ledger is unconditional now -- so there is
    no request left to refuse; what remains is the same missing feature it
    always was (the release renderer still shows no per-library ledger),
    recorded in the plan's 7o section rather than expressed as a usage
    error for a spelling that no longer exists.

    Shared by ``_dispatch_release_compare``'s own check below and
    ``cli_compare_helpers.py``'s pre-``--dry-run`` rejection point (Codex
    review, fresh evidence: without the latter, ``compare --dry-run --view
    leaf`` on a directory/package operand exited 0 while the identical
    non-dry-run invocation exited 64 -- the one thing ``--dry-run`` promises
    not to do, per the sibling checks it already sits next to) -- one
    predicate, so the two call sites cannot silently drift apart.
    """
    if report_mode not in ("full", "impact"):
        raise click.UsageError(
            f"--view {report_mode} is not available when comparing directories "
            "or packages: 'leaf'/'root-cause' restructure a single comparison's "
            "own root-cause graph, and the release summary is an aggregate "
            "report across every library with no single such graph to "
            "restructure. Compare one library at a time (a single old/new "
            f".so pair) to use --view {report_mode}."
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
    # --view's derived values: show_only is threaded through the release
    # engine below (cli_compare_release.py). report_mode's "leaf"/
    # "root-cause" restructure a single DiffResult's own root-cause graph --
    # the release report has no such graph to restructure (the same mismatch
    # -o sarif=.../html/review hits below), so those two are rejected.
    # "impact" is threaded through as show_impact, since (unlike leaf/
    # root-cause) an impact summary is naturally per-library: each library
    # already has its own DiffResult, so
    # `_strip_diff_results_and_adjust_verdict` computes one impact table per
    # library from it. Demangling is no longer a value to thread at all
    # (plan slice 7o): it is resolved per format, from the format alone.
    report_mode = kwargs.pop("report_mode", "full")
    kwargs["show_only"] = kwargs.pop("show_only", None)
    # Already validated ahead of the --dry-run emit (cli_compare_helpers.py's
    # pre-dry-run block) -- re-checked here too since _dispatch_release_
    # compare has its own direct callers/tests and must reject on its own,
    # not merely rely on an upstream caller having done so.
    reject_release_incompatible_view_mode(report_mode)
    # The `impact` -> (`full`, show_impact) fold is shared with every public
    # Python rendering path rather than re-spelled here (Codex review,
    # PR #1284): two private copies of it were why `report_mode="impact"`
    # worked through the CLI and silently did nothing through the typed API.
    report_mode, kwargs["show_impact"] = normalize_report_mode(report_mode)
    if fmt not in _RELEASE_FORMATS:
        raise click.UsageError(
            f"-o {fmt}=... is not available when comparing directories or "
            "packages: sarif/review require a single-pair (non-directory, "
            "non-package) comparison. Choose one of: "
            f"{', '.join(sorted(_RELEASE_FORMATS))}, or compare one library at "
            f"a time (a single old/new .so pair) to export {fmt}."
        )
    # `compare`'s own `-o` accepts sarif/html/review (a single-pair
    # comparison renders all three), and the export set is parsed before
    # this dispatch knows the operands are directories -- so a format this
    # fan-out cannot produce must be rejected here explicitly. Without it,
    # compare_release_cmd's own callback is reached directly (not through
    # Click's arg parsing, so its own decorator-level validation never runs)
    # and _format_release_summary's fallback branch would silently render
    # markdown to the requested sarif/html/review destination instead of
    # erroring.
    #
    # Every export is checked, not just the first: the release engine renders
    # each one from the same already-computed result (one analysis, several
    # artifacts), so each must name a format it can actually produce.
    # Destination collisions and stdout exclusivity were already settled
    # while the operand was parsed (`frontends.cli.options.export`); only the
    # *format* restriction below is release-specific.
    secondary_writes = kwargs.get("secondary_writes", ())
    for secondary_fmt, _ in secondary_writes:
        if secondary_fmt not in _RELEASE_FORMATS:
            raise click.UsageError(
                f"-o {secondary_fmt}=... is not available when comparing "
                "directories or packages: sarif/review require a "
                "single-pair (non-directory, non-package) comparison. Choose "
                f"one of: {', '.join(sorted(_RELEASE_FORMATS))}, or compare one "
                "library at a time (a single old/new .so pair) to export "
                f"{secondary_fmt}."
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
    # Single source of truth: `buildsource.raw_evidence` owns this rule for
    # every caller now (the one-sided audit's own liveness check reads it too),
    # so routing here and the depth floor there cannot answer it differently.
    from ....workflows.extraction import is_raw_evidence_input

    return not is_raw_evidence_input(path)


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
    build_config: Path | None = None,
    changed_paths: tuple[str, ...] = (),
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
    :class:`~abicheck.dry_run_estimate.CompileContext` (the merged per-side context).
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

    ``build_config`` (CLI cleanup phase two, Block 7 -- PR C's tail):
    ``compare``'s own raw ``--config`` value -- ``None`` unless the operator
    explicitly passed it -- forwarded to the nested ``ctx.invoke(dump_cmd,
    ...)`` below's own ``build_config`` parameter, exactly the same explicit-
    only value a direct ``dump --config`` invocation would carry. Without
    this, the nested invocation always resolved it as ``None``, so its own
    pre-flight bazel-target-scoping check (``workflows.plan.AnalysisPlanner.
    resolve``) could only ever see whatever ``.abicheck.yml`` auto-discovery
    found at this side's ``--sources`` tree, never the config an explicit
    ``compare --config`` actually names. **Must stay the raw, explicit-only
    value, never `compare`'s own auto-discovery fallback**
    (`_resolve_compare_config`'s resolved ``cfg_path``) -- `embed_build_source`
    treats a non-``None`` ``build_config`` as operator-authorized to execute
    `build.query` (ADR-032 D5); forwarding an auto-discovered path here would
    let an untrusted, PR-controlled ``.abicheck.yml`` in the sources tree
    authorize its own subprocess execution.

    ``changed_paths`` (ADR-068 Phase 2c) is this run's resolved
    ``--since``/``--changed-path`` seed, forwarded to the nested dump through
    its private ``_resolved_changed_paths`` hook (the same shape
    ``_resolved_compile_context``/``_resolved_collect_mode`` use). Without it
    a localized ``compare`` would narrow its *collect mode* to
    ``source-changed`` while the dump that actually collects had no seed to
    narrow *by*, which ``collect_inline_pack`` correctly treats as "no seed"
    and widens back to headers-only.

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
    dump_sources, dump_build_info, kept_sources, kept_build_info = (
        inline_dump_evidence_routing(sources, build_info, sources_raw, build_info_raw)
    )
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
            f"--depth source is incompatible with compile.frontend: hybrid "
            f"for the --sources {label}= tree: L4 source-ABI replay has no "
            "dual-backend hybrid extractor (unlike the L2 header-AST "
            "snapshot). Set compile.frontend: castxml or compile.frontend: "
            "clang in .abicheck.yml for a --depth source compare."
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
        _registered_dump_command(ctx),
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
        # Phase 7c: forwards compare's already-resolved debug config, mirroring
        # `_resolved_compile_context` above (dump_cmd's own flags are gone).
        _resolved_debug=DumpDebugConfig(
            format=debug_format,
            dwarf_only=dwarf_only,
            debuginfod=debuginfod,
            debuginfod_url=debuginfod_url,
            pdb_path=pdb_path,
        ),
        sources=dump_sources,
        build_info=dump_build_info,
        build_config=build_config,
        _resolved_collect_mode=collect_mode,
        output=out,
        debug_roots=debug_roots,
        _resolved_include_labels=include_labels,
        # Thread compare's own --include-system-declarations flag through, rather
        # than hardcoding it, so this inline `--old/new-sources` embed path
        # scopes the same way the sibling path (a side reaching
        # service.run_dump directly, with no raw source tree) now does --
        # merely adding deeper L3-L5 evidence to an otherwise-identical
        # compare must not silently change this side's dependency scope
        # depending only on which evidence flags happened to be passed.
        include_dependencies=include_dependencies,
        # ADR-068 Phase 2c: this run's changed-path seed, so the nested dump's
        # own L4 replay / L5 call-graph pass narrows to the changed TUs the
        # same way a `--depth source` scan's does (ADR-043 D7). Empty for
        # every run without --since/--changed-path, i.e. bit-for-bit as before.
        _resolved_changed_paths=changed_paths,
    )
    # The raw sources/build-info are now embedded in the snapshot; pack-shaped
    # inputs (kept_*) ride through to the later prepare_embedded_build_source so
    # it does not re-process the consumed raws as bogus packs — Codex review.
    return out, kept_sources, kept_build_info


def _registered_dump_command(ctx: click.Context) -> click.Command:
    """The ``dump`` command registered on this run's root group."""
    root = ctx.find_root().command
    assert isinstance(root, click.Group), "`compare` runs under the root group"
    dump = root.get_command(ctx, "dump")
    assert dump is not None, "`dump` is registered on the root group"
    return dump
