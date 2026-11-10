"""The GLIBCXX_DUAL_ABI_CHANGED detector, its model helpers and the ADR-050
gate waiver (amendment 2026-11-09).

The mangled/demangled pairs below are real g++ 13 output for one source file
compiled with ``-D_GLIBCXX_USE_CXX11_ABI=1`` and ``=0`` (``nm`` + ``c++filt``),
so the oracle is the compiler, not the normalizer under test.
"""

from __future__ import annotations

import itertools
import json
import shutil

import pytest
from hypothesis import given, strategies as st

from abicheck.build_mode import build_mode_from_signals
from abicheck.checker import ChangeKind, compare
from abicheck.checker_policy import BREAKING_KINDS
from abicheck.comparability import (
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.comparability_profile import macro_ops_detail
from abicheck.compare.glibcxx_dual_abi import (
    ExportNames,
    _pair_direction,
    export_names,
    normalize_dual_abi_spelling,
    pair_remangled_exports,
    remangled_export_pairs,
)
from abicheck.demangle import demangle
from abicheck.errors import ProfileMismatchError
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.build_mode_facts import GlibcxxDualAbi
from abicheck.model.glibcxx_dual_abi import (
    SelectionSource,
    dual_abi_from_spellings,
    macro_ops_differ_only_in_runtime_abi,
    parse_macro_ops,
    selection_from_macro_ops,
    selection_from_producer_flags,
    spells_dual_abi_type,
)

# (name, mangled under =1, mangled under =0, demangled under =1, demangled under =0)
_REAL = [
    (
        "make",
        "_ZN3lib4makeB5cxx11Ev",
        "_ZN3lib4makeEv",
        "lib::make[abi:cxx11]()",
        "lib::make()",
    ),
    (
        "take",
        "_ZN3lib4takeERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
        "_ZN3lib4takeERKSs",
        "lib::take(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&)",
        "lib::take(std::basic_string<char, std::char_traits<char>, std::allocator<char> > const&)",
    ),
    (
        "wtake",
        "_ZN3lib5wtakeERKNSt7__cxx1112basic_stringIwSt11char_traitsIwESaIwEEE",
        "_ZN3lib5wtakeERKSbIwSt11char_traitsIwESaIwEE",
        "lib::wtake(std::__cxx11::basic_string<wchar_t, std::char_traits<wchar_t>, std::allocator<wchar_t> > const&)",
        "lib::wtake(std::basic_string<wchar_t, std::char_traits<wchar_t>, std::allocator<wchar_t> > const&)",
    ),
    (
        "ltake",
        "_ZN3lib5ltakeERKNSt7__cxx114listIiSaIiEEE",
        "_ZN3lib5ltakeERKSt4listIiSaIiEE",
        "lib::ltake(std::__cxx11::list<int, std::allocator<int> > const&)",
        "lib::ltake(std::list<int, std::allocator<int> > const&)",
    ),
    (
        "vtake",
        "_ZN3lib5vtakeERKSt6vectorINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEESaIS6_EE",
        "_ZN3lib5vtakeERKSt6vectorISsSaISsEE",
        "lib::vtake(std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&)",
        "lib::vtake(std::vector<std::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&)",
    ),
    (
        "over",
        "_ZN3lib4overERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
        "_ZN3lib4overERKSs",
        "lib::over(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&)",
        "lib::over(std::basic_string<char, std::char_traits<char>, std::allocator<char> > const&)",
    ),
    (
        "W::id",
        "_ZNK3lib1W2idB5cxx11Ev",
        "_ZNK3lib1W2idEv",
        "lib::W::id[abi:cxx11]() const",
        "lib::W::id() const",
    ),
    (
        "W::name",
        "_ZNK3lib1W4nameERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
        "_ZNK3lib1W4nameERKSs",
        "lib::W::name(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&) const",
        "lib::W::name(std::basic_string<char, std::char_traits<char>, std::allocator<char> > const&) const",
    ),
]
#: Exports the selection does not change (identical mangled names under both).
_UNCHANGED = [
    ("over", "_ZN3lib4overEPKc", "lib::over(char const*)"),
    ("plain", "_ZN3lib5plainEi", "lib::plain(int)"),
]

_CXX11, _OLD = 1, 0

#: Pairing re-mangled exports reads their demangled text.
needs_demangler = pytest.mark.skipif(
    demangle(_REAL[0][1]) is None and shutil.which("c++filt") is None,
    reason="pairing re-mangled exports needs cxxfilt or c++filt",
)


# ── model helpers ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("row", _REAL, ids=[r[0] for r in _REAL])
def test_one_declaration_normalizes_equal_under_both_abis(row):
    _, _, _, dem1, dem0 = row
    assert dem1 != dem0
    assert normalize_dual_abi_spelling(dem1) == normalize_dual_abi_spelling(dem0)


def test_normalization_keeps_distinct_declarations_distinct():
    # Over the whole domain, under either ABI: no two different declarations
    # collapse to one spelling (the overload over(char const*) vs
    # over(std::string const&) included).
    for abi_col in (3, 4):
        spellings = [r[abi_col] for r in _REAL] + [u[2] for u in _UNCHANGED]
        normalized = [normalize_dual_abi_spelling(s) for s in spellings]
        assert len(set(normalized)) == len(spellings)
    for a, b in itertools.combinations(_REAL, 2):
        assert normalize_dual_abi_spelling(a[3]) != normalize_dual_abi_spelling(b[4])


@pytest.mark.parametrize(
    ("ops", "expected"),
    [
        ('[["D", "_GLIBCXX_USE_CXX11_ABI=0"]]', GlibcxxDualAbi.OLD),
        ('[["D", "_GLIBCXX_USE_CXX11_ABI=1"]]', GlibcxxDualAbi.CXX11),
        ('[["D", "_GLIBCXX_USE_CXX11_ABI"]]', GlibcxxDualAbi.CXX11),
        (
            '[["D", "_GLIBCXX_USE_CXX11_ABI=0"], ["D", "_GLIBCXX_USE_CXX11_ABI=1"]]',
            GlibcxxDualAbi.CXX11,
        ),
        ('[["D", "_GLIBCXX_USE_CXX11_ABI=0"], ["U", "_GLIBCXX_USE_CXX11_ABI"]]', None),
        ('[["D", "_GLIBCXX_USE_CXX11_ABI_X=0"]]', None),
        ('[["D", "NDEBUG"]]', None),
        ("[]", None),
        ("not json", None),
        (None, None),
    ],
)
def test_selection_from_macro_ops_reads_the_last_operation(ops, expected):
    got = selection_from_macro_ops(parse_macro_ops(ops))
    assert (got.abi if got else None) is expected
    if got is not None:
        assert got.source is SelectionSource.HEADER_PARSE


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (["-O2", "-D_GLIBCXX_USE_CXX11_ABI=0"], GlibcxxDualAbi.OLD),
        (["-D_GLIBCXX_USE_CXX11_ABI=1"], GlibcxxDualAbi.CXX11),
        (["-D_GLIBCXX_USE_CXX11_ABI=0", "-D_GLIBCXX_USE_CXX11_ABI=1"], None),
        (["-O2", "-g"], None),
    ],
)
def test_selection_from_producer_flags(flags, expected):
    got = selection_from_producer_flags(flags)
    assert (got.abi if got else None) is expected


