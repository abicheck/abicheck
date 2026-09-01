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

"""ADR-063 Phase 0: Fact[T] and the RecordType/Param legacy-field bridge.

Pins the actual contract, not just the happy path — several of these
assertions exist specifically because a first draft of the design got them
wrong and a review round caught it (see the plan doc's own Phase 0 section
for the full history): Fact's raising __bool__ but non-raising __eq__, the
omission-vs-explicit-empty distinction for both list-typed and bool-typed
fields, and the "explicit Fact wins and overwrites the legacy field too"
precedence rule.
"""

from __future__ import annotations

import dataclasses

import pytest

from abicheck.model.availability import FactStatus
from abicheck.model.declarations import Param
from abicheck.model.entities import RecordType
from abicheck.model.fact import Fact, replace_with_fact_sync


class TestFactConstructors:
    def test_present_holds_a_confirmed_value(self) -> None:
        f = Fact.present(["a", "b"])
        assert f.status is FactStatus.PRESENT
        assert f.value == ["a", "b"]
        assert f.is_present

    def test_present_none_is_confirmed_absence_not_a_gap(self) -> None:
        f = Fact.present(None)
        assert f.status is FactStatus.PRESENT
        assert f.value is None
        assert f.is_present

    def test_not_collected_carries_no_value(self) -> None:
        f: Fact[list[str]] = Fact.not_collected()
        assert f.status is FactStatus.NOT_COLLECTED
        assert f.value is None
        assert not f.is_present

    def test_failed_stores_reason_as_diagnostic_not_value(self) -> None:
        f: Fact[list[str]] = Fact.failed("castxml exited non-zero")
        assert f.status is FactStatus.FAILED
        assert f.value is None
        assert f.diagnostics == ("castxml exited non-zero",)

    def test_unsupported_and_not_applicable(self) -> None:
        assert Fact.unsupported().status is FactStatus.UNSUPPORTED
        assert Fact.not_applicable().status is FactStatus.NOT_APPLICABLE

    def test_partial_is_present_with_reduced_confidence(self) -> None:
        f = Fact.partial(["a"])
        assert f.status is FactStatus.PARTIAL
        assert f.is_present


class TestFactTruthAndEquality:
    def test_bool_raises(self) -> None:
        with pytest.raises(TypeError, match="Fact\\[T\\] has no truth value"):
            bool(Fact.present([1]))

    def test_bool_raises_even_when_not_present(self) -> None:
        with pytest.raises(TypeError):
            bool(Fact.not_collected())

    def test_eq_does_not_raise_and_is_structural(self) -> None:
        a = Fact.present(["x"])
        b = Fact.present(["x"])
        c = Fact.present(["y"])
        assert a == b
        assert a != c

    def test_containing_dataclass_equality_does_not_raise(self) -> None:
        """The reverted design: a raising __eq__ on Fact would poison the
        containing RecordType's own generated __eq__ the instant comparison
        reached the Fact-typed field. Two field-identical RecordType
        instances (Fact siblings included) must compare equal without
        raising."""
        r1 = RecordType(name="Foo", kind="struct", bases_fact=Fact.present([]))
        r2 = RecordType(name="Foo", kind="struct", bases_fact=Fact.present([]))
        assert r1 == r2


class TestFactValueOr:
    def test_value_or_returns_value_when_present(self) -> None:
        assert Fact.present(["a"]).value_or([]) == ["a"]

    def test_value_or_returns_default_when_not_collected(self) -> None:
        f: Fact[list[str]] = Fact.not_collected()
        assert f.value_or(["fallback"]) == ["fallback"]

    def test_value_or_distinguishes_confirmed_empty_from_default(self) -> None:
        # Confirmed-empty still returns the real (empty) value, not default.
        f: Fact[list[str]] = Fact.present([])
        assert f.value_or(["fallback"]) == []


