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

"""A namespaced struct's size/alignment change reported independently by
the L2 header-tier detector (``diff_types._diff_type_pair``, keyed by
``RecordType.name`` -- deliberately bare) and the L1 DWARF-tier detector
(``diff_platform._diff_struct_layouts``, keyed by ``dwarf_metadata``'s own
fully-qualified ``_process_struct`` key) must collapse to one finding, not
two, once both tiers observe the same struct.

Item 5 of the abicheck code-review report ("Bare-vs-qualified duplicate
keys survive outside enum kinds") -- this is exactly the enum bridge
(``tests/test_enum_cross_tier_dedup.py``) generalized to struct/type
kinds, which had no equivalent bridge at all before this fix: neither
``diff_filtering._dedup_cross_kind``'s exact ``(kind, symbol)`` match nor
``_deduplicate_cross_detector``'s identity-keyed dedup (which never even
included these kinds in ``_DEDUP_CATEGORIES``) could recognize a
namespaced struct's two differently-qualified spellings as the same
finding.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_filtering import _dedup_cross_kind, _deduplicate_ast_dwarf
from abicheck.diff_helpers import canonicalize_record_symbol, record_canonical_names
from abicheck.dwarf_metadata import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model import AbiSnapshot, RecordType, TypeField


def _rec(name: str, qualified: str, size_bits: int) -> RecordType:
    return RecordType(
        name=name, kind="struct", qualified_name=qualified, size_bits=size_bits
    )


def _snap(
    version: str, byte_size: int, *, name: str = "Widget", qualified: str = "ns::Widget"
) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib.so",
        version=version,
        types=[_rec(name, qualified, byte_size * 8)],
        dwarf=DwarfMetadata(
            has_dwarf=True,
            structs={qualified: StructLayout(name=qualified, byte_size=byte_size)},
        ),
    )


# ── Primitive-level: the bare/qualified bridging itself ──────────────────


class TestRecordCanonicalNames:
    def test_bare_and_qualified_both_map_to_the_qualified_form(self) -> None:
        names = record_canonical_names(_snap("1", 8))
        assert names["Widget"] == "ns::Widget"
        assert names["ns::Widget"] == "ns::Widget"

    def test_no_qualified_name_registers_nothing(self) -> None:
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[RecordType(name="Widget", kind="struct", size_bits=64)],
        )
        assert "Widget" not in record_canonical_names(snap)

    def test_none_snapshot_is_empty(self) -> None:
        assert record_canonical_names(None) == {}

    def test_ambiguous_bare_name_across_two_records_is_not_registered(self) -> None:
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[_rec("Widget", "a::Widget", 32), _rec("Widget", "b::Widget", 64)],
        )
        names = record_canonical_names(snap)
        assert "Widget" not in names
        assert names["a::Widget"] == "a::Widget"
        assert names["b::Widget"] == "b::Widget"

    def test_a_global_record_competing_with_a_namespaced_one_is_not_bridged(
        self,
    ) -> None:
        """Codex review: a genuinely global (unqualified) ``Widget`` sharing
        a bare name with a namespaced ``ns::Widget`` must not be silently
        bridged to it -- skipping the unqualified record entirely (an
        earlier revision's bug) let it happen anyway."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[
                RecordType(name="Widget", kind="struct", size_bits=32),
                _rec("Widget", "ns::Widget", 64),
            ],
        )
        names = record_canonical_names(snap)
        assert "Widget" not in names
        assert names["ns::Widget"] == "ns::Widget"

    def test_a_dwarf_only_global_record_competing_with_a_namespaced_header_type_is_not_bridged(
        self,
    ) -> None:
        """Codex review, fresh evidence: a record DWARF sees but the header
        surface never exposes (private, not header-declared) contributes no
        ``snap.types`` entry at all -- so the earlier fix, which only scans
        ``snap.types``, still missed this exact competing-identity shape."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[_rec("Widget", "ns::Widget", 64)],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={"Widget": StructLayout(name="Widget", byte_size=32)},
            ),
        )
        names = record_canonical_names(snap)
        assert "Widget" not in names
        assert names["ns::Widget"] == "ns::Widget"

    def test_a_dwarf_only_global_template_specialization_is_not_bridged_via_its_own_template_argument_colon(
        self,
    ) -> None:
        """Codex review, fresh evidence: a global (unqualified) DWARF-only
        record whose own name embeds a namespaced template argument
        (``"Wrapper<dep::Tag>"``) has no depth-zero ``"::"`` at all -- a
        naive ``rsplit("::", 1)[-1]`` mistakes the argument's own ``"::"``
        for a namespace boundary, extracting the corrupted bare name
        ``"Tag>"`` instead of registering the record under its real,
        already-bare identity. That silently let it not compete for its own
        bare name at all, so an unrelated, differently-namespaced header
        type sharing that exact spelling (``"ns::Wrapper<dep::Tag>"``,
        header-bare ``"Wrapper<dep::Tag>"``) was wrongly bridged as if the
        global DWARF-only record were never in the picture."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[_rec("Wrapper<dep::Tag>", "ns::Wrapper<dep::Tag>", 64)],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "Wrapper<dep::Tag>": StructLayout(
                        name="Wrapper<dep::Tag>", byte_size=32
                    )
                },
            ),
        )
        names = record_canonical_names(snap)
        assert "Wrapper<dep::Tag>" not in names
        assert names["ns::Wrapper<dep::Tag>"] == "ns::Wrapper<dep::Tag>"


