# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Per-pass coverage of the L5 source graph, as I4 coverage records
(evidence-entity-model plan, Phase 4).

``SourceGraphSummary`` records, per extractor pass, whether it ran over the
whole project (``extractor_passes``), over a narrowed set of compile units
(``narrowed_passes`` + ``narrowed_scope``), or hit per-TU diagnostics
(``degraded_passes``) -- see ``docs/learn/graph-coverage.md``. This module
is the one owner of how those flags translate into "which edge kinds does
this pass vouch for, over which scope":

- :data:`PASS_EDGE_KINDS` -- the edge-kind family each pass produces;
- :data:`HEADER_PASS_ALIAS` -- each build-integrated pass's header-only
  counterpart (``buildsource/header_graph*.py``), the same family from a
  different producer;
- :data:`HEADER_FULL_VISIBILITY_KINDS` -- the kinds a header-only pass sees
  project-wide. A call or reference inside an out-of-line body is invisible
  to it, so for those kinds its run covers only header-written bodies.

:func:`pass_coverage_records` turns one graph's flags into
:class:`~abicheck.model.edge_coverage.CoverageRecord` rows for one edge kind.
Scope units are compile-unit / file paths, with
:data:`~abicheck.model.edge_coverage.ALL_UNITS` for a whole-project pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from .edge_coverage import ALL_UNITS, CoverageRecord, ProducerRun


class SourceGraphSummary(Protocol):
    """The pass-flag fields of ``model.source_graph.SourceGraphSummary`` this
    module reads -- a protocol, since ``source_graph`` imports this module."""

    extractor_passes: dict[str, bool]
    narrowed_passes: dict[str, bool]
    narrowed_scope: dict[str, frozenset[str]]
    degraded_passes: dict[str, bool]


__all__ = [
    "BUILD_OPTIONS_PASS",
    "BUILD_TARGETS_PASS",
    "CALL_GRAPH_PASS",
    "HEADER_DECLARATIONS_PASS",
    "SOURCE_ABI_PASS",
    "graph_records_passes",
    "HEADER_CALL_GRAPH_PASS",
    "HEADER_FULL_VISIBILITY_KINDS",
    "HEADER_INCLUDE_GRAPH_PASS",
    "HEADER_INLINE_BODIES",
    "HEADER_PASS_ALIAS",
    "HEADER_TYPE_GRAPH_PASS",
    "INCLUDE_GRAPH_PASS",
    "L5_EDGE_KINDS",
    "PASS_EDGE_KINDS",
    "TYPE_GRAPH_PASS",
    "pass_coverage_records",
    "pass_for_edge_kind",
    "trusted_kinds",
]

CALL_GRAPH_PASS = "call_graph"
#: Evidence-entity-model gap A5: the L4 source-ABI fold, the build-target
#: fold and the build-option linker are producers too, and stamp these flags.
SOURCE_ABI_PASS = "source_abi"
BUILD_TARGETS_PASS = "build_targets"
BUILD_OPTIONS_PASS = "build_options"
HEADER_DECLARATIONS_PASS = "header_declarations"
TYPE_GRAPH_PASS = "type_graph"
INCLUDE_GRAPH_PASS = "include_graph"
HEADER_CALL_GRAPH_PASS = "header_call_graph"
HEADER_TYPE_GRAPH_PASS = "header_type_graph"
HEADER_INCLUDE_GRAPH_PASS = "header_include_graph"

#: Build-integrated pass -> the edge kinds it produces.
PASS_EDGE_KINDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        CALL_GRAPH_PASS: frozenset({"DECL_CALLS_DECL"}),
        TYPE_GRAPH_PASS: frozenset(
            {
                "DECL_REFERENCES_DECL",
                "DECL_HAS_TYPE",
                "TYPE_HAS_FIELD_TYPE",
                "TYPE_INHERITS",
            }
        ),
        INCLUDE_GRAPH_PASS: frozenset({"COMPILE_UNIT_INCLUDES_FILE"}),
        SOURCE_ABI_PASS: frozenset({"SOURCE_DECLARES", "SOURCE_DECL_MAPS_TO_SYMBOL"}),
        BUILD_TARGETS_PASS: frozenset(
            {"TARGET_HAS_PUBLIC_HEADER", "TARGET_DEPENDS_ON"}
        ),
        BUILD_OPTIONS_PASS: frozenset({"BUILD_OPTION_AFFECTS_SYMBOL"}),
    }
)

#: Build-integrated pass -> its header-only counterpart.
HEADER_PASS_ALIAS: Mapping[str, str] = MappingProxyType(
    {
        CALL_GRAPH_PASS: HEADER_CALL_GRAPH_PASS,
        TYPE_GRAPH_PASS: HEADER_TYPE_GRAPH_PASS,
        INCLUDE_GRAPH_PASS: HEADER_INCLUDE_GRAPH_PASS,
        SOURCE_ABI_PASS: HEADER_DECLARATIONS_PASS,
    }
)

#: Kinds a header-only pass has project-wide visibility of: declaration-level
#: facts (a base class, a field type, a parameter type) and file inclusion.
#: ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL`` need a function body, which a
#: header-only pass sees only when it is written in a header.
HEADER_FULL_VISIBILITY_KINDS: frozenset[str] = frozenset(
    {
        "DECL_HAS_TYPE",
        "TYPE_HAS_FIELD_TYPE",
        "TYPE_INHERITS",
        "COMPILE_UNIT_INCLUDES_FILE",
        # Every declaration a parsed header makes is visible to a header pass.
        "SOURCE_DECLARES",
    }
)

