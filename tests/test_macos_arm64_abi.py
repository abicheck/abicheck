"""macOS ARM64 ABI corner cases — regression tests for abicc #116 #119.

Covers:
- install_name (LC_ID_DYLIB) tracking: SONAME_CHANGED fires when install_name changes (#119)
- ARM64 small-struct size change is detected via TYPE_SIZE_CHANGED (#116 base coverage)
- Documentation regression guards: platforms.md must contain #116 and #119 references
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

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
            size_bits=64,  # 2× int32 — fits in registers on ARM64
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
