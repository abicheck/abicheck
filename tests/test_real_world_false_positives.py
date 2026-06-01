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
    return {
        c.symbol
        for c in result.changes
        if getattr(c.severity, "value", c.severity) == "breaking"
    }


# ---------------------------------------------------------------------------
# FP-3 — RTTI/typeinfo of an anonymous lambda must not be a breaking var_removed
#   Real case: Protobuf 6.33.2 -> 6.33.5 (a *patch*) flagged BREAKING because
#   `_ZTIZN6google8protobuf2io7Printer8WithDefs...EUlSt17basic_string...E_`
#   (typeinfo for an internal lambda) "disappeared".  Lambda identity is not
#   stable ABI.  Root cause: dumper._elf_classify_symbols() does not apply
#   _is_abi_relevant_symbol(), and diff_symbols._var_removed has no _ZTI/_ZTS
#   guard, so the symbol becomes a public Variable -> VAR_REMOVED (breaking).
# ---------------------------------------------------------------------------
@pytest.mark.xfail(
    strict=True,
    reason=f"known FP-3: lambda RTTI removal scored as breaking; see {REPORT}",
)
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
#   evidence of removal.  Root cause: diff_types emits TYPE_REMOVED whenever a
#   type is absent from new_map, with no guard for asymmetric type coverage.
# ---------------------------------------------------------------------------
@pytest.mark.xfail(
    strict=True,
    reason=f"known FP-4: mixed DWARF/stripped fabricates removals; see {REPORT}",
)
def test_stripped_new_side_does_not_fabricate_type_removals():
    # old: rich DWARF types
    old = _elf_snapshot(
        types=[
            RecordType(
                name="_xmlNode",
                kind="struct",
                size_bits=960,
                fields=[TypeField(name="type", type="int", offset_bits=0)],
            ),
            RecordType(name="_xmlDoc", kind="struct", size_bits=512),
        ]
    )
    # new: same library, but the binary is stripped -> zero type DWARF
    new = _elf_snapshot(types=[])
    new.dwarf = None
    result = compare(old, new)
    assert result.verdict not in (Verdict.BREAKING,), (
        f"types absent only because the new side is stripped must not read as "
        f"removals; breaking symbols: {_breaking_symbols(result)}"
    )


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