class TestRecordTypeFactBridge:
    """The omission-sentinel bridge for bases/virtual_bases/vtable/vptr_offset_bits."""

    def test_omitted_bases_backfills_not_collected(self) -> None:
        r = RecordType(name="Foo", kind="struct")
        assert r.bases == []
        assert r.bases_fact is not None
        assert r.bases_fact.status is FactStatus.NOT_COLLECTED

    def test_explicit_empty_bases_backfills_present_empty(self) -> None:
        """The exact bug a first-draft mechanism reintroduced: omission and
        an explicitly-supplied empty list must not collapse to the same
        Fact status."""
        r = RecordType(name="Foo", kind="struct", bases=[])
        assert r.bases == []
        assert r.bases_fact is not None
        assert r.bases_fact.status is FactStatus.PRESENT
        assert r.bases_fact.value == []

    def test_explicit_nonempty_bases_backfills_present(self) -> None:
        r = RecordType(name="Foo", kind="struct", bases=["Base"])
        assert r.bases_fact is not None
        assert r.bases_fact.status is FactStatus.PRESENT
        assert r.bases_fact.value == ["Base"]

    def test_explicit_fact_wins_and_overwrites_legacy_field(self) -> None:
        """RecordType(vtable=["old"], vtable_fact=Fact.present(["new"])) ends
        construction with self.vtable == ["new"] — the explicit Fact[...]
        value also overwrites the legacy field, not only the reverse. This is
        deliberately not "whichever value looks newer": there is no such
        signal available inside __post_init__, so this bridge always trusts
        an explicit Fact over a legacy value that might disagree with it —
        see bridge_legacy_and_fact's own docstring for why, and
        TestReplaceWithFactSync below for the safe way to update these
        fields via dataclasses.replace()."""
        r = RecordType(
            name="Foo", kind="struct", vtable=["old"], vtable_fact=Fact.present(["new"])
        )
        assert r.vtable == ["new"]
        assert r.vtable_fact is not None
        assert r.vtable_fact.value == ["new"]

    def test_explicit_not_collected_fact_normalizes_legacy_to_default(self) -> None:
        r = RecordType(
            name="Foo",
            kind="struct",
            vtable=["stale"],
            vtable_fact=Fact.not_collected(),
        )
        assert r.vtable == []
        assert r.vtable_fact is not None
        assert r.vtable_fact.status is FactStatus.NOT_COLLECTED

    def test_virtual_bases_uses_the_identical_mechanism(self) -> None:
        r_omitted = RecordType(name="Foo", kind="struct")
        assert r_omitted.virtual_bases_fact is not None
        assert r_omitted.virtual_bases_fact.status is FactStatus.NOT_COLLECTED
        r_explicit_empty = RecordType(name="Foo", kind="struct", virtual_bases=[])
        assert r_explicit_empty.virtual_bases_fact is not None
        assert r_explicit_empty.virtual_bases_fact.status is FactStatus.PRESENT

    def test_omitted_vptr_offset_bits_backfills_not_collected(self) -> None:
        """vptr_offset_bits already legitimately defaults toward None
        ("no vptr observed") — omission must still be distinguishable from
        an explicit, confirmed None."""
        r = RecordType(name="Foo", kind="struct")
        assert r.vptr_offset_bits is None
        assert r.vptr_offset_bits_fact is not None
        assert r.vptr_offset_bits_fact.status is FactStatus.NOT_COLLECTED

    def test_explicit_none_vptr_offset_bits_backfills_present_none(self) -> None:
        r = RecordType(name="Foo", kind="struct", vptr_offset_bits=None)
        assert r.vptr_offset_bits is None
        assert r.vptr_offset_bits_fact is not None
        assert r.vptr_offset_bits_fact.status is FactStatus.PRESENT
        assert r.vptr_offset_bits_fact.value is None

    def test_explicit_int_vptr_offset_bits_backfills_present(self) -> None:
        r = RecordType(name="Foo", kind="struct", vptr_offset_bits=64)
        assert r.vptr_offset_bits_fact is not None
        assert r.vptr_offset_bits_fact.status is FactStatus.PRESENT
        assert r.vptr_offset_bits_fact.value == 64

    def test_field_types_never_widen(self) -> None:
        """A dataclasses.fields() reader (asdict-based external consumer)
        must see exactly the type each field has always declared — bool/
        list[str], never a union with the sentinel's own type."""
        by_name = {f.name: f for f in dataclasses.fields(RecordType)}
        assert by_name["bases"].type == "list[str]"
        assert by_name["vtable"].type == "list[str]"
        assert by_name["vptr_offset_bits"].type == "int | None"

    def test_two_separately_omitted_instances_do_not_share_a_list_object(self) -> None:
        """The default_factory must return the *same* sentinel each time
        (for the identity check to work), but two real, backfilled RecordType
        instances must not end up sharing one mutable list."""
        r1 = RecordType(name="Foo", kind="struct")
        r2 = RecordType(name="Bar", kind="struct")
        r1.bases.append("Mutated")
        assert r2.bases == []


