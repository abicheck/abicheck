"""Regression scenarios distilled from real-world validation (validation/REPORT.md).

Each test reproduces — at the snapshot/diff level — a false-positive pattern
observed when running ``abicheck compare`` against real upstream release
binaries (oneTBB, Protobuf, libxml2, …). They drive the public
:func:`abicheck.checker.compare` pipeline with minimal synthetic snapshots that
isolate the responsible mechanism.

The four scenarios correspond to FP-1…FP-4 in ``validation/DESIGN_ANALYSIS.md``.
Tests asserting behaviour that abicheck does **not** yet implement are marked
``xfail(strict=True)`` so they flip to PASS the moment the architectural fix
lands (and fail loudly if someone "fixes" them without removing the marker).

No external tools or binaries are required — these run in the default fast lane.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    EnumMember,
    EnumType,
    RecordType,
    TypeField,
    Variable,
    Visibility,
    is_cxx_runtime_library,
    is_non_abi_surface_type,
)

REPORT = "validation/REPORT.md / validation/DESIGN_ANALYSIS.md"


def _elf_snapshot(
    name="libfoo.so.1", version="1", *, functions=None, variables=None, types=None
) -> AbiSnapshot:
    """A no-header (ELF-only) snapshot, as produced when comparing two release
    .so files without headers."""
    return AbiSnapshot(
        library=name,
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        elf_only_mode=True,
        platform="elf",
        language_profile="cpp",
    )


def _breaking_symbols(result) -> set[str]:
    from abicheck.checker_policy import BREAKING_KINDS
    return {c.symbol for c in result.changes if c.kind in BREAKING_KINDS}


# ---------------------------------------------------------------------------
# FP-3 / RD2-4 — RTTI/typeinfo of an anonymous lambda must not be a breaking var_removed
#   Real case: Protobuf 6.33.2 -> 6.33.5 (a *patch*) flagged BREAKING because
#   `_ZTIZN6google8protobuf2io7Printer8WithDefs...EUlSt17basic_string...E_`
#   (typeinfo for an internal lambda) "disappeared".  Lambda identity is not
#   stable ABI.  Fixed in diff_symbols._public_variables: RTTI/vtable symbols of
#   function-local types (Itanium ``_ZTIZ``/``_ZTSZ``/``_ZTVZ``/``_ZTTZ`` local-name
#   production) are excluded from the public-variable surface.
# ---------------------------------------------------------------------------
def test_lambda_rtti_removal_is_not_breaking():
    lambda_rtti = "_ZTIZN3foo3barEvEUlvE_"  # typeinfo of a lambda defined in foo::bar()
    lambda_rtti_name = "_ZTSZN3foo3barEvEUlvE_"
    old = _elf_snapshot(
        variables=[
            Variable(
                name=lambda_rtti,
                mangled=lambda_rtti,
                type="?",
                visibility=Visibility.ELF_ONLY,
            ),
            Variable(
                name=lambda_rtti_name,
                mangled=lambda_rtti_name,
                type="?",
                visibility=Visibility.ELF_ONLY,
            ),
        ]
    )
    new = _elf_snapshot(variables=[])
    result = compare(old, new)
    assert result.verdict not in (Verdict.BREAKING,), (
        f"removing RTTI of an anonymous lambda must not read as an ABI break; "
        f"breaking symbols: {_breaking_symbols(result)}"
    )
    # The lambda RTTI symbols must not surface as findings at all.
    assert lambda_rtti not in _breaking_symbols(result)
    assert lambda_rtti_name not in _breaking_symbols(result)


def test_public_type_rtti_removal_is_still_breaking():
    """Guard against over-filtering: typeinfo/vtable of a NON-local (public,
    nameable) type must still count as a break when removed (RD2-4)."""
    public_rtti = "_ZTIN3foo3BarE"   # typeinfo for foo::Bar (not function-local)
    public_vtable = "_ZTVN3foo3BarE"  # vtable for foo::Bar
    old = _elf_snapshot(
        variables=[
            Variable(name=public_rtti, mangled=public_rtti, type="?", visibility=Visibility.ELF_ONLY),
            Variable(name=public_vtable, mangled=public_vtable, type="?", visibility=Visibility.ELF_ONLY),
        ]
    )
    new = _elf_snapshot(variables=[])
    result = compare(old, new)
    breaking = _breaking_symbols(result)
    assert public_rtti in breaking or public_vtable in breaking, (
        "removing typeinfo/vtable of a public, nameable type must still be a break"
    )


# ---------------------------------------------------------------------------
# FP-1 — Standard-library types leaked via DWARF must not be public ABI surface
#   Real case: oneTBB 2021.5 -> 2021.9 (ABI-compatible, same SONAME) flagged
#   216 breaks; 54 were on std::/__gnu_cxx types like
#   `std::__cxx11::basic_string::npos` and `std::integral_constant::value`.
#   These differ only because the two builds used different GCC versions (9.4
#   vs 11.3) that emit static-member DIEs differently.  std:: layout is fixed by
#   the libstdc++ ABI; it is never the inspected library's surface.
#   Root cause: dwarf_snapshot type extraction + diff_types only filter the 13
#   hardcoded compiler-internal names, not std::/__gnu_cxx/__cxxabiv1.
# ---------------------------------------------------------------------------
def test_stdlib_type_change_is_not_breaking():
    std_string = "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >"
    old = _elf_snapshot(
        types=[
            RecordType(
                name=std_string,
                kind="class",
                size_bits=256,
                fields=[
                    TypeField(name="npos", type="unsigned long", offset_bits=0),
                    TypeField(name="_M_p", type="char *", offset_bits=0),
                ],
            ),
        ]
    )
    new = _elf_snapshot(
        types=[
            # newer toolchain simply did not emit the static `npos` member DIE
            RecordType(
                name=std_string,
                kind="class",
                size_bits=256,
                fields=[TypeField(name="_M_p", type="char *", offset_bits=0)],
            ),
        ]
    )
    result = compare(old, new)
    assert result.verdict not in (Verdict.BREAKING,), (
        f"std:: type DIE churn must not read as an ABI break; "
        f"breaking symbols: {_breaking_symbols(result)}"
    )


# ---------------------------------------------------------------------------
# FP-2 — Anonymous (lambda / unnamed) types have no cross-version ABI identity
#   Real case: residual oneTBB finding `type_removed: <lambda()>`.
# ---------------------------------------------------------------------------
def test_anonymous_type_removal_is_not_breaking():
    old = _elf_snapshot(
        types=[
            RecordType(name="<lambda()>", kind="class", size_bits=8),
        ]
    )
    new = _elf_snapshot(types=[])
    result = compare(old, new)
    assert result.verdict not in (Verdict.BREAKING,), (
        f"anonymous/local type removal must not read as an ABI break; "
        f"breaking symbols: {_breaking_symbols(result)}"
    )


# ---------------------------------------------------------------------------
# FP-4 — Mixed coverage (old has DWARF types, new is stripped) must not fabricate
#   removals.  Real case: libxml2 2.9.7 (DWARF) -> 2.9.9 (stripped) reported
#   1149 breaks incl. `type_removed: _xmlNode` — a core public type that still
#   exists.  Absence of debug info on the new side is absence of *evidence*, not
#   evidence of removal.  Fixed in diff_types._removals_are_unconfirmed:
#   TYPE_REMOVED/TYPEDEF_REMOVED are suppressed when the new side is a stripped
#   binary (elf_only_mode, exports symbols, no type evidence) while the old side
#   has type info. The dwarf detector still emits DWARF_INFO_MISSING so the
#   coverage gap is disclosed.
# ---------------------------------------------------------------------------
def _exported_func(mangled: str):
    from abicheck.model import Function
    return Function(name=mangled, mangled=mangled, return_type="?",
                    visibility=Visibility.ELF_ONLY)


def test_stripped_new_side_does_not_fabricate_type_removals():
    # old: rich DWARF types + exported symbols
    old = _elf_snapshot(
        functions=[_exported_func("xmlNewNode"), _exported_func("xmlFreeDoc")],
        types=[
            RecordType(
                name="_xmlNode",
                kind="struct",
                size_bits=960,
                fields=[TypeField(name="type", type="int", offset_bits=0)],
            ),
            RecordType(name="_xmlDoc", kind="struct", size_bits=512),
        ],
    )
    old.typedefs = {"xmlNodePtr": "_xmlNode *", "xmlDocPtr": "_xmlDoc *"}
    # new: same library, but the binary is stripped -> exports the same symbols,
    # zero type DWARF (a real stripped .so still has a dynamic symbol table).
    new = _elf_snapshot(
        functions=[_exported_func("xmlNewNode"), _exported_func("xmlFreeDoc")],
        types=[],
    )
    new.dwarf = None
    result = compare(old, new)
    assert result.verdict not in (Verdict.BREAKING,), (
        f"types absent only because the new side is stripped must not read as "
        f"removals; breaking symbols: {_breaking_symbols(result)}"
    )
    from abicheck.checker_policy import ChangeKind
    assert not any(c.kind == ChangeKind.TYPE_REMOVED for c in result.changes)
    assert not any(c.kind == ChangeKind.TYPEDEF_REMOVED for c in result.changes)


def test_real_removal_still_reported_when_symbols_also_dropped():
    """The stripped-side guard must NOT hide a genuine class removal: when the
    removed type's exported methods are also gone, symbol retention is low and
    the removal is real (validation RD2-5; examples/case107)."""
    from abicheck.checker_policy import BREAKING_KINDS
    old = _elf_snapshot(
        functions=[_exported_func("_ZN5mylib3Foo3barEv"), _exported_func("_ZN5mylib3FooC1Ev")],
        types=[RecordType(name="mylib::Foo", kind="class", size_bits=64)],
    )
    # new: class and ALL its methods gone (retention 0%), only a new free fn.
    new = _elf_snapshot(functions=[_exported_func("_ZN5mylib5otherEv")], types=[])
    new.dwarf = None
    result = compare(old, new)
    assert any(c.kind in BREAKING_KINDS for c in result.changes), (
        "removing a class together with its exported methods must still break; "
        f"changes: {[(c.kind.value, c.symbol) for c in result.changes]}"
    )


def test_stripped_suppression_counts_only_exported_functions():
    """The stripped-side retention check must ignore internal/static DWARF
    subprograms: a DWARF-primary old snapshot records them as functions, but the
    stripped new side only has dynamic exports. Counting internals would deflate
    retention and defeat the suppression for an intact DWARF→stripped bump
    (Codex review on PR #275)."""
    from abicheck.checker_policy import ChangeKind
    from abicheck.model import Function
    exported = [_exported_func("xmlNewNode"), _exported_func("xmlFreeDoc")]
    # old: 2 exported + many internal (HIDDEN) DWARF subprograms + rich types.
    internal = [
        Function(name=f"__internal_{i}", mangled=f"__internal_{i}",
                 return_type="void", visibility=Visibility.HIDDEN)
        for i in range(50)
    ]
    old = _elf_snapshot(
        functions=exported + internal,
        types=[RecordType(name="_xmlNode", kind="struct", size_bits=960)],
    )
    # new: stripped → exports the same 2 public symbols, no types, no internals.
    new = _elf_snapshot(functions=list(exported), types=[])
    new.dwarf = None
    result = compare(old, new)
    assert not any(c.kind == ChangeKind.TYPE_REMOVED for c in result.changes), (
        "internal DWARF functions must not deflate retention and re-enable the "
        f"phantom avalanche; changes: {[(c.kind.value, c.symbol) for c in result.changes]}"
    )


def test_has_type_evidence_via_dwarf_structs_blocks_suppression():
    """When the new side still carries DWARF *content* (structs), it is not
    stripped and removals are NOT suppressed (covers the DWARF branch of the
    type-evidence check)."""
    from abicheck.checker_policy import ChangeKind
    from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
    old = _elf_snapshot(
        functions=[_exported_func("api")],
        types=[RecordType(name="Gone", kind="struct", size_bits=32)],
    )
    new = _elf_snapshot(functions=[_exported_func("api")], types=[])
    # new has real DWARF content → has type evidence → not "stripped".
    new.dwarf = DwarfMetadata(structs={"Other": StructLayout(name="Other", byte_size=4)}, has_dwarf=True)
    result = compare(old, new)
    assert any(c.kind == ChangeKind.TYPE_REMOVED and c.symbol == "Gone" for c in result.changes)


def test_stripped_suppression_with_only_variable_exports():
    """A stripped new side that exports only variables (no functions) is still
    recognised as stripped (covers the 'no exported functions' branch)."""
    from abicheck.checker_policy import ChangeKind
    old = _elf_snapshot(
        variables=[Variable(name="g", mangled="g", type="int", visibility=Visibility.ELF_ONLY)],
        types=[RecordType(name="_xmlNode", kind="struct", size_bits=960)],
    )
    new = _elf_snapshot(
        variables=[Variable(name="g", mangled="g", type="?", visibility=Visibility.ELF_ONLY)],
        types=[],
    )
    new.dwarf = None
    result = compare(old, new)
    assert not any(c.kind == ChangeKind.TYPE_REMOVED for c in result.changes)


def test_unknown_signature_not_flagged_as_change():
    """A stripped ELF export reports return_type/type '?' — diffing a known type
    against '?' must not fabricate func_return/params/var_type changes (RD2-5)."""
    from abicheck.checker_policy import ChangeKind
    from abicheck.model import Function, Param
    old = _elf_snapshot(
        functions=[Function(name="f", mangled="_Z1fi", return_type="int",
                            params=[Param(name="a", type="int")], visibility=Visibility.PUBLIC)],
        variables=[Variable(name="g", mangled="g", type="int", visibility=Visibility.PUBLIC)],
    )
    # new side: same symbols, but signature/type unknown (stripped).
    new = _elf_snapshot(
        functions=[Function(name="f", mangled="_Z1fi", return_type="?",
                            params=[], visibility=Visibility.PUBLIC)],
        variables=[Variable(name="g", mangled="g", type="?", visibility=Visibility.PUBLIC)],
    )
    result = compare(old, new)
    phantom = {ChangeKind.FUNC_RETURN_CHANGED, ChangeKind.FUNC_PARAMS_CHANGED,
               ChangeKind.VAR_TYPE_CHANGED, ChangeKind.RETURN_POINTER_LEVEL_CHANGED}
    offenders = [c.kind.value for c in result.changes if c.kind in phantom]
    assert offenders == [], f"unknown ('?') signatures must not be diffed: {offenders}"


# ---------------------------------------------------------------------------
# FP-1 guard rail — the std:: exclusion must NOT hide breaks when the inspected
#   DSO *is* the C++ runtime/standard library (libstdc++/libc++).  There std::
#   types ARE the surface under test (Codex review on PR #273).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "library,expected",
    [
        # SONAMEs / paths
        ("libstdc++.so.6", True),
        ("/opt/gcc/lib64/libstdc++.so.6", True),
        ("libc++.so.1", True),
        ("libc++abi.so.1", True),
        ("libsupc++.a", True),
        # ABICC short names (abicheck compat dump writes the -lib value)
        ("stdc++", True),
        ("c++", True),
        ("c++abi", True),
        ("supc++", True),
        # non-runtime libraries must NOT match
        ("libtbb.so.12", False),
        ("libfoo.so.1", False),
        ("libcurl.so.4", False),  # starts with 'lib' but 'curl' != runtime stem
        ("Qt5Core", False),
        (None, False),
        ("", False),
    ],
)
def test_is_cxx_runtime_library(library, expected):
    assert is_cxx_runtime_library(library) is expected


