"""ADR-094 D7: the production ``call`` axis equals the proved model on every
case of the closed fragments E13 (parameter edits) and E14 (return types).

The oracle is the Lean export (``formal/lean/func_fragment_cases.json``,
``ret_fragment_cases.json``): ``call_compatible`` is Lean's ``callCompatible``,
proved for the model and confirmed by execution on gcc and clang
(``formal/FUNC_FRAGMENT.md``, ``formal/RET_FRAGMENT.md``). Each case is
rebuilt here as the ``Function`` declarations abicheck's extractors produce --
parameter names, spelled types, pointer depths and the compiler's ABI fact for
each fundamental slot -- and run through ``call_signature_of`` and
``assess_call_interface``. Struct returns enter as their ``RetShape``, the
classification a snapshot's DWARF facts supply.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from abicheck.compare.call_interface import (
    AxisStatus,
    CallSignature,
    assess_call_interface,
    call_signature_of,
)
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.call_interface import SYSV_AMD64, ArgSlot, Rep, RetShape
from abicheck.model.declarations import Function, Param
from abicheck.model.fact import Fact

FORMAL = Path(__file__).resolve().parent.parent / "formal"


def _load(name: str):  # type: ignore[no-untyped-def]
    path = FORMAL / "experiments" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FUNC = _load("func_fragment")
RET = _load("ret_fragment")

_FACT = {
    "int": AbiFundamental(FundamentalEncoding.SIGNED, 4, 4),
    "long": AbiFundamental(FundamentalEncoding.SIGNED, 8, 8),
    "double": AbiFundamental(FundamentalEncoding.FLOAT, 8, 8),
}
_STRUCT = {
    "struct s2l": RetShape.INT2,
    "struct s2d": RetShape.SSE2,
    "struct s3l": RetShape.MEMORY,
}


def _param(name: str, ty: str) -> Param:
    if ty == "void *":
        return Param(name=name, type="void *", pointer_depth=1)
    return Param(name=name, type=ty, type_abi_fact=Fact.present(_FACT[ty]))


def _function(params: list[tuple[str, str]], ret: str) -> Function:
    kw: dict = {}
    if ret == "void *":
        kw["return_pointer_depth"] = 1
    elif ret in _FACT:
        kw["return_type_abi_fact"] = Fact.present(_FACT[ret])
    return Function(
        name="f",
        mangled="f",
        return_type=ret,
        params=[_param(n, t) for n, t in params],
        is_variadic=False,
        contract_attributes=[],
        **kw,
    )


def _sig(params: list[tuple[str, str]], ret: str) -> CallSignature:
    return call_signature_of(_function(params, ret), _STRUCT.get(ret))


def test_e13_parameter_edits_match_the_proved_model() -> None:
    rows = FUNC.load_export()["rows"]
    assert len(rows) == 2160
    for r in rows:
        old = _sig(FUNC._parse(r["old_body"]), "int")
        new = _sig(FUNC._parse(r["new_body"]), "int")
        axis = assess_call_interface(old, new, SYSV_AMD64)
        assert axis.status is not AxisStatus.UNKNOWN, (r["id"], axis)
        assert (axis.status is AxisStatus.PRESERVED) == r["call"], (r["id"], axis)


def test_e14_return_type_changes_match_the_proved_model() -> None:
    rows = RET.load_export()["rows"]
    assert len(rows) == 1176
    for r in rows:
        params = [(f"p{j}", t) for j, t in enumerate(r["params"])]
        old, new = _sig(params, r["old"]), _sig(params, r["new"])
        axis = assess_call_interface(old, new, SYSV_AMD64)
        assert axis.status is not AxisStatus.UNKNOWN, (r["id"], axis)
        assert (axis.status is AxisStatus.PRESERVED) == r["call"], (r["id"], axis)


def test_the_408_call_compatible_cases_are_all_preserved() -> None:
    """The cases ADR-094 D8 moves from BREAKING to API_BREAK."""
    n = sum(r["call"] for r in FUNC.load_export()["rows"])
    n += sum(r["call"] for r in RET.load_export()["rows"])
    assert n == 408


# ----------------------------------------------- outside the modelled domain

_I = _FACT["int"]


def _one(ty: str, fact: AbiFundamental | None, depth: int = 0) -> Function:
    p = Param(
        name="p0",
        type=ty,
        pointer_depth=depth,
        **({"type_abi_fact": Fact.present(fact)} if fact else {}),
    )
    return Function(
        name="f",
        mangled="f",
        return_type="void",
        params=[p],
        is_variadic=False,
        contract_attributes=[],
    )


@pytest.mark.parametrize(
    ("ty", "fact", "depth"),
    [
        ("unsigned int", AbiFundamental(FundamentalEncoding.UNSIGNED, 4, 4), 0),
        ("short", AbiFundamental(FundamentalEncoding.SIGNED, 2, 2), 0),
        ("_Bool", AbiFundamental(FundamentalEncoding.BOOLEAN, 1, 1), 0),
        ("float", AbiFundamental(FundamentalEncoding.FLOAT, 4, 4), 0),
        ("long double", AbiFundamental(FundamentalEncoding.FLOAT, 16, 16), 0),
        ("struct S", None, 0),  # by value: no fundamental fact
        ("int", None, 0),  # fact not collected
        ("int S::*", None, 1),  # member pointer
        ("void (*)(int)", None, 1),  # function pointer: not modelled
    ],
)
def test_a_slot_outside_the_domain_is_unknown(
    ty: str, fact: AbiFundamental | None, depth: int
) -> None:
    old = call_signature_of(_one(ty, fact, depth))
    new = call_signature_of(_fn())
    assert assess_call_interface(old, new, SYSV_AMD64).status is AxisStatus.UNKNOWN


def _fn(**kw) -> Function:  # type: ignore[no-untyped-def]
    base: dict = {
        "name": "f",
        "mangled": "f",
        "return_type": "void",
        "is_variadic": False,
        "contract_attributes": [],
    }
    base.update(kw)
    return Function(**base)


def test_only_sysv_amd64_is_modelled() -> None:
    sig = call_signature_of(
        _fn(params=[Param(name="a", type="int", type_abi_fact=Fact.present(_I))])
    )
    for abi in ("win64", "aapcs64", "x32", ""):
        assert assess_call_interface(sig, sig, abi).status is AxisStatus.UNKNOWN


@pytest.mark.parametrize("variadic", [True, None])
def test_variadic_or_unknown_variadic_is_unknown(variadic: bool | None) -> None:
    old = call_signature_of(_fn(is_variadic=variadic))
    new = call_signature_of(_fn())
    assert assess_call_interface(old, new, SYSV_AMD64).status is AxisStatus.UNKNOWN


@pytest.mark.parametrize("names", [["", "b"], ["a", "a"]])
def test_unmatchable_parameter_names_are_unknown(names: list[str]) -> None:
    ps = [Param(name=n, type="int", type_abi_fact=Fact.present(_I)) for n in names]
    sig = call_signature_of(_fn(params=ps))
    assert assess_call_interface(sig, sig, SYSV_AMD64).status is AxisStatus.UNKNOWN


def test_stack_passed_arguments_are_unknown() -> None:
    seven = tuple(ArgSlot(f"p{i}", Rep.I64) for i in range(7))
    sig = CallSignature(seven, RetShape.VOID, False)
    assert assess_call_interface(sig, sig, SYSV_AMD64).status is AxisStatus.UNKNOWN
    # A hidden sret pointer uses one of the six INTEGER registers.
    six = CallSignature(seven[:6], RetShape.MEMORY, False)
    assert assess_call_interface(six, six, SYSV_AMD64).status is AxisStatus.UNKNOWN


@pytest.mark.parametrize("shape", [RetShape.INT2, RetShape.SSE2, RetShape.MEMORY])
def test_two_struct_returns_of_one_class_are_unknown(shape: RetShape) -> None:
    """The shape does not carry member layout ({long,long} vs {int,int,long})."""
    sig = CallSignature((), shape, False)
    assert assess_call_interface(sig, sig, SYSV_AMD64).status is AxisStatus.UNKNOWN


def test_a_renamed_parameter_errs_towards_broken() -> None:
    old = CallSignature((ArgSlot("a", Rep.I32),), RetShape.VOID, False)
    new = CallSignature((ArgSlot("b", Rep.I32),), RetShape.VOID, False)
    axis = assess_call_interface(old, new, SYSV_AMD64)
    assert axis.status is AxisStatus.BROKEN
    assert "new" in axis.reason


def test_preserved_reason_names_the_removed_parameter() -> None:
    old = CallSignature(
        (ArgSlot("a", Rep.I32), ArgSlot("b", Rep.F64)), RetShape.VOID, False
    )
    new = CallSignature((ArgSlot("b", Rep.F64),), RetShape.I32, False)
    axis = assess_call_interface(old, new, SYSV_AMD64)
    assert axis.status is AxisStatus.PRESERVED
    assert (
        "a are ignored" in axis.reason and "never reads the new result" in axis.reason
    )


def _long_param() -> list[Param]:
    return [Param(name="a", type="long", type_abi_fact=Fact.present(_FACT["long"]))]


@pytest.mark.parametrize(
    ("old_attrs", "new_attrs"),
    [(["ms_abi"], ["ms_abi"]), (["sysv_abi"], ["ms_abi"]), (None, [])],
)
def test_a_non_default_or_uncaptured_convention_is_unknown(
    old_attrs: list[str] | None, new_attrs: list[str] | None
) -> None:
    old = call_signature_of(_fn(params=_long_param(), contract_attributes=old_attrs))
    new = call_signature_of(_fn(params=_long_param(), contract_attributes=new_attrs))
    axis = assess_call_interface(old, new, SYSV_AMD64)
    assert axis.status is AxisStatus.UNKNOWN, axis


def test_explicit_sysv_abi_is_the_default_convention() -> None:
    old = call_signature_of(
        _fn(params=_long_param(), contract_attributes=["sysv_abi", "nonnull"])
    )
    new = call_signature_of(_fn(params=_long_param()))
    assert assess_call_interface(old, new, SYSV_AMD64).status is AxisStatus.PRESERVED


def test_a_possible_member_function_is_unknown() -> None:
    """static long C::f(long) -> long C::f(long) adds an implicit this. The
    clang and castxml extractors store the leaf name (`f`); the symbol is
    what marks a member."""
    old = call_signature_of(
        _fn(name="f", mangled="_ZN1C1fEl", params=_long_param(), is_static=True)
    )
    new = call_signature_of(_fn(name="f", mangled="_ZN1C1fEl", params=_long_param()))
    axis = assess_call_interface(old, new, SYSV_AMD64)
    assert axis.status is AxisStatus.UNKNOWN and "implicit this" in axis.reason
    qualified = call_signature_of(_fn(name="C::f", params=_long_param()))
    assert (
        assess_call_interface(qualified, qualified, SYSV_AMD64).status
        is AxisStatus.UNKNOWN
    )
    c = call_signature_of(
        _fn(name="ns::f", mangled="_ZN2ns1fEl", params=_long_param(), is_extern_c=True)
    )
    assert assess_call_interface(c, c, SYSV_AMD64).status is AxisStatus.PRESERVED


# ------------------------------------- extractor-produced declarations

_MEMBER_HEADER = """struct C { long f(long a); static long g(long a); };
extern "C" long h(long a);
"""
_MEMBER_SOURCE = """#include "a.h"
long C::f(long a) { return a; }
long C::g(long a) { return a; }
long h(long a) { return a; }
"""


@pytest.mark.integration
@pytest.mark.parametrize("frontend", ["castxml", "clang"])
def test_extractor_members_are_unknown_and_c_linkage_is_modelled(
    tmp_path: Path, frontend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extractors store a member's leaf name (`f`), and castxml adds
    implicit special members with synthetic symbols; none of them may be
    judged, since their implicit `this` is not in `params`."""
    import shutil
    import subprocess

    if shutil.which("g++") is None or shutil.which(frontend) is None:
        pytest.skip(f"g++ and {frontend} required")
    if shutil.which("abicheck") is None:
        pytest.skip("abicheck CLI required")
    from abicheck.serialization import load_snapshot

    (tmp_path / "a.h").write_text(_MEMBER_HEADER)
    (tmp_path / "a.cpp").write_text(_MEMBER_SOURCE)
    lib, snap = tmp_path / "liba.so", tmp_path / "snap.json"
    subprocess.run(
        ["g++", "-g", "-shared", "-fPIC", str(tmp_path / "a.cpp"), "-o", str(lib)],
        check=True,
    )
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", frontend)
    subprocess.run(
        ["abicheck", "dump", str(lib), "-H", str(tmp_path / "a.h"), "-o", str(snap)],
        check=True,
        capture_output=True,
    )
    status = {}
    for fn in load_snapshot(snap).declarations.functions:
        sig = call_signature_of(fn)
        status.setdefault(fn.name, set()).add(
            assess_call_interface(sig, sig, SYSV_AMD64).status
        )
    assert status["h"] == {AxisStatus.PRESERVED}
    assert status["f"] == status["g"] == {AxisStatus.UNKNOWN}
    assert all(v == {AxisStatus.UNKNOWN} for k, v in status.items() if k != "h"), status
