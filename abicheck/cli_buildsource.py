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

"""Build-source pack attach/embed integration (ADR-028 D6, ADR-029).

The standalone `collect`/`merge` commands were removed in the ADR-043 CLI
reset; this module now only holds `embed_build_source()`/`dump_source_only()`
(the inline collection `dump --sources`/`--build-info` drives) plus the
back-compat re-exports for the library functions that survived the command
deletion. Per ADR-028 D6 nothing here runs arbitrary build commands: it only
reads existing build outputs and build-system query interfaces.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .buildsource.merge_support import (
    _combine_packs,
    _filter_pack_layers,
    _layer_value,
    route_inline_source_supplier,
)
from .buildsource.model import DataLayer
from .buildsource.pack import BuildSourcePack
from .cli_buildsource_helpers import (  # noqa: F401  (re-exported for API stability / tests)
    _build_coverage as _build_coverage,
    _collect_source_graph as _collect_source_graph,
    _detect_coverage_asymmetry as _detect_coverage_asymmetry,
    _echo_capabilities as _echo_capabilities,
    _echo_collection_summary as _echo_collection_summary,
    _echo_compare_side_coverage as _echo_compare_side_coverage,
    _echo_coverage as _echo_coverage,
    _enforce_strict_mode as _enforce_strict_mode,
    _exported_symbols_from_binary as _exported_symbols_from_binary,
    _exported_symbols_from_snapshot as _exported_symbols_from_snapshot,
    _ingest_graph_backends as _ingest_graph_backends,
    _intrinsic_coverage as _intrinsic_coverage,
    _is_inputs_pack_dir as _is_inputs_pack_dir,
    _layer_presence as _layer_presence,
    _load_inputs_pack_or_raise as _load_inputs_pack_or_raise,
    _load_pack_or_raise as _load_pack_or_raise,
    _merge_attach_combined as _merge_attach_combined,
    _merge_fold_packs as _merge_fold_packs,
    _merge_handle_conflicts as _merge_handle_conflicts,
    _merge_load_snapshots as _merge_load_snapshots,
    _merge_pick_base as _merge_pick_base,
    _merge_print_summary as _merge_print_summary,
    _optional_coverage as _optional_coverage,
    _purge_external_outputs as _purge_external_outputs,
    _resolve_side_pack as _resolve_side_pack,
    _run_adapters as _run_adapters,
    _run_external_extractors as _run_external_extractors,
    attach_evidence_metrics as attach_evidence_metrics,
    diff_embedded_build_source as diff_embedded_build_source,
    parse_from_specs as parse_from_specs,
    prepare_embedded_build_source as prepare_embedded_build_source,
)

if TYPE_CHECKING:
    from .model import AbiSnapshot


# ── Attach / compare integration (ADR-028 D6, D7; ADR-029 D9) ─────────────────


def embed_build_source(
    snap: AbiSnapshot,
    build_info: Path | None,
    sources: Path | None,
    *,
    build_config: Path | None = None,
    allow_build_query: bool = False,
    clang_bin: str = "clang",
    collect_mode: str = "source-target",
    build_query: str | None = None,
    build_compile_db: str | None = None,
    build_targets: tuple[str, ...] = (),
    changed_paths: tuple[str, ...] = (),
    extractor: str = "auto",
    public_headers: tuple[str, ...] = (),
    public_header_dirs: tuple[str, ...] = (),
    defer_cleanup: list[Callable[[], None]] | None = None,
    quiet: bool = False,
) -> None:
    """Embed build-info / source facts inline in *snap* (single-artifact UX).

    *collect_mode* is the ADR-033 D2 CI evidence mode selecting which layers and
    replay scope to collect: ``build`` captures L3 build context only, ``off``
    embeds nothing, the source/graph modes collect L3+L4+L5 at the matching scope.

    Source-tree-centric inputs (ADR-028..033 amendment): ``sources`` is a source
    checkout — L4 source ABI replay and the L5 graph are run *inline* and
    embedded; ``build_info`` is an optional build dir / ``compile_commands.json``
    / pre-captured pack supplying L3. A ``compile_commands.json`` inside the
    source tree is auto-discovered when ``build_info`` is omitted.

    For back-compatibility a path that is itself a pack directory (it has a
    ``manifest.json`` — e.g. from the ``abicheck-cc`` wrapper, the Clang
    plugin, or a build-emitted ``abicheck_inputs/`` pack) is loaded as that
    pack instead of being collected inline.

    The combined facts ride inside the ``.abi.json`` so a later
    ``compare old.json new.json`` works with no out-of-band directories. Also
    records the matching content-addressed ``build_source_pack`` reference.
    ``quiet=True`` (Codex review) suppresses this function's own
    ``click.echo`` (e.g. the "no compile_commands.json found" warning) for a
    non-CLI caller (``service.run_compare_request``) with no stream to write
    to and no way to suppress it otherwise.
    """
    from .buildsource.inline import (
        collect_inline_pack,
        discover_build_config,
        is_pack_dir,
        load_build_config,
    )
    from .buildsource.source_replay import collection_for_ci_mode

    scope, layers = collection_for_ci_mode(collect_mode)
    if not layers:  # 'off' (or an unknown mode) embeds nothing
        return

    # The analyzed binary's L0 exports — used both to seed a Flow-2
    # abicheck_inputs/ pack's decl→symbol linking at ingest (so a
    # `dump --build-info <inputs pack>`'s source surface maps onto the DSO's
    # exports instead of reporting matched_symbols=0, AC-003) and, below, to
    # seed the inline replay's A1 linking. Empty in the source-only
    # `dump --sources` flow (no binary), where it stays inert.
    exported = _exported_symbols_from_snapshot(snap)

    bi_is_pack = is_pack_dir(build_info)
    src_is_pack = is_pack_dir(sources)
    # A build-emitted abicheck_inputs/ pack (ADR-035 D5) is auto-detected and
    # validated the same way here as a collect-produced BuildSourcePack --
    # `--build-info`/`--sources` is the one public entry point for build-produced
    # information; there is no separate `inputs validate` command to run first.
    bi_is_inputs = (not bi_is_pack) and _is_inputs_pack_dir(build_info)
    src_is_inputs = (not src_is_pack) and _is_inputs_pack_dir(sources)
    bi_pack = (
        _load_inputs_pack_or_raise(build_info, exported_symbols=exported)
        if (bi_is_inputs and build_info is not None)
        else _load_pack_or_raise(build_info)
        if (bi_is_pack and build_info is not None)
        else None
    )
    src_pack = (
        _load_inputs_pack_or_raise(sources, exported_symbols=exported)
        if (src_is_inputs and sources is not None)
        else _load_pack_or_raise(sources)
        if (src_is_pack and sources is not None)
        else None
    )

    raw_build_info = (
        None if (build_info is None or bi_is_pack or bi_is_inputs) else build_info
    )
    raw_sources = None if (sources is None or src_is_pack or src_is_inputs) else sources

    inline_pack: BuildSourcePack | None = None
    if raw_build_info is not None or raw_sources is not None:
        cfg_path = build_config or discover_build_config(raw_sources)
        # Only operator-supplied input is trusted for subprocess execution: an
        # explicit --config file or an explicit --build-query command on the CLI.
        # Auto-discovered source-tree configs may be attacker-controlled; their
        # non-executable settings are still honored, but their query never runs.
        # (Inferred build queries — cmake/make/bazel that abicheck constructs
        # itself — always run regardless; see buildsource.build_query.)
        cfg_trusted_for_query = build_config is not None or build_query is not None
        try:
            cfg = load_build_config(cfg_path) if cfg_path is not None else None
        except ValueError as exc:
            # A bad .abicheck.yml is a usage error (exit 64), not an operational
            # failure of this run (ADR-043 CLI reset: config errors use exit 64).
            raise click.UsageError(str(exc)) from exc
        # CLI overrides (no config file needed): --build-query / --build-compile-db /
        # --build-target win over the .abicheck.yml values when supplied.
        if (
            build_query is not None
            or build_compile_db is not None
            or build_targets
        ):
            import dataclasses

            from .buildsource.inline import BuildConfig

            cfg = cfg or BuildConfig()
            cfg = dataclasses.replace(
                cfg,
                query=build_query if build_query is not None else cfg.query,
                compile_db=build_compile_db
                if build_compile_db is not None
                else cfg.compile_db,
                targets=list(build_targets) if build_targets else cfg.targets,
            )
        # A1: plumb the binary's L0 exports (already computed above) into the
        # inline replay, so the linked source surface knows which decls map to
        # exports and the provenance/mapping checks have a signal.
        inline_pack = collect_inline_pack(
            sources=raw_sources,
            build_info=raw_build_info,
            build_config=cfg,
            allow_build_query=allow_build_query,
            build_config_trusted_for_query=cfg_trusted_for_query,
            # A build.compile_db is an *explicit* L3 input (its miss must surface,
            # not fall through to inference) when it came from the CLI
            # --build-compile-db or an operator --config — never from an
            # auto-discovered .abicheck.yml (review).
            compile_db_explicit=build_compile_db is not None
            or build_config is not None,
            base_build=bi_pack.build_evidence if bi_pack else None,
            clang_bin=clang_bin,
            extractor=extractor,
            scope=scope,
            layers=layers,
            exported_symbols=exported,
            changed_paths=changed_paths,
            public_header_roots=tuple(
                dict.fromkeys((*public_headers, *public_header_dirs))
            ),
            defer_cleanup=defer_cleanup,
        )
        # P09: don't fail *silently* when a source/build tree yields no compile DB.
        # Autotools `configure` (and a bare checkout) emit no compile_commands.json,
        # so L3/L4/L5 collect nothing — previously with no explanation. Warn with an
        # actionable hint (unless a build.query diagnostic already explains it).
        _ev = inline_pack.build_evidence if inline_pack is not None else None
        _has_l3 = _ev is not None and bool(_ev.compile_units)
        _has_query_note = inline_pack is not None and any(
            # Both the trusted `build.query` and the zero-config inferred query
            # ("build_query_auto") record a diagnostic that already explains the
            # missing L3 — don't also emit the generic "run cmake …" hint, which
            # would contradict an inferred query abicheck just attempted.
            e.name in ("build_query", "build_query_auto")
            for e in inline_pack.manifest.extractors
        )
        if not _has_l3 and bi_pack is None and not _has_query_note and not quiet:
            _tree = raw_sources if raw_sources is not None else raw_build_info
            _deeper = "/L4/L5" if ("L4" in layers or "L5" in layers) else ""
            click.echo(
                f"warning: no compile_commands.json found under {_tree} "
                "(looked in: ., build, builddir, out, _build, cmake-build-debug, "
                "and any immediate subdirectory); "
                f"L3{_deeper} not collected. Generate one — CMake: configure with "
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON; Meson: emitted by `meson setup`; "
                "Autotools/Make: run `bear -- make` — or pass "
                "--build-info <dir|compile_commands.json>.",
                err=True,
            )

    # Pre-captured packs must also honour the collect-mode layer set (Codex).
    bi_pack = _filter_pack_layers(bi_pack, layers)
    src_pack = _filter_pack_layers(src_pack, layers)

    # --build-info (pack) wins L3; --sources wins L4/L5; the inline collection of
    # a raw --sources/--build-info tree backfills. AC-001: a raw `--sources` cold
    # scan is the *sources* contributor, so route it into the src_pack slot (which
    # outranks --build-info for L4/L5); a real --sources pack keeps that slot and
    # the inline pack backfills. Coverage is rebuilt per layer from the supplying
    # pack.
    sources_supplier, inline_backfill = route_inline_source_supplier(
        src_pack, inline_pack
    )
    merged = _combine_packs(bi_pack, sources_supplier, inline_backfill)
    if merged is None:
        return
    # ADR-041 addendum / G29 Phase A: the always-on header-only-graph attach
    # already ran and attached a header-only L5 pack to `snap.build_source`
    # before this function ran (see service._attach_header_graph, called from
    # cli_dump_helpers before write_snapshot_output). `_combine_packs` above
    # only sees bi_pack/src_pack/inline_pack, so a plain
    # `snap.build_source = merged` would silently drop that graph whenever
    # this embed step supplies any L3/L4/L5 facts of its own (even
    # build-only facts with no graph) — a `dump --build-info ...` snapshot
    # would then serialize without the graph that is now always built
    # (Codex review). Backfill only: a
    # genuine --sources L5 collection in `merged` always wins; the header-only
    # graph fills the gap only when `merged` carries none. Patched in field-by-
    # field (not via a chained _combine_packs(merged, None, existing) call)
    # because the coverage-row lookup there keys off *pack identity*, first
    # non-None pack in supplier order wins regardless of whether that pack
    # actually supplied the fact — `merged` always carries its own (stale,
    # not_collected) L5 row even when its source_graph is None, so a chained
    # combine would silently keep reporting L5 as not collected despite the
    # backfilled facts now being present.
    existing = snap.build_source
    if (
        merged.source_graph is None
        and existing is not None
        and existing.source_graph is not None
    ):
        import dataclasses

        graph_layer = DataLayer.L5_SOURCE_GRAPH.value
        graph_row = next(
            (
                c
                for c in existing.manifest.coverage
                if _layer_value(c.layer) == graph_layer
            ),
            None,
        )
        coverage = [
            c for c in merged.manifest.coverage if _layer_value(c.layer) != graph_layer
        ]
        if graph_row is not None:
            coverage.append(graph_row)
        # merged.manifest.artifacts (if any) was precomputed from the
        # pre-backfill payloads and does not include a digest for the
        # newly-adopted source_graph. BuildSourcePack.content_hash() prefers
        # a non-empty manifest.artifacts over recomputing it, so a stale list
        # here would let two packs with genuinely different header-only
        # graphs (but identical L3 facts) hash identically. Clear it so
        # content_hash() falls back to _artifact_digests(), which hashes the
        # current in-memory payloads including the backfilled graph — the
        # same "mutating payloads invalidates precomputed digests" rule
        # cli_buildsource_merge.py's own merge step already follows (Codex
        # review).
        merged = dataclasses.replace(
            merged,
            source_graph=existing.source_graph,
            manifest=dataclasses.replace(
                merged.manifest, coverage=coverage, artifacts=[]
            ),
        )
    snap.build_source = merged
    # Provenance hint: prefer the source input, else build-info.
    hint = str(sources) if sources is not None else str(build_info)
    snap.build_source_pack = merged.to_ref(path_hint=hint)


def dump_source_only(
    sources: Path | None,
    build_info: Path | None,
    version: str,
    output: Path | None,
    build_config: Path | None,
    allow_build_query: bool,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
    collect_mode: str = "source-target",
    build_query: str | None = None,
    build_compile_db: str | None = None,
    build_targets: tuple[str, ...] = (),
    extractor: str = "auto",
    depth: str | None = None,
    include_dependencies: bool = False,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    snapshot_compression: str = "auto",
) -> None:
    """Write a binary-less snapshot carrying only the embedded build/source facts.

    The parallel-baseline flow: ``dump --sources <tree>`` / ``--build-info <path>``
    with no ``SO_PATH`` collects L3/L4/L5 inline and embeds them in an otherwise
    empty snapshot, to be combined with an artifact-side dump via ``merge``. A
    bare ``dump`` (no binary and no source/build inputs) errors clearly here.

    ``include_dependencies`` is forwarded to ``_write_snapshot_output`` for
    consistency, though it never has any effect here in practice: this
    path's snapshot starts with no functions/variables at all (only L3/L4/L5
    facts get embedded), and ``scope_snapshot_excluding_dependencies`` is a
    no-op on a snapshot with no header-derived declarations
    (``from_headers`` stays ``False``) either way.

    ``gcc_path``/``gcc_prefix`` are the dump's own ``--compiler``/
    ``--compiler-prefix`` (there is no header AST here to have already resolved a
    ``CompileContext`` from — a source-only dump has no ``-H`` headers
    either), forwarded to ``_write_snapshot_output`` so L4 source-ABI replay
    honors the same compiler override a binary dump would.
    """
    from .cli import _stamp_provenance
    from .dumper_clang import resolve_source_frontend_clang_bin
    from .model import AbiSnapshot

    if sources is None and build_info is None:
        raise click.UsageError(
            "dump requires a binary (SO_PATH), or --sources/--build-info for a "
            "source-only snapshot."
        )
    # Library name from the source/build input so the snapshot is identifiable;
    # `merge` keeps the artifact side as the base regardless.
    hint = sources if sources is not None else build_info
    library = hint.name if hint is not None else "source"
    snap = AbiSnapshot(library=library, version=version)
    _stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    _write_snapshot_output(
        snap,
        output,
        build_info,
        sources,
        build_config,
        allow_build_query,
        collect_mode,
        build_query=build_query,
        build_compile_db=build_compile_db,
        build_targets=build_targets,
        extractor=extractor,
        depth=depth,
        include_dependencies=include_dependencies,
        clang_bin=resolve_source_frontend_clang_bin(
            gcc_path, gcc_prefix, exclude_cl_style=False
        ),
        snapshot_compression=snapshot_compression,
    )


# ---------------------------------------------------------------------------
# Snapshot output (dump's write path)
#
# Relocated here from ``cli.py`` (which sits within a handful of lines of the
# AI-readiness 2000-line hard cap) as one cohesive cluster: writing a snapshot
# *is* the step that folds this module's own ``embed_build_source`` /
# ``embed_inputs_pack`` payloads in and then enforces the requested evidence
# depth, and ``embed_build_source``'s caller here already imported
# ``_write_snapshot_output`` back out of ``cli``. ``cli`` re-exports all three
# names, so ``abicheck.cli._write_snapshot_output`` -- which several tests and
# ``dump_cmd`` itself use -- keeps resolving unchanged. This module, not a new
# one: a *new* module reaching ``service``/``buildsource`` would join the
# allowlisted CLI import-cycle SCC, which CLAUDE.md "M1-3" forbids extending.
# ---------------------------------------------------------------------------


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
    build_targets: tuple[str, ...] = (),
    extractor: str = "auto",
    inputs_pack: Path | None = None,
    depth: str | None = None,
    include_dependencies: bool = False,
    header_roots: tuple[Path, ...] = (),
    clang_bin: str = "clang",
    snapshot_compression: str = "auto",
) -> None:
    """Serialize snapshot and write to file or stdout.

    When *build_info* and/or *sources* are given, their normalized L3/L4/L5 facts
    are collected (inline from a source tree / build dir, or loaded from a pack
    directory) and embedded in the snapshot first (single-artifact UX) so a later
    ``compare old.json new.json`` needs no out-of-band packs. *collect_mode* (the
    ADR-033 D2 CI evidence mode) selects which layers and replay scope to collect:
    ``build`` captures L3 build context only, ``off`` collects nothing.
    *build_query* / *build_compile_db* / *build_targets* are the CLI equivalents of
    the ``.abicheck.yml`` ``build.query`` / ``build.compile_db`` / ``build.targets``
    keys — *build_targets* (P0.2) scopes Bazel evidence collection to the given
    root target(s) and their transitive deps instead of a workspace-wide query.
    *extractor* is the L4 source-ABI
    frontend — the same ``--ast-frontend`` knob that drives the L2 header AST
    (ADR-037 D8): one frontend choice across both pipeline stages. *clang_bin* is
    the caller-resolved L4 replay compiler (forwarded to ``embed_build_source``).
    *depth* is the raw ``--depth`` CLI value (``None`` when not passed); when given,
    ``check_requested_depth_satisfied`` raises if the snapshot did not actually reach
    it. Unless *include_dependencies* is set (``dump --include-system-declarations``),
    toolchain/system-header declarations are excluded from the snapshot right before
    serialization by default, once every embed step above has had its chance to fill
    in the snapshot — see ``dumper_scoping.py`` for what "dependency" means here.
    *header_roots* is the actual ``-H``/``--header`` input the dump was invoked with,
    forwarded to ``scope_snapshot_excluding_dependencies`` so a header that IS one of
    those roots (or lives under one) is never treated as a dependency just because it
    happens to sit under a system prefix (e.g. an installed library dumped via its real
    ``/usr/include`` path).
    """
    from .cli_dump_helpers import (
        check_requested_depth_satisfied,
        fold_dump_provenance_into_dict,
        write_snapshot_and_report,
    )
    from .serialization import snapshot_to_dict

    if build_info is not None or sources is not None:
        from .cli_buildsource import embed_build_source
        embed_build_source(
            snap, build_info, sources,
            build_config=build_config, allow_build_query=allow_build_query,
            collect_mode=collect_mode,
            build_query=build_query, build_compile_db=build_compile_db,
            build_targets=build_targets,
            extractor=extractor, clang_bin=clang_bin,
        )
        # G21.7: fail loud — if a requested evidence layer came back empty, say so
        # prominently instead of leaving it buried in the coverage rows. Permissive
        # by design (a warning, not an error): --collection-mode strict on
        # `collect` remains the hard-fail path (ADR-028 D3).
        # Through the ``cli`` module (which re-exports both) so a monkeypatch on
        # ``abicheck.cli._missing_requested_evidence_layers`` /
        # ``._classify_missing_layers`` is still honoured after the relocation --
        # the same resolution the neighbouring ``cli._normalize_binary_input``
        # calls use, and what several existing tests patch.
        from . import cli as _cli

        missing = _cli._missing_requested_evidence_layers(
            snap.build_source, collect_mode
        )
        if missing:
            absent, ran_empty = _cli._classify_missing_layers(
                snap.build_source, missing
            )
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
    from .dumper_scoping import resolve_dependency_scope
    snap = resolve_dependency_scope(snap, include_dependencies, header_roots)
    # ADR-059: one payload dict, one JSON encode -- previously this built a
    # full JSON *string* via snapshot_to_json(), then fold_dump_provenance_
    # into_json() re-parsed and re-serialized that entire string just to
    # attach one key. For a 100+ MB snapshot that's a second full parse and
    # a second full encode for no reason; fold_dump_provenance_into_dict()
    # does the same augmentation on the already-decoded payload dict.
    payload = snapshot_to_dict(snap)
    payload, resolved_depth_label = fold_dump_provenance_into_dict(payload, depth, snap)
    if output:
        write_snapshot_and_report(
            payload, output, snapshot_compression, resolved_depth_label
        )
    else:
        import json

        click.echo(json.dumps(payload, indent=2))


# ── Back-compat re-export shim (lazy, to avoid an import cycle) ───────────────
# `_load_source_graph` / `_resolve_symbol_from_report` historically lived here
# (re-exported from `cli_buildsource_helpers`, like the block above). They moved
# to `cli_graph` when the `graph` command group was extracted. A *static*
# `from .cli_graph import ...` would form a `cli_buildsource → cli_graph → cli →
# … → cli_buildsource` import cycle (the AI-readiness gate rejects it), so this
# module-level `__getattr__` (PEP 562) resolves them lazily via
# `importlib.import_module` — a runtime call, not a static import edge. It
# preserves the historical path `from abicheck.cli_buildsource import
# _load_source_graph` without coupling the two modules. New code should import
# from `cli_graph` directly.
_GRAPH_REEXPORTS = frozenset({"_load_source_graph", "_resolve_symbol_from_report"})


def __getattr__(name: str) -> Any:
    if name in _GRAPH_REEXPORTS:
        import importlib

        return getattr(importlib.import_module("abicheck.cli_graph"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
