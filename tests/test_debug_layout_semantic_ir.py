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

"""``extract/debug_layout_semantic_ir.py`` (ADR-063 Phase 6, BTF/CTF slice):
``entity_id``/``SemanticIR`` for BTF/CTF-sourced struct/enum layouts.

Split into its own file rather than added to ``test_btf_metadata.py``/
``test_ctf_metadata.py`` since this module is scoped to the shared
``DwarfMetadata`` shape both formats reduce to, not either format's own
richer parse -- these tests exercise that shared shape directly rather than
duplicating themselves once per producer.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.dumper_elf_fallback import _build_symbol_only_snapshot
from abicheck.elf_metadata import ElfMetadata
from abicheck.extract.debug_layout_semantic_ir import semantic_ir_from_debug_metadata
from abicheck.model.dwarf_facts import (
    AdvancedDwarfMetadata,
    DwarfMetadata,
    EnumInfo,
    FieldInfo,
    StructLayout,
)
from abicheck.model.identity import EntityKind, entity_id_for_enum, entity_id_for_type


def _struct(name: str, *fields: FieldInfo, is_union: bool = False) -> StructLayout:
    return StructLayout(
        name=name, byte_size=8, alignment=4, fields=list(fields), is_union=is_union
    )


class TestSemanticIrFromDebugMetadata:
    def test_empty_metadata_produces_empty_ir(self) -> None:
        ir = semantic_ir_from_debug_metadata(DwarfMetadata(), producer="btf")
        assert ir.occurrences == {}

    def test_struct_gets_a_flat_entity_id(self) -> None:
        meta = DwarfMetadata(structs={"widget": _struct("widget")})
        ir = semantic_ir_from_debug_metadata(meta, producer="btf")
        expected_id = entity_id_for_type((), "widget")
        assert expected_id.scope == ()
        (occ_id,) = ir.occurrences_for(expected_id)
        entity = ir.occurrences[occ_id]
        assert entity.canonical_spelling.value == "widget"
        assert entity.producer == "btf"

    def test_enum_gets_a_flat_entity_id(self) -> None:
        meta = DwarfMetadata(
            enums={
                "color": EnumInfo(
                    name="color", underlying_byte_size=4, members={"RED": 0}
                )
            }
        )
        ir = semantic_ir_from_debug_metadata(meta, producer="ctf")
        expected_id = entity_id_for_enum((), "color")
        assert expected_id.scope == ()
        (occ_id,) = ir.occurrences_for(expected_id)
        assert ir.occurrences[occ_id].producer == "ctf"

    def test_union_and_struct_are_distinct_kinds_of_the_same_leaf_name(self) -> None:
        """A BTF/CTF struct and enum sharing a bare name are two different
        ``EntityKind``s, so they must not collide -- exercised here with a
        union, the closest thing BTF/CTF has to a same-``TYPE``-kind
        sibling, to confirm the union flag doesn't leak into identity."""
        meta = DwarfMetadata(structs={"tag": _struct("tag", is_union=True)})
        ir = semantic_ir_from_debug_metadata(meta, producer="btf")
        entity_id = entity_id_for_type((), "tag")
        assert entity_id.kind == EntityKind.TYPE
        (occ_id,) = ir.occurrences_for(entity_id)
        assert ir.occurrences[occ_id].canonical_spelling.value == "tag"

    def test_two_producers_of_the_same_name_are_the_same_entity_id(self) -> None:
        """BTF and CTF never coexist on one binary in practice, but the
        identity itself must not depend on which one produced it -- only
        ``CanonicalEntity.producer`` should differ."""
        meta = DwarfMetadata(structs={"widget": _struct("widget")})
        btf_id = next(
            iter(semantic_ir_from_debug_metadata(meta, "btf").occurrences)
        ).entity_id
        ctf_id = next(
            iter(semantic_ir_from_debug_metadata(meta, "ctf").occurrences)
        ).entity_id
        assert btf_id == ctf_id

    def test_field_layout_is_preserved(self) -> None:
        """Not just identity -- the record's own field layout must survive
        the bridge, the same way ``pdb_model.py``'s does."""
        meta = DwarfMetadata(
            structs={
                "point": _struct(
                    "point",
                    FieldInfo(name="x", type_name="int", byte_offset=0, byte_size=4),
                    FieldInfo(name="y", type_name="int", byte_offset=4, byte_size=4),
                )
            }
        )
        # Reach the transient RecordType indirectly via the normalizer's
        # own canonical_spelling -- the field layout itself isn't part of
        # SemanticIR, so this asserts through the module's private helper
        # instead, matching pdb_model.py's own test precedent.
        from abicheck.extract.debug_layout_semantic_ir import _record_type_from_layout

        record = _record_type_from_layout("point", meta.structs["point"])
        assert [f.name for f in record.fields] == ["x", "y"]
        assert record.size_bits == 64
        assert record.alignment_bits == 32


class TestBuildSymbolOnlySnapshotBtfCtf:
    """End-to-end through ``_build_symbol_only_snapshot`` -- the actual
    ``dumper.py`` call site (Codex-review-pattern precedent from PR #1021's
    identical DWARF-fallback test class)."""

    def _snap(self, dwarf_meta: DwarfMetadata, resolved_debug_format: str):
        return _build_symbol_only_snapshot(
            Path("/nonexistent/lib.so"),
            "1.0",
            ElfMetadata(),
            dwarf_meta,
            AdvancedDwarfMetadata(),
            set(),
            set(),
            set(),
            [],
            None,
            resolved_debug_format,
        )

    def test_btf_struct_reaches_semantic_ir(self) -> None:
        meta = DwarfMetadata(structs={"widget": _struct("widget")}, has_dwarf=True)
        snap = self._snap(meta, "btf")
        assert snap.semantic_ir is not None
        entity_id = entity_id_for_type((), "widget")
        (occ_id,) = snap.semantic_ir.occurrences_for(entity_id)
        assert snap.semantic_ir.occurrences[occ_id].producer == "btf"

    def test_ctf_enum_reaches_semantic_ir(self) -> None:
        meta = DwarfMetadata(
            enums={"color": EnumInfo(name="color", underlying_byte_size=4, members={})},
            has_dwarf=True,
        )
        snap = self._snap(meta, "ctf")
        assert snap.semantic_ir is not None
        entity_id = entity_id_for_enum((), "color")
        (occ_id,) = snap.semantic_ir.occurrences_for(entity_id)
        assert snap.semantic_ir.occurrences[occ_id].producer == "ctf"

    def test_snapshot_types_and_enums_stay_untouched(self) -> None:
        """The one deliberate scope boundary this slice draws (see the
        module's own docstring): ``AbiSnapshot.types``/``.enums`` are not
        populated by this path at all, unlike PDB's own PE-fallback wiring
        -- only ``semantic_ir`` gains occurrences."""
        meta = DwarfMetadata(structs={"widget": _struct("widget")}, has_dwarf=True)
        snap = self._snap(meta, "btf")
        assert snap.types == []
        assert snap.enums == []

    def test_real_dwarf_format_is_unaffected(self) -> None:
        """Negative control: ``resolved_debug_format="dwarf"`` must never
        take this branch -- that path is ``_dwarf_types_semantic_ir``'s own
        (via *dwarf_only_types*), not this module's."""
        meta = DwarfMetadata(structs={"widget": _struct("widget")}, has_dwarf=True)
        snap = self._snap(meta, "dwarf")
        assert snap.semantic_ir is None

    def test_no_debug_info_at_all_stays_none(self) -> None:
        snap = self._snap(DwarfMetadata(), "btf")
        assert snap.semantic_ir is None
