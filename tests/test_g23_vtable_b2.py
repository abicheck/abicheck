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

"""G23 Phase B2 — L1 DWARF vtable-group reconstruction detector."""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.diff_vtable_layout import _is_polymorphic, _secondary_groups
from abicheck.model import AbiSnapshot, RecordType


def _snap(*types: RecordType) -> AbiSnapshot:
    # Non-elf-only, with types → the vtable_layout detector is supported.
    return AbiSnapshot(library="lib.so", version="1", types=list(types))


def _poly(name: str, *, vtable=None, bases=None, virtual_bases=None) -> RecordType:
    return RecordType(
        name=name,
        kind="class",
        size_bits=64,
        vtable=vtable or [],
        bases=bases or [],
        virtual_bases=virtual_bases or [],
    )


def _kinds(r) -> set[ChangeKind]:
    return {c.kind for c in r.changes}


# ── secondary_vtable_group_changed ──────────────────────────────────────────


class TestSecondaryVtableGroup:
    def _hierarchy(self, b_is_poly: bool) -> AbiSnapshot:
        # class D : A, B  (A polymorphic primary; B polymorphic-or-not).
        a = _poly("A", vtable=["_ZN1A1fEv"])
        b = _poly("B", vtable=["_ZN1B1gEv"]) if b_is_poly else _poly("B")
        d = _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"])
        return _snap(a, b, d)

    def test_base_becomes_polymorphic_adds_group(self):
        # B gains a virtual → D now has a secondary vtable group for B, though
        # D's own base list ["A", "B"] is unchanged.
        old = self._hierarchy(b_is_poly=False)
        new = self._hierarchy(b_is_poly=True)
        r = compare(old, new)
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_base_loses_polymorphism_removes_group(self):
        old = self._hierarchy(b_is_poly=True)
        new = self._hierarchy(b_is_poly=False)
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED in _kinds(compare(old, new))

    def test_stable_hierarchy_not_flagged(self):
        old = self._hierarchy(b_is_poly=True)
        new = self._hierarchy(b_is_poly=True)
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED not in _kinds(compare(old, new))

    def test_primary_only_base_change_not_flagged(self):
        # Only the primary base A is polymorphic on both sides → no secondary
        # groups → nothing to report even if A's own vtable churns.
        old = _snap(_poly("A", vtable=["_ZN1A1fEv"]), _poly("B"),
                    _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"]))
        new = _snap(_poly("A", vtable=["_ZN1A1fEv", "_ZN1A1hEv"]), _poly("B"),
                    _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"]))
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED not in _kinds(compare(old, new))

    def test_indeterminate_base_skips_finding(self):
        # B is absent from the new snapshot → polymorphism indeterminate →
        # reconstruction returns None → no fabricated finding.
        old = self._hierarchy(b_is_poly=True)
        a = _poly("A", vtable=["_ZN1A1fEv"])
        d = _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"])
        new = _snap(a, d)  # no B
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED not in _kinds(compare(old, new))

    def test_moved_base_left_to_position_detector(self):
        # When the derived class's OWN base list reorders, the secondary-group
        # detector stays quiet (base_class_position_changed owns that case).
        old = _snap(_poly("A", vtable=["_ZN1A1fEv"]), _poly("B", vtable=["_ZN1B1gEv"]),
                    _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"]))
        new = _snap(_poly("A", vtable=["_ZN1A1fEv"]), _poly("B", vtable=["_ZN1B1gEv"]),
                    _poly("D", vtable=["_ZN1D1fEv"], bases=["B", "A"]))
        ks = _kinds(compare(old, new))
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED not in ks
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED in ks


# ── virtual_base_offset_changed ─────────────────────────────────────────────


class TestVirtualBaseOffset:
    def test_virtual_base_reorder_detected(self):
        # class D : virtual A, virtual B  →  virtual B, virtual A
        old = _snap(_poly("A"), _poly("B"),
                    _poly("D", vtable=["_ZN1D1fEv"], virtual_bases=["A", "B"]))
        new = _snap(_poly("A"), _poly("B"),
                    _poly("D", vtable=["_ZN1D1fEv"], virtual_bases=["B", "A"]))
        r = compare(old, new)
        assert ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_same_order_not_flagged(self):
        old = _snap(_poly("A"), _poly("B"),
                    _poly("D", virtual_bases=["A", "B"]))
        new = _snap(_poly("A"), _poly("B"),
                    _poly("D", virtual_bases=["A", "B"]))
        assert ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED not in _kinds(compare(old, new))

    def test_single_virtual_base_not_flagged(self):
        # Reorder needs ≥2 virtual bases; a single one has nothing to reorder.
        old = _snap(_poly("A"), _poly("D", virtual_bases=["A"]))
        new = _snap(_poly("A"), _poly("D", virtual_bases=["A"]))
        assert ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED not in _kinds(compare(old, new))

    def test_virtual_base_set_change_left_to_base_detectors(self):
        # Adding/removing a virtual base (set change) is not a pure reorder, so
        # this detector stays quiet and the base-set detectors handle it.
        old = _snap(_poly("A"), _poly("B"),
                    _poly("D", virtual_bases=["A", "B"]))
        new = _snap(_poly("A"), _poly("B"), _poly("C"),
                    _poly("D", virtual_bases=["A", "B", "C"]))
        assert ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED not in _kinds(compare(old, new))

    def test_stdlib_owner_reorder_not_flagged(self):
        # A virtual-base reorder inside a debug-only std:: record (not this
        # library's own ABI surface) must not surface as a BREAKING finding.
        old = _snap(_poly("A"), _poly("B"),
                    _poly("std::D", vtable=["_ZNSt1D1fEv"], virtual_bases=["A", "B"]))
        new = _snap(_poly("A"), _poly("B"),
                    _poly("std::D", vtable=["_ZNSt1D1fEv"], virtual_bases=["B", "A"]))
        assert ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED not in _kinds(compare(old, new))


# ── reconstruction helpers ──────────────────────────────────────────────────


class TestReconstruction:
    def test_polymorphic_via_own_vtable(self):
        types = {"A": _poly("A", vtable=["_ZN1A1fEv"])}
        assert _is_polymorphic("A", types, {}) is True

    def test_polymorphic_via_inheritance(self):
        types = {"A": _poly("A", vtable=["_ZN1A1fEv"]), "D": _poly("D", bases=["A"])}
        assert _is_polymorphic("D", types, {}) is True

    def test_non_polymorphic_leaf(self):
        types = {"P": _poly("P")}
        assert _is_polymorphic("P", types, {}) is False

    def test_unknown_type_is_indeterminate(self):
        assert _is_polymorphic("Missing", {}, {}) is None

    def test_inheritance_cycle_terminates(self):
        # Malformed A→B→A cycle, neither with a vtable: resolves to False, no hang.
        types = {"A": _poly("A", bases=["B"]), "B": _poly("B", bases=["A"])}
        assert _is_polymorphic("A", types, {}) is False

    def test_secondary_groups_primary_and_secondary(self):
        types = {
            "A": _poly("A", vtable=["_ZN1A1fEv"]),
            "B": _poly("B", vtable=["_ZN1B1gEv"]),
            "D": _poly("D", bases=["A", "B"]),
        }
        # A is primary, B is the one secondary group.
        assert _secondary_groups(types["D"], types, {}) == ["B"]

    def test_polymorphic_indeterminate_through_base_chain(self):
        # D → X → (missing): X's polymorphism is indeterminate, so D's is too.
        types = {"D": _poly("D", bases=["X"]), "X": _poly("X", bases=["Gone"])}
        assert _is_polymorphic("D", types, {}) is None

    def test_secondary_groups_indeterminate_direct_base(self):
        types = {"D": _poly("D", bases=["A"])}  # A missing
        assert _secondary_groups(types["D"], types, {}) is None

    def test_secondary_groups_indeterminate_virtual_base(self):
        # A concrete primary base, but a missing virtual base → indeterminate.
        types = {"A": _poly("A", vtable=["_ZN1A1fEv"]),
                 "D": _poly("D", bases=["A"], virtual_bases=["Gone"])}
        assert _secondary_groups(types["D"], types, {}) is None

    def test_secondary_groups_include_polymorphic_virtual_base(self):
        types = {
            "A": _poly("A", vtable=["_ZN1A1fEv"]),
            "V": _poly("V", vtable=["_ZN1V1gEv"]),
            "D": _poly("D", bases=["A"], virtual_bases=["V"]),
        }
        # A is primary (non-virtual), V is a polymorphic virtual base → secondary.
        assert _secondary_groups(types["D"], types, {}) == ["V"]