_ABI0 = '["D", "_GLIBCXX_USE_CXX11_ABI=0"]'
_ABI1 = '["D", "_GLIBCXX_USE_CXX11_ABI=1"]'
_NDEBUG = '["D", "NDEBUG"]'


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("[]", f"[{_ABI0}]", True),
        (f"[{_ABI1}]", f"[{_ABI0}]", True),
        (f"[{_NDEBUG}, {_ABI1}]", f"[{_ABI0}, {_NDEBUG}]", True),
        # Same operations: no difference to waive.
        (f"[{_ABI0}]", f"[{_ABI0}]", False),
        # An unrelated macro difference rides along: still incomparable.
        ("[]", f"[{_ABI0}, {_NDEBUG}]", False),
        ("[]", f"[{_NDEBUG}]", False),
    ],
)
def test_macro_ops_waiver_covers_only_runtime_abi_operations(old, new, expected):
    assert macro_ops_differ_only_in_runtime_abi(old, new) is expected
    assert macro_ops_differ_only_in_runtime_abi(new, old) is expected


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("string", True),
        ("const std::string&", True),
        ("std::wstring", True),
        (
            "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >",
            True,
        ),
        ("list<int>", True),
        ("std::list<int, std::allocator<int> > *", True),
        ("std::vector<std::u16string>", True),
        ("int", False),
        ("std::vector<int>", False),
        ("mylist<int>", False),
        ("my::list<int>", False),
        ("stringy", False),
        ("std::string_view", False),
        (None, False),
    ],
)
def test_spells_dual_abi_type(spelling, expected):
    assert spells_dual_abi_type(spelling) is expected