#: The one unit a header-only pass covers for a body-dependent kind.
HEADER_INLINE_BODIES = "header_inline_bodies"

L5_EDGE_KINDS: frozenset[str] = frozenset().union(*PASS_EDGE_KINDS.values())

#: The kinds a header pass sees only inside a header-written body.
_BODY_KINDS: frozenset[str] = frozenset({"DECL_CALLS_DECL", "DECL_REFERENCES_DECL"})


def pass_for_edge_kind(edge_kind: str) -> str | None:
    """The build-integrated pass producing *edge_kind*, or ``None``."""
    for pass_name, kinds in PASS_EDGE_KINDS.items():
        if edge_kind in kinds:
            return pass_name
    return None


def trusted_kinds(
    graph: SourceGraphSummary, pass_name: str, family: frozenset[str]
) -> frozenset[str]:
    """Which kinds in *family* a confirmed whole-project *pass_name* vouches
    for on *graph*: nothing when neither pass ran to completion.

    A build-integrated confirmation vouches for the *whole* family -- a real
    per-TU AST replay sees function bodies too, so its "zero" is
    authoritative for every kind. A header-only confirmation vouches only
    for :data:`HEADER_FULL_VISIBILITY_KINDS`, whatever the *other* side of a
    comparison is: a header-only pass's blindness to out-of-line bodies is a
    property of that side alone. This loses a little recall for a
    header-only-vs-header-only comparison's body-dependent kinds, in
    exchange for never tracking the other side's shape -- the simpler,
    strictly-safe rule (``buildsource/source_graph_findings.py``'s pairwise
    comparisons read it)."""
    if graph.extractor_passes.get(pass_name, False):
        return family
    header = HEADER_PASS_ALIAS.get(pass_name, "")
    if header and graph.extractor_passes.get(header, False):
        return family & HEADER_FULL_VISIBILITY_KINDS
    return frozenset()


def _record(
    graph: SourceGraphSummary, pass_name: str, edge_kind: str, *, header: bool
) -> CoverageRecord | None:
    source = f"SourceGraphSummary[{pass_name}]"
    units = frozenset({ALL_UNITS})
    body_blind = header and edge_kind not in HEADER_FULL_VISIBILITY_KINDS
    if graph.degraded_passes.get(pass_name, False):
        return CoverageRecord(
            edge_kind, pass_name, ProducerRun.FAILED, units,
            reason="pass_degraded", source=source,
        )  # fmt: skip
    if graph.extractor_passes.get(pass_name, False):
        if body_blind and edge_kind in _BODY_KINDS:
            return CoverageRecord(
                edge_kind, pass_name, ProducerRun.PARTIAL, units,
                covered=frozenset({HEADER_INLINE_BODIES}),
                reason="header_only_pass_body_blind", source=source,
            )  # fmt: skip
        if body_blind:
            # A header pass never produces this kind at all (a declaration's
            # exported symbol needs the binary / an L4 replay).
            return CoverageRecord(
                edge_kind, pass_name, ProducerRun.PARTIAL, units,
                covered=frozenset(), reason="header_only_pass_cannot_produce",
                source=source,
            )  # fmt: skip
        return CoverageRecord(
            edge_kind, pass_name, ProducerRun.RAN, units, source=source
        )
    if graph.narrowed_passes.get(pass_name, False):
        scope = graph.narrowed_scope.get(pass_name, frozenset())
        return CoverageRecord(
            edge_kind, pass_name, ProducerRun.PARTIAL, units,
            covered=frozenset() if body_blind else frozenset(scope),
            reason="pass_narrowed", source=source,
        )  # fmt: skip
    return None


def pass_coverage_records(
    graph: SourceGraphSummary, edge_kind: str
) -> tuple[CoverageRecord, ...]:
    """Coverage records of the passes that produce *edge_kind* on *graph*:
    the build-integrated pass and its header-only counterpart, each only when
    it left a flag. With neither on record (a hand-built or pre-flag graph),
    one ``not_run`` record -- "unknown whether it ran", never "ran and found
    nothing"."""
    pass_name = pass_for_edge_kind(edge_kind)
    if pass_name is None:
        return ()
    records = [
        rec
        for name, header in (
            (pass_name, False),
            (HEADER_PASS_ALIAS.get(pass_name), True),
        )
        if name and (rec := _record(graph, name, edge_kind, header=header)) is not None
    ]
    if not records:
        records.append(
            CoverageRecord(
                edge_kind,
                pass_name,
                ProducerRun.NOT_RUN,
                frozenset({ALL_UNITS}),
                reason="pass_not_recorded",
                source=f"SourceGraphSummary[{pass_name}]",
            )  # fmt: skip
        )
    return tuple(records)


def graph_records_passes(graph: SourceGraphSummary) -> bool:
    """Whether *graph* recorded any pass flag at all. An unflagged graph (a
    hand-built one, or one stored before its producers stamped coverage)
    cannot say whether a missing edge was looked for: every absence on it is
    ``unknown`` (``compare.edge_query.source_graph_covers``), never the
    legacy "an edge of this kind exists somewhere, so its absence elsewhere
    is real" reading."""
    return bool(
        graph.extractor_passes or graph.narrowed_passes or graph.degraded_passes
    )
