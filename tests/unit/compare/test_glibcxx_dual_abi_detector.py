"""The GLIBCXX_DUAL_ABI_CHANGED detector, its model helpers and the ADR-050
gate waiver (amendment 2026-11-09).

The mangled/demangled pairs below are real g++ 13 output for one source file
compiled with ``-D_GLIBCXX_USE_CXX11_ABI=1`` and ``=0`` (``nm`` + ``c++filt``),
so the oracle is the compiler, not the normalizer under test.
"""

from __future__ import annotations

import itertools
import shutil

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.checker_policy import BREAKING_KINDS
from abicheck.comparability import (
    check_contracts_comparable,
    compute_extraction_contract,
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
    macro_ops_differ_only_in_runtime_abi,
    normalize_dual_abi_spelling,
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
    got = selection_from_macro_ops(ops)
    assert (got.abi if got else None) is expected
    if got is not None:
        assert got.source == "header parse"


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


def test_gate_still_refuses_another_profile_field_beside_the_selector():
    old = _contract_snap(macro_ops=[], compiler_family="gcc")
    new = _contract_snap(
        macro_ops=[("D", "_GLIBCXX_USE_CXX11_ABI=0")], compiler_family="clang"
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)