@pytest.mark.parametrize(
    ("spellings", "expected"),
    [
        # demangled exports, as c++filt prints each ABI (the _REAL rows)
        ([r[3] for r in _REAL], GlibcxxDualAbi.CXX11),
        ([r[4] for r in _REAL], GlibcxxDualAbi.OLD),
        (["lib::take(std::string const&)"], GlibcxxDualAbi.OLD),
        # DWARF record names
        (["std::__cxx11::list<int, std::allocator<int> >"], GlibcxxDualAbi.CXX11),
        (["std::list<int, std::allocator<int> >"], GlibcxxDualAbi.OLD),
        (
            ["std::basic_string<char, std::char_traits<char>, std::allocator<char> >"],
            GlibcxxDualAbi.OLD,
        ),
        # no dual-ABI type: libc++, string_view, vector<int>, user types
        (
            [
                "std::__1::basic_string<char, std::__1::char_traits<char>, std::__1::allocator<char> >"
            ],
            None,
        ),
        (
            [
                "std::basic_string_view<char, std::char_traits<char> >",
                "f(std::string_view)",
            ],
            None,
        ),
        (
            [
                "std::vector<int, std::allocator<int> >",
                "my::list<int>",
                "lib::plain(int)",
            ],
            None,
        ),
        # both ABIs exported (libstdc++ itself): no single selection
        ([_REAL[1][3], _REAL[1][4]], None),
        ([], None),
    ],
)
def test_dual_abi_from_spellings_reads_positive_evidence_only(spellings, expected):
    assert dual_abi_from_spellings(spellings) is expected


# ── detector ───────────────────────────────────────────────────────────────


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _snap(*, functions=(), variables=(), types=(), macro_ops=None) -> AbiSnapshot:
    contract = (
        compute_extraction_contract(l2_frontend_ran=True, macro_ops=macro_ops)
        if macro_ops is not None
        else None
    )
    snap = AbiSnapshot(
        library="libapi.so",
        version="1",
        functions=list(functions),
        variables=list(variables),
        types=list(types),
        contract=contract,
    )
    return snap


def _side(rows, abi: int) -> list[Function]:
    col = 1 if abi == _CXX11 else 2
    return [_fn(r[0], r[col]) for r in rows] + [_fn(u[0], u[1]) for u in _UNCHANGED]


def _dual(result) -> list:
    return [c for c in result.changes if c.kind is ChangeKind.GLIBCXX_DUAL_ABI_CHANGED]


def test_kind_is_breaking():
    assert ChangeKind.GLIBCXX_DUAL_ABI_CHANGED in BREAKING_KINDS


@needs_demangler
@pytest.mark.parametrize("row", _REAL, ids=[r[0] for r in _REAL])
@pytest.mark.parametrize(
    ("old_abi", "new_abi"), [(_CXX11, _OLD), (_OLD, _CXX11)], ids=["1to0", "0to1"]
)
def test_each_re_mangled_export_alone_proves_the_flip(row, old_abi, new_abi):
    # Binary only: no selection fact on either side, one affected export.
    result = compare(
        _snap(functions=_side([row], old_abi)), _snap(functions=_side([row], new_abi))
    )
    (root,) = _dual(result)
    assert result.verdict.value == "BREAKING"
    assert "1 export(s) re-mangled" in root.description
    old_m, new_m = (row[1], row[2]) if old_abi == _CXX11 else (row[2], row[1])
    assert f"{old_m} -> {new_m}" in root.description
    assert root.affected_symbols == [old_m]
    assert f"_GLIBCXX_USE_CXX11_ABI={old_abi}" in root.old_value
    assert f"_GLIBCXX_USE_CXX11_ABI={new_abi}" in root.new_value


@needs_demangler
def test_whole_library_flip_names_every_pair_once():
    result = compare(
        _snap(functions=_side(_REAL, _CXX11)), _snap(functions=_side(_REAL, _OLD))
    )
    (root,) = _dual(result)
    assert f"{len(_REAL)} export(s) re-mangled" in root.description
    assert sorted(root.affected_symbols) == sorted(r[1] for r in _REAL)


def test_same_abi_rebuild_reports_nothing():
    for abi in (_CXX11, _OLD):
        result = compare(
            _snap(functions=_side(_REAL, abi)), _snap(functions=_side(_REAL, abi))
        )
        assert _dual(result) == []


def test_unrelated_removal_and_addition_is_not_paired():
    old = _snap(functions=[_fn("take", _REAL[1][1])])
    new = _snap(functions=[_fn("give", "_ZN3lib4giveERKSs")])
    assert _dual(compare(old, new)) == []


