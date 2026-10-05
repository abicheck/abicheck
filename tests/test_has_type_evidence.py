"""``diff_types._has_type_evidence``: each type-evidence source, alone."""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot, RecordType


def _elf_snapshot(*, types=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1",
        types=types or [],
        elf_only_mode=True,
        platform="elf",
        language_profile="cpp",
    )


def _evidence_snapshot(source: str) -> AbiSnapshot:
    """An ELF-only snapshot carrying exactly one type-evidence source."""
    from abicheck.model import EnumType
    from abicheck.model.dwarf_facts import DwarfMetadata, EnumInfo, StructLayout

    snap = _elf_snapshot(
        types=[RecordType(name="R", kind="struct", size_bits=32)]
        if source == "types"
        else None
    )
    if source == "enums":
        snap.declarations.enums = [EnumType(name="E", members=[])]
    elif source == "typedefs":
        snap.declarations.typedefs = {"T": "int"}
    elif source == "dwarf_structs":
        snap.declarations.debug_layout = DwarfMetadata(
            structs={"S": StructLayout(name="S", byte_size=4)}, has_dwarf=True
        )
    elif source == "dwarf_enums":
        snap.declarations.debug_layout = DwarfMetadata(
            enums={"E": EnumInfo(name="E", underlying_byte_size=4)}, has_dwarf=True
        )
    elif source == "dwarf_empty":
        snap.declarations.debug_layout = DwarfMetadata(has_dwarf=True)
    return snap


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("none", False),
        ("types", True),
        ("enums", True),
        ("typedefs", True),
        ("dwarf_structs", True),
        ("dwarf_enums", True),
        # A present-but-empty debug section is not type evidence.
        ("dwarf_empty", False),
    ],
)
def test_has_type_evidence_each_source_alone(source: str, expected: bool) -> None:
    """Each evidence source, alone, decides the answer: dropping or weakening
    any one disjunct of ``_has_type_evidence`` flips exactly one row."""
    from abicheck.diff_types import _has_type_evidence

    assert _has_type_evidence(_evidence_snapshot(source)) is expected