def test_is_non_abi_surface_type_keeps_stdlib_when_requested():
    std_type = "std::__cxx11::basic_string<char>"
    # default: std:: excluded from a normal library's surface
    assert is_non_abi_surface_type(std_type) is True
    # opt-out: std:: kept when the runtime owns the namespace
    assert is_non_abi_surface_type(std_type, exclude_stdlib_namespaces=False) is False
    # anonymous + compiler-internal stay excluded regardless of the flag
    assert is_non_abi_surface_type("<lambda()>", exclude_stdlib_namespaces=False) is True
    assert is_non_abi_surface_type("__va_list_tag", exclude_stdlib_namespaces=False) is True
    # libstdc++ debug-mode namespace is toolchain-owned too
    assert is_non_abi_surface_type("__gnu_debug::_Safe_iterator<int>") is True
    assert is_non_abi_surface_type("__gnu_debug::_Safe_iterator<int>", exclude_stdlib_namespaces=False) is False


def test_stdlib_size_change_is_breaking_when_target_is_the_runtime():
    """A real std::basic_string size change in libstdc++ must NOT be hidden."""
    std_string = "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >"
    old = _elf_snapshot(name="libstdc++.so.6", types=[
        RecordType(name=std_string, kind="class", size_bits=256),
    ])
    new = _elf_snapshot(name="libstdc++.so.6", types=[
        RecordType(name=std_string, kind="class", size_bits=512),  # layout actually changed
    ])
    result = compare(old, new)
    assert result.verdict == Verdict.BREAKING, (
        "a std:: type size change in libstdc++ itself is a real ABI break and "
        "must not be filtered out"
    )


