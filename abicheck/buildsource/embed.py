# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Embedding L3-L5 build/source evidence into a snapshot.

ADR-061 Phase 3. This was ``cli_buildsource.embed_build_source``: a real
engine operation that happened to live in the CLI layer and raise Click
exceptions, which ``service_input_resolution`` then caught and translated.
That made the engine depend on the CLI's *error type* -- the inversion in its
purest form, and the last edge blocking two service pipelines from moving
into ``workflows/``.

The move preserves both error contracts exactly, which is the whole risk it
carries. Two error classes leave this function and they mean different things
to a CI consumer:

* :class:`~abicheck.errors.ValidationError` -- a **usage** error (a malformed
  ``.abicheck.yml``). The CLI renders it as ``click.UsageError``, which
  ``cli.main`` remaps to **exit 64**.
* :class:`~abicheck.errors.SnapshotError` -- an **operational** failure (an
  invalid pack). The CLI renders it as a plain ``click.ClickException`` --
  **exit 1**. The invocation was well-formed; the data was not.

``service_input_resolution`` flattens both onto ``SnapshotError``, because
that is what its callers have always had to catch. All of it is pinned by
``tests/test_build_source_embed_errors.py``, written before the move.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import ValidationError
from .inputs_pack import is_inputs_pack_dir
from .merge_support import (
    _combine_packs,
    _filter_pack_layers,
    _layer_value,
    route_inline_source_supplier,
)
from .model import DataLayer
from .pack import BuildSourcePack
from .pack_io import to_ref
from .pack_load import load_inputs_pack_or_raise, load_pack_or_raise
from .snapshot_exports import exported_symbols_from_snapshot

if TYPE_CHECKING:
    from ..model import AbiSnapshot


def embed_build_source(
    snap: AbiSnapshot,
    build_info: Path | None,
    sources: Path | None,
    *,
    build_config: Path | None = None,
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
    on_warning: Callable[[str], None] | None = None,
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
    *on_warning* receives this function's own advisory messages (e.g. the
    "no compile_commands.json found" warning). It is ``None`` by default --
    an engine module owns no stream; the CLI adapter passes a stderr writer,
    and a Tier-2 caller passes nothing. This replaced a ``quiet`` flag for a
    non-CLI caller (``service.run_compare_request``) with no stream to write
    to and no way to suppress it otherwise.
    """
    from .build_config import discover_build_config, load_build_config
    from .inline import collect_inline_pack, is_pack_dir
    from .source_replay import collection_for_ci_mode

    scope, layers = collection_for_ci_mode(collect_mode)
    if not layers:  # 'off' (or an unknown mode) embeds nothing
        return

    # The analyzed binary's L0 exports — used both to seed a Flow-2
    # abicheck_inputs/ pack's decl→symbol linking at ingest (so a
    # `dump --build-info <inputs pack>`'s source surface maps onto the DSO's
    # exports instead of reporting matched_symbols=0, AC-003) and, below, to
    # seed the inline replay's A1 linking. Empty in the source-only
    # `dump --sources` flow (no binary), where it stays inert.
    exported = exported_symbols_from_snapshot(snap)

    bi_is_pack = is_pack_dir(build_info)
    src_is_pack = is_pack_dir(sources)
    # A build-emitted abicheck_inputs/ pack (ADR-035 D5) is auto-detected and
    # validated the same way here as a collect-produced BuildSourcePack --
    # `--build-info`/`--sources` is the one public entry point for build-produced
    # information; there is no separate `inputs validate` command to run first.
    bi_is_inputs = (not bi_is_pack) and is_inputs_pack_dir(build_info)
    src_is_inputs = (not src_is_pack) and is_inputs_pack_dir(sources)
    bi_pack = (
        load_inputs_pack_or_raise(
                build_info, exported_symbols=exported, on_warning=on_warning
            )
        if (bi_is_inputs and build_info is not None)
        else load_pack_or_raise(build_info)
        if (bi_is_pack and build_info is not None)
        else None
    )
    src_pack = (
        load_inputs_pack_or_raise(
                sources, exported_symbols=exported, on_warning=on_warning
            )
        if (src_is_inputs and sources is not None)
        else load_pack_or_raise(sources)
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
        # explicit --config file (PR 3C removed the CLI --build-query; a
        # programmatic `build_query` argument is the other operator route).
        # Auto-discovered source-tree configs may be attacker-controlled; their
        # non-executable settings are still honored, but their query never runs.
        # (Inferred build queries — cmake/make/bazel that abicheck constructs
        # itself — always run regardless; see buildsource.build_query.)
        cfg_trusted_for_query = build_config is not None or build_query is not None
        try:
            cfg = load_build_config(cfg_path) if cfg_path is not None else None
        except ValueError as exc:
            # A bad .abicheck.yml is a *usage* error, not an operational
            # failure of this run: the CLI adapter renders ValidationError as
            # click.UsageError, which cli.main remaps to exit 64 (ADR-043 CLI
            # reset: config errors use exit 64). Keeping the two error classes
            # distinct here is what lets that split survive the move -- see
            # tests/test_build_source_embed_errors.py.
            raise ValidationError(str(exc)) from exc
        # Programmatic overrides (no config file needed): build_query / build_compile_db /
        # --build-target win over the .abicheck.yml values when supplied.
        if (
            build_query is not None
            or build_compile_db is not None
            or build_targets
        ):
            import dataclasses

            from .build_config import BuildConfig

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
            build_config_trusted_for_query=cfg_trusted_for_query,
            # A build.compile_db is an *explicit* L3 input (its miss must surface,
            # not fall through to inference) when it came from the CLI
            # build_compile_db or an operator --config — never from an
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
        if (
            not _has_l3
            and bi_pack is None
            and not _has_query_note
            and on_warning is not None
        ):
            _tree = raw_sources if raw_sources is not None else raw_build_info
            _deeper = "/L4/L5" if ("L4" in layers or "L5" in layers) else ""
            on_warning(
                f"warning: no compile_commands.json found under {_tree} "
                "(looked in: ., build, builddir, out, _build, cmake-build-debug, "
                "and any immediate subdirectory); "
                f"L3{_deeper} not collected. Generate one — CMake: configure with "
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON; Meson: emitted by `meson setup`; "
                "Autotools/Make: run `bear -- make` — or pass "
                "--build-info <dir|compile_commands.json>."
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
        # newly-adopted source_graph. pack_io.content_hash() prefers
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
    snap.build_source_pack = to_ref(merged, path_hint=hint)
