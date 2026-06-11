# Copyright 2026 Nikolay Petrov
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

"""Unit tests for the fine-grained class-layout descriptor diff."""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import BREAKING_KINDS, RISK_KINDS, ChangeKind
from abicheck.model import AbiSnapshot, RecordType, TypeField


def _snap(version: str, *, types: list[RecordType]) -> AbiSnapshot:
    return AbiSnapshot(library="lib.so", version=version, types=types)


def _rec(name: str = "A", **kwargs: object) -> RecordType:
    """A public struct with a known size, plus any overridden layout fields."""
    defaults: dict[str, object] = dict(name=name, kind="struct", size_bits=128)
    defaults.update(kwargs)
    return RecordType(**defaults)  # type: ignore[arg-type]


def _kinds(old: AbiSnapshot, new: AbiSnapshot) -> set[ChangeKind]:
    return {c.kind for c in compare(old, new).changes}


class TestLayoutDescriptorDiff:
    def test_inert_on_default_snapshots(self) -> None:
        # Records with no layout descriptor populated (the default for every
        # existing snapshot) emit none of the new kinds.
        old = _snap("1", types=[_rec()])
        new = _snap("2", types=[_rec()])
        kinds = _kinds(old, new)
        for k in (
            ChangeKind.BASE_CLASS_OFFSET_CHANGED,
            ChangeKind.VPTR_INTRODUCED,
            ChangeKind.TRIVIALLY_COPYABLE_LOST,
            ChangeKind.STANDARD_LAYOUT_LOST,
            ChangeKind.TAIL_PADDING_REUSE_CHANGED,
            ChangeKind.LAYOUT_UNVERIFIABLE,
        ):
            assert k not in kinds

    def test_base_class_offset_changed(self) -> None:
        old = _snap("1", types=[_rec(base_offsets={"Base": 0})])
        new = _snap("2", types=[_rec(base_offsets={"Base": 64})])
        assert ChangeKind.BASE_CLASS_OFFSET_CHANGED in _kinds(old, new)
        assert ChangeKind.BASE_CLASS_OFFSET_CHANGED in BREAKING_KINDS

    def test_base_offset_unchanged_not_flagged(self) -> None:
        old = _snap("1", types=[_rec(base_offsets={"Base": 64})])
        new = _snap("2", types=[_rec(base_offsets={"Base": 64})])
        assert ChangeKind.BASE_CLASS_OFFSET_CHANGED not in _kinds(old, new)

    def test_base_offset_one_sided_not_flagged(self) -> None:
        # Base present only on the new side (not a *move*) → no finding.
        old = _snap("1", types=[_rec(base_offsets={})])
        new = _snap("2", types=[_rec(base_offsets={"Base": 64})])
        assert ChangeKind.BASE_CLASS_OFFSET_CHANGED not in _kinds(old, new)

    def test_vptr_introduced(self) -> None:
        # Old: positively non-polymorphic (empty vtable, no vptr). New: gained a
        # virtual method (non-empty vtable) and records the vptr at offset 0.
        old = _snap("1", types=[_rec(vtable=[], vptr_offset_bits=None)])
        new = _snap("2", types=[_rec(vtable=["_ZN1A3fooEv"], vptr_offset_bits=0)])
        assert ChangeKind.VPTR_INTRODUCED in _kinds(old, new)
        assert ChangeKind.VPTR_INTRODUCED in BREAKING_KINDS

    def test_vptr_present_both_sides_not_flagged(self) -> None:
        old = _snap("1", types=[_rec(vtable=["_ZN1A3fooEv"], vptr_offset_bits=0)])
        new = _snap("2", types=[_rec(vtable=["_ZN1A3fooEv"], vptr_offset_bits=0)])
        assert ChangeKind.VPTR_INTRODUCED not in _kinds(old, new)

    def test_vptr_not_flagged_against_pre_layout_polymorphic_baseline(self) -> None:
        # Regression (Codex #345): an old pre-layout-descriptor snapshot of an
        # *already-polymorphic* type has a non-empty vtable but vptr_offset_bits
        # defaulting to None. Comparing it to a newer dump that records
        # vptr_offset_bits=0 must NOT report VPTR_INTRODUCED — the old vtable is
        # proof the type was already polymorphic.
        old = _snap("1", types=[_rec(vtable=["_ZN1A3fooEv"], vptr_offset_bits=None)])
        new = _snap("2", types=[_rec(vtable=["_ZN1A3fooEv"], vptr_offset_bits=0)])
        assert ChangeKind.VPTR_INTRODUCED not in _kinds(old, new)

    def test_trivially_copyable_lost(self) -> None:
        old = _snap("1", types=[_rec(is_trivially_copyable=True)])
        new = _snap("2", types=[_rec(is_trivially_copyable=False)])
        assert ChangeKind.TRIVIALLY_COPYABLE_LOST in _kinds(old, new)
        assert ChangeKind.TRIVIALLY_COPYABLE_LOST in BREAKING_KINDS

    def test_layout_finding_gets_affected_symbols(self) -> None:
        # A layout-only BREAKING finding on a type used by an exported by-value
        # API must carry affected_symbols, so app-compat filtering doesn't mark
        # a consumer of take(A) as unaffected (Codex review #345).
        from abicheck.model import Function, Param, ParamKind

        def snap(version: str, trivially_copyable: bool) -> AbiSnapshot:
            fn = Function(
                name="take",
                mangled="_Z4take1A",
                return_type="void",
                params=[Param(name="a", type="A", kind=ParamKind.VALUE)],
            )
            return AbiSnapshot(
                library="lib.so",
                version=version,
                functions=[fn],
                types=[_rec(name="A", is_trivially_copyable=trivially_copyable)],
            )

        changes = compare(snap("1", True), snap("2", False)).changes
        tc = [c for c in changes if c.kind == ChangeKind.TRIVIALLY_COPYABLE_LOST]
        assert tc, "expected a TRIVIALLY_COPYABLE_LOST finding"
        assert tc[0].affected_symbols, "layout finding must carry affected_symbols"
        assert any("take" in s for s in tc[0].affected_symbols)

    def test_trivially_copyable_unknown_old_not_flagged(self) -> None:
        # Tri-state guard: old side unknown (None) → no finding.
        old = _snap("1", types=[_rec(is_trivially_copyable=None)])
        new = _snap("2", types=[_rec(is_trivially_copyable=False)])
        assert ChangeKind.TRIVIALLY_COPYABLE_LOST not in _kinds(old, new)

    def test_standard_layout_lost(self) -> None:
        old = _snap("1", types=[_rec(is_standard_layout=True)])
        new = _snap("2", types=[_rec(is_standard_layout=False)])
        kinds = _kinds(old, new)
        assert ChangeKind.STANDARD_LAYOUT_LOST in kinds
        assert ChangeKind.STANDARD_LAYOUT_LOST in RISK_KINDS

    def test_tail_padding_reuse_changed_at_stable_sizeof(self) -> None:
        # dsize changes while sizeof stays the same → tail-padding reuse risk.
        old = _snap("1", types=[_rec(size_bits=128, data_size_bits=96)])
        new = _snap("2", types=[_rec(size_bits=128, data_size_bits=120)])
        kinds = _kinds(old, new)
        assert ChangeKind.TAIL_PADDING_REUSE_CHANGED in kinds
        assert ChangeKind.TAIL_PADDING_REUSE_CHANGED in RISK_KINDS

    def test_tail_padding_not_flagged_when_sizeof_also_changed(self) -> None:
        # sizeof changed too → the coarse size detector owns it; we stay quiet.
        old = _snap("1", types=[_rec(size_bits=128, data_size_bits=96)])
        new = _snap("2", types=[_rec(size_bits=192, data_size_bits=160)])
        assert ChangeKind.TAIL_PADDING_REUSE_CHANGED not in _kinds(old, new)

    def test_layout_unverifiable_on_asymmetric_evidence(self) -> None:
        # New side carries a layout descriptor; old side has no size at all.
        old = _snap("1", types=[_rec(name="A", size_bits=None)])
        new = _snap("2", types=[_rec(name="A", size_bits=128, is_standard_layout=True)])
        kinds = _kinds(old, new)
        assert ChangeKind.LAYOUT_UNVERIFIABLE in kinds
        assert ChangeKind.LAYOUT_UNVERIFIABLE in RISK_KINDS

    def test_opaque_type_skipped(self) -> None:
        # An opaque/forward-declared side is owned by the incomplete-type
        # detectors, not the layout descriptor diff.
        old = _snap("1", types=[_rec(is_opaque=True, vptr_offset_bits=None)])
        new = _snap("2", types=[_rec(is_opaque=False, vptr_offset_bits=0)])
        assert ChangeKind.VPTR_INTRODUCED not in _kinds(old, new)

    def test_stdlib_record_not_flagged(self) -> None:
        # Toolchain-owned std:: records are excluded from public-surface
        # reasoning for a normal library, so their layout churn (here a real
        # vtable/vptr transition) does not produce a finding.
        old = _snap(
            "1", types=[_rec(name="std::__1::thing", vtable=[], vptr_offset_bits=None)]
        )
        new = _snap(
            "2",
            types=[
                _rec(name="std::__1::thing", vtable=["_ZN1A3fooEv"], vptr_offset_bits=0)
            ],
        )
        assert ChangeKind.VPTR_INTRODUCED not in _kinds(old, new)

    def test_stdlib_record_flagged_when_comparing_the_runtime_itself(self) -> None:
        # When abicheck compares the C++ runtime to itself (libstdc++/libc++
        # SONAME), the std:: filter is OFF — the runtime's own std:: layout
        # changes ARE the surface under test and must be reported (Codex #345).
        old = AbiSnapshot(
            library="libstdc++.so.6",
            version="1",
            types=[_rec(name="std::__1::thing", vtable=[], vptr_offset_bits=None)],
        )
        new = AbiSnapshot(
            library="libstdc++.so.6",
            version="2",
            types=[
                _rec(name="std::__1::thing", vtable=["_ZN1A3fooEv"], vptr_offset_bits=0)
            ],
        )
        assert ChangeKind.VPTR_INTRODUCED in _kinds(old, new)


