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
        such a caller)."""
        import dataclasses

        f = next(
            field for field in dataclasses.fields(Change) if field.name == "evidence_provenance"
        )
        assert f.kw_only is True
        all_names = [field.name for field in dataclasses.fields(Change)]
        assert all_names[-1] == "evidence_provenance", (
            "evidence_provenance must be the last-declared field on Change"
        )


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
    bucket, rather than after the gap is already there."""

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
        assert emitted, f"{mutation.__name__}: expected_kind {expected_kind.name} not emitted"
        stamped = [c for c in emitted if c.evidence_provenance is not None]
        assert not stamped, (
            f"{expected_kind.value} is classified PROVENANCE_UNVERIFIED in "
            "tests/evidence_provenance_contract.py, but its real producer "
            f"now sets evidence_provenance ({stamped[0].evidence_provenance!r}) "
            "-- move it to PROVENANCE_STATIC or PROVENANCE_PER_FINDING."
        )

    @pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
    def test_static_kinds_are_stamped_identically_on_every_finding(self, mutation) -> None:
        """PROVENANCE_STATIC's own definition is "a constant tuple, the
        same value for every instance of this kind" -- checked exhaustively
        (every emitted finding, not just one) and checked for value
        equality, not merely non-None (Codex review: an `any(...)` check
        would pass a producer that only migrated one of several call sites
        emitting this kind, or a "static" producer whose tuple actually
        varies -- the second round-trip test test_unverified_kinds_are_not_
        yet_actually_stamped already checks exhaustively for the other
        direction; this makes the STATIC/PER_FINDING direction match)."""
        old_extra, new_extra, expected_kind, _ = mutation(tag=1)
        if expected_kind.value not in PROVENANCE_STATIC:
            pytest.skip(f"{expected_kind.value} is not classified PROVENANCE_STATIC")
        old = build_snapshot("1.0", _CONTEXT, old_extra)
        new = build_snapshot("2.0", _CONTEXT, new_extra)
        emitted = [c for c in compare(old, new).changes if c.kind == expected_kind]
        assert emitted, f"{mutation.__name__}: expected_kind {expected_kind.name} not emitted"
        unstamped = [c for c in emitted if c.evidence_provenance is None]
        assert not unstamped, (
            f"{expected_kind.value} is classified PROVENANCE_STATIC, but "
            f"{len(unstamped)}/{len(emitted)} of its real emitted findings "
            "still leave evidence_provenance unset -- every construction "
            "path for this kind must be wired, not just one."
        )
        distinct_values = {c.evidence_provenance for c in emitted}
        assert len(distinct_values) == 1, (
            f"{expected_kind.value} is classified PROVENANCE_STATIC (one "
            "constant tuple for every instance), but its real producer(s) "
            f"emit {len(distinct_values)} distinct evidence_provenance "
            f"values across findings: {sorted(distinct_values)} -- either "
            "reclassify as PROVENANCE_PER_FINDING, or fix the producer."
        )

    @pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
    def test_per_finding_kinds_are_stamped_on_every_finding(self, mutation) -> None:
        old_extra, new_extra, expected_kind, _ = mutation(tag=1)
        if expected_kind.value not in PROVENANCE_PER_FINDING:
            pytest.skip(f"{expected_kind.value} is not classified PROVENANCE_PER_FINDING")
        old = build_snapshot("1.0", _CONTEXT, old_extra)
        new = build_snapshot("2.0", _CONTEXT, new_extra)
        emitted = [c for c in compare(old, new).changes if c.kind == expected_kind]
        assert emitted, f"{mutation.__name__}: expected_kind {expected_kind.name} not emitted"
        unstamped = [c for c in emitted if c.evidence_provenance is None]
        assert not unstamped, (
            f"{expected_kind.value} is classified PROVENANCE_PER_FINDING, "
            f"but {len(unstamped)}/{len(emitted)} of its real emitted "
            "findings still leave evidence_provenance unset -- either the "
            "classification is premature, or the producer's own wiring "
            "(for this specific construction path) is incomplete."
        )
