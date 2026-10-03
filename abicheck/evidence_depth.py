# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""What evidence depth an artifact *actually* carries, and how depths compare.

ADR-061 Phase 3's recorded blocker was that this vocabulary had no owner:
``cli_dump_helpers.py`` held the implementation, so every non-CLI consumer
either imported *through the CLI layer* — the ``workflows -> frontends``
inversion that blocks moving the service pipelines into ``workflows/`` — or
kept a private copy. Both happened. Before this module the ladder existed
four times (``model.evidence_depth_levels.USER_DEPTHS`` plus three separate
``_DEPTH_RANK`` dicts in ``cli_dump_helpers.py``, ``analysis_assurance.py``,
and ``buildsource/check_report.py``), and ``analysis_assurance`` additionally
carried a hand-copied ``_effective_depth_label``, whose own comment recorded
why: "duplicated rather than imported ... avoiding a CLI-layer import from
this leaf-ish module."

So this is the leaf both sides may depend on. It imports no CLI module, no
service module, and nothing that reaches ``cli.py``/``checker.py``.

:data:`DEPTH_RANK` is *derived* from :data:`~abicheck.model.evidence_depth_levels.
USER_DEPTHS` rather than restating it. That is the point: the ordering is
declared once, on the enum that already owns it, so adding or reordering a
public rung cannot leave a rank map silently disagreeing with the ladder.

Two distinctions this module keeps deliberately separate, because callers
genuinely need both:

- :func:`depth_label_for` (removed) answers "what does this artifact carry", taking the
  pack explicitly and never defaulting to ``snap.build_source``. A caller that
  resolved an out-of-band pack must not have the snapshot's own (absent or
  unrelated) payload silently substituted.
- :func:`gated_source_label` answers "may an explicit ``--depth source`` be
  considered satisfied", which is strictly stricter: a non-empty L5 can come
  from a header-only declaration graph that never ran source-tier replay, so
  the gate requires L4 to have been genuinely *attempted*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .model.evidence_depth_levels import USER_DEPTHS

if TYPE_CHECKING:
    from .buildsource.model import LayerCoverage
    from .buildsource.pack import BuildSourcePack
    from .model import AbiSnapshot
    from .model.source_graph import SourceGraphSummary

#: The public evidence ladder as a rank map, derived from ``USER_DEPTHS`` so
#: the ordering has exactly one definition. Each rung is a strict superset of
#: the facts below it.
DEPTH_RANK: dict[str, int] = {
    depth.value: rank for rank, depth in enumerate(USER_DEPTHS)
}


def depth_rank(label: str | None) -> int:
    """Rank *label* on the public ladder, treating anything unknown as the floor.

    Unknown includes the internal-only ``full``/``graph`` rungs and ``None``.
    Answering ``0`` rather than raising is deliberate and matches every call
    site this replaced: rank is used to decide whether an achieved depth
    *clears* a requested one, so an unrecognized value must never be read as
    clearing something.
    """
    return DEPTH_RANK.get(label or "", 0)


def weaker_depth(a: str, b: str) -> str:
    """The shallower of two depth labels — a pair's achieved depth is its weaker side."""
    return a if depth_rank(a) <= depth_rank(b) else b


def layer_payload_empty(pack: BuildSourcePack, key: str) -> bool:
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


