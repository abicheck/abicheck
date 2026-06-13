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
from .pack import BuildSourcePack, _payload_sha256

if TYPE_CHECKING:
    from ..model import AbiSnapshot


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

def _first_attr(attr: str, *packs: BuildSourcePack | None) -> Any:
    """Return the first non-None value of *attr* across *packs*."""
    for cand in packs:
        if cand is not None and getattr(cand, attr) is not None:
            return getattr(cand, attr)
    return None


def _coverage_row_for_present_layer(
    layer: str,
    packs: tuple[BuildSourcePack | None, ...],
) -> LayerCoverage:
    """Return the supplying pack's existing coverage row for *layer*, or a
    synthetic PRESENT row when none of *packs* carries one."""
    for cand in packs:
        if cand is None:
            continue
        row = next(
            (c for c in cand.manifest.coverage if _layer_value(c.layer) == layer),
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
    supplier: dict[str, tuple[BuildSourcePack | None, ...]],
    present: dict[str, bool],
    bi_pack: BuildSourcePack | None,
    src_pack: BuildSourcePack | None,
    embedded: BuildSourcePack | None,
) -> list[LayerCoverage]:
    """Rebuild the coverage manifest for the combined pack.

    Non-managed rows (L0/L1/L2/…) come from the base manifest. Always emit one
    row per managed layer (ADR-028 D7). When we carry the facts, reuse the
    supplying pack's row; otherwise mark the layer not_collected so the report
    never advertises a check with no facts behind it (Codex review) — and never
    drops the row entirely either.
    """
    managed = set(supplier)
    coverage: list[LayerCoverage] = [
        c for c in base.manifest.coverage if _layer_value(c.layer) not in managed
    ]
    for layer, packs in supplier.items():
        if present[layer]:
            row = _coverage_row_for_present_layer(layer, packs)
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
        if not contributed and e.name != "build_query":
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
    has_diag = any(e.name == "build_query" for e in p.manifest.extractors)
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
        digest = "sha256:" + _payload_sha256(payload.to_dict())  # type: ignore[attr-defined]
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


def _combine_packs(
    bi_pack: BuildSourcePack | None,
    src_pack: BuildSourcePack | None,
    embedded: BuildSourcePack | None = None,
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
    """
    build_evidence = _first_attr("build_evidence", bi_pack, src_pack, embedded)
    source_abi = _first_attr("source_abi", src_pack, bi_pack, embedded)
    source_graph = _first_attr("source_graph", src_pack, bi_pack, embedded)

    base = bi_pack or src_pack or embedded
    if base is None:
        return None

    # supplier order per managed layer, and whether we actually carry it
    supplier: dict[str, tuple[BuildSourcePack | None, ...]] = {
        DataLayer.L3_BUILD.value: (bi_pack, src_pack, embedded),
        DataLayer.L4_SOURCE_ABI.value: (src_pack, bi_pack, embedded),
        DataLayer.L5_SOURCE_GRAPH.value: (src_pack, bi_pack, embedded),
    }
    present = {
        DataLayer.L3_BUILD.value: build_evidence is not None,
        DataLayer.L4_SOURCE_ABI.value: source_abi is not None,
        DataLayer.L5_SOURCE_GRAPH.value: source_graph is not None,
    }

    chosen = (build_evidence, source_abi, source_graph)
    coverage = _build_combined_coverage(
        base, supplier, present, bi_pack, src_pack, embedded
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