class TestCanonicalizeRecordSymbol:
    def test_bare_whole_type_symbol_resolves_to_qualified(self) -> None:
        names = record_canonical_names(_snap("1", 8))
        assert canonicalize_record_symbol("Widget", names) == "ns::Widget"

    def test_qualified_whole_type_symbol_resolves_to_itself(self) -> None:
        names = record_canonical_names(_snap("1", 8))
        assert canonicalize_record_symbol("ns::Widget", names) == "ns::Widget"

    def test_bare_field_qualified_symbol_resolves_its_type_prefix(self) -> None:
        """STRUCT_FIELD_* kinds carry "Type::field" -- only the type-name
        prefix should ever be rewritten, never the field name itself."""
        names = record_canonical_names(_snap("1", 8))
        assert (
            canonicalize_record_symbol("Widget::x", names, None, "x") == "ns::Widget::x"
        )

    def test_unrelated_symbol_is_returned_unchanged(self) -> None:
        names = record_canonical_names(_snap("1", 8))
        assert canonicalize_record_symbol("Unrelated", names) == "Unrelated"

    def test_qualified_hint_wins_over_an_ambiguous_bare_table(self) -> None:
        """Codex review: an ambiguous bare name has no table entry at all,
        but a caller that already knows exactly which type it matched
        (``qualified_hint``) must still resolve correctly."""
        names: dict[str, str] = {}  # simulates the ambiguous, no-entry case
        assert canonicalize_record_symbol("Widget", names, "a::Widget") == "a::Widget"
        assert (
            canonicalize_record_symbol("Widget::x", names, "a::Widget", "x")
            == "a::Widget::x"
        )

    def test_field_name_is_the_only_signal_for_field_qualification(self) -> None:
        """Codex review: a scoped *whole-type* symbol containing ``::`` (a
        template specialization over a namespaced argument) must never be
        corrupted by a stale ``"::" in symbol`` guess -- only an explicit
        ``field_name`` may split a symbol into parent + field."""
        names: dict[str, str] = {}
        symbol = "Wrapper<dep::Tag>"
        # No field_name given: the whole scoped symbol is the parent, and the
        # qualified_hint replaces it wholesale -- it must not be corrupted
        # into "Wrapper<dep::Tag>::Tag>".
        assert (
            canonicalize_record_symbol(symbol, names, "ns::Wrapper<dep::Tag>")
            == "ns::Wrapper<dep::Tag>"
        )
        # A genuinely field-qualified symbol on the same scoped type still
        # resolves correctly once field_name is given explicitly.
        assert (
            canonicalize_record_symbol(
                f"{symbol}::count", names, "ns::Wrapper<dep::Tag>", "count"
            )
            == "ns::Wrapper<dep::Tag>::count"
        )

    def test_qualified_hint_of_none_falls_back_to_the_table(self) -> None:
        names = record_canonical_names(_snap("1", 8))
        assert canonicalize_record_symbol("Widget", names, None) == "ns::Widget"