def _rec(string_bits: int) -> RecordType:
    return RecordType(
        name="Rec",
        kind="struct",
        size_bits=32 + 32 + string_bits,
        fields=[
            TypeField(name="id", type="int", offset_bits=0),
            TypeField(name="name", type="string", offset_bits=64),
        ],
    )


_RECORD_FN = [_fn("rec_id", "_Z6rec_idPK3Rec")]


def test_known_flip_with_a_record_holding_a_string_is_a_break():
    # Both header parses name their selection; the record need not change
    # layout for the flip to reach it (a std::string* field would not).
    old = _snap(
        functions=_RECORD_FN,
        types=[_rec(256)],
        macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=1")],
    )
    new = _snap(
        functions=_RECORD_FN,
        types=[_rec(256)],
        macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=0")],
    )
    (root,) = _dual(compare(old, new))
    assert "public record(s) holding a changed type: Rec" in root.description
    assert root.old_value == "_GLIBCXX_USE_CXX11_ABI=1 (header parse)"
    assert root.new_value == "_GLIBCXX_USE_CXX11_ABI=0 (header parse)"


@pytest.mark.parametrize("direction", ["default_to_0", "0_to_default"])
def test_one_sided_selector_needs_the_record_layout_to_move(direction):
    # One header parse ran -D_GLIBCXX_USE_CXX11_ABI=0, the other used the
    # compiler default. The default could already be =0, so only an observed
    # layout change of a record holding a dual-ABI type counts.
    ops = {"default": [], "0": [("D", "_GLIBCXX_USE_CXX11_ABI=0")]}
    old_key, new_key = (
        ("default", "0") if direction == "default_to_0" else ("0", "default")
    )
    bits = {"default": 256, "0": 64}
    moved = compare(
        _snap(
            functions=_RECORD_FN, types=[_rec(bits[old_key])], macro_ops=ops[old_key]
        ),
        _snap(
            functions=_RECORD_FN, types=[_rec(bits[new_key])], macro_ops=ops[new_key]
        ),
    )
    (root,) = _dual(moved)
    assert "Rec" in root.description
    assert "compiler default (header parse)" in f"{root.old_value} {root.new_value}"

    unmoved = compare(
        _snap(functions=_RECORD_FN, types=[_rec(64)], macro_ops=ops[old_key]),
        _snap(functions=_RECORD_FN, types=[_rec(64)], macro_ops=ops[new_key]),
    )
    assert _dual(unmoved) == []
    # The waived macro difference is still recorded (ADR-067), never gated.
    (drift,) = [c for c in unmoved.changes if c.kind is ChangeKind.TOOLCHAIN_FLAG_DRIFT]
    assert "_GLIBCXX_USE_CXX11_ABI=0" in drift.description
    assert unmoved.verdict.value != "BREAKING"