def test_stdlib_size_change_is_filtered_for_a_normal_library():
    """The same std:: churn in a non-runtime library stays filtered (FP-1)."""
    std_string = "std::__cxx11::basic_string<char>"
    old = _elf_snapshot(name="libtbb.so.12", types=[
        RecordType(name=std_string, kind="class", size_bits=256),
    ])
    new = _elf_snapshot(name="libtbb.so.12", types=[
        RecordType(name=std_string, kind="class", size_bits=512),
    ])
    result = compare(old, new)
    assert result.verdict not in (Verdict.BREAKING,)


def test_stdlib_union_field_churn_is_filtered_for_a_normal_library():
    """std:: *union* field churn must be filtered too — the union-specific diff
    path must apply the same surface filter as _diff_types (Codex review #273)."""
    std_union = "std::__detail::_Variant_storage<char, int>"  # a std:: union
    old = _elf_snapshot(name="libtbb.so.12", types=[
        RecordType(name=std_union, kind="union", is_union=True, size_bits=64,
                   fields=[TypeField(name="_M_first", type="char", offset_bits=0),
                           TypeField(name="_M_rest", type="int", offset_bits=0)]),
    ])
    new = _elf_snapshot(name="libtbb.so.12", types=[
        RecordType(name=std_union, kind="union", is_union=True, size_bits=64,
                   fields=[TypeField(name="_M_first", type="char", offset_bits=0)]),  # field removed
    ])
    result = compare(old, new)
    assert result.verdict not in (Verdict.BREAKING,), (
        f"std:: union field churn in a non-runtime library must stay filtered; "
        f"breaking symbols: {_breaking_symbols(result)}"
    )