# ── _dedup_cross_kind / _deduplicate_ast_dwarf must actually bridge it ────


class TestDedupCrossKindBridgesBareAndQualified:
    def test_without_record_names_the_duplicate_survives(self) -> None:
        """Documents exactly what the bridge closes: with no record_names
        given, a namespaced struct's two tier-specific spellings do not
        match, so the pre-existing exact-string behavior keeps both."""
        header_tier = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Widget", description="d"
        )
        dwarf_tier = Change(
            kind=ChangeKind.STRUCT_SIZE_CHANGED, symbol="ns::Widget", description="d"
        )
        result = _dedup_cross_kind([header_tier, dwarf_tier])
        assert len(result) == 2

    def test_with_record_names_the_duplicate_collapses(self) -> None:
        header_tier = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Widget", description="d"
        )
        dwarf_tier = Change(
            kind=ChangeKind.STRUCT_SIZE_CHANGED, symbol="ns::Widget", description="d"
        )
        names = {"Widget": "ns::Widget", "ns::Widget": "ns::Widget"}
        result = _dedup_cross_kind([header_tier, dwarf_tier], names)
        assert len(result) == 1
        assert result[0].kind is ChangeKind.TYPE_SIZE_CHANGED

    def test_deduplicate_ast_dwarf_bridges_given_the_snapshots(self) -> None:
        old = _snap("1", 8)
        new = _snap("2", 16)
        header_tier = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Widget", description="d"
        )
        dwarf_tier = Change(
            kind=ChangeKind.STRUCT_SIZE_CHANGED, symbol="ns::Widget", description="d"
        )
        result = _deduplicate_ast_dwarf([header_tier, dwarf_tier], old, new)
        assert len(result) == 1

    def test_deduplicate_ast_dwarf_without_snapshots_degrades_to_no_dedup(self) -> None:
        """A caller with no old/new at hand must not regress: this is a
        missed dedup, never an incorrect one."""
        header_tier = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Widget", description="d"
        )
        dwarf_tier = Change(
            kind=ChangeKind.STRUCT_SIZE_CHANGED, symbol="ns::Widget", description="d"
        )
        assert len(_deduplicate_ast_dwarf([header_tier, dwarf_tier])) == 2

    def test_field_level_parent_match_bridges_too(self) -> None:
        """FIX-F parent-type matching (STRUCT_FIELD_* naming a bare parent
        via a coarser AST-tier finding) must also benefit from the bridge."""
        header_tier = Change(
            kind=ChangeKind.TYPE_FIELD_OFFSET_CHANGED, symbol="Widget", description="d"
        )
        dwarf_tier = Change(
            kind=ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
            symbol="ns::Widget::x",
            description="d",
        )
        names = {"Widget": "ns::Widget", "ns::Widget": "ns::Widget"}
        result = _dedup_cross_kind([header_tier, dwarf_tier], names)
        assert len(result) == 1

    def test_field_level_parent_match_requires_the_same_field(self) -> None:
        """Codex review on PR #873: an AST-tier field-level ``Change.symbol``
        names only the parent type ("Widget"), never the field -- so a
        parent-only match would drop a DWARF finding for field ``y`` merely
        because field ``x`` of the *same* type also changed at the AST tier.
        Both findings carry a distinct ``field_name`` now, so they must
        survive as two findings, not collapse into one."""
        header_tier = Change(
            kind=ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
            symbol="Widget",
            description="d",
            field_name="x",
        )
        dwarf_tier = Change(
            kind=ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
            symbol="ns::Widget::y",
            description="d",
            field_name="y",
        )
        names = {"Widget": "ns::Widget", "ns::Widget": "ns::Widget"}
        result = _dedup_cross_kind([header_tier, dwarf_tier], names)
        assert len(result) == 2, result

    def test_field_level_parent_match_still_bridges_the_same_field(self) -> None:
        """The field-identity requirement must not regress the ordinary
        case: the same field changing at both tiers still collapses."""
        header_tier = Change(
            kind=ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
            symbol="Widget",
            description="d",
            field_name="x",
        )
        dwarf_tier = Change(
            kind=ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
            symbol="ns::Widget::x",
            description="d",
            field_name="x",
        )
        names = {"Widget": "ns::Widget", "ns::Widget": "ns::Widget"}
        result = _dedup_cross_kind([header_tier, dwarf_tier], names)
        assert len(result) == 1