def test_known_flip_with_an_exported_string_variable_is_a_break():
    var = Variable(
        name="lib::banner",
        mangled="_ZN3lib6bannerE",
        type="std::string",
        visibility=Visibility.PUBLIC,
    )
    old = _snap(variables=[var], macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=1")])
    new = _snap(variables=[var], macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=0")])
    (root,) = _dual(compare(old, new))
    assert "exported variable(s) of a changed type: lib::banner" in root.description


def test_known_flip_with_nothing_affected_is_recorded_not_gated():
    plain = [_fn(u[0], u[1]) for u in _UNCHANGED]
    old = _snap(functions=plain, macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=1")])
    new = _snap(functions=plain, macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=0")])
    result = compare(old, new)
    assert _dual(result) == []
    (drift,) = [c for c in result.changes if c.kind is ChangeKind.TOOLCHAIN_FLAG_DRIFT]
    assert drift.old_value == "_GLIBCXX_USE_CXX11_ABI=1 (header parse)"
    assert result.verdict.value in ("NO_CHANGE", "COMPATIBLE")


def test_no_selection_and_no_pair_claims_nothing_for_a_record():
    # Partial evidence: a record changed layout but nothing names the
    # selection. The layout finding decides; no dual-ABI root is invented.
    result = compare(
        _snap(functions=_RECORD_FN, types=[_rec(256)]),
        _snap(functions=_RECORD_FN, types=[_rec(64)]),
    )
    assert _dual(result) == []
    assert ChangeKind.TOOLCHAIN_FLAG_DRIFT not in {c.kind for c in result.changes}


def _std_string_record(name: str) -> RecordType:
    return RecordType(name=name, kind="class", size_bits=256, fields=[])


@pytest.mark.parametrize("direction", ["1to0", "0to1"])
def test_debug_info_type_names_select_each_side(direction):
    # Binary-only with DWARF, the record passed by pointer: no export is
    # re-mangled and nothing records a -D; each side's debug-info names
    # spell its string type, so the flip and the record are known.
    new_abi = "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >"
    old_abi = "std::basic_string<char, std::char_traits<char>, std::allocator<char> >"
    names = (new_abi, old_abi) if direction == "1to0" else (old_abi, new_abi)
    old = _snap(functions=_RECORD_FN, types=[_rec(256), _std_string_record(names[0])])
    new = _snap(functions=_RECORD_FN, types=[_rec(64), _std_string_record(names[1])])
    (root,) = _dual(compare(old, new))
    assert "public record(s) holding a changed type: Rec" in root.description
    expected = ("1", "0") if direction == "1to0" else ("0", "1")
    assert root.old_value.startswith(f"_GLIBCXX_USE_CXX11_ABI={expected[0]} (inferred")
    assert root.new_value.startswith(f"_GLIBCXX_USE_CXX11_ABI={expected[1]} (inferred")


def _ns_rec(ns: str, field_type: str, bits: int) -> RecordType:
    return RecordType(
        name="Rec",
        qualified_name=f"{ns}::Rec",
        kind="struct",
        size_bits=bits,
        fields=[TypeField(name="f", type=field_type, offset_bits=0)],
    )


def test_records_sharing_a_bare_name_are_paired_by_qualified_identity():
    # a::Rec holds a string and keeps its layout; the unrelated b::Rec (an
    # int) grows. Paired by bare name, a::Rec would meet b::Rec and read as a
    # moved string-holding record; paired by qualified identity, nothing the
    # flip changes moved.
    ops = {"old": [], "new": [("D", "_GLIBCXX_USE_CXX11_ABI=0")]}
    old = _snap(
        functions=_RECORD_FN,
        types=[_ns_rec("a", "string", 256), _ns_rec("b", "int", 32)],
        macro_ops=ops["old"],
    )
    new = _snap(
        functions=_RECORD_FN,
        types=[_ns_rec("a", "string", 256), _ns_rec("b", "int", 64)],
        macro_ops=ops["new"],
    )
    result = compare(old, new)
    assert _dual(result) == []
    assert ChangeKind.TOOLCHAIN_FLAG_DRIFT in {c.kind for c in result.changes}

    # The same shape with a::Rec's layout moved names it, by its qualified key.
    moved = _snap(
        functions=_RECORD_FN,
        types=[_ns_rec("a", "string", 64), _ns_rec("b", "int", 32)],
        macro_ops=ops["new"],
    )
    (root,) = _dual(compare(old, moved))
    assert "public record(s) holding a changed type: a::Rec" in root.description


def test_a_flip_read_from_names_needs_a_moved_layout():
    # Each side's names spell a different ABI, but nothing a consumer shares
    # moved: Rec keeps its layout and the exported string variable keeps its
    # name. A library that merely started or stopped using a dual-ABI type
    # changes its names the same way, so no break is claimed.
    new_abi = "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >"
    old_abi = "std::basic_string<char, std::char_traits<char>, std::allocator<char> >"
    var = Variable(
        name="lib::banner",
        mangled="_ZN3lib6bannerE",
        type="std::string",
        visibility=Visibility.PUBLIC,
    )
    old = _snap(
        functions=_RECORD_FN,
        variables=[var],
        types=[_rec(256), _std_string_record(new_abi)],
    )
    new = _snap(
        functions=_RECORD_FN,
        variables=[var],
        types=[_rec(256), _std_string_record(old_abi)],
    )
    assert _dual(compare(old, new)) == []


# ── ADR-050 gate ───────────────────────────────────────────────────────────


def _contract_snap(**kwargs) -> AbiSnapshot:
    return AbiSnapshot(
        library="libapi.so",
        version="1",
        contract=compute_extraction_contract(l2_frontend_ran=True, **kwargs),
    )


@pytest.mark.parametrize(
    ("old_ops", "new_ops"),
    [
        ([], [("D", "_GLIBCXX_USE_CXX11_ABI=0")]),
        ([("D", "_GLIBCXX_USE_CXX11_ABI=0")], []),
        ([("D", "_GLIBCXX_USE_CXX11_ABI=1")], [("D", "_GLIBCXX_USE_CXX11_ABI=0")]),
        ([("D", "NDEBUG")], [("D", "NDEBUG"), ("D", "_GLIBCXX_USE_CXX11_ABI=0")]),
    ],
)
def test_gate_compares_a_selector_only_macro_difference(old_ops, new_ops):
    old, new = _contract_snap(macro_ops=old_ops), _contract_snap(macro_ops=new_ops)
    assert old.contract.profile_fingerprint != new.contract.profile_fingerprint
    assert check_contracts_comparable(old, new) is None


def test_gate_still_refuses_when_another_macro_differs_too():
    old = _contract_snap(macro_ops=[])
    new = _contract_snap(macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=0"), ("D", "API_V2")])
    with pytest.raises(ProfileMismatchError) as excinfo:
        check_contracts_comparable(old, new)
    message = str(excinfo.value)
    assert "-DAPI_V2" in message
    assert "glibcxx_dual_abi_changed" in message


@pytest.mark.parametrize(
    ("old_target", "new_target"),
    [
        (("x86_64-linux-gnu", 64), ("aarch64-linux-gnu", 64)),
        (("x86_64-linux-gnu", 64), ("i686-linux-gnu", 32)),
    ],
    ids=["other-arch", "other-width"],
)
def test_gate_still_refuses_a_different_architecture_beside_the_selector(
    old_target, new_target
):
    # The waiver covers the selector only: a target the binaries do not
    # corroborate stays an incomparable profile, with or without the flip.
    old = _contract_snap(
        macro_ops=[], target_triple=old_target[0], pointer_width=old_target[1]
    )
    new = _contract_snap(
        macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=0")],
        target_triple=new_target[0],
        pointer_width=new_target[1],
    )
    with pytest.raises(ProfileMismatchError) as excinfo:
        check_contracts_comparable(old, new)
    assert "target_triple" in str(excinfo.value)


def test_gate_still_refuses_another_profile_field_beside_the_selector():
    old = _contract_snap(macro_ops=[], compiler_family="gcc")
    new = _contract_snap(
        macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=0")], compiler_family="clang"
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


# ── primitive properties (quality-gates.md "Primitive-level property tests") ──
#
# A declaration is ``(name, parameter type kind)``. Its spelling under each ABI
# comes from the hand-written oracle below (how c++filt prints each ABI, in
# both of the old ABI's printed forms), not from ``normalize_dual_abi_spelling``.

_PARAM_SPELLING = {
    # kind: (=1 spelling, (=0 spellings ...))
    "int": ("int", ("int",)),
    "string": (
        "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&",
        (
            "std::basic_string<char, std::char_traits<char>, std::allocator<char> > const&",
            "std::string const&",
        ),
    ),
    "wstring": (
        "std::__cxx11::basic_string<wchar_t, std::char_traits<wchar_t>, std::allocator<wchar_t> > const&",
        (
            "std::basic_string<wchar_t, std::char_traits<wchar_t>, std::allocator<wchar_t> > const&",
        ),
    ),
    "list": (
        "std::__cxx11::list<int, std::allocator<int> > const&",
        ("std::list<int, std::allocator<int> > const&",),
    ),
}
_AFFECTED = {"string", "wstring", "list"}

_decl = st.tuples(
    st.sampled_from(["f", "g", "h", "take", "give"]),
    st.sampled_from(sorted(_PARAM_SPELLING)),
)


def _spell(decl: tuple[str, str], abi: int, variant: int = 0) -> str:
    name, kind = decl
    new, olds = _PARAM_SPELLING[kind]
    return f"lib::{name}({new if abi == _CXX11 else olds[variant % len(olds)]})"


def _mangled(decl: tuple[str, str], abi: int) -> str:
    # Distinct mangled names per ABI only where the type's mangling changes.
    name, kind = decl
    tag = abi if kind in _AFFECTED else "x"
    return f"_Z{len(name)}{name}_{kind}_{tag}"


def _side_of(exports: dict[tuple[str, str], int], variant: int) -> ExportNames:
    """``{declaration: abi}`` -> the side's ExportNames, built by the oracle."""
    names = {_mangled(d, abi): d[0] for d, abi in exports.items()}
    demangled = {
        _mangled(d, abi): _spell(d, abi, variant) for d, abi in exports.items()
    }
    return ExportNames(names, demangled)


_exports = st.dictionaries(_decl, st.sampled_from([_CXX11, _OLD]), max_size=8)


class TestRemangledExportPairsProperties:
    @given(old=_exports, new=_exports, variant=st.integers(0, 1))
    def test_pairs_only_one_declaration_under_two_abis(self, old, new, variant):
        """Soundness: every pair is one declaration, removed under one ABI and
        added under the other, of a type the selection changes."""
        o, n = _side_of(old, 0), _side_of(new, variant)
        by_mangled = {
            **{_mangled(d, a): d for d, a in old.items()},
            **{_mangled(d, a): d for d, a in new.items()},
        }
        for a, b in pair_remangled_exports(o, n):
            assert a not in n.exports and b not in o.exports
            assert by_mangled[a] == by_mangled[b]
            assert by_mangled[a][1] in _AFFECTED
            assert old[by_mangled[a]] != new[by_mangled[b]]

    @given(old=_exports, new=_exports, variant=st.integers(0, 1))
    def test_every_flipped_declaration_is_paired(self, old, new, variant):
        """Completeness: a declaration of an affected type exported on both
        sides under different ABIs is always paired (every generated
        declaration has a distinct normalized spelling, so no twin is
        ambiguous)."""
        o, n = _side_of(old, 0), _side_of(new, variant)
        pairs = set(pair_remangled_exports(o, n))
        for d, abi in old.items():
            if d in new and new[d] != abi and d[1] in _AFFECTED:
                assert (_mangled(d, abi), _mangled(d, new[d])) in pairs

    @given(old=_exports, new=_exports)
    def test_mirror_and_order_independence(self, old, new):
        """Swapping the sides mirrors the pairs; insertion order is irrelevant."""
        o, n = _side_of(old, 0), _side_of(new, 0)
        pairs = pair_remangled_exports(o, n)
        mirrored = pair_remangled_exports(n, o)
        assert sorted((b, a) for a, b in pairs) == sorted(mirrored)
        reversed_o = ExportNames(
            dict(reversed(list(o.exports.items()))),
            dict(reversed(list(o.demangled.items()))),
        )
        assert pair_remangled_exports(reversed_o, n) == pairs

    @given(decl=_decl, abi=st.sampled_from([_CXX11, _OLD]), variant=st.integers(0, 1))
    def test_normalization_is_idempotent_and_abi_blind(self, decl, abi, variant):
        once = normalize_dual_abi_spelling(_spell(decl, abi, variant))
        assert normalize_dual_abi_spelling(once) == once
        assert once == normalize_dual_abi_spelling(_spell(decl, 1 - abi, 0))

    @given(
        a=_decl, b=_decl, abi_a=st.sampled_from([0, 1]), abi_b=st.sampled_from([0, 1])
    )
    def test_normalization_keeps_declarations_apart(self, a, b, abi_a, abi_b):
        same = normalize_dual_abi_spelling(
            _spell(a, abi_a)
        ) == normalize_dual_abi_spelling(_spell(b, abi_b))
        assert same == (a == b)


_other_op = st.sampled_from([("D", "NDEBUG"), ("D", "API_V2=1"), ("U", "FOO")])
_abi_op = st.sampled_from(
    [
        ("D", "_GLIBCXX_USE_CXX11_ABI=0"),
        ("D", "_GLIBCXX_USE_CXX11_ABI=1"),
        ("U", "_GLIBCXX_USE_CXX11_ABI"),
    ]
)


def _json_ops(ops) -> str:
    return json.dumps([list(op) for op in ops])


class TestMacroOpsWaiverProperties:
    @given(
        common=st.lists(_other_op, max_size=3),
        old_abi=st.lists(_abi_op, max_size=2),
        new_abi=st.lists(_abi_op, max_size=2),
    )
    def test_selector_only_differences_are_waived(self, common, old_abi, new_abi):
        """Same other operations, any selector operations: waived exactly when
        the lists differ at all; symmetric."""
        old, new = _json_ops(common + old_abi), _json_ops(common + new_abi)
        expected = (common + old_abi) != (common + new_abi)
        assert macro_ops_differ_only_in_runtime_abi(old, new) is expected
        assert macro_ops_differ_only_in_runtime_abi(new, old) is expected

    @given(
        common=st.lists(_other_op, max_size=3),
        extra=_other_op,
        old_abi=st.lists(_abi_op, max_size=2),
        new_abi=st.lists(_abi_op, max_size=2),
    )
    def test_another_operation_difference_is_never_waived(
        self, common, extra, old_abi, new_abi
    ):
        old, new = _json_ops(common + old_abi), _json_ops(common + [extra] + new_abi)
        assert macro_ops_differ_only_in_runtime_abi(old, new) is False
        assert macro_ops_differ_only_in_runtime_abi(new, old) is False


# ── evidence branches ──────────────────────────────────────────────────────


def _with_producer(snap: AbiSnapshot, flags: str) -> AbiSnapshot:
    snap.build_mode = build_mode_from_signals(
        raw_producer=f"GNU C++17 13.3.0 {flags} -g"
    )
    return snap


def test_producer_flags_name_the_selection_on_both_sides():
    # No header parse, no re-mangled export: the -D each unit recorded in
    # DW_AT_producer names the flip, so an unchanged record and an exported
    # string variable are attributed (both sides *name* their selection).
    var = Variable(
        name="lib::banner",
        mangled="_ZN3lib6bannerE",
        type="std::string",
        visibility=Visibility.PUBLIC,
    )
    old = _with_producer(
        _snap(functions=_RECORD_FN, variables=[var], types=[_rec(256)]),
        "-D_GLIBCXX_USE_CXX11_ABI=1",
    )
    new = _with_producer(
        _snap(functions=_RECORD_FN, variables=[var], types=[_rec(256)]),
        "-D_GLIBCXX_USE_CXX11_ABI=0",
    )
    (root,) = _dual(compare(old, new))
    assert root.old_value == "_GLIBCXX_USE_CXX11_ABI=1 (DW_AT_producer)"
    assert root.new_value == "_GLIBCXX_USE_CXX11_ABI=0 (DW_AT_producer)"
    assert "lib::banner" in root.description and "Rec" in root.description


def test_pairs_need_spellings_that_name_different_abis():
    # Normalize-equal spellings that differ only in spacing, and exports with
    # no demangled spelling (C names), are never paired.
    old = ExportNames(
        {"_Z1fv_a": "f", "c_api": "c_api"},
        {"_Z1fv_a": "lib::f(int)"},
    )
    new = ExportNames(
        {"_Z1fv_b": "f", "c_api2": "c_api2"},
        {"_Z1fv_b": "lib::f( int )"},
    )
    assert pair_remangled_exports(old, new) == []


def test_mixed_direction_pairs_do_not_claim_one_selection():
    # One export moved to the new ABI, another to the old: the pairs prove a
    # flip but no single selection per side, so the values say so.
    take1, take0 = _REAL[1][1], _REAL[1][2]
    lt1, lt0 = _REAL[3][1], _REAL[3][2]
    old = ExportNames(
        {take1: "take", lt0: "ltake"},
        {take1: _REAL[1][3], lt0: _REAL[3][4]},
    )
    new = ExportNames(
        {take0: "take", lt1: "ltake"},
        {take0: _REAL[1][4], lt1: _REAL[3][3]},
    )
    pairs = pair_remangled_exports(old, new)
    assert sorted(pairs) == sorted([(take1, take0), (lt0, lt1)])
    assert _pair_direction(pairs, old, new) == ("one libstdc++ ABI", "the other")


@needs_demangler
def test_snapshot_pairing_matches_the_pure_primitive():
    old = _snap(functions=_side(_REAL, _CXX11))
    new = _snap(functions=_side(_REAL, _OLD))
    assert remangled_export_pairs(old, new) == pair_remangled_exports(
        export_names(old), export_names(new)
    )
    assert len(remangled_export_pairs(old, new)) == len(_REAL)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"D": "_GLIBCXX_USE_CXX11_ABI=0"}', []),
        ('[["D"], ["D", 1], "x"]', []),
        ('[["D", "_GLIBCXX_USE_CXX11_ABI=0"]]', [("D", "_GLIBCXX_USE_CXX11_ABI=0")]),
    ],
)
def test_parse_macro_ops_keeps_only_well_formed_operations(raw, expected):
    assert parse_macro_ops(raw) == expected


def test_an_undefined_selector_value_selects_nothing():
    assert selection_from_macro_ops([("D", "_GLIBCXX_USE_CXX11_ABI=2")]) is None
    assert selection_from_producer_flags(["-D_GLIBCXX_USE_CXX11_ABI=2"]) is None


@pytest.mark.parametrize(
    ("old_ops", "new_ops", "contains", "absent"),
    [
        ([], [], None, "Macro operations"),
        ([("D", "A")], [("D", "B")], "only in old: -DA; only in new: -DB", "selects"),
        ([], [("D", "_GLIBCXX_USE_CXX11_ABI=0")], "glibcxx_dual_abi_changed", None),
    ],
)
def test_macro_ops_detail_names_what_differs(old_ops, new_ops, contains, absent):
    detail = macro_ops_detail(
        {"macro_ops": json.dumps([list(o) for o in old_ops])},
        {"macro_ops": json.dumps([list(o) for o in new_ops])},
    )
    if contains is None:
        assert detail == ""
    else:
        assert contains in detail
    if absent is not None:
        assert absent not in detail