class TestPlainReplaceIsUnsafeForFactBridgedFields:
    """Documents, rather than hides, the tradeoff bridge_legacy_and_fact's
    docstring names: a raw dataclasses.replace() call updating a legacy
    field without also updating its Fact sibling has the update silently
    discarded, because __post_init__ cannot tell the carried-forward Fact
    apart from a fresh, deliberate one (Codex review — trusting the Fact
    unconditionally is what "explicit Fact wins" requires for the sibling
    class above). TestReplaceWithFactSync below is the safe alternative."""

    def test_replace_updating_only_the_legacy_field_is_silently_discarded(
        self,
    ) -> None:
        r = RecordType(name="Foo", kind="struct", bases=["OldBase"])
        r2 = dataclasses.replace(r, bases=["NewBase"])
        assert r2.bases == ["OldBase"]


class TestPostConstructionMutationIsUnsafeForFactBridgedFields:
    """A second, related trap `bridge_legacy_and_fact`'s docstring names
    (Codex review, confirmed to reproduce identically across every bridged
    field, not something one conversion introduces): plain attribute
    assignment after construction never re-runs `__post_init__` at all, so
    the sibling Fact[T] is never re-derived and the pair goes out of sync
    silently -- there is no `replace_with_fact_sync`-shaped escape hatch
    for this path; the guidance is to treat a bridged field as effectively
    immutable after construction."""

    def test_mutating_the_legacy_field_leaves_the_fact_sibling_stale(self) -> None:
        r = RecordType(name="Foo", kind="struct", bases=["OldBase"])
        r.bases = ["NewBase"]
        assert r.bases == ["NewBase"]
        assert r.bases_fact is not None
        assert r.bases_fact.value == ["OldBase"]


class TestReplaceWithFactSync:
    """The safe alternative to dataclasses.replace() for these fields."""

    def test_updates_the_legacy_field_and_derives_a_present_fact(self) -> None:
        r = RecordType(name="Foo", kind="struct", bases=["OldBase"])
        r2 = replace_with_fact_sync(r, bases=["NewBase"])
        assert r2.bases == ["NewBase"]
        assert r2.bases_fact is not None
        assert r2.bases_fact.status is FactStatus.PRESENT
        assert r2.bases_fact.value == ["NewBase"]

    def test_an_explicitly_supplied_fact_is_never_second_guessed(self) -> None:
        r = RecordType(name="Foo", kind="struct", bases=["OldBase"])
        r2 = replace_with_fact_sync(
            r, bases=["NewBase"], bases_fact=Fact.not_collected("depth capped")
        )
        assert r2.bases_fact is not None
        assert r2.bases_fact.status is FactStatus.NOT_COLLECTED

    def test_a_field_with_no_fact_sibling_passes_through_unaffected(self) -> None:
        r = RecordType(name="Foo", kind="struct")
        r2 = replace_with_fact_sync(r, kind="union")
        assert r2.kind == "union"

    def test_touching_an_unrelated_field_leaves_the_pair_untouched(self) -> None:
        r = RecordType(name="Foo", kind="struct", bases=["Base"])
        r2 = replace_with_fact_sync(r, kind="union")
        assert r2.bases == ["Base"]
        assert r2.bases_fact == r.bases_fact


class TestParamFactBridge:
    """The bool-typed sentinel bridge for is_va_list — its own mechanism,
    since bool has only two instances and cannot reuse the list-typed
    approach."""

    def test_omitted_is_va_list_backfills_not_collected(self) -> None:
        p = Param(name="args", type="...")
        assert p.is_va_list is False
        assert p.is_va_list_fact is not None
        assert p.is_va_list_fact.status is FactStatus.NOT_COLLECTED

    def test_explicit_false_backfills_present_false(self) -> None:
        """The exact bug a naive `self.is_va_list is _OMITTED` truthiness
        check would reintroduce for the boolean field: an explicit False
        must not collapse into omission."""
        p = Param(name="x", type="int", is_va_list=False)
        assert p.is_va_list is False
        assert p.is_va_list_fact is not None
        assert p.is_va_list_fact.status is FactStatus.PRESENT
        assert p.is_va_list_fact.value is False

    def test_explicit_true_backfills_present_true(self) -> None:
        p = Param(name="args", type="va_list", is_va_list=True)
        assert p.is_va_list is True
        assert p.is_va_list_fact is not None
        assert p.is_va_list_fact.status is FactStatus.PRESENT
        assert p.is_va_list_fact.value is True

    def test_explicit_fact_wins_and_overwrites_legacy_field(self) -> None:
        p = Param(
            name="args",
            type="va_list",
            is_va_list=False,
            is_va_list_fact=Fact.present(True),
        )
        assert p.is_va_list is True

    def test_field_type_never_widens(self) -> None:
        by_name = {f.name: f for f in dataclasses.fields(Param)}
        assert by_name["is_va_list"].type == "bool"