# ── End-to-end through compare() ──────────────────────────────────────────


class TestEndToEndOnlyOneFindingSurvivesForANamespacedStruct:
    def test_struct_size_changed_across_both_tiers_collapses_to_one(self) -> None:
        old = _snap("1", 8)
        new = _snap("2", 16)
        result = compare(old, new)
        size_changes = [
            c
            for c in result.changes
            if c.kind in (ChangeKind.TYPE_SIZE_CHANGED, ChangeKind.STRUCT_SIZE_CHANGED)
        ]
        assert len(size_changes) == 1

    def test_unrelated_namespaced_structs_are_not_accidentally_merged(self) -> None:
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[_rec("Widget", "ns::Widget", 64), _rec("Gadget", "ns::Gadget", 32)],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "ns::Widget": StructLayout(name="ns::Widget", byte_size=8),
                    "ns::Gadget": StructLayout(name="ns::Gadget", byte_size=4),
                },
            ),
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            types=[_rec("Widget", "ns::Widget", 128), _rec("Gadget", "ns::Gadget", 32)],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "ns::Widget": StructLayout(name="ns::Widget", byte_size=16),
                    "ns::Gadget": StructLayout(name="ns::Gadget", byte_size=4),
                },
            ),
        )
        result = compare(old, new)
        size_changes = [
            c
            for c in result.changes
            if c.kind in (ChangeKind.TYPE_SIZE_CHANGED, ChangeKind.STRUCT_SIZE_CHANGED)
        ]
        assert len(size_changes) == 1
        assert size_changes[0].symbol in ("Widget", "ns::Widget")

    def test_bare_name_collision_across_namespaces_still_dedups(self) -> None:
        """Codex review on PR #873: ``a::Widget`` and ``b::Widget`` share
        the bare name ``Widget``, which makes ``record_canonical_names``
        correctly decline to bridge that bare name at all (genuinely
        ambiguous). Without a per-finding qualified-identity hint, the AST-
        tier ``TYPE_SIZE_CHANGED`` for ``a::Widget`` (bare symbol
        ``Widget``) could never be bridged to the DWARF-tier
        ``STRUCT_SIZE_CHANGED`` for the same struct (qualified symbol
        ``a::Widget``), so both survived as two separate findings for one
        real change. ``diff_types._append_type_size_and_alignment_changes``
        now stamps ``Change.qualified_name`` directly from the matched
        ``RecordType`` pair, which ``canonicalize_record_symbol`` prefers
        over the (necessarily ambiguous) table lookup."""
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[_rec("Widget", "a::Widget", 64), _rec("Widget", "b::Widget", 32)],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "a::Widget": StructLayout(name="a::Widget", byte_size=8),
                    "b::Widget": StructLayout(name="b::Widget", byte_size=4),
                },
            ),
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            types=[_rec("Widget", "a::Widget", 128), _rec("Widget", "b::Widget", 32)],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "a::Widget": StructLayout(name="a::Widget", byte_size=16),
                    "b::Widget": StructLayout(name="b::Widget", byte_size=4),
                },
            ),
        )
        result = compare(old, new)
        size_changes = [
            c
            for c in result.changes
            if c.kind in (ChangeKind.TYPE_SIZE_CHANGED, ChangeKind.STRUCT_SIZE_CHANGED)
        ]
        assert len(size_changes) == 1, size_changes
        assert size_changes[0].symbol in ("Widget", "a::Widget")

    def test_two_different_fields_changing_at_each_tier_both_survive(self) -> None:
        """Codex review on PR #873: field ``x`` changes only visibly at the
        AST tier and field ``y`` only at the DWARF tier (of the same
        namespaced struct). Before the field-identity fix, the parent-type
        match in ``_dedup_cross_kind`` would wrongly drop the DWARF ``y``
        finding merely because *some* field-level AST finding exists for
        the same parent type -- silently losing a real, distinct change."""
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[
                RecordType(
                    name="Widget",
                    kind="struct",
                    qualified_name="ns::Widget",
                    size_bits=128,
                    fields=[
                        TypeField(name="x", type="int", offset_bits=0),
                        TypeField(name="y", type="int", offset_bits=64),
                    ],
                )
            ],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "ns::Widget": StructLayout(
                        name="ns::Widget",
                        byte_size=16,
                        fields=[
                            FieldInfo(
                                name="x", type_name="int", byte_offset=0, byte_size=4
                            ),
                            FieldInfo(
                                name="y", type_name="int", byte_offset=8, byte_size=4
                            ),
                        ],
                    )
                },
            ),
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            types=[
                RecordType(
                    name="Widget",
                    kind="struct",
                    qualified_name="ns::Widget",
                    size_bits=128,
                    fields=[
                        # x's offset moved -- an AST-tier-only observation.
                        TypeField(name="x", type="int", offset_bits=32),
                        TypeField(name="y", type="int", offset_bits=64),
                    ],
                )
            ],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "ns::Widget": StructLayout(
                        name="ns::Widget",
                        byte_size=16,
                        fields=[
                            FieldInfo(
                                name="x", type_name="int", byte_offset=0, byte_size=4
                            ),
                            # y's offset moved -- a DWARF-tier-only observation.
                            FieldInfo(
                                name="y", type_name="int", byte_offset=12, byte_size=4
                            ),
                        ],
                    )
                },
            ),
        )
        result = compare(old, new)
        offset_changes = [
            c
            for c in result.changes
            if c.kind
            in (
                ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
                ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
            )
        ]
        field_names = {c.field_name for c in offset_changes}
        assert field_names == {"x", "y"}, offset_changes