def test_stdlib_enum_member_churn_is_filtered_for_a_normal_library():
    """std:: *enum* member churn must be filtered too — the enum detectors must
    apply the same surface filter (Codex review on PR #273)."""
    std_enum = "std::__detail::_S_state"  # a std:: enum
    old = _elf_snapshot(name="libtbb.so.12")
    old.enums = [EnumType(name=std_enum, members=[
        EnumMember(name="_S_a", value=0), EnumMember(name="_S_b", value=1)])]
    new = _elf_snapshot(name="libtbb.so.12")
    new.enums = [EnumType(name=std_enum, members=[
        EnumMember(name="_S_a", value=0)])]  # member removed
    result = compare(old, new)
    assert result.verdict not in (Verdict.BREAKING,), (
        f"std:: enum member churn in a non-runtime library must stay filtered; "
        f"breaking symbols: {_breaking_symbols(result)}"
    )


def test_stdlib_enum_member_change_is_breaking_when_target_is_the_runtime():
    """The same std:: enum churn IS a break when the target is libstdc++ itself."""
    std_enum = "std::__detail::_S_state"
    old = _elf_snapshot(name="libstdc++.so.6")
    old.enums = [EnumType(name=std_enum, members=[
        EnumMember(name="_S_a", value=0), EnumMember(name="_S_b", value=1)])]
    new = _elf_snapshot(name="libstdc++.so.6")
    new.enums = [EnumType(name=std_enum, members=[
        EnumMember(name="_S_a", value=0)])]
    result = compare(old, new)
    assert result.verdict == Verdict.BREAKING


