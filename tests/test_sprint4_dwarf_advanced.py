# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""Sprint 4 tests: advanced DWARF detectors (calling convention, packing, toolchain drift)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_advanced import (
    AdvancedDwarfMetadata,
    ToolchainInfo,
    _parse_producer,
    diff_advanced_dwarf,
    parse_advanced_dwarf,
)
from abicheck.model import AbiSnapshot
from abicheck.serialization import (
    snapshot_from_dict,
    snapshot_to_dict,
    snapshot_to_json,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _snap(adv: AdvancedDwarfMetadata | None) -> AbiSnapshot:
    s = AbiSnapshot(library="libx.so", version="v")
    s.dwarf_advanced = adv  # type: ignore[attr-defined]
    return s


def _adv(
    *,
    has_dwarf: bool = True,
    target_arch: str = "",
    calling: dict[str, str] | None = None,
    value_traits: dict[str, str] | None = None,
    packed: set[str] | None = None,
    flags: set[str] | None = None,
    all_structs: set[str] | None = None,
    frame_regs: dict[str, str] | None = None,
    callee_saved: dict[str, frozenset[str]] | None = None,
) -> AdvancedDwarfMetadata:
    packed_set = packed or set()
    # all_struct_names must include packed structs so diff guards work correctly
    struct_names = (all_structs or set()) | packed_set
    return AdvancedDwarfMetadata(
        has_dwarf=has_dwarf,
        target_arch=target_arch,
        toolchain=ToolchainInfo(
            producer_string="gcc",
            compiler="GCC",
            version="13.2",
            abi_flags=flags or set(),
        ),
        calling_conventions=calling or {},
        value_abi_traits=value_traits or {},
        packed_structs=packed_set,
        all_struct_names=struct_names,
        frame_registers=frame_regs or {},
        callee_saved_regs=callee_saved or {},
    )


# ── graceful degradation ──────────────────────────────────────────────────────

def test_diff_advanced_dwarf_no_dwarf() -> None:
    old = _adv(has_dwarf=False)
    new = _adv(has_dwarf=True)
    assert diff_advanced_dwarf(old, new) == []


def test_diff_both_no_dwarf() -> None:
    old = _adv(has_dwarf=False)
    new = _adv(has_dwarf=False)
    assert diff_advanced_dwarf(old, new) == []


# ── calling convention ────────────────────────────────────────────────────────

def test_calling_convention_changed() -> None:
    old = _snap(_adv(calling={"foo": "program"}))
    new = _snap(_adv(calling={"foo": "normal"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.CALLING_CONVENTION_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_calling_convention_added_non_default() -> None:
    # Both binaries have "foo" (present in both dicts); old is normal, new is vectorcall.
    # With full-dict storage, "normal" must be explicit so diff knows foo existed in old.
    old = _snap(_adv(calling={"foo": "normal"}))
    new = _snap(_adv(calling={"foo": "LLVM_vectorcall"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.CALLING_CONVENTION_CHANGED in kinds


def test_calling_convention_between_non_defaults() -> None:
    """Changed from one non-normal CC to another."""
    results = diff_advanced_dwarf(
        _adv(calling={"bar": "program"}),
        _adv(calling={"bar": "LLVM_vectorcall"}),
    )
    assert len(results) == 1
    assert results[0][0] == "calling_convention_changed"
    assert results[0][1] == "bar"
    assert results[0][3] == "program"
    assert results[0][4] == "LLVM_vectorcall"


def test_calling_convention_removed() -> None:
    """Non-default CC dropped back to normal (function still exists in both binaries)."""
    # Both dicts contain "foo": old has non-standard CC, new has "normal" explicitly.
    # This represents a function that changed CC, not a removed function.
    results = diff_advanced_dwarf(
        _adv(calling={"foo": "BORLAND_stdcall"}),
        _adv(calling={"foo": "normal"}),
    )
    assert len(results) == 1
    assert results[0][0] == "calling_convention_changed"
    assert results[0][3] == "BORLAND_stdcall"
    assert results[0][4] == "normal"


def test_calling_convention_unchanged_no_change() -> None:
    results = diff_advanced_dwarf(
        _adv(calling={"f": "program"}),
        _adv(calling={"f": "program"}),
    )
    assert results == []


def test_value_abi_trait_changed_breaking() -> None:
    # A parameter-position triviality flip (ret unchanged) stays a generic
    # value-ABI trait change; a *return*-position flip is the more specific
    # struct_return_convention_changed (see test below).
    old = _snap(_adv(value_traits={"foo": "ret:v(trivial)|p0:trivial"}))
    new = _snap(_adv(value_traits={"foo": "ret:v(trivial)|p0:nontrivial"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_return_trait_flip_is_struct_return_convention_changed() -> None:
    # A return-position triviality flip means the aggregate moved between
    # in-register return and hidden sret pointer — struct_return_convention_changed.
    old = _snap(_adv(value_traits={"foo": "ret:v(trivial)"}))
    new = _snap(_adv(value_traits={"foo": "ret:v(nontrivial)"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED in kinds
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED not in kinds
    assert r.verdict == Verdict.BREAKING


def test_return_trait_flip_on_sysv_amd64_arch_is_convention_change() -> None:
    # Explicit x86_64 arch → SysV AMD64 model, sret-flip classification applies.
    old = _snap(_adv(target_arch="x86_64", value_traits={"foo": "ret:v(trivial)"}))
    new = _snap(_adv(target_arch="x86_64", value_traits={"foo": "ret:v(nontrivial)"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED in kinds
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED not in kinds


def test_return_trait_flip_on_non_sysv_arch_is_generic_trait_change() -> None:
    # On AArch64 an HFA can be returned in vector registers despite being >16
    # bytes; on i386 every aggregate is memory-returned. The SysV-AMD64 16-byte
    # register model does not apply, so a return-triviality flip is reported as a
    # generic value-ABI trait change rather than a register<->sret convention flip.
    for arch in ("aarch64", "i386"):
        old = _snap(_adv(target_arch=arch, value_traits={"foo": "ret:v(trivial)"}))
        new = _snap(_adv(target_arch=arch, value_traits={"foo": "ret:v(nontrivial)"}))
        r = compare(old, new)
        kinds = {c.kind for c in r.changes}
        assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED not in kinds, arch
        assert ChangeKind.VALUE_ABI_TRAIT_CHANGED in kinds, arch
        # Still a value-ABI change → still breaking.
        assert r.verdict == Verdict.BREAKING


def test_return_trait_flip_mixed_arch_falls_back_to_generic() -> None:
    # If one side's arch is a known non-SysV target, do not claim a convention
    # flip — only when BOTH sides use the SysV-AMD64 return model.
    old = _snap(_adv(target_arch="x86_64", value_traits={"foo": "ret:v(trivial)"}))
    new = _snap(_adv(target_arch="aarch64", value_traits={"foo": "ret:v(nontrivial)"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED not in kinds
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED in kinds


def test_target_arch_round_trips_through_serialization() -> None:
    snap = _snap(_adv(target_arch="aarch64", value_traits={"foo": "ret:v(trivial)"}))
    restored = snapshot_from_dict(snapshot_to_dict(snap))
    assert restored.dwarf_advanced.target_arch == "aarch64"  # type: ignore[attr-defined]


def test_callee_saved_fallback_detects_calling_convention_drift() -> None:
    """ELF CFI fallback: saved rdi/rsi indicates ms_abi shift."""
    old = _snap(_adv(callee_saved={"foo": frozenset({"rbx", "rbp", "r12"})}))
    new = _snap(_adv(callee_saved={"foo": frozenset({"rbx", "rbp", "r12", "rdi", "rsi"})}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.CALLING_CONVENTION_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_callee_saved_fallback_ignores_non_marker_register_churn() -> None:
    """rbx/r12 churn alone is not enough to claim calling-convention drift."""
    old = _snap(_adv(callee_saved={"foo": frozenset({"rbx", "rbp", "r12"})}))
    new = _snap(_adv(callee_saved={"foo": frozenset({"rbx", "rbp", "r12", "r13"})}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.CALLING_CONVENTION_CHANGED not in kinds


def test_extract_callee_saved_regs_mocked() -> None:
    """Test _extract_callee_saved_regs with a mocked FDE."""
    from abicheck.dwarf_advanced import _extract_callee_saved_regs

    class MockRow:
        def __init__(self, pc, regs):
            self.pc = pc
            self.regs = regs

        def items(self):
            return self.regs.items()

    class MockRule:
        def __init__(self, typ):
            self.type = typ

    class MockTable:
        table = [
            MockRow(pc=0x1000, regs={16: MockRule("offset")}),
            MockRow(pc=0x1004, regs={3: MockRule("offset"), 4: MockRule("undefined")}),
        ]

    class MockDecoded:
        table = MockTable.table

    class MockEntry:
        def get_decoded(self):
            return MockDecoded()

    entry = MockEntry()
    result = _extract_callee_saved_regs(entry, "x86_64")
    # x86_64: reg 16 = rip, reg 3 = rbx, reg 4 = rsi (but undefined → not saved)
    assert result == frozenset({"rip", "rbx"})


def test_value_abi_trait_unchanged_no_change() -> None:
    results = diff_advanced_dwarf(
        _adv(value_traits={"foo": "ret:v(trivial)|p0:v(trivial)"}),
        _adv(value_traits={"foo": "ret:v(trivial)|p0:v(trivial)"}),
    )
    assert not any(r[0] == "value_abi_trait_changed" for r in results)


# ── struct packing ────────────────────────────────────────────────────────────

def test_struct_packing_added() -> None:
    # "Ctx" must exist in old all_struct_names so diff knows it's a pre-existing
    # struct that became packed (not a brand-new packed struct, which has no ABI contract).
    old = _snap(_adv(packed=set(), all_structs={"Ctx"}))
    new = _snap(_adv(packed={"Ctx"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_PACKING_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_struct_packing_added_new_struct_no_report() -> None:
    """Brand-new packed struct (not in old binary) should NOT report packing change."""
    old = _snap(_adv(packed=set()))           # "Ctx" never existed in old
    new = _snap(_adv(packed={"Ctx"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_PACKING_CHANGED not in kinds


def test_struct_packing_removed() -> None:
    """packed→unpacked is a breaking layout change when the struct still exists.

    all_structs must be set on the new side to prove the struct still exists
    (not removed). Without it the diff guard would skip the report to avoid
    false positives from struct deletion.
    """
    old = _snap(_adv(packed={"Hdr"}))
    new = _snap(_adv(packed=set(), all_structs={"Hdr"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_PACKING_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_struct_packing_unchanged_no_change() -> None:
    results = diff_advanced_dwarf(
        _adv(packed={"A", "B"}),
        _adv(packed={"A", "B"}),
    )
    assert not any(r[0] == "struct_packing_changed" for r in results)


# ── toolchain flag drift ──────────────────────────────────────────────────────

def test_toolchain_flag_added_compatible_warning() -> None:
    old = _snap(_adv(flags={"-fshort-enums"}))
    new = _snap(_adv(flags={"-fshort-enums", "-mabi=lp64"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.TOOLCHAIN_FLAG_DRIFT in kinds
    # informational only — must NOT be BREAKING
    assert r.verdict != Verdict.BREAKING


def test_toolchain_flag_removed() -> None:
    results = diff_advanced_dwarf(
        _adv(flags={"-fshort-enums", "-fno-common"}),
        _adv(flags={"-fshort-enums"}),
    )
    flag_r = [r for r in results if r[0] == "toolchain_flag_drift"]
    assert len(flag_r) == 1
    assert "removed" in flag_r[0][2]


def test_toolchain_no_drift_no_change() -> None:
    results = diff_advanced_dwarf(
        _adv(flags={"-fshort-enums"}),
        _adv(flags={"-fshort-enums"}),
    )
    assert not any(r[0] == "toolchain_flag_drift" for r in results)


# ── DW_AT_producer parsing ────────────────────────────────────────────────────

def test_parse_producer_gcc() -> None:
    info = _parse_producer("GNU C17 13.2.1 20230812 -fshort-enums -m64 -fabi-version=18")
    assert info.compiler == "GCC"
    assert info.version == "13.2.1"
    assert "-fshort-enums" in info.abi_flags
    assert "-m64" in info.abi_flags
    assert "-fabi-version=18" in info.abi_flags


def test_parse_producer_clang() -> None:
    info = _parse_producer("clang version 17.0.0 -fpack-struct=4")
    assert info.compiler == "clang"
    assert "-fpack-struct=4" in info.abi_flags


def test_parse_producer_icc() -> None:
    info = _parse_producer("Intel(R) oneAPI DPC++/C++ Compiler 2024.0.0 -m64")
    assert info.compiler == "ICC"
    assert "-m64" in info.abi_flags


def test_parse_producer_cxx11abi() -> None:
    info = _parse_producer("GNU C++17 12.3 -D_GLIBCXX_USE_CXX11_ABI=0")
    assert "-D_GLIBCXX_USE_CXX11_ABI=0" in info.abi_flags


def test_parse_producer_no_flags() -> None:
    info = _parse_producer("GNU C17 11.4.0")
    assert info.compiler == "GCC"
    assert info.abi_flags == set()


# ── JSON serialization (set → list → set roundtrip) ──────────────────────────

def test_serialization_roundtrip_no_crash() -> None:
    """snapshot_to_json must not raise TypeError on set fields."""
    snap = _snap(_adv(calling={"foo": "program"}, packed={"A", "B"}, flags={"-fshort-enums"}))
    # This must not raise TypeError: Object of type set is not JSON serializable
    json_str = snapshot_to_json(snap)
    data = json.loads(json_str)
    assert isinstance(data["dwarf_advanced"]["packed_structs"], list)
    assert isinstance(data["dwarf_advanced"]["toolchain"]["abi_flags"], list)


def test_serialization_roundtrip_set_values() -> None:
    snap = _snap(_adv(calling={"foo": "program"}, packed={"A", "B"}, flags={"-fshort-enums"}))
    d = snapshot_to_dict(snap)
    snap2 = snapshot_from_dict(d)
    assert snap2.dwarf_advanced is not None
    assert snap2.dwarf_advanced.calling_conventions == {"foo": "program"}
    assert snap2.dwarf_advanced.packed_structs == {"A", "B"}
    assert snap2.dwarf_advanced.toolchain.abi_flags == {"-fshort-enums"}


def test_serialization_empty_sets_roundtrip() -> None:
    snap = _snap(_adv())
    json_str = snapshot_to_json(snap)
    data = json.loads(json_str)
    assert data["dwarf_advanced"]["packed_structs"] == []


# ── Integration: real packed struct detection via DWARF ───────────────────────

@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux")
def test_packed_struct_detected_from_real_dwarf() -> None:
    """Compile a packed struct with gcc -g and verify DWARF detection."""
    src = """
typedef struct __attribute__((packed)) {
    char a;
    int b;       /* misaligned: offset 1 (int needs align 4) */
    double c;    /* misaligned: offset 5 */
} PackedCtx;
PackedCtx g_ctx;
"""
    with tempfile.TemporaryDirectory() as td:
        so = Path(td) / "libpacked.so"
        result = subprocess.run(
            ["gcc", "-g", "-shared", "-fPIC", "-o", str(so), "-x", "c", "-"],
            input=src.encode(), capture_output=True,
        )
        if result.returncode != 0:
            pytest.skip(f"gcc failed: {result.stderr.decode()[:200]}")

        meta = parse_advanced_dwarf(so)

    assert meta.has_dwarf
    assert "PackedCtx" in meta.packed_structs, (
        f"Expected 'PackedCtx' in packed_structs, got: {meta.packed_structs}"
    )


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux")
def test_standard_struct_not_flagged_as_packed() -> None:
    """Standard-layout struct must NOT be flagged as packed."""
    src = """
typedef struct { int x; int y; double z; } NormalCtx;
NormalCtx g;
"""
    with tempfile.TemporaryDirectory() as td:
        so = Path(td) / "libnormal.so"
        result = subprocess.run(
            ["gcc", "-g", "-shared", "-fPIC", "-o", str(so), "-x", "c", "-"],
            input=src.encode(), capture_output=True,
        )
        if result.returncode != 0:
            pytest.skip(f"gcc failed: {result.stderr.decode()[:200]}")

        meta = parse_advanced_dwarf(so)

    assert meta.has_dwarf
    assert "NormalCtx" not in meta.packed_structs


# ── C3: compare()-level no-change test for value_abi_traits ──────────────────

def test_value_abi_traits_same_no_change_emitted() -> None:
    """Same value_abi_traits in both snapshots must NOT emit VALUE_ABI_TRAIT_CHANGED."""
    trait = "ret:trivial|p0:nontrivial"
    old = _snap(_adv(value_traits={"_Z6computeP3Foo": trait}))
    new = _snap(_adv(value_traits={"_Z6computeP3Foo": trait}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED not in kinds
    assert r.verdict == Verdict.NO_CHANGE


def test_value_abi_traits_changed_emits_change() -> None:
    """Different value_abi_traits for same symbol → a value-ABI finding emitted.

    A return-position flip is the struct-return-convention refinement; a
    parameter-position flip stays the generic value-ABI trait change.
    """
    old = _snap(_adv(value_traits={"_Z6computev": "ret:trivial"}))
    new = _snap(_adv(value_traits={"_Z6computev": "ret:nontrivial"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED in kinds

    old_p = _snap(_adv(value_traits={"_Z3fooP1S": "ret:trivial|p0:trivial"}))
    new_p = _snap(_adv(value_traits={"_Z3fooP1S": "ret:trivial|p0:nontrivial"}))
    r_p = compare(old_p, new_p)
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED in {c.kind for c in r_p.changes}
