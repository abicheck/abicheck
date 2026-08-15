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

"""Completeness contract for ``canonical_finding_id``.

`tests/test_canonical_finding_id.py` covers the kinds someone thought to write
a test for. This file covers *all* of them — it is the gate that would have
caught the #753 -> #759 escape, where three type-slot kinds were omitted from
``_TYPE_BEARING_DISCRIMINATOR_KINDS`` and nothing failed for hours because a
missing entry produces no failure anywhere.

The classification lives in `tests/canonical_identity_contract.py`; see its
docstring for why the judgement stays manual while the *exhaustiveness* is
enforced mechanically.
"""

from __future__ import annotations

import pytest
from canonical_identity_contract import (
    ALL_BUCKETS,
    TYPE_BEARING,
    UNVERIFIED,
    VALUE_INSENSITIVE,
)

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.finding_identity import report_canonical_finding_id

ALL_KIND_VALUES = frozenset(k.value for k in ChangeKind)

#: Two spellings of the identical type that different header backends really
#: do produce: CastXML writes `char const*`, direct-clang `char const *`.
SPELLING_A = "char const*"
SPELLING_B = "char const *"


def _kind(value: str) -> ChangeKind:
    return ChangeKind(value)


def _cid(kind: str, *, old: str, new: str, symbol: str = "sym", desc: str = "d") -> str:
    return report_canonical_finding_id(
        # Keywords: `Change`'s field order is public API, and a
        # positional call here would silently take a different meaning
        # if it ever changed (CodeRabbit review).
        Change(
            _kind(kind), symbol=symbol, description=desc, old_value=old, new_value=new
        )
    )


def _is_spelling_stable(kind: str) -> bool:
    """Do two backend-equivalent spellings of one change hash the same?"""
    return _cid(kind, old=SPELLING_A, new="int") == _cid(
        kind, old=SPELLING_B, new="int"
    )


# --- The partition -----------------------------------------------------------


class TestClassificationIsExhaustive:
    """A new ChangeKind must not be able to skip the identity question."""

    def test_every_change_kind_is_classified(self) -> None:
        classified = TYPE_BEARING | VALUE_INSENSITIVE | UNVERIFIED
        unclassified = ALL_KIND_VALUES - classified
        assert not unclassified, (
            "these ChangeKinds are in no identity bucket — add each to exactly "
            "one bucket in tests/canonical_identity_contract.py after reading "
            f"its emission call site: {sorted(unclassified)}"
        )

    def test_no_bucket_names_a_kind_that_does_not_exist(self) -> None:
        stale = (TYPE_BEARING | VALUE_INSENSITIVE | UNVERIFIED) - ALL_KIND_VALUES
        assert not stale, f"buckets name removed/renamed ChangeKinds: {sorted(stale)}"

    @pytest.mark.parametrize(
        "left, right",
        [
            ("TYPE_BEARING", "VALUE_INSENSITIVE"),
            ("TYPE_BEARING", "UNVERIFIED"),
            ("VALUE_INSENSITIVE", "UNVERIFIED"),
        ],
    )
    def test_buckets_are_disjoint(self, left: str, right: str) -> None:
        overlap = ALL_BUCKETS[left] & ALL_BUCKETS[right]
        assert not overlap, f"{left} and {right} both claim: {sorted(overlap)}"

    def test_partition_covers_exactly_the_enum(self) -> None:
        total = sum(len(b) for b in ALL_BUCKETS.values())
        assert total == len(ALL_KIND_VALUES) == len(ChangeKind)


# --- Each bucket's behaviour must match its claim -----------------------------


@pytest.mark.parametrize("kind", sorted(TYPE_BEARING))
def test_type_bearing_kinds_are_backend_spelling_stable(kind: str) -> None:
    """The promise the feature exists to make.

    A kind declared type-bearing must hash identically for two spellings of the
    same type, or a `finding_id:` suppression stops matching when the header
    backend changes — which is the whole failure `canonical_finding_id`
    prevents.
    """
    assert _is_spelling_stable(kind), (
        f"{kind} is declared TYPE_BEARING but its canonical id still varies "
        "with type spelling — it is listed but not actually canonicalized"
    )


@pytest.mark.parametrize("kind", sorted(VALUE_INSENSITIVE))
def test_value_insensitive_kinds_are_backend_spelling_stable(kind: str) -> None:
    """These are stable via `_EQUIVALENT_CHANGE_CATEGORIES`, not canonicalization.

    Pinned separately so that removing a kind from that category machinery
    surfaces here rather than as a silently backend-dependent suppression key.
    """
    assert _is_spelling_stable(kind), (
        f"{kind} was classified VALUE_INSENSITIVE but its identity now varies "
        "with value spelling — it left the equivalent-category path"
    )


@pytest.mark.parametrize("kind", sorted(UNVERIFIED))
def test_unverified_kinds_are_still_spelling_sensitive(kind: str) -> None:
    """The backlog may only shrink *deliberately*.

    If a kind here becomes spelling-stable — because someone added it to
    `_TYPE_BEARING_DISCRIMINATOR_KINDS` or changed the identity path — this
    fails and asks for it to be moved to the bucket that now describes it.
    Without this direction the backlog would rot into a list of claims nobody
    re-checks.
    """
    assert not _is_spelling_stable(kind), (
        f"{kind} is now backend-spelling-stable — move it out of UNVERIFIED "
        "into TYPE_BEARING (if it is canonicalized) or VALUE_INSENSITIVE (if "
        "it resolves through an equivalent category) in "
        "tests/canonical_identity_contract.py"
    )


# --- Non-collision: equal is not the only requirement -------------------------
#
# Backend-stability alone is satisfiable by hashing everything to a constant.
# These pin the other half: semantically distinct events must stay distinct.


@pytest.mark.parametrize("kind", sorted(TYPE_BEARING))
class TestTypeBearingKindsStayDistinct:
    def test_direction_is_preserved(self, kind: str) -> None:
        """`int -> long` and `long -> int` are different findings."""
        assert _cid(kind, old="int", new="long") != _cid(kind, old="long", new="int")

    def test_a_different_target_type_is_a_different_finding(self, kind: str) -> None:
        assert _cid(kind, old="int", new="long") != _cid(kind, old="int", new="short")

    def test_a_different_source_type_is_a_different_finding(self, kind: str) -> None:
        assert _cid(kind, old="int", new="long") != _cid(kind, old="short", new="long")

    def test_the_same_change_on_a_different_symbol_is_a_different_finding(
        self, kind: str
    ) -> None:
        assert _cid(kind, old="int", new="long", symbol="a") != _cid(
            kind, old="int", new="long", symbol="b"
        )

    def test_canonicalization_does_not_merge_a_pointer_with_its_pointee(
        self, kind: str
    ) -> None:
        """`const char` and `char const*` normalize to the same const ordering
        but are not the same type; collapsing them would hide a real change."""
        assert _cid(kind, old="const char", new="int") != _cid(
            kind, old="char const*", new="int"
        )


# --- The specific escape this file exists to prevent --------------------------


@pytest.mark.parametrize(
    "kind",
    ["atomic_qualifier_changed", "char8t_migration", "bit_int_width_changed"],
)
def test_the_three_kinds_pr_759_had_to_add_are_covered(kind: str) -> None:
    """Regression anchor for the escape that motivated this contract.

    These three shipped in #753 outside `_TYPE_BEARING_DISCRIMINATOR_KINDS` and
    were added in #759. The generalized tests above would now catch an
    equivalent omission on any kind; this keeps the original three named, so
    the story stays legible in the test output.
    """
    assert kind in TYPE_BEARING
    assert _is_spelling_stable(kind)
