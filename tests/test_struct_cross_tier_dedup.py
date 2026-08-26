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

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_filtering import _dedup_cross_kind, _deduplicate_ast_dwarf
from abicheck.diff_helpers import canonicalize_record_symbol, record_canonical_names
from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
from abicheck.model import AbiSnapshot, RecordType


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
        assert canonicalize_record_symbol("Widget::x", names) == "ns::Widget::x"

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
            canonicalize_record_symbol("Widget::x", names, "a::Widget")
            == "a::Widget::x"
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
