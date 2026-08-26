# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""What evidence depth an artifact *actually* carries, and how depths compare.

ADR-061 Phase 3's recorded blocker was that this vocabulary had no owner:
``cli_dump_helpers.py`` held the implementation, so every non-CLI consumer
either imported *through the CLI layer* — the ``workflows -> frontends``
inversion that blocks moving the service pipelines into ``workflows/`` — or
kept a private copy. Both happened. Before this module the ladder existed
four times (``buildsource.scan_levels.USER_DEPTHS`` plus three separate
``_DEPTH_RANK`` dicts in ``cli_dump_helpers.py``, ``analysis_assurance.py``,
and ``buildsource/check_report.py``), and ``analysis_assurance`` additionally
carried a hand-copied ``_effective_depth_label``, whose own comment recorded
why: "duplicated rather than imported ... avoiding a CLI-layer import from
this leaf-ish module."

So this is the leaf both sides may depend on. It imports no CLI module, no
service module, and nothing that reaches ``cli.py``/``checker.py``.

:data:`DEPTH_RANK` is *derived* from :data:`~abicheck.buildsource.scan_levels.
USER_DEPTHS` rather than restating it. That is the point: the ordering is
declared once, on the enum that already owns it, so adding or reordering a
public rung cannot leave a rank map silently disagreeing with the ladder.

Two distinctions this module keeps deliberately separate, because callers
genuinely need both:

- :func:`depth_label_for` answers "what does this artifact carry", taking the
  pack explicitly and never defaulting to ``snap.build_source``. A caller that
  resolved an out-of-band pack must not have the snapshot's own (absent or
  unrelated) payload silently substituted. ``cli_dump_helpers.
  evidence_depth_label`` is the defaulting wrapper for the embedded-only case.
- :func:`gated_source_label` answers "may an explicit ``--depth source`` be
  considered satisfied", which is strictly stricter: a non-empty L5 can come
  from a header-only declaration graph that never ran source-tier replay, so
  the gate requires L4 to have been genuinely *attempted*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .buildsource.scan_levels import USER_DEPTHS

if TYPE_CHECKING:
    from .buildsource.pack import BuildSourcePack
    from .model import AbiSnapshot

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


def depth_label_for(snap: AbiSnapshot, pack: BuildSourcePack | None) -> str:
    """Which evidence depth *snap* (with *pack*) actually reached (CLI-audit P2).

    Computed purely from what was resolved -- ``binary``/``headers``/``build``/
    ``source`` -- rather than echoing back a requested ``--depth``: an explicit
    ``--depth source`` with no usable source facts still produces a snapshot
    that only reaches ``headers`` (or ``binary``), and this makes that honest
    instead of silently overstating what was collected.

    *pack* is explicit and is **not** defaulted to ``snap.build_source`` here:
    ``compare`` can resolve an out-of-band ``--old/new-sources`` pack that is
    never attached back to the snapshot object, and defaulting inside this
    function would silently report the unrelated embedded payload's depth
    instead (Codex review). ``cli_dump_helpers.evidence_depth_label`` applies
    the embedded-only default for the single-artifact ``dump -o`` case.

    Uses payload-emptiness rather than presence (:func:`layer_payload_empty`):
    a coverage row can be non-``None`` over an empty payload, and checking
    presence alone would overstate ``source``/``build`` for a layer that ran
    but linked nothing (CodeRabbit review).

    ``snap.parsed_with_build_context`` (ADR-020a/039: ``-p``/``--compile-db``,
    a much older, narrower build-context mechanism than the ``BuildSourcePack``
    machinery, with no ``BuildEvidence``/compile-unit model of its own) also
    reaches ``build``: without it a ``dump lib.so -H api.h -p build/`` run has
    no ``snap.build_source`` at all and would report ``headers``, even though
    the depth-gate error message this feeds already documents "build via
    --build-info/a compile database" as a valid way to satisfy ``--depth
    build`` (Codex review).
    """
    if pack is not None and (
        not layer_payload_empty(pack, "L4") or not layer_payload_empty(pack, "L5")
    ):
        return "source"
    if pack is not None and not layer_payload_empty(pack, "L3"):
        return "build"
    if snap.parsed_with_build_context:
        return "build"
    if snap.from_headers:
        return "headers"
    return "binary"


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

    :func:`depth_label_for` honestly reports ``source`` whenever L4 *or* L5
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
