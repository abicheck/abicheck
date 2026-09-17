# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""An implicit depth is *satisfied*, not *unknown* -- and never overstated.

Two defects reported together against one real run (a byte-identical Linux
ELF C++ rebuild compared with headers and no ``--depth``):

1. ``requested_depth``/``depth_satisfied`` both read ``null`` beside
   ``status: complete``, so nothing in the block said which depth the run
   had actually been asked for. The identical run with an explicit
   ``--depth headers`` reported both correctly.
2. ``effective_depth`` read ``"source"`` while the L4 source-ABI row read
   ``not_collected`` -- the always-on, header-only L5 declaration graph was
   being described as a complete source-level analysis.

The bug class for (2) is a *gate* and a *report* answering the same
question with two different rules (``gated_source_label`` vs.
``depth_label_for``); the invariant below is stated over the whole cross
product of the two rules' inputs rather than over the one reported shape.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.evidence_depth import (
    depth_label_for,
    gated_source_label,
    reported_depth_label,
)
from abicheck.model import AbiSnapshot
from abicheck.model.source_graph import GraphNode, SourceGraphSummary


def _snapshot(*, from_headers: bool) -> AbiSnapshot:
    snap = AbiSnapshot("lib", "1.0")
    snap.from_headers = from_headers
    return snap


def _header_only_graph() -> SourceGraphSummary:
    """What ``service._attach_header_graph`` leaves behind: L5 nodes derived
    from parsed headers alone, with no source-tier replay anywhere near
    them."""
    graph = SourceGraphSummary()
    graph.add_node(GraphNode(id="decl:f", kind="declaration"))
    return graph


def _pack(tmp_path, *, graph: bool) -> BuildSourcePack:
    return BuildSourcePack(
        root=tmp_path, source_graph=_header_only_graph() if graph else None
    )


class TestReportedDepthNeverOutrunsTheGate:
    @pytest.mark.parametrize(
        ("from_headers", "graph", "build_context", "with_pack"),
        list(itertools.product((False, True), repeat=4)),
    )
    def test_reported_depth_equals_the_gates_answer(
        self,
        tmp_path,
        from_headers: bool,
        graph: bool,
        build_context: bool,
        with_pack: bool,
    ) -> None:
        """Exhaustive over the snapshot/pack shapes a real ELF+headers
        comparison produces. The report may never claim a rung the gate
        would refuse -- the contradiction the defect reported.

        Equality, deliberately, rather than "no higher than
        ``depth_label_for``": the gate's rule is not uniformly stricter. A
        zero-match source-only dump (replay parsed TUs, linked nothing
        because there is no binary to link against) leaves L4 and L5 both
        empty, so ``depth_label_for`` answers ``build`` while the gate
        correctly answers ``source``. One rule shared is the invariant; a
        one-sided bound would be satisfied by a second, quietly different
        one.
        """
        snap = _snapshot(from_headers=from_headers)
        snap.parsed_with_build_context = build_context
        pack = _pack(tmp_path, graph=graph) if with_pack else None
        assert reported_depth_label(snap, pack) == gated_source_label(pack, snap)

    def test_a_header_only_graph_is_not_source_depth(self, tmp_path) -> None:
        snap = _snapshot(from_headers=True)
        pack = _pack(tmp_path, graph=True)
        assert depth_label_for(snap, pack) == "source"
        assert reported_depth_label(snap, pack) == "headers"


class TestImplicitDepthIsNormalized:
    def test_no_depth_requested_reports_the_depth_reached(self) -> None:
        from abicheck.analysis_assurance import AnalysisAssurance

        block = AnalysisAssurance()
        assert block.requested_depth_source == "implicit"
        assert "requested_depth_source" in block.to_dict()

    @pytest.mark.parametrize("depth", ["binary", "headers", "build", "source"])
    def test_an_explicit_request_keeps_its_own_value(self, depth: str) -> None:
        """The normalization must never overwrite a stated request."""
        from abicheck.analysis_assurance import AnalysisAssurance

        block = AnalysisAssurance(
            requested_depth=depth, requested_depth_source="explicit"
        )
        assert block.to_dict()["requested_depth"] == depth
        assert block.to_dict()["requested_depth_source"] == "explicit"