def test_stdlib_qualified_typedef_churn_is_filtered_for_a_normal_library():
    """A namespace-qualified std:: typedef (as the DWARF extractor now emits,
    e.g. ``std::size_type``) must be filtered for a non-runtime library — the
    extractor qualifies typedefs with their scope so the FP-1 filter sees the
    ``std::`` prefix (Codex review on PR #273)."""
    old = _elf_snapshot(name="libtbb.so.12")
    old.typedefs = {"std::vector<int>::size_type": "unsigned long"}
    new = _elf_snapshot(name="libtbb.so.12")
    new.typedefs = {}  # std:: typedef "removed" by toolchain churn
    result = compare(old, new)
    assert result.verdict not in (Verdict.BREAKING,), (
        f"qualified std:: typedef churn must stay filtered for a non-runtime "
        f"library; breaking symbols: {_breaking_symbols(result)}"
    )


def test_qualified_public_typedef_removal_still_breaking():
    """A genuine, non-std public typedef removal must still be reported."""
    old = _elf_snapshot(name="libtbb.so.12")
    old.typedefs = {"tbb::concurrent_vector<int>::handle": "void *"}
    new = _elf_snapshot(name="libtbb.so.12")
    new.typedefs = {}
    result = compare(old, new)
    assert _breaking_symbols(result) or result.verdict in (
        Verdict.BREAKING, Verdict.API_BREAK,
    ), "removing a genuine public typedef must still be reported"


