# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Completeness contract for G39's ``Change.evidence_provenance`` (Phase 2).

Mirrors ``tests/test_canonical_finding_id_completeness.py`` exactly -- see
``tests/evidence_provenance_contract.py``'s own docstring for why the
classification stays manual while the exhaustiveness is enforced here.
"""

from __future__ import annotations

import pytest
from _detector_mutations import CTX_PREFIX, MUTATIONS, build_snapshot
from evidence_provenance_contract import (
    ALL_BUCKETS,
    PROVENANCE_PER_FINDING,
    PROVENANCE_STATIC,
    PROVENANCE_UNVERIFIED,
)

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model import Function, RecordType, Visibility

ALL_KIND_VALUES = frozenset(k.value for k in ChangeKind)

# Mirrors test_detector_oracle.py's own fixed, unrelated context.
_CONTEXT = {
    "functions": [
        Function(
            name=f"{CTX_PREFIX}keep",
            mangled=f"_Z{CTX_PREFIX}keepv",
            return_type="int",
            visibility=Visibility.PUBLIC,
        ),
    ],
    "types": [RecordType(name=f"{CTX_PREFIX}Keep", kind="struct", size_bits=64)],
}


class TestClassificationIsExhaustive:
    """A new ChangeKind must not be able to skip the provenance question."""

    def test_every_change_kind_is_classified(self) -> None:
        classified = PROVENANCE_STATIC | PROVENANCE_PER_FINDING | PROVENANCE_UNVERIFIED
        unclassified = ALL_KIND_VALUES - classified
        assert not unclassified, (
            "these ChangeKinds are in no evidence_provenance bucket — add "
            "each to exactly one bucket in tests/evidence_provenance_contract.py: "
            f"{sorted(unclassified)}"
        )

    def test_no_bucket_names_a_kind_that_does_not_exist(self) -> None:
        stale = (
            PROVENANCE_STATIC | PROVENANCE_PER_FINDING | PROVENANCE_UNVERIFIED
        ) - ALL_KIND_VALUES
        assert not stale, f"buckets name removed/renamed ChangeKinds: {sorted(stale)}"

    @pytest.mark.parametrize(
        "left, right",
        [
            ("PROVENANCE_STATIC", "PROVENANCE_PER_FINDING"),
            ("PROVENANCE_STATIC", "PROVENANCE_UNVERIFIED"),
            ("PROVENANCE_PER_FINDING", "PROVENANCE_UNVERIFIED"),
        ],
    )
    def test_buckets_are_disjoint(self, left: str, right: str) -> None:
        overlap = ALL_BUCKETS[left] & ALL_BUCKETS[right]
        assert not overlap, f"{left} and {right} both claim: {sorted(overlap)}"

    def test_partition_covers_exactly_the_enum(self) -> None:
        total = sum(len(b) for b in ALL_BUCKETS.values())
        assert total == len(ALL_KIND_VALUES) == len(ChangeKind)


class TestFieldDefaultsToNone:
    """Phase 0's own contract: a Change with no evidence_provenance set
    reads as "not yet computed", never as "computed, no provider" -- the
    two are distinguishable (None vs ()) and a plain construction must land
    on the former, matching every pre-Phase-1 call site."""

    def test_default_is_none_not_empty_tuple(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, symbol="sym", description="d")
        assert c.evidence_provenance is None

    def test_field_accepts_a_real_tuple(self) -> None:
        c = Change(
            ChangeKind.FUNC_REMOVED,
            symbol="sym",
            description="d",
            evidence_provenance=("l0:elf_symtab",),
        )
        assert c.evidence_provenance == ("l0:elf_symtab",)

    def test_field_accepts_the_explicit_no_provider_value(self) -> None:
        c = Change(
            ChangeKind.FUNC_REMOVED,
            symbol="sym",
            description="d",
            evidence_provenance=(),
        )
        assert c.evidence_provenance == ()

    def test_field_is_keyword_only_appended_last(self) -> None:
        """`Change` is public API -- a new optional field must be
        `field(kw_only=True)`, appended after every pre-existing field, so
        a caller passing later fields positionally (e.g.
        `compatibility_evaluation_status`/`compatibility_decision`) keeps
        landing on the same field it always did (Codex review: an earlier
        revision inserted this field positionally between
        `contract_evidence_refs` and `compatibility_evaluation_status`,
        which would have silently shifted every later field's position for
        such a caller). `entity_id` (ADR-063 Phase 2) is the newest such
        field, appended immediately after this one -- both must stay
        keyword-only, and `entity_id` must stay last until some still-newer
        field is appended after it in turn."""
        import dataclasses

        by_name = {f.name: f for f in dataclasses.fields(Change)}
        assert by_name["evidence_provenance"].kw_only is True
        assert by_name["entity_id"].kw_only is True
        all_names = [f.name for f in dataclasses.fields(Change)]
        assert all_names[-1] == "entity_id", (
            "entity_id must be the last-declared field on Change"
        )
        assert (
            all_names.index("entity_id") == all_names.index("evidence_provenance") + 1
        ), "entity_id must be appended immediately after evidence_provenance"


class TestVerifiedBucketsHaveProducerCoverage:
    """Codex review, fresh evidence: the parametrized tests below only run
    for a kind that already has a ``_detector_mutations.MUTATIONS`` entry
    -- they say nothing about a kind classified PROVENANCE_STATIC/
    PROVENANCE_PER_FINDING with *no* entry at all, which would silently
    receive zero real-producer verification while still passing every
    exhaustiveness test above (those only check bucket *membership*, not
    behavior). This requires the mutation catalogue's own coverage to
    keep pace with Phase 1's bucket reclassifications: a kind can't move
    out of PROVENANCE_UNVERIFIED without a mutation to actually verify it
    against. Vacuously true today (both verified buckets are still
    empty -- Phase 1 hasn't started), which is exactly the point: it
    starts enforcing the invariant from the first kind moved into either
    bucket, rather than after the gap is already there.

    Known, stated limit (Codex review, fresh evidence): "covered" here
    means *a* mutation-catalogue entry exists for the kind, not that
    *every* independent producer path for it does. A kind with two
    genuinely separate emitters -- e.g. FUNC_REMOVED's function-model
    path in diff_symbols.py alongside a PE/Mach-O export-delta path in
    diff_platform.py -- could be reclassified on the strength of only
    one path's mutation, leaving the other silently unverified while
    this gate, `TestClassificationTracksRealProducerBehavior`, and every
    exhaustiveness test above all report green. Closing that needs a
    real per-kind producer-path inventory (which call sites in which
    diff_*.py modules can emit each kind) that does not exist anywhere
    in this codebase today -- `changekind-detector`
    (`scripts/check_ai_readiness.py`) only answers "is this kind
    produced at all", the same single-path blind spot. Building that
    inventory is Phase 1 infrastructure in its own right, not a Phase
    0/2 completeness-gate fix, and doing it before any real producer
    exists to validate its shape against risks guessing wrong. Until
    then: a Phase 1 PR reclassifying a kind with more than one known
    producer path must manually confirm every path is covered, not just
    the one its own mutation catalogue entry happens to exercise --
    review discipline stands in for the missing mechanical check."""

    def test_every_verified_kind_has_a_mutation_catalogue_entry(self) -> None:
        covered = {mutation(tag=1)[2].value for mutation in MUTATIONS}
        verified = PROVENANCE_STATIC | PROVENANCE_PER_FINDING
        uncovered = verified - covered
        assert not uncovered, (
            "these kinds are classified PROVENANCE_STATIC/PROVENANCE_PER_FINDING "
            "but have no tests/_detector_mutations.py MUTATIONS entry, so "
            "TestClassificationTracksRealProducerBehavior never actually "
            "verifies their real producer -- add a mutation-catalogue entry "
            f"before reclassifying out of PROVENANCE_UNVERIFIED: {sorted(uncovered)}"
        )


class TestClassificationTracksRealProducerBehavior:
    """Codex review: the exhaustiveness tests above only prove every enum
    value is in *some* bucket -- they say nothing about whether a kind's
    real producer actually behaves the way its bucket claims. A Phase 1
    change that starts setting `evidence_provenance` for a kind but forgets
    to move it out of `PROVENANCE_UNVERIFIED` (the exact class of bug
    #753 -> #759 already taught this codebase to guard against) would pass
    every test above silently.

    Ties each mutation in `_detector_mutations.MUTATIONS` (the same
    known-edit catalogue `test_detector_oracle.py` uses) to its
    `expected_kind`'s bucket: a `PROVENANCE_UNVERIFIED` kind's real,
    end-to-end emitted `Change` must still carry `evidence_provenance is
    None` (nothing has wired it yet); a `PROVENANCE_STATIC`/
    `PROVENANCE_PER_FINDING` kind's must not (something has). Only 16 of
    397 kinds have a mutation-catalogue entry today, so this is partial
    coverage, not the completeness tests' full-enum guarantee -- but for
    every kind it does cover, a producer silently drifting from its
    declared bucket fails here instead of nowhere.
    """

    @pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
    def test_unverified_kinds_are_not_yet_actually_stamped(self, mutation) -> None:
        old_extra, new_extra, expected_kind, _ = mutation(tag=1)
        if expected_kind.value not in PROVENANCE_UNVERIFIED:
            pytest.skip(f"{expected_kind.value} is not classified UNVERIFIED")
        old = build_snapshot("1.0", _CONTEXT, old_extra)
        new = build_snapshot("2.0", _CONTEXT, new_extra)
        emitted = [c for c in compare(old, new).changes if c.kind == expected_kind]
        assert emitted, (
            f"{mutation.__name__}: expected_kind {expected_kind.name} not emitted"
        )
        stamped = [c for c in emitted if c.evidence_provenance is not None]
        assert not stamped, (
            f"{expected_kind.value} is classified PROVENANCE_UNVERIFIED in "
            "tests/evidence_provenance_contract.py, but its real producer "
            f"now sets evidence_provenance ({stamped[0].evidence_provenance!r}) "
            "-- move it to PROVENANCE_STATIC or PROVENANCE_PER_FINDING."
        )

    @staticmethod
    def _emitted_for_kind(kind_value: str) -> list[Change]:
        """Runs every mutation catalogued for ``kind_value`` and returns the
        union of real emitted findings across all of them -- not just one
        mutation's own output. A kind can have several independent producer
        paths (several `MUTATIONS` entries sharing one `expected_kind`), and
        `PROVENANCE_STATIC`'s "one constant value for every instance" claim
        is a claim about the kind as a whole, not about any single
        construction path in isolation (Codex review, fresh evidence: the
        prior per-mutation-only checks below let path A always emit one
        tuple and path B a different tuple while every parametrized case
        still passed, since no case ever saw both)."""
        emitted: list[Change] = []
        for mutation in MUTATIONS:
            old_extra, new_extra, expected_kind, _ = mutation(tag=1)
            if expected_kind.value != kind_value:
                continue
            old = build_snapshot("1.0", _CONTEXT, old_extra)
            new = build_snapshot("2.0", _CONTEXT, new_extra)
            found = [c for c in compare(old, new).changes if c.kind == expected_kind]
            assert found, (
                f"{mutation.__name__}: expected_kind {expected_kind.name} not emitted"
            )
            emitted.extend(found)
        return emitted

    @pytest.mark.parametrize("kind_value", sorted(PROVENANCE_STATIC))
    def test_static_kinds_are_stamped_identically_on_every_finding(
        self, kind_value: str
    ) -> None:
        """PROVENANCE_STATIC's own definition is "a constant tuple, the
        same value for every instance of this kind" -- checked exhaustively
        across *every* mutation catalogued for this kind (not just one
        construction path) and checked for value equality, not merely
        non-None (Codex review: an `any(...)` check would pass a producer
        that only migrated one of several call sites emitting this kind, or
        a "static" producer whose tuple actually varies -- the second
        round-trip test test_unverified_kinds_are_not_yet_actually_stamped
        already checks exhaustively for the other direction; this makes the
        STATIC/PER_FINDING direction match, and aggregating across every
        `MUTATIONS` entry for the kind -- not just one -- is what catches a
        second producer path emitting a differing tuple)."""
        emitted = self._emitted_for_kind(kind_value)
        unstamped = [c for c in emitted if c.evidence_provenance is None]
        assert not unstamped, (
            f"{kind_value} is classified PROVENANCE_STATIC, but "
            f"{len(unstamped)}/{len(emitted)} of its real emitted findings "
            "still leave evidence_provenance unset -- every construction "
            "path for this kind must be wired, not just one."
        )
        distinct_values = {c.evidence_provenance for c in emitted}
        assert len(distinct_values) == 1, (
            f"{kind_value} is classified PROVENANCE_STATIC (one "
            "constant tuple for every instance), but its real producer(s) "
            f"emit {len(distinct_values)} distinct evidence_provenance "
            f"values across findings: {sorted(distinct_values)} -- either "
            "reclassify as PROVENANCE_PER_FINDING, or fix the producer."
        )

    @pytest.mark.parametrize("kind_value", sorted(PROVENANCE_PER_FINDING))
    def test_per_finding_kinds_are_stamped_on_every_finding(
        self, kind_value: str
    ) -> None:
        """Also checks PROVENANCE_PER_FINDING is not behaviorally
        indistinguishable from PROVENANCE_STATIC (Codex review, fresh
        evidence): a producer that stamps the same constant tuple on
        every finding it emits would still pass a mere non-None check.

        The variation check is deliberately scoped to *across independent
        mutations* for the kind, not within one mutation's own findings
        (Codex review, fresh evidence, second round): two findings from the
        *same* mutation can legitimately compute the identical tuple --
        e.g. two layout findings both corroborated by the same DWARF
        evidence -- so requiring inequality within one mutation's output
        would reject a correct per-finding producer purely because its
        inputs happened to coincide. Two *different* mutations exercise
        different symbols/context by construction (`_detector_mutations.py`
        prefixes every mutation's target identifiers uniquely), so a
        real per-finding producer varying its computation with its input is
        overwhelmingly expected to differ across them, while a silently
        constant producer never will -- this is the signal that actually
        distinguishes the two, not raw cardinality of one mutation's
        output. Still partial coverage, stated honestly: a kind covered by
        only one mutation can't be checked this way at all (the loop below
        no-ops for it), which is exactly why this docstring doesn't claim a
        stronger guarantee than the test actually gives.

        Known, stated residual (Codex review, fresh evidence, third
        round): "overwhelmingly expected to differ" is not "guaranteed
        to differ" -- two independent mutations for a genuinely
        PROVENANCE_PER_FINDING kind can still legitimately compute the
        identical tuple (e.g. both findings corroborated by the same
        single evidence provider), which this check cannot distinguish
        from a producer that is silently constant. A fully sound version
        needs each MUTATIONS entry to state its own *expected*
        evidence_provenance value and compare against that directly,
        rather than inferring per-instance-ness from cross-mutation
        inequality -- deliberately not built here, since authoring
        expected-value fixtures ahead of any real Phase 1 producer would
        mean guessing at a shape nothing has committed to yet. This
        assertion is therefore a heuristic, kept because it still catches
        the common, real failure mode (a producer that never varies at
        all) at the cost of a narrow false-positive risk on a coincidence
        Phase 1 will need to watch for by hand: if this assertion ever
        rejects a producer that is genuinely per-finding but happened to
        coincide across its catalogued mutations, the fix is to add a
        mutation exercising a case where the two providers differ for
        real, not to weaken or remove this check."""
        emitted = self._emitted_for_kind(kind_value)
        unstamped = [c for c in emitted if c.evidence_provenance is None]
        assert not unstamped, (
            f"{kind_value} is classified PROVENANCE_PER_FINDING, "
            f"but {len(unstamped)}/{len(emitted)} of its real emitted "
            "findings still leave evidence_provenance unset -- either the "
            "classification is premature, or the producer's own wiring "
            "(for this specific construction path) is incomplete."
        )
        mutations_for_kind = sum(
            1 for mutation in MUTATIONS if mutation(tag=1)[2].value == kind_value
        )
        if mutations_for_kind > 1:
            distinct_values = {c.evidence_provenance for c in emitted}
            assert len(distinct_values) > 1, (
                f"{kind_value} is classified PROVENANCE_PER_FINDING "
                "(a value computed per instance, not a detector-wide "
                "constant), but its real producer emits the identical "
                f"evidence_provenance value {emitted[0].evidence_provenance!r} "
                f"across all {mutations_for_kind} independently-catalogued "
                "mutations for this kind -- either reclassify as "
                "PROVENANCE_STATIC, or fix the producer to actually vary "
                "with its input."
            )


class TestProvenanceTagsAreRegistered:
    """G39 Phase 0's own contract: the string vocabulary needs a single
    code-level owner (``model.vocabulary.EVIDENCE_PROVENANCE_TAGS``), not
    just the plan's own prose table -- so a typo'd or independently-
    invented tag at a new Phase 1 call site fails here instead of
    silently shipping as an unrecognized value no consumer can key off
    (Codex review, PR #900)."""

    @pytest.mark.parametrize(
        "kind_value", sorted(PROVENANCE_STATIC | PROVENANCE_PER_FINDING)
    )
    def test_every_stamped_tag_is_registered(self, kind_value: str) -> None:
        from abicheck.model.vocabulary import EVIDENCE_PROVENANCE_TAGS

        emitted = TestClassificationTracksRealProducerBehavior._emitted_for_kind(
            kind_value
        )
        unregistered = {
            tag
            for c in emitted
            if c.evidence_provenance is not None
            for tag in c.evidence_provenance
        } - EVIDENCE_PROVENANCE_TAGS
        assert not unregistered, (
            f"{kind_value}'s real producer emits evidence_provenance "
            f"tag(s) not in model.vocabulary.EVIDENCE_PROVENANCE_TAGS: "
            f"{sorted(unregistered)} -- register the tag there before "
            "a detector call site stamps it."
        )

    @pytest.mark.parametrize(
        "kind_value", sorted(PROVENANCE_STATIC | PROVENANCE_PER_FINDING)
    )
    def test_every_stamped_tuple_is_normalized(self, kind_value: str) -> None:
        """G39 Phase 0's own normalization rule: every non-``None``
        ``evidence_provenance`` MUST be ``tuple(sorted(set(entries)))`` --
        deduplicated and lexicographically sorted -- checked here rather
        than only stated as a docstring rule, so a construction site or
        roll-up that unions two tuples without normalizing (e.g. a
        duplicate entry, or two entries out of order) fails this gate
        instead of shipping unstable JSON/SARIF/JUnit output once Phase 3
        projects this field (Codex review, PR #900)."""
        emitted = TestClassificationTracksRealProducerBehavior._emitted_for_kind(
            kind_value
        )
        for c in emitted:
            if c.evidence_provenance is None:
                continue
            normalized = tuple(sorted(set(c.evidence_provenance)))
            assert c.evidence_provenance == normalized, (
                f"{kind_value}'s real producer emitted a non-normalized "
                f"evidence_provenance {c.evidence_provenance!r} -- every "
                "constructor must return tuple(sorted(set(entries))), "
                f"expected {normalized!r}."
            )