class TestMergedRollUpNeverPresentsANormalizationAsARequest:
    @pytest.mark.parametrize(
        "sources",
        list(itertools.product(("explicit", "implicit"), repeat=2))
        + list(itertools.product(("explicit", "implicit"), repeat=3)),
    )
    def test_one_implicit_member_makes_the_release_implicit(
        self, sources: tuple[str, ...]
    ) -> None:
        from abicheck.analysis_assurance import AnalysisAssurance
        from abicheck.policy.analysis_assurance_merge import merge_analysis_assurance

        blocks = [
            AnalysisAssurance(
                requested_depth="headers", requested_depth_source=src, status="complete"
            )
            for src in sources
        ]
        merged = merge_analysis_assurance(blocks)
        assert merged is not None
        expected = "explicit" if all(s == "explicit" for s in sources) else "implicit"
        assert merged.requested_depth_source == expected


class TestPositionalConstructorStaysBackwardCompatible:
    """A new ``AnalysisAssurance`` field is appended, never inserted.

    ``schema_staleness_status``'s own comment already recorded this rule
    after a review caught the same mistake (PR #1209 round 10), and
    ``requested_depth_source`` was still first written next to
    ``depth_satisfied`` where it conceptually belongs -- silently rebinding
    every positional argument from ``target_accounting`` onward. A comment
    is evidently not enough, so this states it as an executable invariant
    over the whole field list rather than over the one field that moved.
    """

    def test_every_pre_existing_field_keeps_its_position(self) -> None:
        import dataclasses

        from abicheck.analysis_assurance import AnalysisAssurance

        names = [f.name for f in dataclasses.fields(AnalysisAssurance)]
        # The order this dataclass's generated positional constructor has
        # published. Appending is allowed (the tail check below); reordering
        # or inserting is not, whatever the new field is called.
        frozen_prefix = [
            "schema_version",
            "status",
            "requested_depth",
            "effective_depth",
            "depth_satisfied",
            "target_accounting",
            "translation_units",
            "export_accounting",
        ]
        assert names[: len(frozen_prefix)] == frozen_prefix
        for late_addition in ("schema_staleness_status", "requested_depth_source"):
            assert late_addition in names
            assert names.index(late_addition) >= len(frozen_prefix)

    def test_a_positional_caller_still_binds_the_fields_it_named(self) -> None:
        """The failure mode itself, not just the field order.

        Constructed positionally through the first five slots, exactly as an
        external caller predating either late addition would.
        """
        from abicheck.analysis_assurance import AnalysisAssurance

        block = AnalysisAssurance("1.0", "complete", "headers", "headers", True)
        assert block.schema_version == "1.0"
        assert block.status == "complete"
        assert block.requested_depth == "headers"
        assert block.effective_depth == "headers"
        assert block.depth_satisfied is True


class TestNotComparableKeepsTheRequestItWasGiven:
    """The short-circuit may not assert a provenance it never established.

    ``compute_analysis_assurance`` returns early when the two sides were not
    provably comparable, before any depth is resolved. With
    ``requested_depth_source`` defaulting to ``"implicit"``, that early
    return positively claimed no ``--depth`` was given -- a claim about the
    run that this path never checked, which is precisely the defect class
    this whole change set removes.
    """

    @staticmethod
    def _result(depth: str | None):
        from abicheck.checker_types import DiffResult

        result = DiffResult(old_version="1", new_version="2", library="libfoo.so")
        result.assurance = "none"
        result.requested_depth = depth
        return result

    @pytest.mark.parametrize("depth", ["binary", "headers", "build", "source"])
    def test_an_explicit_request_is_reported_as_explicit(self, depth: str) -> None:
        from abicheck.analysis_assurance import compute_analysis_assurance

        block = compute_analysis_assurance(
            self._result(depth),
            _snapshot(from_headers=True),
            _snapshot(from_headers=True),
        )
        assert block.status == "not_comparable"
        assert block.requested_depth == depth
        assert block.requested_depth_source == "explicit"
        # Nothing was evaluated against it, so satisfaction is unknown --
        # never the trivially-true value the normalized path uses.
        assert block.depth_satisfied is None

    def test_no_request_stays_implicit_and_unanswered(self) -> None:
        from abicheck.analysis_assurance import compute_analysis_assurance

        block = compute_analysis_assurance(
            self._result(None),
            _snapshot(from_headers=True),
            _snapshot(from_headers=True),
        )
        assert block.status == "not_comparable"
        assert block.requested_depth is None
        assert block.requested_depth_source == "implicit"
        assert block.depth_satisfied is None