def resolve_l5_source_graph(
    snap: AbiSnapshot, pack: BuildSourcePack | None
) -> SourceGraphSummary | None:
    """The L5 evidence graph for *snap*/*pack* (ADR-063 Phase 10), the one
    shared resolver every migrated reader (``internal_leak.py``,
    ``buildsource/cross_source_checks.py``, ``buildsource/evidence_report.py``)
    goes through, so the same
    fallback rule can't independently drift per call site the way it did
    across three earlier review rounds on this migration.

    Prefers *pack*'s own ``source_graph``. With no pack at all (neither
    *pack* nor ``snap.build_source`` -- a plain header-only dump, which no
    longer carries a synthesized pack), the header graph on
    ``AbiSnapshot.surface_graph`` *is* the snapshot's L5 evidence; depth
    projection below ``source`` clears it, so a projected snapshot cannot
    resurrect L5 this way. With a pack, falls back to ``surface_graph`` only
    when ALL of:

    - *pack* is the snapshot's own embedded ``build_source`` (never an
      unrelated out-of-band ``--old/new-build-info``/``--old/new-sources``
      pack a caller resolved independently -- that pack has no relationship
      to ``snap.surface_graph`` at all, so its own ``source_graph`` is read
      directly, unchanged, regardless of this fallback);
    - *pack* carries no graph of its own -- a real ``--sources``/
      ``--build-info`` embed can leave ``build_source.source_graph`` a
      strictly richer, real L3-L5 evidence graph than the always-on,
      header-only-only ``surface_graph``, so preferring the latter would
      silently drop real graph edges (security review, PR #1216);
    - *pack*'s manifest records no L5 coverage row at all --
      ``policy/depth_projection.py`` deliberately clears ``build_source.
      source_graph`` for a ``--depth build`` (or shallower) comparison
      *while stamping an explicit L5 "not collected" row* and retaining
      ``surface_graph`` untouched (it is an L2 fact, cleared at a lower
      floor); falling back there would resurrect L5-labeled findings/depth
      claims for a comparison whose own report says L5 was excluded
      (`_mark_layers_not_collected` guarantees this row exists even for a
      pack that started with none at all, so "no row" reliably means
      "never went through any collection/projection pipeline", not merely
      "this specific projection pass didn't touch it");
    - ``surface_graph`` is a genuine ``SourceGraphSummary``, not merely a
      structurally-conforming ``SurfaceGraphLike`` (`model/graph_facts.py`)
      implementation -- every one of this function's callers eventually
      feeds the result into code that reads concrete-only attributes
      (e.g. ``buildsource/evidence_report.py``'s
      ``diff_source_graph_findings`` reads ``narrowed_scope``/
      ``extractor_passes``/``degraded_passes``, none of which the protocol
      declares), so a merely-structural implementation must be treated the
      same as "no graph" here rather than passed through and crashing
      downstream.
    """
    if pack is not None and pack.source_graph is not None:
        return pack.source_graph
    if pack is None:
        # No pack at all: a plain header-only dump carries its header graph
        # on `surface_graph` alone (ADR-063 Phase 10 -- no synthesized pack).
        # A caller that passed None while an embedded pack exists asked for
        # out-of-band evidence only, and gets none.
        return embedded_header_graph(snap) if snap.build_source is None else None
    if (
        pack is snap.build_source
        and pack.manifest.coverage_for("L5_source_graph") is None
    ):
        return embedded_header_graph(snap)
    return None


def embedded_header_graph(snap: AbiSnapshot) -> SourceGraphSummary | None:
    """*snap*'s always-on header graph (``surface_graph``), when it is a real
    ``SourceGraphSummary`` (see :func:`resolve_l5_source_graph`'s last
    condition for why a merely structural one is treated as absent)."""
    from .model.source_graph import SourceGraphSummary as _SourceGraphSummary

    graph = snap.surface_graph
    return graph if isinstance(graph, _SourceGraphSummary) else None


def embedded_evidence_pack(snap: AbiSnapshot) -> BuildSourcePack | None:
    """*snap*'s embedded pack, or -- for a header-only dump, which carries no
    pack since ADR-063 Phase 10 -- a pack standing in for its header graph,
    for the callers that fold packs together (``buildsource merge``,
    ``embed_inputs_pack``) and so need the graph in pack form. A fresh object
    each call; never attached back to *snap*."""
    if snap.build_source is not None:
        return snap.build_source
    graph = embedded_header_graph(snap)
    if graph is None:
        return None
    from pathlib import Path

    from .buildsource.pack import BuildSourcePack as _BuildSourcePack

    pack = _BuildSourcePack(root=Path(""), source_graph=graph)
    pack.manifest.coverage = header_graph_coverage(graph)
    return pack


def header_graph_coverage(graph: SourceGraphSummary) -> list[LayerCoverage]:
    """The L3/L4/L5 coverage rows a header-only graph stands for: L3/L4
    honestly not collected (no build or source-ABI replay ran), L5 present at
    reduced confidence when the graph has edges, partial otherwise. Formerly
    stamped on a pack synthesized for every header-only dump; now derived on
    read wherever that pack's manifest used to be consulted."""
    from .buildsource.model import (
        CoverageStatus,
        DataLayer,
        LayerConfidence,
        LayerCoverage,
    )

    return [
        LayerCoverage(
            layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED
        ),
        LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED
        ),
        LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value,
            status=CoverageStatus.PRESENT if graph.edges else CoverageStatus.PARTIAL,
            confidence=LayerConfidence.REDUCED
            if graph.edges
            else LayerConfidence.UNKNOWN,
        ),
    ]


