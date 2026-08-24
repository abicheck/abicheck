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

"""Old/new type matching must not pair two *distinct* qualified identities.

``diff_helpers.lookup_matched_type`` is the one primitive every RecordType/
EnumType detector matches through, so it gets the "Primitive-level property
tests" treatment CLAUDE.md prescribes: its contract stated as invariants over
generated inputs, decoupled from any one detector's domain logic, alongside the
concrete oneTBB-shaped regression that motivated it (``tbb::detail::d1::graph``
vs. ``tbb::detail::d2::graph`` — same bare leaf, different namespace, different
mangled vtable symbol, and emphatically not the same type).
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.diff_helpers import build_type_map, lookup_matched_type
from abicheck.model import AbiSnapshot, EnumType, RecordType, TypeField


def _rec(
    bare: str,
    qualified: str | None = None,
    *,
    size_bits: int = 64,
    fields: list[TypeField] | None = None,
) -> RecordType:
    return RecordType(
        name=bare,
        kind="class",
        qualified_name=qualified,
        size_bits=size_bits,
        fields=fields or [],
    )


class TestDistinctNamespacesNeverMatch:
    """(a) Two distinct types in different namespaces sharing a bare name."""

    def test_onetbb_d1_to_d2_graph_is_not_a_match(self) -> None:
        old = _rec("graph", "tbb::detail::d1::graph", size_bits=64)
        new = _rec("graph", "tbb::detail::d2::graph", size_bits=128)
        old_map, new_map = build_type_map([old]), build_type_map([new])
        assert lookup_matched_type(old_map, new_map, old) is None
        assert lookup_matched_type(new_map, old_map, new) is None

    def test_namespace_move_reports_removal_and_addition_not_a_mutation(self) -> None:
        """End-to-end: the phantom mutation findings are gone, and the truth
        (one type removed, one added) is what is reported instead."""
        old_snap = AbiSnapshot(
            library="libtbb.so",
            version="2021",
            types=[
                _rec(
                    "graph",
                    "tbb::detail::d1::graph",
                    size_bits=64,
                    fields=[TypeField(name="my_root_task", type="int*", offset_bits=0)],
                ),
                # A second, unrelated type so the old side is not a single-entry
                # map (which would make the assertion trivially true).
                _rec("task_group", "tbb::detail::d1::task_group", size_bits=32),
            ],
        )
        new_snap = AbiSnapshot(
            library="libtbb.so",
            version="2022",
            types=[
                _rec(
                    "graph",
                    "tbb::detail::d2::graph",
                    size_bits=192,
                    fields=[
                        TypeField(name="my_context", type="int*", offset_bits=0),
                        TypeField(name="my_root_task", type="int*", offset_bits=64),
                    ],
                ),
                _rec("task_group", "tbb::detail::d1::task_group", size_bits=32),
            ],
        )
        kinds = {c.kind for c in compare(old_snap, new_snap).changes}
        assert ChangeKind.TYPE_SIZE_CHANGED not in kinds
        assert ChangeKind.TYPE_FIELD_OFFSET_CHANGED not in kinds
        assert ChangeKind.TYPE_FIELD_ADDED not in kinds
        assert ChangeKind.TYPE_REMOVED in kinds
        assert ChangeKind.TYPE_ADDED in kinds

    def test_enums_share_the_same_matching_primitive(self) -> None:
        old = EnumType(name="reduction", qualified_name="ccl::v1::reduction")
        new = EnumType(name="reduction", qualified_name="ccl::v2::reduction")
        old_map, new_map = build_type_map([old]), build_type_map([new])
        assert lookup_matched_type(old_map, new_map, old) is None


class TestGenuineMutationsStillMatch:
    """(b) A same-namespace mutation is still detected."""

    def test_same_qualified_identity_matches(self) -> None:
        old = _rec("graph", "tbb::detail::d1::graph", size_bits=64)
        new = _rec("graph", "tbb::detail::d1::graph", size_bits=128)
        assert (
            lookup_matched_type(build_type_map([old]), build_type_map([new]), old)
            is new
        )

    def test_same_namespace_size_change_is_reported(self) -> None:
        old_snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[_rec("graph", "tbb::detail::d1::graph", size_bits=64)],
        )
        new_snap = AbiSnapshot(
            library="lib.so",
            version="2",
            types=[_rec("graph", "tbb::detail::d1::graph", size_bits=128)],
        )
        kinds = {c.kind for c in compare(old_snap, new_snap).changes}
        assert ChangeKind.TYPE_SIZE_CHANGED in kinds


class TestLegacySchemaCompatibilityIsPreserved:
    """The bare-name alias exists for one reason only — a side that never
    recorded ``qualified_name`` — and that reason still works, in both
    directions."""

    def test_new_side_is_legacy(self) -> None:
        old = _rec("Foo", "ns::Foo")
        new = _rec("Foo", None, size_bits=128)
        assert (
            lookup_matched_type(build_type_map([old]), build_type_map([new]), old)
            is new
        )

    def test_old_side_is_legacy(self) -> None:
        old = _rec("Foo", None)
        new = _rec("Foo", "ns::Foo", size_bits=128)
        assert (
            lookup_matched_type(build_type_map([old]), build_type_map([new]), old)
            is new
        )

    def test_legacy_side_with_two_same_leaf_types_stays_ambiguous(self) -> None:
        old_a = _rec("Foo", "ns1::Foo")
        old_b = _rec("Foo", "ns2::Foo")
        new = _rec("Foo", None)
        own = build_type_map([old_a, old_b])
        other = build_type_map([new])
        assert lookup_matched_type(own, other, old_a) is None
        assert lookup_matched_type(own, other, old_b) is None


# ── Property-level contract of the matching primitive ─────────────────────

_NS = st.sampled_from(["", "a", "b", "a::b", "x::y::z", "tbb::detail::d1"])
_LEAF = st.sampled_from(["Foo", "graph", "Impl", "value_type"])


def _qualified(ns: str, leaf: str) -> str | None:
    return f"{ns}::{leaf}" if ns else None


@settings(max_examples=250, deadline=None)
@given(ns_old=_NS, ns_new=_NS, leaf=_LEAF)
def test_a_match_never_crosses_two_recorded_qualified_identities(
    ns_old: str, ns_new: str, leaf: str
) -> None:
    """The invariant: when *both* sides recorded a qualified identity, a match
    implies those identities are equal. A bare-name coincidence is never
    sufficient — which is exactly what the oneTBB namespace move exposed."""
    old = _rec(leaf, _qualified(ns_old, leaf))
    new = _rec(leaf, _qualified(ns_new, leaf))
    matched = lookup_matched_type(build_type_map([old]), build_type_map([new]), old)
    if matched is not None and old.qualified_name and new.qualified_name:
        assert old.qualified_name == new.qualified_name


@settings(max_examples=250, deadline=None)
@given(ns_old=_NS, ns_new=_NS, leaf=_LEAF)
def test_matching_is_symmetric(ns_old: str, ns_new: str, leaf: str) -> None:
    """Probing old->new and new->old must agree: half the detectors walk the
    old map and half walk the new one (``diff_layout`` probes from the new
    side), so an asymmetric answer would make one detector see a mutation
    where its sibling sees an add/remove pair."""
    old = _rec(leaf, _qualified(ns_old, leaf))
    new = _rec(leaf, _qualified(ns_new, leaf))
    old_map, new_map = build_type_map([old]), build_type_map([new])
    forward = lookup_matched_type(old_map, new_map, old) is not None
    backward = lookup_matched_type(new_map, old_map, new) is not None
    assert forward == backward


@settings(max_examples=200, deadline=None)
@given(
    leaf=_LEAF,
    namespaces=st.lists(_NS, min_size=1, max_size=4, unique=True),
)
def test_identical_snapshots_always_match_every_type_to_itself(
    leaf: str, namespaces: list[str]
) -> None:
    """Reflexivity: comparing a snapshot's type set against itself must pair
    every type with its own counterpart, however many share a bare leaf."""
    types = [_rec(leaf, _qualified(ns, leaf)) for ns in namespaces]
    own = build_type_map(types)
    other = build_type_map([_rec(t.name, t.qualified_name) for t in types])
    for t in own.values():
        matched = lookup_matched_type(own, other, t)
        assert matched is not None
        assert (matched.qualified_name or matched.name) == (t.qualified_name or t.name)