# ── Structured identity on the three TYPE_FIELD_* emitters ────────────────


class TestTypeFieldEmittersStampStructuredIdentity:
    """Codex review on PR #873: TYPE_FIELD_REMOVED/_OFFSET_CHANGED/_TYPE_CHANGED
    must carry both ``qualified_name`` (mirroring
    ``_append_type_size_and_alignment_changes``) and ``field_name`` (needed
    by ``_dedup_cross_kind``'s field-identity check above)."""

    def _pair(self, old_fields: list[TypeField], new_fields: list[TypeField]) -> tuple:
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[
                RecordType(
                    name="Widget",
                    kind="struct",
                    qualified_name="ns::Widget",
                    size_bits=64,
                    fields=old_fields,
                )
            ],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            types=[
                RecordType(
                    name="Widget",
                    kind="struct",
                    qualified_name="ns::Widget",
                    size_bits=64,
                    fields=new_fields,
                )
            ],
        )
        return old, new

    def test_offset_changed_carries_qualified_and_field_name(self) -> None:
        old, new = self._pair(
            [TypeField(name="x", type="int", offset_bits=0)],
            [TypeField(name="x", type="int", offset_bits=32)],
        )
        result = compare(old, new)
        (c,) = [
            c for c in result.changes if c.kind is ChangeKind.TYPE_FIELD_OFFSET_CHANGED
        ]
        assert c.qualified_name == "ns::Widget"
        assert c.field_name == "x"

    def test_type_changed_carries_qualified_and_field_name(self) -> None:
        old, new = self._pair(
            [TypeField(name="x", type="int", offset_bits=0)],
            [TypeField(name="x", type="double", offset_bits=0)],
        )
        result = compare(old, new)
        (c,) = [
            c for c in result.changes if c.kind is ChangeKind.TYPE_FIELD_TYPE_CHANGED
        ]
        assert c.qualified_name == "ns::Widget"
        assert c.field_name == "x"

    def test_removed_carries_qualified_and_field_name(self) -> None:
        old, new = self._pair([TypeField(name="x", type="int", offset_bits=0)], [])
        result = compare(old, new)
        (c,) = [c for c in result.changes if c.kind is ChangeKind.TYPE_FIELD_REMOVED]
        assert c.qualified_name == "ns::Widget"
        assert c.field_name == "x"