def _record_field_access(library, old_access, new_access):
    tname = "std::__detail::_Node"
    old = _elf_snapshot(name=library, types=[
        RecordType(name=tname, kind="class",
                   fields=[TypeField(name="x", type="int", access=old_access)])])
    new = _elf_snapshot(name=library, types=[
        RecordType(name=tname, kind="class",
                   fields=[TypeField(name="x", type="int", access=new_access)])])
    return compare(old, new)


def test_stdlib_field_access_change_is_filtered_for_a_normal_library():
    """A std:: record reached by a cross-module detector (FIELD_ACCESS_CHANGED in
    diff_symbols) must also be filtered — the surface predicate is shared across
    detector modules, not just diff_types (Codex review on PR #273)."""
    result = _record_field_access("libtbb.so.12", AccessLevel.PUBLIC, AccessLevel.PRIVATE)
    assert result.verdict not in (Verdict.BREAKING, Verdict.API_BREAK), (
        f"std:: field access churn in a non-runtime library must stay filtered; "
        f"kinds: {[c.kind.value for c in result.changes]}"
    )


def test_stdlib_field_access_change_is_breaking_when_target_is_the_runtime():
    """The same std:: field access narrowing IS a source break for libstdc++."""
    result = _record_field_access("libstdc++.so.6", AccessLevel.PUBLIC, AccessLevel.PRIVATE)
    assert result.verdict in (Verdict.BREAKING, Verdict.API_BREAK), (
        f"std:: field access narrowing in libstdc++ itself must still be reported; "
        f"kinds: {[c.kind.value for c in result.changes]}"
    )
