# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
"""Pack-combination and merge-conflict support for ``cli_buildsource``.

Split out of ``cli_buildsource.py`` to keep it under the 2000-line cap. Holds
``_combine_packs`` (fold per-layer facts from build-info / sources / embedded
packs) and the A2 merge-layer-conflict detection that ``merge`` uses.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .model import CoverageStatus, DataLayer, ExtractorRecord, LayerCoverage
from .pack import BuildSourcePack, _normalize_source_abi_payload, _payload_sha256

if TYPE_CHECKING:
    from ..model import AbiSnapshot

# Extractor names carrying a build-query no-facts diagnostic — the explicit
# trusted `build.query` and the zero-config inferred query. Both must be
# forwarded from a diagnostic-only pack so the combined manifest keeps the
# explanation for why source scanning produced no L3 facts (mirrors inline.py).
_BUILD_QUERY_DIAG_NAMES = ("build_query", "build_query_auto")


def _layer_value(layer: object) -> str:
    return layer.value if hasattr(layer, "value") else str(layer)

def _filter_pack_layers(
    pack: BuildSourcePack | None, layers: tuple[str, ...]
) -> BuildSourcePack | None:
    """Null out a loaded pack's facts for layers the collect-mode excludes, so a
    pre-captured pack can't smuggle past the ADR-033 D2 layer set (Codex review).
    ``_combine_packs`` derives coverage from these attributes, so nulling them
    drops both the facts and their coverage rows."""
    if pack is None:
        return None
    if "L3" not in layers:
        pack.build_evidence = None
    if "L4" not in layers:
        pack.source_abi = None
    if "L5" not in layers:
        pack.source_graph = None
    return pack

def _payload_empty(attr: str, payload: Any) -> bool:
    """True when a non-``None`` layer payload carries no real facts.

    A layer object can be present but empty — e.g. ``_run_inline_source_abi``
    returns a bare ``SourceAbiSurface()`` when clang/castxml is missing, or an
    inline replay yields an empty ``BuildEvidence``. Mirrors
    ``cli._layer_payload_empty``'s per-layer emptiness so an empty placeholder is
    treated like absence when choosing which pack supplies a layer."""
    if payload is None:
        return True
    if attr == "build_evidence":
        return not (getattr(payload, "targets", None) or getattr(payload, "compile_units", None))
    if attr == "source_abi":
        buckets = payload.reachable_buckets() if hasattr(payload, "reachable_buckets") else {}
        return not any(buckets.values())
    if attr == "source_graph":
        return not getattr(payload, "nodes", None)
    return False


def _first_attr_with_supplier(
    attr: str, *packs: BuildSourcePack | None, prefer_nonempty: bool = True
) -> tuple[Any, BuildSourcePack | None]:
    """Return ``(value, supplying_pack)`` for the pack that supplies *attr*.

    With ``prefer_nonempty`` (the default, the dump/merge fold): prefer the first
    pack whose payload is **non-empty**, so a non-``None`` but empty placeholder
    (e.g. an empty ``SourceAbiSurface()`` from a clang-less inline replay routed
    into the L4 slot) never masks a lower-priority pack's real facts (Codex
    review); fall back to the first non-``None`` payload only when no candidate
    carries real facts, so a genuinely empty layer still embeds with its row.

    With ``prefer_nonempty=False`` (``compare``'s ``_resolve_side_pack``): plain
    first-non-``None``, so an explicit ``--*-build-info``/``--*-sources`` pack
    overrides the snapshot's embedded payload **even when the explicit layer is
    intentionally empty** (a failed/absent replay) — the documented "explicit
    flags override embedded" contract, which the non-empty preference would
    otherwise break by falling through to stale embedded facts (Codex review).

    The *supplier* is the pack whose facts we embed, so its coverage row — and
    only its row — describes what landed (AC-002)."""
    first_present: tuple[Any, BuildSourcePack | None] | None = None
    for cand in packs:
        if cand is None:
            continue
        val = getattr(cand, attr)
        if val is None:
            continue
        if not prefer_nonempty or not _payload_empty(attr, val):
            return val, cand
        if first_present is None:
            first_present = (val, cand)
    return first_present if first_present is not None else (None, None)


def _coverage_row_for_present_layer(
    layer: str,
    supplier: BuildSourcePack | None,
) -> LayerCoverage:
    """Return the coverage row for a layer whose payload we *do* embed.

    Read it from the *supplier* — the pack that actually supplied the payload —
    not merely the first pack in supplier order that happens to carry a row for
    this layer; those can differ and produce a manifest that describes facts a
    different pack lacked (AC-002). The supplier's own row is honored **as-is**,
    whatever its status: ``ingest_inputs_pack`` (and the collectors) already
    emit ``not_collected`` for a non-``None`` but *empty* placeholder payload —
    e.g. an explicit empty ``compile_commands.json`` yields a non-``None`` but
    empty ``BuildEvidence`` — and ``present``/``partial`` for real facts, so
    trusting it keeps the combined manifest honest instead of advertising build
    context that has no compile units behind it (Codex P2). Only when the
    supplier carries no row for this layer at all do we synthesize a PRESENT row
    — we embed the payload and have no more precise statement to make."""
    if supplier is not None:
        row = next(
            (
                c
                for c in supplier.manifest.coverage
                if _layer_value(c.layer) == layer
            ),
            None,
        )
        if row is not None:
            return row
    return LayerCoverage(layer=layer, status=CoverageStatus.PRESENT)


def _coverage_row_for_absent_layer(
    layer: str,
    bi_pack: BuildSourcePack | None,
    src_pack: BuildSourcePack | None,
    embedded: BuildSourcePack | None,
) -> LayerCoverage:
    """Return a diagnostic PARTIAL row if any input pack reported one, else a
    NOT_COLLECTED row.

    No facts for this layer — but preserve a *diagnostic* coverage row
    (e.g. the A3 ``partial`` L3 from a failed/blocked build query) that an
    input pack reported, rather than overwriting it with not_collected
    and dropping the only explanation (Codex).
    Only a ``partial`` *diagnostic* row (e.g. the A3 build-query explanation)
    is preserved; a ``present`` row claimed by a loaded pack whose facts we did
    NOT embed must still downgrade to not_collected so the report never
    advertises facts it lacks.
    """
    for cand in (bi_pack, src_pack, embedded):
        if cand is None:
            continue
        hit = next(
            (c for c in cand.manifest.coverage
             if _layer_value(c.layer) == layer
             and c.status == CoverageStatus.PARTIAL),
            None,
        )
        if hit is not None:
            return hit
    return LayerCoverage(layer=layer, status=CoverageStatus.NOT_COLLECTED)


def _build_combined_coverage(
    base: BuildSourcePack,
    supplier_pack: dict[str, BuildSourcePack | None],
    present: dict[str, bool],
    bi_pack: BuildSourcePack | None,
    src_pack: BuildSourcePack | None,
    embedded: BuildSourcePack | None,
) -> list[LayerCoverage]:
    """Rebuild the coverage manifest for the combined pack.

    Non-managed rows (L0/L1/L2/…) come from the base manifest. Always emit one
    row per managed layer (ADR-028 D7). When we carry the facts, reuse the row
    from the *pack that actually supplied them* (``supplier_pack[layer]``);
    otherwise mark the layer not_collected so the report never advertises a
    check with no facts behind it (Codex review) — and never drops the row
    entirely either.
    """
    managed = set(supplier_pack)
    coverage: list[LayerCoverage] = [
        c for c in base.manifest.coverage if _layer_value(c.layer) not in managed
    ]
    for layer, supplier in supplier_pack.items():
        if present[layer]:
            row = _coverage_row_for_present_layer(layer, supplier)
        else:
            row = _coverage_row_for_absent_layer(layer, bi_pack, src_pack, embedded)
        coverage.append(row)
    return coverage


def _merge_artifacts(
    p: BuildSourcePack,
    artifacts: list[str],
) -> None:
    """Append *p*'s on-disk artifact digests to *artifacts*, deduplicating."""
    for a in p.manifest.artifacts:
        if a not in artifacts:
            artifacts.append(a)


def _merge_extractors(
    p: BuildSourcePack,
    contributed: bool,
    extractors: list[ExtractorRecord],
    seen_extractors: set[tuple[str, str, str]],
) -> None:
    """Append *p*'s extractor records to *extractors*, deduplicating by key.

    When the pack did not contribute any facts (*contributed* is False) only
    ``build_query`` diagnostic records are forwarded — all others are skipped so
    they do not pollute the combined manifest with unrelated extractor metadata.
    """
    for e in p.manifest.extractors:
        if not contributed and e.name not in _BUILD_QUERY_DIAG_NAMES:
            continue
        key = (e.name, e.version, e.detail)
        if key not in seen_extractors:
            seen_extractors.add(key)
            extractors.append(e)


def _accumulate_pack_provenance(
    p: BuildSourcePack,
    chosen_ids: set[int],
    artifacts: list[str],
    extractors: list[ExtractorRecord],
    seen_extractors: set[tuple[str, str, str]],
) -> None:
    """Fold one pack's artifacts and extractor records into the running lists.

    A pack "contributed" when at least one of its layer facts is the object
    we actually embedded in *chosen_ids*. A diagnostic-only pack (e.g. an A3
    build-query failure with no facts) contributes no payload but must still
    carry its ``build_query`` diagnostic forward — otherwise the combined pack
    is a silent all-not_collected surface and the only explanation is lost (Codex).
    """
    contributed = bool(
        chosen_ids & {id(p.build_evidence), id(p.source_abi), id(p.source_graph)}
    )
    has_diag = any(e.name in _BUILD_QUERY_DIAG_NAMES for e in p.manifest.extractors)
    if not (contributed or has_diag):
        return
    if contributed:
        _merge_artifacts(p, artifacts)
    _merge_extractors(p, contributed, extractors, seen_extractors)


def _append_chosen_payload_digests(
    chosen: tuple[Any, Any, Any],
    artifacts: list[str],
) -> None:
    for payload in chosen:
        if payload is None:
            continue
        # _normalize_source_abi_payload strips the same replay wall-clock/
        # cache-hit fields BuildSourcePack._artifact_digests() already
        # strips from an on-disk/self-contained pack's source_abi.json --
        # without it here, a combined/embedded pack (this function's own
        # docstring: an inline-collected --sources contributor that was
        # never written to disk) hashed the chosen SourceAbiSurface's raw
        # payload, bypassing that fix for the --sources/--build-info
        # combine path (Codex review). A no-op for build_evidence/
        # source_graph payloads, neither of which carries these keys under
        # a top-level "coverage" dict.
        digest = "sha256:" + _payload_sha256(
            _normalize_source_abi_payload(payload.to_dict())  # type: ignore[attr-defined]
        )
        if digest not in artifacts:
            artifacts.append(digest)


def _build_combined_provenance(
    bi_pack: BuildSourcePack | None,
    src_pack: BuildSourcePack | None,
    embedded: BuildSourcePack | None,
    chosen: tuple[Any, Any, Any],
) -> tuple[list[str], list[ExtractorRecord]]:
    """Build the artifacts + extractors lists for the combined pack.

    The combined manifest's artifacts/extractors must reflect every pack that
    supplied an embedded fact, not just the base pack — otherwise
    to_ref()/content_hash() would omit the source pack's artifacts for a
    cross-pack self-contained snapshot (Codex review). A pack "contributed"
    when one of its facts is the object we actually embedded.

    An *inline*-collected contributor (e.g. ``--sources <raw tree>``) is never
    written to disk, so its manifest.artifacts is empty and the loop below
    adds no digest for its source_abi/source_graph. Since content_hash() trusts
    a non-empty manifest.artifacts, a mixed ``--build-info <pack> --sources
    <tree>`` would then hash only the build pack's digest and ignore the source
    facts — two different trees with the same build pack collide (Codex P2). Add
    the in-memory payload digest for every chosen fact; a fact that *was*
    written to disk hashes identically (_payload_sha256 mirrors _write_json), so
    it dedups against the on-disk digest above rather than double-counting.
    """
    chosen_ids = {id(x) for x in chosen if x is not None}
    artifacts: list[str] = []
    extractors: list[ExtractorRecord] = []
    seen_extractors: set[tuple[str, str, str]] = set()

    for p in (bi_pack, src_pack, embedded):
        if p is None:
            continue
        _accumulate_pack_provenance(p, chosen_ids, artifacts, extractors, seen_extractors)

    _append_chosen_payload_digests(chosen, artifacts)

    return artifacts, extractors


def route_inline_source_supplier(
    src_pack: BuildSourcePack | None,
    inline_pack: BuildSourcePack | None,
) -> tuple[BuildSourcePack | None, BuildSourcePack | None]:
    """Split the inline-collected pack into ``(sources_supplier, backfill)``.

    AC-001: a raw ``--sources`` cold scan (``inline_pack``) is the *sources*
    contributor, so it must win L4/L5 over a Flow-2 ``--build-info`` pack. Route
    it into ``_combine_packs``'s ``src_pack`` slot (which outranks ``bi_pack``
    for L4/L5) when no ``--sources`` *pack* was given; a real ``--sources`` pack
    keeps that slot and the inline pack becomes the lowest-priority backfill.
    Keeping the inline pack out of the ``embedded`` slot on this path leaves that
    slot free for ``compare``'s snapshot payload, so an explicit ``--build-info``
    pack still overrides embedded facts there (Codex review)."""
    if src_pack is not None:
        return src_pack, inline_pack
    return inline_pack, None


def _combine_packs(
    bi_pack: BuildSourcePack | None,
    src_pack: BuildSourcePack | None,
    embedded: BuildSourcePack | None = None,
    *,
    prefer_nonempty: bool = True,
) -> BuildSourcePack | None:
    """Combine a build-info pack and a sources pack into one embeddable pack.

    Facts are taken from the pack that supplies each layer — ``build_evidence``
    from ``--build-info``, ``source_abi``/``source_graph`` from ``--sources`` —
    with *embedded* backfilling any gap. The coverage manifest is rebuilt by
    pulling each layer's row from the *same* pack that supplied its facts (not
    just the base pack), then dropping rows for layers we do not actually carry.
    This keeps a later compare's coverage/capability report honest when the two
    flags point at different packs (Codex review). Returns ``None`` when no pack
    contributes any facts.

    ``prefer_nonempty`` (default True — the dump/merge fold) skips a non-``None``
    but *empty* layer placeholder so a lower-priority pack's real facts still win
    (see ``_first_attr_with_supplier``). ``compare``'s ``_resolve_side_pack``
    passes ``prefer_nonempty=False`` so an explicit ``--*-build-info``/
    ``--*-sources`` pack overrides the snapshot's embedded payload **even when the
    explicit layer is intentionally empty** — the documented "explicit flags
    override embedded" contract (Codex review).
    """
    # L3 comes from --build-info (bi_pack) first, then --sources, then the
    # embedded/backfill pack. L4/L5 come from --sources (src_pack) first, then
    # --build-info, then the embedded/backfill pack — an explicit --build-info
    # pack's L4/L5 override the snapshot's embedded payload on the `compare`
    # path (its documented "explicit flags override embedded" contract). The
    # AC-001 need — a raw `--sources` cold scan winning L4/L5 over a Flow-2
    # `--build-info` pack — is handled by `embed_build_source` routing that
    # inline pack into *this* function's `src_pack` slot, not by reordering here
    # (which would wrongly let a snapshot's embedded facts beat an explicit
    # `--build-info` pack, Codex review).
    build_evidence, l3_supplier = _first_attr_with_supplier(
        "build_evidence", bi_pack, src_pack, embedded, prefer_nonempty=prefer_nonempty
    )
    source_abi, l4_supplier = _first_attr_with_supplier(
        "source_abi", src_pack, bi_pack, embedded, prefer_nonempty=prefer_nonempty
    )
    source_graph, l5_supplier = _first_attr_with_supplier(
        "source_graph", src_pack, bi_pack, embedded, prefer_nonempty=prefer_nonempty
    )

    base = bi_pack or src_pack or embedded
    if base is None:
        return None

    # The pack that actually supplied each managed layer's payload — its row is
    # the one that honestly describes what we embedded (AC-002).
    supplier_pack: dict[str, BuildSourcePack | None] = {
        DataLayer.L3_BUILD.value: l3_supplier,
        DataLayer.L4_SOURCE_ABI.value: l4_supplier,
        DataLayer.L5_SOURCE_GRAPH.value: l5_supplier,
    }
    present = {
        DataLayer.L3_BUILD.value: build_evidence is not None,
        DataLayer.L4_SOURCE_ABI.value: source_abi is not None,
        DataLayer.L5_SOURCE_GRAPH.value: source_graph is not None,
    }

    chosen = (build_evidence, source_abi, source_graph)
    coverage = _build_combined_coverage(
        base, supplier_pack, present, bi_pack, src_pack, embedded
    )
    artifacts, extractors = _build_combined_provenance(
        bi_pack, src_pack, embedded, chosen
    )

    return BuildSourcePack(
        root=Path(""),
        manifest=replace(
            base.manifest, coverage=coverage, artifacts=artifacts, extractors=extractors
        ),
        build_evidence=build_evidence,  # type: ignore[arg-type]
        source_abi=source_abi,  # type: ignore[arg-type]
        source_graph=source_graph,  # type: ignore[arg-type]
    )

_MERGE_LAYER_ATTRS: dict[str, str] = {
    DataLayer.L3_BUILD.value: "build_evidence",
    DataLayer.L4_SOURCE_ABI.value: "source_abi",
    DataLayer.L5_SOURCE_GRAPH.value: "source_graph",
}

# Layers where `_combine_packs(accumulator, new_input)` keeps the *latest*
# contributor (it prefers its second arg for source_abi/source_graph), as
# opposed to L3 which keeps the accumulator (first contributor). The conflict
# winner must be resolved in this direction so that, when two inputs share a
# digest, the reported survivor is the one whose facts actually landed.
_LATEST_WINS_LAYERS: frozenset[str] = frozenset(
    {DataLayer.L4_SOURCE_ABI.value, DataLayer.L5_SOURCE_GRAPH.value}
)

# The only lists whose *order is significant* — compiler/linker argument
# sequences and ordered define lists where a later entry overrides an earlier one.
# Every other list (fact records, and unordered scalar fact sets like a target's
# source_files / public_headers / dependencies / generated_files) is sorted so a
# reorder is not a false conflict (Codex review).
_ORDERED_LIST_KEYS = frozenset(
    {
        "argv", "linker_argv", "command", "inputs", "defines", "undefines",
        # -I / -isystem order is compiler-visible: swapping two include dirs can
        # select different headers and change the source ABI, so these stay
        # order-sensitive (Codex).
        "include_paths", "system_include_paths",
        # abi_relevant_flags is a replay input (source_replay joins it in order
        # for the cache key, _argv appends it in order); last-wins pairs like
        # -fexceptions/-fno-exceptions or -frtti/-fno-rtti change the parsed ABI
        # when swapped, so a reorder must still read as a conflict (Codex).
        "abi_relevant_flags",
    }
)


def _canonicalize(obj: Any, key: str | None = None) -> Any:
    """Order-normalize a layer payload so equivalent facts hash the same.

    Lists are sorted by canonical JSON — both lists of **fact records** (compile
    units, graph nodes/edges, and the nested L4 ``reachable_declarations``/
    ``reachable_types``) and **unordered scalar fact sets** (a target's
    ``source_files``/``public_headers``/``dependencies``/``generated_files``),
    which downstream checks consume by identity rather than position. Only lists
    under an :data:`_ORDERED_LIST_KEYS` key are left in place — compiler/linker
    argument sequences and ordered define lists, where order can change the
    produced ABI, so a reorder there *should* still read as a conflict. Dict key
    order is normalized by recursion.

    Cost is O(n log n) per fact list (sort by canonical JSON) over a layer
    payload that is already bounded by the on-disk pack size, so it is not a hot
    path; no memoization needed.
    """
    if isinstance(obj, dict):
        return {k: _canonicalize(obj[k], k) for k in sorted(obj)}
    if isinstance(obj, list):
        items = [_canonicalize(x) for x in obj]
        if key in _ORDERED_LIST_KEYS:
            return items
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
    return obj

def _canonical_layer_digest(payload_dict: dict[str, Any]) -> str:
    """Digest of one layer's facts that is independent of *fact* ordering (even
    nested fact arrays) but preserves *ordered* scalar fields (A2)."""
    blob = json.dumps(
        _canonicalize(payload_dict), sort_keys=True, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

def _detect_merge_layer_conflicts(
    snaps: list[tuple[Path, AbiSnapshot]],
) -> dict[str, list[tuple[str, str]]]:
    """A2: per managed layer, return ``layer -> [(input_name, digest), ...]`` when
    >1 input supplies that layer with *differing* normalized facts.

    The comparison is an order-independent **per-layer payload digest** of just
    that layer's facts, not the pack-wide ``BuildSourcePack.content_hash()`` —
    the pack hash folds in every layer plus coverage/extractor metadata, so two
    inputs with identical L4/L5 facts but a differing unrelated layer would
    false-positive. A layer with one contributor, or several contributors that
    all agree (even in a different fact order), is not a conflict.
    """
    seen: dict[str, list[tuple[str, str]]] = {layer: [] for layer in _MERGE_LAYER_ATTRS}
    for path, s in snaps:
        pack = s.build_source
        if pack is None:
            continue
        for layer, attr in _MERGE_LAYER_ATTRS.items():
            payload = getattr(pack, attr, None)
            if payload is None:
                continue
            digest = _canonical_layer_digest(payload.to_dict())
            seen[layer].append((path.name, digest))

    conflicts: dict[str, list[tuple[str, str]]] = {}
    for layer, entries in seen.items():
        if len(entries) > 1 and len({d for _n, d in entries}) > 1:
            conflicts[layer] = entries
    return conflicts

def _resolve_conflict_winners(
    combined: BuildSourcePack, conflicts: dict[str, list[tuple[str, str]]]
) -> dict[str, str]:
    """Return ``layer -> winning input name``: which input's facts actually
    landed in the folded baseline for each conflicting layer.

    ``_combine_packs`` has layer-specific preference (it keeps the accumulator's
    L3 but the latest input's L4/L5), so the recorded/printed winner must be the
    *actual* survivor, not an assumed first-wins (Codex review). Resolved by
    matching the combined pack's per-layer digest back to the contributor digests.
    """
    winners: dict[str, str] = {}
    for layer, entries in conflicts.items():
        payload = getattr(combined, _MERGE_LAYER_ATTRS[layer], None)
        if payload is None:
            continue
        won = _canonical_layer_digest(payload.to_dict())
        # When two inputs share the winning digest, pick the one the fold actually
        # kept: the last contributor for latest-wins layers (L4/L5), the first for
        # accumulator-wins layers (L3) — so the recorded survivor is not an
        # arbitrary same-digest sibling (Codex).
        ordered = list(reversed(entries)) if layer in _LATEST_WINS_LAYERS else entries
        for name, digest in ordered:
            if digest == won:
                winners[layer] = name
                break
    return winners

def _record_merge_conflicts(
    combined: BuildSourcePack,
    conflicts: dict[str, list[tuple[str, str]]],
    winners: dict[str, str],
) -> None:
    """Persist A2 conflicts into the combined pack's extractor ledger.

    ``BuildSourceManifest.to_dict()`` serializes ``extractors`` (but has no
    ``diagnostics`` field), so an ``ExtractorRecord`` is the channel that
    survives embedding/round-trip. ``warn`` mode keeps one input's facts per
    layer and leaves this record behind — naming the *actual* survivor — so the
    divergence rides forward in the baseline.
    """
    records = list(combined.manifest.extractors)
    for layer, entries in sorted(conflicts.items()):
        detail = "; ".join(f"{name}={digest}" for name, digest in entries)
        won = winners.get(layer, "one input")
        records.append(
            ExtractorRecord(
                name="merge_layer_conflict",
                status="failed",
                detail=f"layer {layer} supplied with differing facts: {detail}",
                diagnostics=[
                    f"kept {won} for {layer}; verify each layer comes from "
                    "exactly one input."
                ],
            )
        )
    combined.manifest = replace(combined.manifest, extractors=records)