# ── Primitive-level property tests (Codex review) ──────────────────────────
#
# The fixed examples above are each shaped to confirm one specific bug this
# module's own review history already found; per this repo's own "Primitive-
# level property tests" convention (AGENTS.md), a reusable identity-merge
# primitive additionally needs invariant-style tests that search the input
# space the way an adversarial reviewer does, rather than only re-checking
# the inputs a fix's own author already thought of.

_BARE_NAMES = ("Widget", "Gadget")
_NAMESPACES = (None, "a", "b")  # None == global/unqualified


def _qualify(ns: str | None, bare: str) -> str:
    return f"{ns}::{bare}" if ns is not None else bare


@st.composite
def _record_specs(draw):
    """A small, collision-prone population of (bare, namespace) pairs, each
    independently placed as a header ``RecordType``, a DWARF-only key, or
    both -- exactly the two competitor sources ``record_canonical_names``
    reads."""
    n = draw(st.integers(min_value=0, max_value=4))
    specs = []
    for _ in range(n):
        bare = draw(st.sampled_from(_BARE_NAMES))
        ns = draw(st.sampled_from(_NAMESPACES))
        source = draw(st.sampled_from(("header", "dwarf", "both")))
        specs.append((bare, ns, source))
    return specs


def _build_snapshot(specs) -> AbiSnapshot:
    types = []
    dwarf_structs: dict[str, StructLayout] = {}
    for bare, ns, source in specs:
        qualified = _qualify(ns, bare)
        if source in ("header", "both"):
            types.append(
                RecordType(
                    name=bare,
                    kind="struct",
                    qualified_name=(qualified if ns is not None else None),
                )
            )
        if source in ("dwarf", "both"):
            dwarf_structs[qualified] = StructLayout(name=qualified, byte_size=1)
    return AbiSnapshot(
        library="lib.so",
        version="1",
        types=types,
        dwarf=DwarfMetadata(has_dwarf=True, structs=dwarf_structs),
    )


def _competing_identities(specs, bare: str) -> set[str | None]:
    """Every identity (a qualified name, or ``None`` for a global/unqualified
    competitor) *any* source in *specs* registers for *bare* -- the ground
    truth ``record_canonical_names`` must agree with."""
    out: set[str | None] = set()
    for spec_bare, ns, _source in specs:
        if spec_bare != bare:
            continue
        out.add(_qualify(ns, bare) if ns is not None else None)
    return out


