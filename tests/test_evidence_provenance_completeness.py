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
from evidence_provenance_contract import (
    ALL_BUCKETS,
    PROVENANCE_PER_FINDING,
    PROVENANCE_STATIC,
    PROVENANCE_UNVERIFIED,
)

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change

ALL_KIND_VALUES = frozenset(k.value for k in ChangeKind)


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
