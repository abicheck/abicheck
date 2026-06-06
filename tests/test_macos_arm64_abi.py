"""macOS ARM64 ABI corner cases — regression tests for abicc #116 #119.

Covers:
- install_name (LC_ID_DYLIB) tracking: SONAME_CHANGED fires when install_name changes (#119)
- ARM64 small-struct size change is detected via TYPE_SIZE_CHANGED (#116 base coverage)
- Documentation regression guards: platforms.md must contain #116 and #119 references
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot, Function, RecordType, TypeField, Visibility


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="libfoo.dylib", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def _macho(install_name: str = "", compat_version: str = "1.0.0") -> object:
    """Return a minimal MachoMetadata-compatible object without requiring macholib."""

    @dataclass
    class _MockMacho:
        install_name: str = ""
        compat_version: str = "1.0.0"
        cpu_type: str = "ARM64"
        filetype: str = "MH_DYLIB"
        flags: int = 0
        dependent_libs: list = dc_field(default_factory=list)
        reexported_libs: list = dc_field(default_factory=list)
        exports: list = dc_field(default_factory=list)
        current_version: str = ""
        min_os_version: str = ""

    return _MockMacho(install_name=install_name, compat_version=compat_version)


class TestInstallNameTracking:
    """install_name change triggers SONAME_CHANGED (abicc #119)."""

    def test_soname_changed_when_install_name_differs(self) -> None:
        old = _snap(macho=_macho("libfoo.1.dylib"))
        new = _snap(macho=_macho("libfoo.2.dylib"))

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.SONAME_CHANGED in kinds

    def test_soname_changed_symbol_is_lc_id_dylib(self) -> None:
        old = _snap(macho=_macho("libfoo.1.dylib"))
        new = _snap(macho=_macho("libfoo.2.dylib"))

        result = compare(old, new)
        soname_change = next(
            c for c in result.changes if c.kind == ChangeKind.SONAME_CHANGED
        )

        assert soname_change.symbol == "LC_ID_DYLIB"
        assert soname_change.old_value == "libfoo.1.dylib"
        assert soname_change.new_value == "libfoo.2.dylib"

    def test_no_soname_change_when_same(self) -> None:
        old = _snap(macho=_macho("libfoo.1.dylib"))
        new = _snap(macho=_macho("libfoo.1.dylib"))

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.SONAME_CHANGED not in kinds

    def test_soname_none_to_set(self) -> None:
        """install_name going from empty to set must be tracked."""
        old = _snap(macho=_macho(""))
        new = _snap(macho=_macho("libfoo.1.dylib"))

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.SONAME_CHANGED in kinds

    def test_soname_set_to_none_also_tracked(self) -> None:
        """install_name going from set to empty must also emit SONAME_CHANGED."""
        old = _snap(macho=_macho("libfoo.1.dylib"))
        new = _snap(macho=_macho(""))

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.SONAME_CHANGED in kinds

    def test_compat_version_only_change_does_not_emit_soname(self) -> None:
        """Changing only compat_version should not produce SONAME_CHANGED."""
        old = _snap(macho=_macho("libfoo.1.dylib", compat_version="1.0.0"))
        new = _snap(macho=_macho("libfoo.1.dylib", compat_version="2.0.0"))

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.SONAME_CHANGED not in kinds


class TestArm64AbiDocumentedLimits:
    """ARM64 ABI coverage guards (abicc #116)."""

    def test_small_struct_size_change_detected(self) -> None:
        """ARM64: struct size change that affects register-passing IS caught as TYPE_SIZE_CHANGED."""
        small_v1 = RecordType(
            name="Point",
            kind="struct",
            size_bits=64,   # 2× int32 — fits in registers on ARM64
            fields=[
                TypeField(name="x", type="int", offset_bits=0),
                TypeField(name="y", type="int", offset_bits=32),
            ],
        )
        small_v2 = RecordType(
            name="Point",
            kind="struct",
            size_bits=128,  # grown — crosses HFA/HVA register-passing boundary
            fields=[
                TypeField(name="x", type="long", offset_bits=0),
                TypeField(name="y", type="long", offset_bits=64),
            ],
        )

        old = _snap(types=[small_v1])
        new = _snap(types=[small_v2])

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.TYPE_SIZE_CHANGED in kinds

    def test_small_struct_no_change_when_identical(self) -> None:
        s = RecordType(name="Vec2", kind="struct", size_bits=64)
        old = _snap(types=[s])
        new = _snap(types=[s])

        result = compare(old, new)
        assert not result.changes

class TestAapcs64AggregateClassification:
    """AArch64 AAPCS64 by-value aggregate passing classifier.

    Models the calling-convention dimension that a size-only diff misses: HFAs
    pass in SIMD registers, aggregates <=16 bytes in GP registers, and larger
    aggregates indirectly. Crossing a boundary is a real ARM64 ABI change.
    """

    def test_hfa_of_floats(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        # struct { float x, y, z; } → HFA of 3 floats (passed in v0..v2)
        assert classify_aapcs64_aggregate(12, ["float", "float", "float"]) == "hfa3"

    def test_hfa_of_doubles(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        assert classify_aapcs64_aggregate(16, ["double", "double"]) == "hfa2"

    def test_more_than_four_floats_is_not_hfa(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        # 5 floats (20 bytes) exceeds the 4-member HFA limit AND 16-byte limit.
        assert classify_aapcs64_aggregate(20, ["float"] * 5) == "indirect"

    def test_mixed_member_types_is_not_hfa(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        # struct { float x; int y; } → not homogeneous; <=16 bytes → register
        assert classify_aapcs64_aggregate(8, ["float", "int"]) == "register"

    def test_small_integer_aggregate_in_registers(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        assert classify_aapcs64_aggregate(16, ["long", "long"]) == "register"

    def test_large_aggregate_passed_indirectly(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        # 24 bytes > 16-byte limit → indirect (by reference)
        assert classify_aapcs64_aggregate(24, ["long", "long", "long"]) == "indirect"

    def test_register_to_indirect_boundary_at_16_bytes(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        # The boundary is the ABI break a struct growing 16→24 bytes triggers:
        # register-passed → indirect-passed (different calling convention).
        before = classify_aapcs64_aggregate(16, ["long", "long"])
        after = classify_aapcs64_aggregate(24, ["long", "long", "long"])
        assert before == "register" and after == "indirect"
        assert before != after  # calling convention changed

    def test_hfa_lost_when_non_float_member_added(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        # struct { float x, y; } (HFA, SIMD regs) → add int → no longer HFA.
        before = classify_aapcs64_aggregate(8, ["float", "float"])
        after = classify_aapcs64_aggregate(12, ["float", "float", "int"])
        assert before == "hfa2" and after == "register"
        assert before != after  # SIMD-register passing lost

    def test_hva_of_short_vectors_over_16_bytes(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        # struct { float32x4_t v[4]; } → 64 bytes, but an HVA: SIMD-register
        # passed (NOT indirect), so it must be recognized before the size cutoff.
        assert classify_aapcs64_aggregate(64, ["float32x4_t"] * 4) == "hva4"

    def test_hva_single_neon_vector(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        assert classify_aapcs64_aggregate(16, ["int8x16_t"]) == "hva1"

    def test_hva_requires_homogeneous_vectors(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        # Mixed vector types are not an HVA; 32 bytes > 16 → indirect.
        assert classify_aapcs64_aggregate(32, ["int8x16_t", "float32x4_t"]) == "indirect"

    def test_more_than_four_vectors_is_not_hva(self) -> None:
        from abicheck.macho_metadata import classify_aapcs64_aggregate
        # 5 identical vectors exceed the 4-member HVA limit → indirect.
        assert classify_aapcs64_aggregate(80, ["float32x4_t"] * 5) == "indirect"


    def test_arm64_limitation_note_exists(self) -> None:
        """Documentation regression guard: platforms.md must keep the ARM64 section."""
        p = Path("docs/reference/platforms.md")
        text = p.read_text(encoding="utf-8")
        assert "## macOS ARM64 — Known ABI Differences" in text
        assert "HFA (Homogeneous Floating-point Aggregate" in text
        assert "#116" in text, "docs/reference/platforms.md must reference abicc #116"

    def test_install_name_limitation_note_exists(self) -> None:
        """Documentation regression guard: #119 text must stay consistent."""
        p = Path("docs/reference/platforms.md")
        text = p.read_text(encoding="utf-8")
        assert "### install_name tracking (#119)" in text
        assert "emits `SONAME_CHANGED`" in text
        assert "not surfaced as `soname_changed` yet" not in text