class TestStdlibEmbeddingAttribution:
    """The owner's size change is attributed to an embedded std:: member."""

    @staticmethod
    def _owner(size: int) -> RecordType:
        # A public type embedding std::vector<int> by value.
        return RecordType(
            name="A",
            kind="struct",
            size_bits=size,
            fields=[TypeField(name="v", type="std::vector<int>", offset_bits=0)],
        )

    def test_size_change_attributed_to_embedded_stdlib_member(self) -> None:
        old = _snap("1", types=[self._owner(128)])
        new = _snap("2", types=[self._owner(192)])
        changes = compare(old, new).changes
        size_changes = [
            c
            for c in changes
            if c.kind in (ChangeKind.TYPE_SIZE_CHANGED, ChangeKind.STRUCT_SIZE_CHANGED)
            and c.symbol == "A"
        ]
        assert size_changes, "expected a size change on the owner type A"
        assert any(
            "embeds a standard-library type by value" in c.description
            and "std::vector<int>" in c.description
            for c in size_changes
        )

    def test_no_attribution_without_stdlib_embedding(self) -> None:
        # A plain int-field struct gets no embedding clause appended.
        plain_old = RecordType(
            name="B",
            kind="struct",
            size_bits=64,
            fields=[TypeField(name="x", type="int", offset_bits=0)],
        )
        plain_new = RecordType(
            name="B",
            kind="struct",
            size_bits=128,
            fields=[TypeField(name="x", type="int", offset_bits=0)],
        )
        changes = compare(
            _snap("1", types=[plain_old]), _snap("2", types=[plain_new])
        ).changes
        assert not any(
            "embeds a standard-library type by value" in c.description for c in changes
        )

    def test_pointer_to_stdlib_member_not_attributed(self) -> None:
        # A pointer to a std:: type is layout-neutral, so a size change on the
        # owner is NOT attributed to it.
        def owner(size: int) -> RecordType:
            return RecordType(
                name="C",
                kind="struct",
                size_bits=size,
                fields=[TypeField(name="p", type="std::vector<int> *", offset_bits=0)],
            )

        changes = compare(
            _snap("1", types=[owner(64)]), _snap("2", types=[owner(128)])
        ).changes
        assert not any(
            "embeds a standard-library type by value" in c.description for c in changes
        )

    def test_attribution_helper_idempotent_and_tolerates_missing_type(self) -> None:
        # Direct-call coverage for the dedup branch (clause appended once) and the
        # "owner type absent from the new snapshot" guard.
        from abicheck.checker_types import Change
        from abicheck.diff_filtering import _attribute_stdlib_embedding

        new = _snap(
            "2",
            types=[
                RecordType(
                    name="A",
                    kind="struct",
                    size_bits=192,
                    fields=[
                        TypeField(name="v", type="std::vector<int>", offset_bits=0)
                    ],
                )
            ],
        )
        present = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="A", description="size changed"
        )
        missing = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Gone", description="size changed"
        )
        changes = [present, missing]
        _attribute_stdlib_embedding(changes, new)
        _attribute_stdlib_embedding(changes, new)  # second pass must not duplicate
        assert present.description.count("embeds a standard-library type by value") == 1
        # The change whose type is absent from `new` is left untouched.
        assert "embeds a standard-library type by value" not in missing.description
