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
from abicheck.diff_types_vtable import _virtual_signatures_by_owner
from abicheck.diff_vtable_layout import _is_polymorphic, _secondary_groups
from abicheck.model import AbiSnapshot, Fact, Function, RecordType


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
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED not in _kinds(
            compare(old, new)
        )

    def test_legacy_unreliable_snapshot_suppresses_the_finding(self):
        # Codex review, this slice: a whole-snapshot clang_vtable_facts_
        # reliable=False (the legacy pre-v21 direct-clang shape) must
        # suppress the finding even though the per-record vtable_fact
        # reads confirmed (this test constructs it that way on purpose --
        # a real legacy load always keeps the two in sync via storage.
        # fact_backfill, but this defense-in-depth check does not assume a
        # caller does).
        old = self._hierarchy(b_is_poly=False)
        old.clang_vtable_facts_reliable = False
        new = self._hierarchy(b_is_poly=True)
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED not in _kinds(
            compare(old, new)
        )

    def test_primary_only_base_change_not_flagged(self):
        # Only the primary base A is polymorphic on both sides → no secondary
        # groups → nothing to report even if A's own vtable churns.
        old = _snap(
            _poly("A", vtable=["_ZN1A1fEv"]),
            _poly("B"),
            _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"]),
        )
        new = _snap(
            _poly("A", vtable=["_ZN1A1fEv", "_ZN1A1hEv"]),
            _poly("B"),
            _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"]),
        )
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED not in _kinds(
            compare(old, new)
        )

    def test_indeterminate_base_skips_finding(self):
        # B is absent from the new snapshot → polymorphism indeterminate →
        # reconstruction returns None → no fabricated finding.
        old = self._hierarchy(b_is_poly=True)
        a = _poly("A", vtable=["_ZN1A1fEv"])
        d = _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"])
        new = _snap(a, d)  # no B
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED not in _kinds(
            compare(old, new)
        )

    def test_moved_base_left_to_position_detector(self):
        # When the derived class's OWN base list reorders, the secondary-group
        # detector stays quiet (base_class_position_changed owns that case).
        old = _snap(
            _poly("A", vtable=["_ZN1A1fEv"]),
            _poly("B", vtable=["_ZN1B1gEv"]),
            _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"]),
        )
        new = _snap(
            _poly("A", vtable=["_ZN1A1fEv"]),
            _poly("B", vtable=["_ZN1B1gEv"]),
            _poly("D", vtable=["_ZN1D1fEv"], bases=["B", "A"]),
        )
        ks = _kinds(compare(old, new))
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED not in ks
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED in ks

    def test_retained_virtual_function_evidence_reaches_the_real_detector(self):
        # End-to-end (Codex review, fresh evidence, fifth round): B's own
        # vtable_fact is uncollected on the old side (the legacy
        # direct-clang shape), but a retained Function with is_virtual=True
        # still proves B was polymorphic -- so losing that virtual (and the
        # function) on the new side is a real SECONDARY_VTABLE_GROUP_CHANGED,
        # not a silently-dropped indeterminate base. clang_vtable_facts_
        # reliable=False on the old side (Codex review, eighth round) is
        # this path's actual scope -- the legacy pre-v21 direct-clang shape
        # its own docstring names, where a retained Function is guaranteed
        # header-AST-sourced rather than DWARF-sourced.
        a = _poly("A", vtable=["_ZN1A1fEv"])
        b_old = RecordType(
            name="B", kind="class", size_bits=64, vtable_fact=Fact.not_collected()
        )
        b_new = RecordType(name="B", kind="class", size_bits=64, vtable=[])
        d = _poly("D", vtable=["_ZN1D1fEv"], bases=["A", "B"])
        b_method = Function(
            name="B::method",
            mangled="_ZN1B6methodEv",
            return_type="void",
            is_virtual=True,
        )
        old = AbiSnapshot(
            library="lib.so", version="1", types=[a, b_old, d], functions=[b_method]
        )
        old.clang_vtable_facts_reliable = False
        new = AbiSnapshot(library="lib.so", version="2", types=[a, b_new, d])
        assert ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED in _kinds(compare(old, new))


