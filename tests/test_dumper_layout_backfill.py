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

"""Unit tests for abicheck.dumper_layout_backfill."""
from __future__ import annotations

import pytest

from abicheck.dumper_layout_backfill import (
    backfill_dwarf_layout,
    dwarf_layout_types_or_empty,
)
from abicheck.model import RecordType, TypeField


def _dwarf_meta(has_dwarf: bool):
    class _M:
        pass
    m = _M()
    m.has_dwarf = has_dwarf
    return m


class TestDwarfLayoutTypesOrEmpty:
    """dwarf_layout_types_or_empty must gate on the *actual* parser backend,
    not a static --ast-frontend guess (Codex/CodeRabbit review: the "auto"
    frontend can fall back from castxml to clang internally)."""

    @pytest.mark.parametrize(
        ("is_clang_backend", "has_dwarf", "symbols_only", "debug_presence_only"),
        [
            (False, True, False, False),
            (True, False, False, False),
            (True, True, True, False),
            (True, True, False, True),
        ],
        ids=["not-clang-backend", "no-dwarf", "symbols-only", "debug-presence-only"],
    )
    def test_no_op_gates(
        self,
        is_clang_backend: bool,
        has_dwarf: bool,
        symbols_only: bool,
        debug_presence_only: bool,
    ) -> None:
        """Each gate is a no-op on its own: wrong backend, no DWARF, or either
        of the two evidence-skipping modes (CodeRabbit: the original tests
        didn't cover debug_presence_only, so a regression there could have
        started building a full DWARF snapshot it never needs)."""
        result = dwarf_layout_types_or_empty(
            None, None, _dwarf_meta(has_dwarf), None, is_clang_backend,
            symbols_only=symbols_only, debug_presence_only=debug_presence_only,
            version="1.0", language_profile=None, session=None,
        )
        assert result == []

    def test_extracts_dwarf_types_when_applicable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The one branch that actually calls build_snapshot_from_dwarf."""
        import abicheck.dwarf_snapshot as dwarf_snapshot

        expected = [RecordType(name="Point", kind="struct", size_bits=64)]

        class _FakeSnap:
            types = expected

        calls = []

        def _fake_build(so_path, elf_meta, dwarf_meta, dwarf_adv, *, version, language_profile, session):
            calls.append((so_path, version, language_profile))
            return _FakeSnap()

        monkeypatch.setattr(dwarf_snapshot, "build_snapshot_from_dwarf", _fake_build)

        result = dwarf_layout_types_or_empty(
            "libfoo.so", None, _dwarf_meta(True), None, True,
            symbols_only=False, debug_presence_only=False,
            version="1.0", language_profile="c++", session=None,
        )
        assert result == expected
        assert calls == [("libfoo.so", "1.0", "c++")]


class TestBackfillDwarfLayout:
    def test_backfills_size_and_field_offsets(self) -> None:
        header = RecordType(
            name="Point", kind="struct",
            fields=[TypeField(name="x", type="int"), TypeField(name="y", type="int")],
        )
        dwarf = RecordType(
            name="Point", kind="struct", size_bits=64, alignment_bits=32,
            fields=[
                TypeField(name="x", type="int", offset_bits=0),
                TypeField(name="y", type="int", offset_bits=32),
            ],
        )
        out = backfill_dwarf_layout([header], [dwarf])
        assert len(out) == 1
        assert out[0].size_bits == 64
        assert out[0].alignment_bits == 32
        assert [f.offset_bits for f in out[0].fields] == [0, 32]

    def test_leaves_field_untouched_when_already_offset_or_unmatched(self) -> None:
        """A field that already has an offset (e.g. from a prior backfill), or
        one with no same-named DWARF counterpart, is left as-is rather than
        overwritten or dropped."""
        header = RecordType(
            name="Point", kind="struct",
            fields=[
                TypeField(name="x", type="int", offset_bits=0),  # already known
                TypeField(name="ghost", type="int"),  # no DWARF counterpart
            ],
        )
        dwarf = RecordType(
            name="Point", kind="struct", size_bits=64,
            fields=[TypeField(name="x", type="int", offset_bits=999)],
        )
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].fields[0].offset_bits == 0  # untouched, not overwritten with 999
        assert out[0].fields[1].offset_bits is None  # left alone, not dropped

    def test_leaves_castxml_derived_type_untouched(self) -> None:
        """A type that already has size_bits (castxml) must not be touched,
        even if a same-named DWARF type carries different layout."""
        header = RecordType(name="Point", kind="struct", size_bits=64)
        dwarf = RecordType(name="Point", kind="struct", size_bits=128)
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 64

    def test_unique_bare_name_without_field_overlap_is_not_trusted(self) -> None:
        """A unique bare-name candidate is not necessarily the *right* one:
        if the public header type's own DWARF counterpart is simply absent
        (e.g. declared in a broad header but not instantiated by this
        binary), an unrelated internal helper coincidentally sharing the
        bare name (`impl::Foo` for public `Foo`) would otherwise be accepted
        as the sole candidate with nothing to disambiguate against (Codex
        review). Field-name overlap is required as corroboration."""
        header = RecordType(
            name="Foo", kind="struct",
            fields=[TypeField(name="public_field", type="int")],
        )
        unrelated = RecordType(
            name="impl::Foo", kind="struct", size_bits=999,
            fields=[TypeField(name="internal_thing", type="void *")],
        )
        out = backfill_dwarf_layout([header], [unrelated])
        assert out[0].size_bits is None

    def test_unique_bare_name_with_field_overlap_is_trusted(self) -> None:
        """The common, legitimate case: same source, so fields genuinely
        overlap — corroboration passes and the match is used."""
        header = RecordType(
            name="Foo", kind="struct",
            fields=[TypeField(name="x", type="int")],
        )
        dwarf = RecordType(
            name="Foo", kind="struct", size_bits=32,
            fields=[TypeField(name="x", type="int", offset_bits=0)],
        )
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 32

    def test_both_sides_empty_fields_is_trusted(self) -> None:
        """Two empty tag types have no fields to disagree on; the name match
        alone is trusted (matches the pre-corroboration behavior for this
        case, e.g. case94_empty_tag_gained_state before a field is added)."""
        header = RecordType(name="Tag", kind="struct")
        dwarf = RecordType(name="Tag", kind="struct", size_bits=8)
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 8

    def test_empty_dwarf_fields_from_anonymous_aggregate_is_trusted(self) -> None:
        """Regression (Codex review): a struct whose only members come from an
        anonymous struct/union (`struct Foo { union { int i; float f; }; };`)
        is flattened onto the header side by dumper_clang.py (header.fields
        lists i/f directly) but the DWARF builder does not flatten it the
        same way, leaving dwarf.fields empty even though DWARF does carry the
        record's real size_bits. Rejecting this on "no overlap" would make
        every such struct permanently layout-blind under the clang backend —
        reproduced directly against real clang+gcc+DWARF output before this
        fix (size_bits stayed None; fixed, it backfills correctly)."""
        header = RecordType(
            name="Foo", kind="struct",
            fields=[TypeField(name="i", type="int"), TypeField(name="f", type="float")],
        )
        dwarf = RecordType(name="Foo", kind="struct", size_bits=32, fields=[])
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 32

    def test_nonempty_header_against_dwarf_empty_with_different_bases_is_never_guessed(
        self,
    ) -> None:
        """Regression (Codex review): the anonymous-aggregate exception above
        used to trust *any* header-has-fields/dwarf-empty match unconditionally,
        even when the header type declares bases that don't overlap the unique
        DWARF candidate's — e.g. a public `struct Foo : PublicBase { int x; }`
        matched against an unrelated, genuinely fieldless `impl::Foo :
        InternalBase {}` that merely shares the bare suffix. Base-class overlap
        is now checked here too, not just in the "both fieldless" case."""
        header = RecordType(
            name="Foo", kind="struct", bases=["PublicBase"],
            fields=[TypeField(name="x", type="int")],
        )
        unrelated = RecordType(
            name="impl::Foo", kind="struct", size_bits=8, bases=["InternalBase"], fields=[],
        )
        out = backfill_dwarf_layout([header], [unrelated])
        assert out[0].size_bits is None

    def test_empty_header_against_nonempty_dwarf_is_never_guessed(self) -> None:
        """Regression (Codex review): the anonymous-aggregate exception only
        justifies trusting a one-sided-empty match in the header-has-fields,
        dwarf-empty direction. The reverse — an empty header tag type (e.g.
        `struct Foo {};`, never itself emitted in DWARF) matched to a unique
        but unrelated internal `impl::Foo { int x; }` via bare-name suffix —
        must NOT be trusted: that would silently backfill the public empty
        type's layout from a type that isn't actually the same declaration."""
        header = RecordType(name="Foo", kind="struct", fields=[])
        unrelated = RecordType(
            name="impl::Foo", kind="struct", size_bits=32,
            fields=[TypeField(name="x", type="int")],
        )
        out = backfill_dwarf_layout([header], [unrelated])
        assert out[0].size_bits is None

    def test_fieldless_but_different_bases_is_never_guessed(self) -> None:
        """Regression (Codex review): a fieldless C++ record's ABI surface can
        still come from its base classes (an empty derived class) or virtual
        methods, so "both sides have no data fields" alone must not be
        trusted when the header and the unique DWARF candidate declare
        different bases — that's the same unrelated-internal-type risk as
        the field-overlap check, just via a different ABI-surface signal."""
        header = RecordType(name="Foo", kind="class", fields=[], bases=["PublicBase"])
        unrelated = RecordType(
            name="impl::Foo", kind="class", size_bits=64, fields=[], bases=["InternalBase"],
        )
        out = backfill_dwarf_layout([header], [unrelated])
        assert out[0].size_bits is None

    def test_fieldless_with_matching_base_is_trusted(self) -> None:
        """The legitimate counterpart: same source, so the base class name
        genuinely overlaps even though neither side has data fields (e.g. an
        empty derived class that only exists to establish a base relation)."""
        header = RecordType(name="Foo", kind="class", fields=[], bases=["Base"])
        dwarf = RecordType(name="Foo", kind="class", size_bits=8, fields=[], bases=["Base"])
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 8

    def test_fieldless_virtual_base_only_mismatch_is_never_guessed(self) -> None:
        """Regression (Codex review): virtual inheritance is stored in
        RecordType.virtual_bases, not .bases, on both the clang header
        parser and the DWARF builder. A fieldless class that only inherits
        virtually (`Foo : virtual PublicBase`) would otherwise leave both
        `.bases` sets empty and fall through to the "truly trivial" trust
        path even when the unique DWARF candidate is an unrelated class
        with a different virtual base (`impl::Foo : virtual InternalBase`)."""
        header = RecordType(name="Foo", kind="class", fields=[], virtual_bases=["PublicBase"])
        unrelated = RecordType(
            name="impl::Foo", kind="class", size_bits=64, fields=[],
            virtual_bases=["InternalBase"],
        )
        out = backfill_dwarf_layout([header], [unrelated])
        assert out[0].size_bits is None

    def test_fieldless_with_matching_virtual_base_is_trusted(self) -> None:
        """The legitimate counterpart: same source, so the virtual base name
        genuinely overlaps even though neither side has data fields or
        ordinary (non-virtual) bases."""
        header = RecordType(name="Foo", kind="class", fields=[], virtual_bases=["Base"])
        dwarf = RecordType(
            name="Foo", kind="class", size_bits=16, fields=[], virtual_bases=["Base"],
        )
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 16

    def test_fieldless_and_baseless_on_both_sides_is_trusted(self) -> None:
        """A truly trivial record (no fields, no bases on either side) has
        nothing left to disagree on beyond the name match itself — same
        residual-risk trade-off already accepted for plain tag types."""
        header = RecordType(name="Tag", kind="struct", fields=[], bases=[])
        dwarf = RecordType(name="Tag", kind="struct", size_bits=8, fields=[], bases=[])
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 8

    def test_suffix_only_match_with_no_remaining_evidence_is_never_guessed(self) -> None:
        """Regression (CodeRabbit review): with zero fields and zero bases on
        either side, name equality is the only signal left — and a *suffix*
        match (the unique DWARF candidate is only reached via the bare-name
        fallback, e.g. `impl::Foo` recovered for a public `Foo`) is a weaker
        signal than an exact match, since it already means the header's bare
        name lacks the scope DWARF's qualified name carries. Stacking that on
        top of zero field/base evidence must not be trusted."""
        header = RecordType(name="Foo", kind="struct", fields=[], bases=[])
        unrelated = RecordType(name="impl::Foo", kind="struct", size_bits=8, fields=[], bases=[])
        out = backfill_dwarf_layout([header], [unrelated])
        assert out[0].size_bits is None

    def test_suffix_only_match_with_no_remaining_evidence_and_header_fields_is_never_guessed(
        self,
    ) -> None:
        """Same as above but for the anonymous-aggregate branch (header has
        real fields, DWARF's are empty): a suffix-only match to an unrelated,
        genuinely empty type must still be rejected, not silently trusted."""
        header = RecordType(
            name="Foo", kind="struct", fields=[TypeField(name="x", type="int")],
        )
        unrelated = RecordType(name="impl::Foo", kind="struct", size_bits=8, fields=[])
        out = backfill_dwarf_layout([header], [unrelated])
        assert out[0].size_bits is None

    def test_union_vs_struct_kind_mismatch_is_never_guessed(self) -> None:
        """Regression (Codex review): a struct and a union can share a bare
        name and even a field name while having fundamentally different
        layouts (a union's members overlap in memory; a struct's don't) —
        field-name overlap corroboration alone is not enough to accept a
        match across that boundary. `is_union` must agree first."""
        header = RecordType(
            name="Foo", kind="struct", is_union=False,
            fields=[TypeField(name="x", type="int")],
        )
        dwarf_union = RecordType(
            name="impl::Foo", kind="union", is_union=True, size_bits=64,
            fields=[TypeField(name="x", type="int", offset_bits=0)],
        )
        out = backfill_dwarf_layout([header], [dwarf_union])
        assert out[0].size_bits is None

    def test_leaves_opaque_type_untouched(self) -> None:
        header = RecordType(name="Handle", kind="struct", is_opaque=True)
        dwarf = RecordType(name="Handle", kind="struct", size_bits=64)
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits is None
        assert out[0].is_opaque

    def test_leaves_template_pattern_untouched(self) -> None:
        """A class template's pattern body (e.g. clang's CXXRecordDecl inside a
        ClassTemplateDecl) shares the bare name "Foo" with any unrelated real
        type or instantiation named "Foo" in DWARF — matching it by name would
        silently attach the wrong layout, since the pattern itself has no
        fixed layout for any one instantiation (Codex review)."""
        header = RecordType(
            name="Buffer", kind="class", is_template_pattern=True,
            fields=[TypeField(name="data_", type="T *")],
        )
        dwarf = RecordType(name="Buffer", kind="class", size_bits=128)
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits is None
        assert out[0].is_template_pattern
        assert [f.name for f in out[0].fields] == ["data_"]

    def test_matches_namespaced_dwarf_name_by_unambiguous_suffix(self) -> None:
        """The clang header backend emits a bare name ("Foo") while DWARF
        qualifies it ("api::Foo"); an unambiguous suffix match must still
        recover the layout (Codex review)."""
        header = RecordType(name="Foo", kind="struct", fields=[TypeField(name="v", type="int")])
        dwarf = RecordType(
            name="api::Foo", kind="struct", size_bits=32,
            fields=[TypeField(name="v", type="int", offset_bits=0)],
        )
        out = backfill_dwarf_layout([header], [dwarf])
        assert out[0].size_bits == 32
        assert out[0].fields[0].offset_bits == 0

    def test_ambiguous_suffix_is_never_guessed(self) -> None:
        """Two different DWARF types sharing a bare suffix (different
        namespaces) must never be silently attached to the wrong header
        type — this is the dangerous half of the namespace-matching gap
        (Codex review): a wrong match would be silent data corruption, not
        just a missed backfill."""
        header = RecordType(name="Foo", kind="struct")
        dwarf_a = RecordType(name="ns_a::Foo", kind="struct", size_bits=32)
        dwarf_b = RecordType(name="ns_b::Foo", kind="struct", size_bits=999)
        out = backfill_dwarf_layout([header], [dwarf_a, dwarf_b])
        assert out[0].size_bits is None

    def test_global_and_namespaced_same_bare_name_is_never_guessed(self) -> None:
        """A global ``Foo`` matches the header's bare "Foo" by exact name just
        as validly as a namespaced ``api::Foo`` matches it by suffix — an
        exact-match-first lookup would silently pick the global one and never
        even reach the ambiguity check (Codex review: the dangerous mixed
        global/namespaced case). Both must be left unmatched."""
        header = RecordType(name="Foo", kind="struct")
        dwarf_global = RecordType(name="Foo", kind="struct", size_bits=64)
        dwarf_namespaced = RecordType(name="api::Foo", kind="struct", size_bits=999)
        out = backfill_dwarf_layout([header], [dwarf_global, dwarf_namespaced])
        assert out[0].size_bits is None

    def test_no_dwarf_types_is_a_no_op(self) -> None:
        header = RecordType(name="Point", kind="struct")
        assert backfill_dwarf_layout([header], []) == [header]