def l4_source_abi_was_attempted(pack: BuildSourcePack) -> bool:
    """True when L4 source-ABI extraction genuinely parsed source, whether or not it linked anything.

    Coverage *status* alone (``PRESENT``/``PARTIAL`` vs ``NOT_COLLECTED``) is
    not enough: ``buildsource.inline._run_inline_source_abi`` stamps L4
    ``PARTIAL`` (never ``NOT_COLLECTED``) both for the *expected*, warn-only
    "ran but 0/N symbols matched" outcome of a source-only ``dump --sources``
    (no binary to link declarations against) **and** for a genuinely *failed*
    attempt — the selected extractor missing from ``PATH``, or every selected
    TU failing to parse — which returns the same empty ``SourceAbiSurface()``
    shape with the same ``PARTIAL`` status (Codex review, fifth finding: a
    missing/failing extractor must not satisfy an explicit ``--depth source``,
    matching representation notwithstanding).

    The reliable signal is the presence of
    ``SourceAbiSurface.coverage["compile_units_parsed"]`` specifically — set
    unconditionally by ``source_replay.run_source_replay`` whenever replay
    actually executes, independent of whether anything downstream matched
    against binary exports (parsing happens before, and regardless of,
    linking). The *key* (not just a non-empty ``coverage`` dict) is what
    matters: it is absent for the tool-unavailable short-circuit, which
    returns a bare ``SourceAbiSurface()`` before replay ever runs, but a
    non-empty ``coverage`` dict populated by a *different* stage —
    ``link_source_abi``'s own ``reachable_declarations``/``matched_symbols``
    stats, stamped on a Flow-2 ``inputs_pack.ingest_inputs_pack()`` pack that
    never went through ``run_source_replay`` at all — must not be mistaken for
    "replay ran" just because it happens to be truthy. ``NOT_COLLECTED`` still
    covers the "no extraction attempted at all" cases (no ``--sources``, no L3
    to replay against, or ``--ast-frontend hybrid``, which
    ``_run_inline_source_abi`` records as ``"skipped"``).

    Falls back to the payload-based check whenever the ``compile_units_parsed``
    key is absent — covering both the ingested Flow-2 pack above and a
    hand-built pack (a test fixture, or an out-of-band ``--old/new-sources``
    pack assembled without going through ``inline.py``'s replay) with genuine
    ``source_abi`` facts but no replay coverage stats, so neither is mistaken
    for "never attempted".
    """
    from .buildsource.model import CoverageStatus, DataLayer

    cov = pack.manifest.coverage_for(DataLayer.L4_SOURCE_ABI)
    if cov is not None and cov.status == CoverageStatus.NOT_COLLECTED:
        return False
    surface = pack.source_abi
    if surface is not None and "compile_units_parsed" in surface.coverage:
        try:
            return int(surface.coverage.get("compile_units_parsed", 0) or 0) > 0
        except (TypeError, ValueError, OverflowError):
            return False
    return not layer_payload_empty(pack, "L4")


def gated_source_label(pack: BuildSourcePack | None, snap: AbiSnapshot) -> str:
    """Recompute the ``source`` evidence label for the *strict* depth gate.

    :func:`depth_label_for` (removed) honestly reports ``source`` whenever L4 *or* L5
    carries facts — correct for its own honesty contract, since genuine
    source-tier collection can legitimately populate L5 (``source_graph``)
    without L4: ``source_graph.build_source_graph`` folds ``BuildEvidence``
    structure into a graph even when the L4 surface found nothing.

    That L4-or-L5 rule is too permissive for a *gate*. A non-empty L5 can also
    come from a header-only (L2) declaration graph that never ran any source-
    tier replay at all — ``service._attach_header_graph`` (always attempted
    since G29 Phase A when headers are available and no ``--sources``/
    ``--build-info`` triggered a deeper collection) attaches one directly, and
    ``cli_buildsource.embed_build_source``'s backfill step can graft that same
    header-only graph onto an otherwise-real, L3-only ``--build-info`` pack —
    so "L3 present" does not rule out a header-only-graph L5 either (Codex
    review, second finding).

    The reliable signal is whether L4 extraction was genuinely *attempted*
    (:func:`l4_source_abi_was_attempted`) — a coverage-status check, not a
    payload-emptiness one: a source-only dump legitimately links zero
    declarations (no binary to link against) yet must still satisfy an explicit
    ``--depth source``, the same way it already only warns about that case;
    only a *never-attempted* L4 (the header-graph cases above) is downgraded
    here, to ``build`` (real L3, or a ``-p``/``--compile-db`` build context) or
    ``headers``/``binary`` (nothing).
    """
    if pack is not None and l4_source_abi_was_attempted(pack):
        return "source"
    if pack is not None and not layer_payload_empty(pack, "L3"):
        return "build"
    if snap.parsed_with_build_context:
        return "build"
    return "headers" if snap.from_headers else "binary"