# ── virtual_base_offset_changed ─────────────────────────────────────────────


class TestVirtualBaseOffset:
    def test_virtual_base_reorder_detected(self):
        # class D : virtual A, virtual B  →  virtual B, virtual A
        old = _snap(
            _poly("A"),
            _poly("B"),
            _poly("D", vtable=["_ZN1D1fEv"], virtual_bases=["A", "B"]),
        )
        new = _snap(
            _poly("A"),
            _poly("B"),
            _poly("D", vtable=["_ZN1D1fEv"], virtual_bases=["B", "A"]),
        )
        r = compare(old, new)
        assert ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_same_order_not_flagged(self):
        old = _snap(_poly("A"), _poly("B"), _poly("D", virtual_bases=["A", "B"]))
        new = _snap(_poly("A"), _poly("B"), _poly("D", virtual_bases=["A", "B"]))
        assert ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED not in _kinds(compare(old, new))

    def test_single_virtual_base_not_flagged(self):
        # Reorder needs ≥2 virtual bases; a single one has nothing to reorder.
        old = _snap(_poly("A"), _poly("D", virtual_bases=["A"]))
        new = _snap(_poly("A"), _poly("D", virtual_bases=["A"]))
        assert ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED not in _kinds(compare(old, new))

    def test_virtual_base_set_change_left_to_base_detectors(self):
        # Adding/removing a virtual base (set change) is not a pure reorder, so
        # this detector stays quiet and the base-set detectors handle it.
        old = _snap(_poly("A"), _poly("B"), _poly("D", virtual_bases=["A", "B"]))
        new = _snap(
            _poly("A"),
            _poly("B"),
            _poly("C"),
            _poly("D", virtual_bases=["A", "B", "C"]),
        )
        assert ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED not in _kinds(compare(old, new))

    def test_stdlib_owner_reorder_not_flagged(self):
        # A virtual-base reorder inside a debug-only std:: record (not this
        # library's own ABI surface) must not surface as a BREAKING finding.
        old = _snap(
            _poly("A"),
            _poly("B"),
            _poly("std::D", vtable=["_ZNSt1D1fEv"], virtual_bases=["A", "B"]),
        )
        new = _snap(
            _poly("A"),
            _poly("B"),
            _poly("std::D", vtable=["_ZNSt1D1fEv"], virtual_bases=["B", "A"]),
        )
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
        types = {
            "A": _poly("A", vtable=["_ZN1A1fEv"]),
            "D": _poly("D", bases=["A"], virtual_bases=["Gone"]),
        }
        assert _secondary_groups(types["D"], types, {}) is None

    def test_secondary_groups_include_polymorphic_virtual_base(self):
        types = {
            "A": _poly("A", vtable=["_ZN1A1fEv"]),
            "V": _poly("V", vtable=["_ZN1V1gEv"]),
            "D": _poly("D", bases=["A"], virtual_bases=["V"]),
        }
        # A is primary (non-virtual), V is a polymorphic virtual base → secondary.
        assert _secondary_groups(types["D"], types, {}) == ["V"]


