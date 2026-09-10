# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``ReportEnvelope.findings_for`` as a primitive, stated as invariants.

Root ``AGENTS.md``'s "Primitive-level property tests": a reusable lookup/merge
helper gets a small standalone class stating its *contract*, decoupled from any
one caller's domain logic, rather than only the example-shaped tests its first
caller happens to need. ``findings_for`` is exactly that shape -- a
change-sequence-to-finding join over an id-keyed index with a fallback for
objects the index cannot contain (the shallow ``Change`` copies
``report_correlation._suppress_dangling_correlation_notes`` hands a renderer).
The acceptance test for the envelope *as a whole* lives with the other
renderer-convergence tests, in ``test_build_report_document.py``'s
``TestRendererOrderIndependence``; this file is the narrower sibling.
"""

from __future__ import annotations

import copy
import itertools

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult
from abicheck.model import AbiSnapshot
from abicheck.report.build import build_report_envelope
from abicheck.report.envelope import RenderOptions, ReportEnvelope

_KINDS = (
    ChangeKind.FUNC_REMOVED,
    ChangeKind.FUNC_ADDED,
    ChangeKind.VISIBILITY_LEAK,
    ChangeKind.TYPE_SIZE_CHANGED,
)


def _changes(n: int) -> list[Change]:
    return [
        Change(_KINDS[i % len(_KINDS)], f"_Z3sym{i}v", f"detail {i}") for i in range(n)
    ]


def _envelope(
    changes: list[Change], scoped_only: list[Change] | None = None
) -> ReportEnvelope:
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=list(changes),
        policy="strict_abi",
    )
    if scoped_only is not None:
        result.scoped_only_changes = list(scoped_only)  # type: ignore[attr-defined]
    snap = AbiSnapshot(library="libtest.so.1", version="1.0")
    return build_report_envelope(result, snap, snap, options=RenderOptions())


class TestFindingsForProperties:
    """Invariants that must hold for *any* change sequence, not one example."""

    @pytest.mark.parametrize("size", [0, 1, 2, 5, 9])
    def test_one_finding_per_input_change_in_input_order(self, size: int) -> None:
        changes = _changes(size)
        env = _envelope(changes)
        findings = env.findings_for(changes)
        assert [f.change for f in findings] == changes

    @pytest.mark.parametrize("size", [1, 2, 4])
    def test_result_is_a_permutation_when_the_input_is(self, size: int) -> None:
        """No ordering, grouping, or caching effect may leak between calls: a
        permuted query returns the identically permuted answer."""
        changes = _changes(size)
        env = _envelope(changes)
        base = {
            id(f.change): (f.verdict, f.category) for f in env.findings_for(changes)
        }
        for order in itertools.permutations(changes):
            got = env.findings_for(list(order))
            assert [(f.verdict, f.category) for f in got] == [
                base[id(c)] for c in order
            ]

    def test_a_subset_query_never_invents_or_drops_a_finding(self) -> None:
        changes = _changes(6)
        env = _envelope(changes)
        for keep in ([], changes[:1], changes[2:5], changes):
            assert [f.change for f in env.findings_for(keep)] == keep

    def test_a_known_change_is_read_from_the_envelope_not_re_resolved(self) -> None:
        """The whole point of the envelope: a change the completed evaluation
        already resolved is looked up, never decided a second time."""
        changes = _changes(4)
        env = _envelope(changes)
        first = env.findings_for(changes)
        second = env.findings_for(changes)
        # Identity, not equality: a re-resolution would build new objects.
        assert [id(f) for f in first] == [id(f) for f in second]
        assert all(a is b for a, b in zip(first, second, strict=True))

    def test_a_display_layer_copy_resolves_to_its_original_s_classification(
        self,
    ) -> None:
        """A ``Change`` the display layer copied (dangling-correlation
        suppression under ``--show-only``) has no index entry, so it takes the
        fallback -- which must reach the same verdict/category as the original,
        through the same policy inputs, for every kind.
        """
        changes = _changes(len(_KINDS))
        env = _envelope(changes)
        originals = {id(f.change): f for f in env.findings_for(changes)}
        copies = [copy.copy(c) for c in changes]
        assert all(id(c) not in originals for c in copies)

        resolved = env.findings_for(copies)
        assert [f.change for f in resolved] == copies
        for original, got in zip(changes, resolved, strict=True):
            assert (got.verdict, got.category) == (
                originals[id(original)].verdict,
                originals[id(original)].category,
            )

    def test_a_mixed_known_and_copied_sequence_keeps_both_kinds_in_order(self) -> None:
        changes = _changes(4)
        env = _envelope(changes)
        mixed = [changes[0], copy.copy(changes[1]), changes[2], copy.copy(changes[3])]
        assert [f.change for f in env.findings_for(mixed)] == mixed

    def test_scoped_only_changes_are_resolved_by_the_envelope_too(self) -> None:
        """JUnit folds ``scoped_only_changes`` into its own testcase tree, so
        the envelope resolves them alongside ``result.changes`` -- otherwise
        every one of them would take the fallback path."""
        changes = _changes(3)
        scoped_only = _changes(2)
        env = _envelope(changes, scoped_only=scoped_only)
        assert [f.change for f in env.scoped_only_findings] == scoped_only

        combined = [*changes, *scoped_only]
        resolved = env.findings_for(combined)
        assert [f.change for f in resolved] == combined
        known = {id(f.change): f for f in (*env.findings, *env.scoped_only_findings)}
        assert all(f is known[id(f.change)] for f in resolved)

    def test_an_empty_envelope_answers_an_empty_query(self) -> None:
        env = _envelope([])
        assert env.findings == ()
        assert env.findings_for([]) == ()
