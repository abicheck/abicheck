from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.model import AbiSnapshot


def test_advanced_dwarf_detector_reports_coverage_gap_when_unsupported() -> None:
    old = AbiSnapshot(library="libx.so", version="1.0")
    new = AbiSnapshot(library="libx.so", version="2.0")

    result = compare(old, new)
    adv = next(d for d in result.detector_results if d.name == "advanced_dwarf")
    assert adv.enabled is False
    assert adv.coverage_gap == "missing DWARF advanced metadata"


def test_advanced_dwarf_detector_requires_both_sides() -> None:
    old = AbiSnapshot(
        library="libx.so",
        version="1.0",
        dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True),
    )
    new = AbiSnapshot(library="libx.so", version="2.0")

    result = compare(old, new)
    adv = next(d for d in result.detector_results if d.name == "advanced_dwarf")
    assert adv.enabled is False
    assert adv.coverage_gap == "missing DWARF advanced metadata"


def test_advanced_dwarf_detector_enabled_when_both_have_metadata() -> None:
    """Detector is enabled when both snapshots have dwarf_advanced."""
    old = AbiSnapshot(
        library="libx.so",
        version="1.0",
        dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True),
    )
    new = AbiSnapshot(
        library="libx.so",
        version="2.0",
        dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True),
    )
    result = compare(old, new)
    adv = next(d for d in result.detector_results if d.name == "advanced_dwarf")
    assert adv.enabled is True
    assert adv.coverage_gap is None


def test_advanced_dwarf_detector_finds_calling_convention_change() -> None:
    """Detector reports CALLING_CONVENTION_CHANGED when CC differs."""
    old = AbiSnapshot(
        library="libx.so",
        version="1.0",
        dwarf_advanced=AdvancedDwarfMetadata(
            has_dwarf=True,
            calling_conventions={"_Z3foov": "normal"},
        ),
    )
    new = AbiSnapshot(
        library="libx.so",
        version="2.0",
        dwarf_advanced=AdvancedDwarfMetadata(
            has_dwarf=True,
            calling_conventions={"_Z3foov": "stdcall"},
        ),
    )
    result = compare(old, new)
    assert any(c.kind == ChangeKind.CALLING_CONVENTION_CHANGED for c in result.changes)


def test_advanced_dwarf_detector_finds_packing_change() -> None:
    """Detector reports STRUCT_PACKING_CHANGED when packing status changes."""
    old = AbiSnapshot(
        library="libx.so",
        version="1.0",
        dwarf_advanced=AdvancedDwarfMetadata(
            has_dwarf=True,
            packed_structs=set(),
            all_struct_names={"MyStruct"},
        ),
    )
    new = AbiSnapshot(
        library="libx.so",
        version="2.0",
        dwarf_advanced=AdvancedDwarfMetadata(
            has_dwarf=True,
            packed_structs={"MyStruct"},
            all_struct_names={"MyStruct"},
        ),
    )
    result = compare(old, new)
    assert any(c.kind == ChangeKind.STRUCT_PACKING_CHANGED for c in result.changes)


def test_advanced_dwarf_detector_no_changes_when_identical() -> None:
    """Detector produces no changes when metadata is identical."""
    meta = AdvancedDwarfMetadata(
        has_dwarf=True,
        calling_conventions={"_Z3foov": "normal"},
        packed_structs=set(),
        all_struct_names={"A"},
    )
    old = AbiSnapshot(library="libx.so", version="1.0", dwarf_advanced=meta)
    new = AbiSnapshot(library="libx.so", version="2.0", dwarf_advanced=meta)

    result = compare(old, new)
    dwarf_kinds = {
        ChangeKind.CALLING_CONVENTION_CHANGED,
        ChangeKind.STRUCT_PACKING_CHANGED,
        ChangeKind.TOOLCHAIN_FLAG_DRIFT,
        ChangeKind.FRAME_REGISTER_CHANGED,
        ChangeKind.VALUE_ABI_TRAIT_CHANGED,
    }
    assert not any(c.kind in dwarf_kinds for c in result.changes)