class TestReconstructionFactStatus:
    """ADR-063 Phase 5B (vtable/vptr_offset_bits slice): ``_is_polymorphic``
    reads ``vtable_fact.status`` directly rather than trusting an empty
    ``vtable`` value alone -- see that function's own docstring.
    """

    @staticmethod
    def _uncollected(name: str, *, bases=None, virtual_bases=None) -> RecordType:
        """A record whose own vtable evidence was never collected -- the
        real shape a legacy pre-v21 direct-clang snapshot's own
        ``apply_legacy_fact_backfill`` produces for every record
        (``storage/fact_backfill.py``), unlike ``_poly``'s always-confirmed
        ``Fact.present([])``.
        """
        return RecordType(
            name=name,
            kind="class",
            size_bits=64,
            bases=bases or [],
            virtual_bases=virtual_bases or [],
            vtable_fact=Fact.not_collected(),
        )

    def test_own_uncollected_vtable_with_no_bases_is_indeterminate(self):
        # No bases to fall back on, and the class's own vtable evidence was
        # never actually collected -- must not read as confirmed False.
        types = {"P": self._uncollected("P")}
        assert _is_polymorphic("P", types, {}) is None

    def test_own_uncollected_vtable_with_non_polymorphic_bases_is_indeterminate(
        self,
    ):
        types = {
            "Base": _poly("Base"),  # confirmed non-polymorphic
            "D": self._uncollected("D", bases=["Base"]),
        }
        assert _is_polymorphic("D", types, {}) is None

    def test_uncollected_vtable_does_not_hide_a_real_base_signal(self):
        # A positive signal from the transitive base walk still settles the
        # question, regardless of this record's own vtable_fact status.
        types = {
            "Base": _poly("Base", vtable=["_ZN4Base1fEv"]),
            "D": self._uncollected("D", bases=["Base"]),
        }
        assert _is_polymorphic("D", types, {}) is True

    def test_confirmed_empty_vtable_still_reads_false(self):
        # A genuinely confirmed-empty vtable (Fact.present([]), what _poly
        # always constructs) is unaffected -- only an uncollected fact
        # changes behavior.
        types = {"P": _poly("P")}
        assert _is_polymorphic("P", types, {}) is False

    def test_vtable_facts_reliable_false_is_indeterminate_even_when_confirmed(
        self,
    ):
        # Codex review, this slice: defense in depth alongside the per-record
        # FactStatus check -- a whole-snapshot vtable_facts_reliable=False
        # (the flag diff_layout/diff_types_vtable already thread) must not be
        # overridden by a record whose own vtable_fact happens to read
        # PRESENT (e.g. a hand-constructed snapshot where the two are out of
        # sync -- storage.fact_backfill keeps them in sync on every real
        # load path, but this function does not assume that of its caller).
        types = {"P": _poly("P")}
        assert _is_polymorphic("P", types, {}, vtable_facts_reliable=False) is None

    def test_vtable_facts_reliable_true_is_the_default(self):
        # Positive control: omitting the flag keeps today's behavior.
        types = {"P": _poly("P")}
        assert _is_polymorphic("P", types, {}, vtable_facts_reliable=True) is False

    def test_partial_empty_vtable_is_indeterminate_not_confirmed_false(self):
        """Codex review, this slice: a ``PARTIAL`` empty vtable means only
        the *observed* portion is empty -- the uncovered remainder could
        still hold a virtual method -- so it must not be trusted as
        confirmed non-polymorphic, unlike a genuine ``Fact.present([])``.
        """
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.partial([]),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is None

    def test_uncollected_vtable_confirmed_non_polymorphic_via_standard_layout(
        self,
    ):
        """Codex review, fresh evidence -- mirrors ``diff_layout.
        _check_vptr_introduced``'s identical fallback: a confirmed
        ``is_standard_layout=True`` conclusively proves the record's own
        vtable is empty (the standard-layout requirement excludes virtual
        functions/bases transitively), so it substitutes for an uncollected
        ``vtable_fact`` rather than leaving this record indeterminate.
        """
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            is_standard_layout=True,
            is_standard_layout_fact=Fact.present(True),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is False

    def test_uncollected_vtable_stays_indeterminate_when_standard_layout_also_uncollected(
        self,
    ):
        # The fallback only applies when is_standard_layout is itself
        # confirmed -- an uncollected is_standard_layout_fact provides no
        # evidence, so this stays indeterminate exactly like the
        # no-fallback case above.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is None

    def test_uncollected_vtable_stays_indeterminate_when_standard_layout_confirmed_false(
        self,
    ):
        # A confirmed is_standard_layout=False says nothing about
        # polymorphism either way (plenty of non-standard-layout classes
        # are still non-polymorphic) -- must not be treated as the
        # fallback signal.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            is_standard_layout=False,
            is_standard_layout_fact=Fact.present(False),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is None

    def test_uncollected_vtable_confirmed_non_polymorphic_via_trivially_copyable(
        self,
    ):
        # Codex review, fresh evidence, second round: is_trivially_copyable
        # is equally conclusive as is_standard_layout -- mirrors the
        # sibling test above.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            is_trivially_copyable=True,
            is_trivially_copyable_fact=Fact.present(True),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is False

    def test_uncollected_vtable_stays_indeterminate_when_trivially_copyable_confirmed_false(
        self,
    ):
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            is_trivially_copyable=False,
            is_trivially_copyable_fact=Fact.present(False),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is None

    def test_confirmed_present_vptr_offset_bits_proves_polymorphic(self):
        # Codex review, fresh evidence, third round: a genuinely confirmed
        # non-None vptr_offset_bits is unconditional positive proof of
        # polymorphism -- the mirror image of the already-rejected
        # "confirmed None proves absence" findings (there is no ambiguity
        # in the positive direction the way there is for a confirmed None).
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            vptr_offset_bits=0,
            vptr_offset_bits_fact=Fact.present(0),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is True

    def test_confirmed_partial_vptr_offset_bits_still_proves_polymorphic(self):
        # PARTIAL earns the same trust as PRESENT here -- a scalar fact,
        # so no "uncovered remainder" risk, matching diff_layout's own
        # permissive treatment of this field.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            vptr_offset_bits=0,
            vptr_offset_bits_fact=Fact.partial(0),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is True

    def test_confirmed_none_vptr_offset_bits_does_not_prove_polymorphic(self):
        # A confirmed None value is NOT evidence either way (tri-state
        # ambiguity -- also covers "polymorphic only via a virtual base")
        # -- must not trigger the new positive-evidence path, so this stays
        # indeterminate exactly like the pre-existing uncollected-vtable
        # case.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            vptr_offset_bits=None,
            vptr_offset_bits_fact=Fact.present(None),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is None

    def test_confirmed_present_vptr_offset_bits_ignored_when_facts_unreliable(self):
        # vtable_facts_reliable=False must suppress this positive-evidence
        # path too, same defense-in-depth discipline as the sibling
        # own_vtable_confirmed_empty check.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            vptr_offset_bits=0,
            vptr_offset_bits_fact=Fact.present(0),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}, vtable_facts_reliable=False) is None

    def test_confirmed_abstract_proves_polymorphic(self):
        # Codex review, fresh evidence, fourth round: a confirmed
        # is_abstract=True is unconditional proof of polymorphism -- an
        # abstract class has at least one pure virtual function, and a
        # pure virtual function is still a virtual function.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            is_abstract=True,
            is_abstract_fact=Fact.present(True),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is True

    def test_confirmed_not_abstract_does_not_prove_polymorphic(self):
        # A confirmed is_abstract=False says nothing about polymorphism
        # either way -- a perfectly ordinary concrete polymorphic class
        # reads is_abstract=False too.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            is_abstract=False,
            is_abstract_fact=Fact.present(False),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}) is None

    def test_confirmed_abstract_is_not_gated_on_vtable_facts_reliable(self):
        # is_abstract_fact carries no known unreliable-producer history tied
        # to vtable_facts_reliable (no storage.fact_backfill rule exists for
        # it), so this positive-evidence path fires even when that flag is
        # False -- same treatment virtual_bases_fact already gets.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
            is_abstract=True,
            is_abstract_fact=Fact.present(True),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}, vtable_facts_reliable=False) is True

    def test_retained_virtual_function_proves_polymorphic(self):
        # Codex review, fresh evidence, fifth round: a retained Function
        # with is_virtual=True, owned by this class, is a separate evidence
        # stream from RecordType.vtable (the class DIE's own virtual-method
        # children) -- the same independence diff_types_vtable's own
        # "class's own virtual functions" branch already relies on. This
        # matters most for a legacy direct-clang snapshot that predates
        # vtable reconstruction: function-level is_virtual metadata
        # survives even though vtable/vtable_fact do not. Gated on
        # vtable_facts_reliable=False -- its actual motivating scope (Codex
        # review, eighth round) -- since that's the one case a retained
        # Function is guaranteed header-AST-sourced, not DWARF-sourced (and
        # so immune to DWARF's own per-TU capture-gap false positive).
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
        )
        types = {"P": rec}
        method = Function(
            name="P::method",
            mangled="_ZN1P6methodEv",
            return_type="void",
            is_virtual=True,
        )
        index = _virtual_signatures_by_owner({method.mangled: method})
        assert (
            _is_polymorphic(
                "P", types, {}, vtable_facts_reliable=False, virtual_owner_index=index
            )
            is True
        )

    def test_retained_virtual_function_ignored_when_vtable_facts_reliable(self):
        # Codex review, fresh evidence, eighth round: outside the
        # vtable_facts_reliable=False scope, a retained Function could
        # itself be DWARF-sourced and vulnerable to the exact per-TU
        # capture-gap false positive diff_types_vtable.py's own module
        # docstring documents and accepts for its own "class's own virtual
        # functions" branch -- so this path must decline (stay
        # indeterminate) rather than fabricate True.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
        )
        types = {"P": rec}
        method = Function(
            name="P::method",
            mangled="_ZN1P6methodEv",
            return_type="void",
            is_virtual=True,
        )
        index = _virtual_signatures_by_owner({method.mangled: method})
        assert (
            _is_polymorphic(
                "P", types, {}, vtable_facts_reliable=True, virtual_owner_index=index
            )
            is None
        )

    def test_retained_non_virtual_function_does_not_prove_polymorphic(self):
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
        )
        types = {"P": rec}
        method = Function(
            name="P::method",
            mangled="_ZN1P6methodEv",
            return_type="void",
            is_virtual=False,
        )
        index = _virtual_signatures_by_owner({method.mangled: method})
        assert (
            _is_polymorphic(
                "P", types, {}, vtable_facts_reliable=False, virtual_owner_index=index
            )
            is None
        )

    def test_virtual_owner_index_defaults_to_none_and_preserves_prior_behavior(self):
        # Omitting virtual_owner_index entirely (every pre-existing caller)
        # must keep today's behavior unchanged -- no positive evidence to
        # consult.
        rec = RecordType(
            name="P",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
        )
        types = {"P": rec}
        assert _is_polymorphic("P", types, {}, vtable_facts_reliable=False) is None

    def test_same_leaf_name_different_namespace_does_not_fabricate_polymorphic(
        self,
    ):
        # Codex review, fresh evidence, sixth round: an unrelated class
        # sharing only the leaf name "Foo" in a different namespace must
        # not fabricate ns1::Foo's own polymorphism. Uses the *exact*
        # qualified-identity matcher, not the eager namespace-suffix one
        # _vtable_transition_is_evidenced's own suppression-oriented use
        # gets away with -- that eager matching, reused verbatim here in
        # an earlier revision, would have wrongly matched ns2::Foo's own
        # virtual while checking ns1::Foo.
        rec = RecordType(
            name="Foo",
            qualified_name="ns1::Foo",
            kind="class",
            size_bits=64,
            vtable_fact=Fact.not_collected(),
        )
        types = {"ns1::Foo": rec}
        unrelated_method = Function(
            name="ns2::Foo::method",
            mangled="_ZN3ns23Foo6methodEv",
            return_type="void",
            is_virtual=True,
        )
        index = _virtual_signatures_by_owner(
            {unrelated_method.mangled: unrelated_method}
        )
        assert (
            _is_polymorphic(
                "ns1::Foo",
                types,
                {},
                vtable_facts_reliable=False,
                virtual_owner_index=index,
            )
            is None
        )