class TestRecordCanonicalNamesProperties:
    @given(specs=_record_specs())
    def test_never_fabricates_an_identity_not_present_in_the_input(self, specs) -> None:
        """Every ``bare -> qualified`` entry in the output must be an
        identity some record in the snapshot actually declared -- the
        primitive may omit a bare name (real ambiguity), but it may never
        invent a mapping to a qualified spelling nothing in the input used."""
        snap = _build_snapshot(specs)
        names = record_canonical_names(snap)
        for bare, qualified in names.items():
            # A qualified spelling always maps to itself too (identity, not
            # a bridge guess) -- only a *bare* key's mapping needs checking
            # against what the input actually declared under that bare name.
            assert qualified == bare or qualified in _competing_identities(specs, bare)

    @given(specs=_record_specs())
    def test_never_bridges_a_bare_name_with_two_or_more_competitors(
        self, specs
    ) -> None:
        """A bare name backed by two or more distinct identities (including
        an unqualified/global competitor, encoded as ``None``) must not
        appear in the output at all -- silently preferring one guess over
        another is exactly the false-bridge bug this module's own review
        history repeatedly found."""
        snap = _build_snapshot(specs)
        names = record_canonical_names(snap)
        for bare in _BARE_NAMES:
            competitors = _competing_identities(specs, bare)
            if len(competitors) >= 2:
                assert bare not in names

    @given(specs=_record_specs())
    def test_a_uniquely_identified_bare_name_is_always_bridged(self, specs) -> None:
        """The converse of the ambiguity guard: when exactly one qualified
        identity backs a bare name (no competing global or differently-
        namespaced declaration), the bridge must actually fire -- an
        overly conservative primitive that never bridges anything would
        vacuously satisfy the ambiguity property above without doing its
        job."""
        snap = _build_snapshot(specs)
        names = record_canonical_names(snap)
        for bare in _BARE_NAMES:
            competitors = _competing_identities(specs, bare)
            if len(competitors) == 1:
                (only,) = competitors
                if only is not None:
                    assert names.get(bare) == only

    @given(specs=_record_specs(), seed=st.integers())
    def test_order_independent(self, specs, seed) -> None:
        """The result must not depend on the order records happen to appear
        in the snapshot -- a real castxml/DWARF dump's own declaration
        order is not a semantic signal this primitive should key on."""
        import random

        shuffled = list(specs)
        random.Random(seed).shuffle(shuffled)
        assert record_canonical_names(_build_snapshot(specs)) == record_canonical_names(
            _build_snapshot(shuffled)
        )


class TestCanonicalizeRecordSymbolProperties:
    @given(
        symbol=st.text(min_size=0, max_size=12),
        qualified_hint=st.text(min_size=1, max_size=12),
        field_name=st.one_of(st.none(), st.text(min_size=1, max_size=8)),
    )
    def test_qualified_hint_always_wins_when_field_name_matches(
        self, symbol, qualified_hint, field_name
    ) -> None:
        """An explicit ``qualified_hint`` (the emitting detector already
        knows exactly which type it matched) must always be used verbatim
        for the type portion, regardless of what the ambiguity table would
        have guessed -- that table is intentionally not even consulted
        when a hint is given."""
        if field_name is not None:
            symbol = f"{symbol}::{field_name}"
        result = canonicalize_record_symbol(symbol, {}, qualified_hint, field_name)
        expected = f"{qualified_hint}::{field_name}" if field_name else qualified_hint
        assert result == expected

    @given(symbol=st.text(min_size=0, max_size=16))
    def test_empty_table_and_no_hint_is_always_identity(self, symbol) -> None:
        """With no bridging information at all (record_names empty, no
        qualified_hint), the symbol is returned byte-for-byte unchanged --
        regardless of how many ``::`` it contains -- since field_name is
        the only signal that ever triggers a split (Codex review: an
        earlier revision guessed field-qualification from a bare ``"::" in
        symbol`` check, corrupting a scoped whole-type symbol like a
        template specialization over a namespaced argument)."""
        assert canonicalize_record_symbol(symbol, {}, None, None) == symbol