def reported_depth_label(snap: AbiSnapshot, pack: BuildSourcePack | None) -> str:
    """The depth a *report* may claim for *snap*/*pack*.

    :func:`depth_label_for` (removed) answers ``"source"`` whenever L4 **or** L5
    carries facts, which is right for its own contract but wrong for a
    report's assurance block: the always-on, header-only L5 declaration
    graph ``service._attach_header_graph`` attaches makes *every*
    header-parsing comparison claim ``effective_depth: "source"`` while its
    L4 row reads ``not_collected``. A byte-identical Linux ELF C++ rebuild
    therefore reported ``status: complete`` at ``effective_depth: source``
    over evidence that was headers plus a reduced source graph -- reduced
    source-graph evidence described as a complete source-level analysis.

    So a report reuses the *gate*'s rule (:func:`gated_source_label`): the
    ``source`` rung requires L4 to have been genuinely attempted. Using the
    gate's own function rather than a second, parallel rule is deliberate --
    it is the same question ("may this run be called source-depth?"), and
    the two answering differently is what let ``analysis_assurance`` report
    ``effective_depth: "source"`` for a run whose explicit ``--depth
    source`` the CLI gate would have rejected.
    """
    return gated_source_label(pack, snap)


@dataclass(frozen=True, slots=True)
class ReportedDepth:
    """How a report states the depth a run was asked for and reached.

    Resolution lives here, with the ladder, rather than inline in
    ``analysis_assurance.compute_analysis_assurance``: deciding what an
    *absent* request means is a statement about the ladder's vocabulary,
    and the rule has to hold identically for every consumer that publishes
    a depth (the pairwise block, the release roll-up, the artifact
    executors).
    """

    requested: str
    source: str
    satisfied: bool
    note: str | None


def depth_request_source(explicit: str | None) -> str:
    """``"explicit"`` when a front end stated ``--depth``, else ``"implicit"``.

    One line, but two callers now decide it -- the normalized path below
    and ``analysis_assurance``'s ``not_comparable`` short-circuit, which
    resolves no depth at all and so cannot go through
    :func:`resolve_reported_depth`. Stating the rule twice is how the
    short-circuit came to default to ``"implicit"`` and assert something it
    never established (CodeRabbit review), so it is stated once.
    """
    return "explicit" if explicit is not None else "implicit"


def resolve_reported_depth(explicit: str | None, effective: str) -> ReportedDepth:
    """The reported depth trio for an *explicit* request (or the lack of one).

    A run given no ``--depth`` still ran at some depth, and reporting
    ``requested_depth``/``depth_satisfied`` as ``null`` made that
    unauditable: the block said ``status: complete`` beside two nulls, so a
    reader could not tell an *unrequested* depth from an *unanswered* one
    (the identical run with an explicit ``--depth headers`` reported both).
    The implicit request is normalized to the depth actually reached and
    labelled ``"implicit"`` -- the label is what keeps the normalization
    honest, and it is why callers must keep gating on *explicit* rather
    than on :attr:`ReportedDepth.requested`.
    """
    source = depth_request_source(explicit)
    if explicit is None:
        return ReportedDepth(effective, source, True, None)
    satisfied = depth_rank(effective) >= depth_rank(explicit)
    note = (
        None
        if satisfied
        else (
            f"requested depth {explicit!r} not reached; effective "
            f"depth is {effective!r}"
        )
    )
    return ReportedDepth(explicit, source, satisfied, note)
