"""Sprint 1 tests — enum/method qualifier/union/typedef/bitfield ABI detection.

All tests build AbiSnapshot objects directly (no castxml required).
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    TypeField,
)


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


def test_enum_member_removed() -> None:
    old = _snap(enums=[EnumType("Status", [EnumMember("OK", 0), EnumMember("FOO", 2)])])
    new = _snap(enums=[EnumType("Status", [EnumMember("OK", 0)])])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_MEMBER_REMOVED in kinds


def test_enum_member_value_changed() -> None:
    # ERROR is not the last member here (LAST is)
    old = _snap(
        enums=[
            EnumType(
                "Err",
                [EnumMember("OK", 0), EnumMember("ERROR", 1), EnumMember("LAST", 2)],
            )
        ]
    )
    new = _snap(
        enums=[
            EnumType(
                "Err",
                [EnumMember("OK", 0), EnumMember("ERROR", 99), EnumMember("LAST", 2)],
            )
        ]
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED in kinds
    assert ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED not in kinds


def test_enum_last_member_changed() -> None:
    old = _snap(enums=[EnumType("Err", [EnumMember("OK", 0), EnumMember("LAST", 10)])])
    new = _snap(enums=[EnumType("Err", [EnumMember("OK", 0), EnumMember("LAST", 99)])])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED in kinds
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_enum_member_added() -> None:
    old = _snap(enums=[EnumType("Color", [EnumMember("RED", 0)])])
    new = _snap(
        enums=[EnumType("Color", [EnumMember("RED", 0), EnumMember("BLUE", 1)])]
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_MEMBER_ADDED in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_enum_additions_plus_last_change_are_risk_not_breaking() -> None:
    """Multiple enum member additions + sentinel move should be COMPATIBLE_WITH_RISK."""
    from abicheck.elf_metadata import ElfMetadata

    old = _snap(
        enums=[EnumType("Tag", [EnumMember("A", 0), EnumMember("LAST", 10)])],
        elf=ElfMetadata(needed=["libc.so.6"]),
    )
    new = _snap(
        enums=[
            EnumType(
                "Tag", [EnumMember("A", 0), EnumMember("B", 1), EnumMember("LAST", 11)]
            )
        ],
        elf=ElfMetadata(needed=["libc.so.6", "libsvml.so"]),
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_MEMBER_ADDED in kinds
    assert ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED in kinds
    assert ChangeKind.NEEDED_ADDED in kinds
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_enum_named_sentinel_not_max_value_is_risk() -> None:
    """Named sentinel (*_last/*_max/*_count) should downgrade even if not max value."""
    old = _snap(
        enums=[EnumType("Err", [EnumMember("LAST", 1), EnumMember("OTHER", 99)])]
    )
    new = _snap(
        enums=[EnumType("Err", [EnumMember("LAST", 2), EnumMember("OTHER", 99)])]
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED in kinds
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


# ---------------------------------------------------------------------------
# Method qualifier tests
# ---------------------------------------------------------------------------


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void")
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def test_method_became_static() -> None:
    # Itanium ABI: static member functions have the SAME mangled name as non-static
    # with identical signature (is_static is not encoded in mangling).
    # Checker detects this via the same-mangled loop.
    old = _snap(functions=[_func("bar", "_ZN6Widget3barEv", is_static=False)])
    new = _snap(functions=[_func("bar", "_ZN6Widget3barEv", is_static=True)])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FUNC_STATIC_CHANGED in kinds


def test_method_const_changed() -> None:
    # const adds K prefix in Itanium mangling: Foo::get() → _ZN3Foo3getEv
    #                                            Foo::get() const → _ZNK3Foo3getEv
    old = _snap(functions=[_func("get", "_ZN6Widget3getEv", is_const=False)])
    new = _snap(functions=[_func("get", "_ZNK6Widget3getEv", is_const=True)])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FUNC_CV_CHANGED in kinds


def test_method_volatile_changed() -> None:
    # volatile adds V prefix in mangling
    old = _snap(functions=[_func("run", "_ZN6Widget3runEv", is_volatile=False)])
    new = _snap(functions=[_func("run", "_ZNV6Widget3runEv", is_volatile=True)])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FUNC_CV_CHANGED in kinds


def test_pure_virtual_added() -> None:
    """Non-virtual function that gets pure_virtual=True → FUNC_PURE_VIRTUAL_ADDED."""
    old = _snap(
        functions=[
            _func(
                "process",
                "_ZN9Processor7processEv",
                is_virtual=False,
                is_pure_virtual=False,
            )
        ]
    )
    new = _snap(
        functions=[
            _func(
                "process",
                "_ZN9Processor7processEv",
                is_virtual=False,
                is_pure_virtual=True,
            )
        ]
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FUNC_PURE_VIRTUAL_ADDED in kinds


def test_virtual_became_pure() -> None:
    """Virtual function that becomes pure virtual → FUNC_VIRTUAL_BECAME_PURE."""
    old = _snap(
        functions=[
            _func(
                "process",
                "_ZN9Processor7processEv",
                is_virtual=True,
                is_pure_virtual=False,
            )
        ]
    )
    new = _snap(
        functions=[
            _func(
                "process",
                "_ZN9Processor7processEv",
                is_virtual=True,
                is_pure_virtual=True,
            )
        ]
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FUNC_VIRTUAL_BECAME_PURE in kinds


# ---------------------------------------------------------------------------
# Union tests
# ---------------------------------------------------------------------------


def _union(name: str, fields: list[TypeField]) -> RecordType:
    return RecordType(name=name, kind="union", fields=fields, is_union=True)


def test_union_field_removed() -> None:
    old = _snap(
        types=[_union("Data", [TypeField("i", "int"), TypeField("f", "float")])]
    )
    new = _snap(types=[_union("Data", [TypeField("i", "int")])])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.UNION_FIELD_REMOVED in kinds


def test_union_field_added() -> None:
    old = _snap(types=[_union("Data", [TypeField("i", "int")])])
    new = _snap(
        types=[_union("Data", [TypeField("i", "int"), TypeField("f", "float")])]
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.UNION_FIELD_ADDED in kinds
    assert result.verdict == Verdict.COMPATIBLE


def test_union_field_type_changed() -> None:
    old = _snap(types=[_union("Data", [TypeField("x", "int")])])
    new = _snap(types=[_union("Data", [TypeField("x", "double")])])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.UNION_FIELD_TYPE_CHANGED in kinds


# ---------------------------------------------------------------------------
# Typedef tests
# ---------------------------------------------------------------------------


def test_typedef_base_changed() -> None:
    old = _snap(typedefs={"MyInt": "int"})
    new = _snap(typedefs={"MyInt": "long"})
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.TYPEDEF_BASE_CHANGED in kinds


# ---------------------------------------------------------------------------
# Bitfield tests
# ---------------------------------------------------------------------------


def _struct_with_bitfield(name: str, field_name: str, bits: int | None) -> RecordType:
    f = TypeField(
        name=field_name,
        type="int",
        is_bitfield=bits is not None,
        bitfield_bits=bits,
    )
    return RecordType(name=name, kind="struct", fields=[f])


def test_bitfield_changed() -> None:
    old = _snap(types=[_struct_with_bitfield("Flags", "x", 4)])
    new = _snap(types=[_struct_with_bitfield("Flags", "x", 8)])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FIELD_BITFIELD_CHANGED in kinds


# ---------------------------------------------------------------------------
# Verdict checks (all sprint1 changes are BREAKING)
# ---------------------------------------------------------------------------


def test_sprint1_breaking_subset() -> None:
    """Sprint1 binary-breaking subset remains in BREAKING bucket."""
    from abicheck.checker import _BREAKING_KINDS

    sprint1_kinds = {
        ChangeKind.ENUM_MEMBER_REMOVED,
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        ChangeKind.FUNC_STATIC_CHANGED,
        ChangeKind.FUNC_CV_CHANGED,
        ChangeKind.FUNC_PURE_VIRTUAL_ADDED,
        ChangeKind.FUNC_VIRTUAL_BECAME_PURE,
        ChangeKind.UNION_FIELD_REMOVED,
        ChangeKind.UNION_FIELD_TYPE_CHANGED,
        ChangeKind.TYPEDEF_BASE_CHANGED,
        ChangeKind.FIELD_BITFIELD_CHANGED,
    }
    assert sprint1_kinds.issubset(_BREAKING_KINDS), (
        f"Not in _BREAKING_KINDS: {sprint1_kinds - _BREAKING_KINDS}"
    )


def test_regular_enum_member_value_change_remains_breaking() -> None:
    """Non-sentinel enum member value change stays BREAKING."""
    old = _snap(
        enums=[
            EnumType(
                "Err", [EnumMember("OK", 0), EnumMember("E", 1), EnumMember("LAST", 2)]
            )
        ]
    )
    new = _snap(
        enums=[
            EnumType(
                "Err", [EnumMember("OK", 0), EnumMember("E", 99), EnumMember("LAST", 2)]
            )
        ]
    )
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED in kinds
    assert result.verdict == Verdict.BREAKING
