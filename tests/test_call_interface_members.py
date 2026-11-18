"""ADR-094 D8: the ``call`` axis for C++ member functions (E15).

A non-static member's implicit ``this`` is a prepended pointer argument
(``formal/MEMBER_FRAGMENT.md``). The oracles are independent of the
production code: Lean's ``callCompatible`` for every E13/E14 case re-generated
as a member (confirmed by execution on g++ and clang++), and the committed
execution observations of 170 static/non-static transitions
(``formal/member_fragment_observed.json``). Whether a function is a member is
``Function.member_kind_fact``, which the header backends read from the AST
node kind; the integration tests dump real classes with castxml and clang.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from abicheck.compare.call_interface import (
    AxisStatus,
    CallSignature,
    ImplicitThis,
    assess_call_interface,
    call_signature_of,
)
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.call_interface import SYSV_AMD64, Rep, RetShape, arg_locations
from abicheck.model.declarations import Function, Param
from abicheck.model.fact import Fact
from abicheck.model.member_kind import MemberKind

FORMAL = Path(__file__).resolve().parent.parent / "formal"
OBSERVED = FORMAL / "member_fragment_observed.json"


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        name, FORMAL / "experiments" / f"{name}.py"
    )
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


def _member(
    params: list[tuple[str, str]],
    ret: str = "int",
    kind: MemberKind | None = MemberKind.INSTANCE,
    **extra,  # type: ignore[no-untyped-def]
) -> Function:
    kw: dict = {}
    if ret == "void *":
        kw["return_pointer_depth"] = 1
    elif ret in _FACT:
        kw["return_type_abi_fact"] = Fact.present(_FACT[ret])
    if kind is not None:
        kw["member_kind_fact"] = Fact.present(kind)
    kw = {"mangled": "_ZN1K1fEv", **kw, **extra}
    return Function(
        name="f",
        return_type=ret,
        params=[_param(n, t) for n, t in params],
        is_variadic=False,
        contract_attributes=[],
        **kw,
    )


def _sig(fn: Function, ret: str = "int") -> CallSignature:
    return call_signature_of(fn, _STRUCT.get(ret), linkage_reliable=True)


def _axis(old: Function, new: Function, ret_old: str = "int", ret_new: str = "int"):  # type: ignore[no-untyped-def]
    return assess_call_interface(_sig(old, ret_old), _sig(new, ret_new), SYSV_AMD64)


# ----------------------------------------- member <-> member: Lean's answer


def test_e13_cases_as_members_keep_the_proved_answer() -> None:
    rows = FUNC.load_export()["rows"]
    assert len(rows) == 2160
    for r in rows:
        old = _member(FUNC._parse(r["old_body"]))
        new = _member(FUNC._parse(r["new_body"]))
        axis = _axis(old, new)
        assert axis.status is not AxisStatus.UNKNOWN, (r["id"], axis)
        assert (axis.status is AxisStatus.PRESERVED) == r["call"], (r["id"], axis)


def test_e14_cases_as_members_keep_the_proved_answer() -> None:
    rows = RET.load_export()["rows"]
    assert len(rows) == 1176
    for r in rows:
        params = [(f"p{j}", t) for j, t in enumerate(r["params"])]
        old, new = _member(params, r["old"]), _member(params, r["new"])
        axis = _axis(old, new, r["old"], r["new"])
        assert axis.status is not AxisStatus.UNKNOWN, (r["id"], axis)
        assert (axis.status is AxisStatus.PRESERVED) == r["call"], (r["id"], axis)


@pytest.mark.parametrize("sret", [False, True])
@pytest.mark.parametrize(
    "reps",
    [[], [Rep.F64], [Rep.I32, Rep.F64, Rep.I64], [Rep.F64, Rep.I64, Rep.F64]],
)
def test_this_takes_the_first_integer_register_after_sret(
    reps: list[Rep], sret: bool
) -> None:
    """The ABI fact itself: `this` is rdi, or rsi behind an sret pointer
    (g++/clang++ assembly, formal/MEMBER_FRAGMENT.md), and every declared
    INTEGER argument moves up one register while SSE ones stay."""
    sig = _sig(
        _member(
            [
                (f"p{j}", {"i32": "int", "i64": "long", "f64": "double"}[r])
                for j, r in enumerate(reps)
            ],
            "struct s3l" if sret else "int",
        ),
        "struct s3l" if sret else "int",
    )
    assert sig.implicit_this is ImplicitThis.PRESENT
    assert sig.params is not None and sig.params[0].name == "this"
    locs = arg_locations([s.rep for s in sig.params], sret)
    assert locs[0] == ("integer", 1 if sret else 0)
    free = arg_locations(reps, sret)
    for (cls, i), (fcls, fi) in zip(locs[1:], free, strict=True):
        assert cls == fcls and i == fi + (cls == "integer")


# ------------------------------- static <-> instance: execution observations


def _observed() -> dict:
    data = json.loads(OBSERVED.read_text(encoding="utf-8"))
    rows = data["rows"]
    digest = hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode())
    assert digest.hexdigest() == data["rows_sha256"]
    return data


def test_the_committed_observations_are_intact_and_complete() -> None:
    data = _observed()
    assert sorted(data["compilers"]) == ["clang++", "g++"]
    assert len(data["rows"]) == 170
    assert sum(r[3] for r in data["rows"]) == 4


_KIND = {"static": MemberKind.STATIC, "instance": MemberKind.INSTANCE}


def test_static_instance_transitions_match_execution() -> None:
    for old_kind, new_kind, base, call in _observed()["rows"]:
        params = [(f"p{j}", t) for j, t in enumerate(base)]
        axis = _axis(
            _member(params, kind=_KIND[old_kind]),
            _member(params, kind=_KIND[new_kind]),
        )
        assert axis.status is not AxisStatus.UNKNOWN, (old_kind, new_kind, base)
        assert (axis.status is AxisStatus.PRESERVED) == call, (
            old_kind,
            new_kind,
            base,
            axis,
        )


# ------------------------------------------------------ evidence and domain


@pytest.mark.parametrize(
    ("kind", "this"),
    [
        (MemberKind.FREE, ImplicitThis.ABSENT),
        (MemberKind.STATIC, ImplicitThis.ABSENT),
        (MemberKind.INSTANCE, ImplicitThis.PRESENT),
        (MemberKind.CONSTRUCTOR, ImplicitThis.UNKNOWN),
        (MemberKind.DESTRUCTOR, ImplicitThis.UNKNOWN),
        (None, ImplicitThis.UNKNOWN),
    ],
)
def test_member_kind_decides_the_implicit_this(
    kind: MemberKind | None, this: ImplicitThis
) -> None:
    sig = _sig(_member([("a", "long")], kind=kind))
    assert sig.implicit_this is this
    status = assess_call_interface(sig, sig, SYSV_AMD64).status
    assert status is (
        AxisStatus.UNKNOWN if this is ImplicitThis.UNKNOWN else AxisStatus.PRESERVED
    )
    if this is ImplicitThis.UNKNOWN:
        assert sig.unknown_reason


def test_a_static_free_function_is_not_a_static_member() -> None:
    """`is_static` on a free function is internal linkage: no `this` either
    way, and it is the member kind, not `is_static`, that decides."""
    fn = _member([("a", "long")], kind=MemberKind.FREE, is_static=True)
    assert _sig(fn).implicit_this is ImplicitThis.ABSENT
    inst = _member([("a", "long")], is_static=True)  # a stale flag cannot remove `this`
    assert _sig(inst).implicit_this is ImplicitThis.PRESENT


@pytest.mark.parametrize(
    "extra",
    [
        {"is_virtual": True},
        {"is_const": True},
        {"ref_qualifier": "&&"},
        {"is_volatile": True},
    ],
)
def test_virtual_and_qualified_members_pass_this_alike(extra: dict) -> None:
    """A vtable call passes the same registers; cv- and ref-qualifiers change
    the symbol (the load axis), not the argument registers."""
    old = _member([("a", "long"), ("b", "double")], **extra)
    new = _member([("a", "long")], **extra)
    assert _axis(old, new).status is AxisStatus.PRESERVED
    ins = _member([("x", "long"), ("a", "long")], **extra)
    assert _axis(old, ins).status is AxisStatus.BROKEN


def test_not_collected_member_kind_is_unknown() -> None:
    fn = _member([("a", "long")], kind=None, member_kind_fact=Fact.not_collected())
    sig = _sig(fn)
    assert sig.implicit_this is ImplicitThis.UNKNOWN
    assert assess_call_interface(sig, sig, SYSV_AMD64).status is AxisStatus.UNKNOWN


def test_this_counts_against_the_register_budget() -> None:
    """Six declared INTEGER parameters fit a free function, not a member."""
    six = [(f"p{j}", "long") for j in range(6)]
    free = _sig(_member(six, kind=MemberKind.STATIC))
    assert assess_call_interface(free, free, SYSV_AMD64).status is AxisStatus.PRESERVED
    inst = _sig(_member(six))
    assert assess_call_interface(inst, inst, SYSV_AMD64).status is AxisStatus.UNKNOWN


def test_member_kind_survives_save_and_load(tmp_path: Path) -> None:
    from abicheck.model.snapshot import AbiSnapshot
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    fns = [
        _member([("a", "long")], kind=k, mangled=f"_ZN1K{i}fEl")
        for i, k in enumerate([*MemberKind, None])
    ]
    path = tmp_path / "snap.json"
    save_snapshot(
        AbiSnapshot(library="l", version="1", ast_producer="clang", functions=fns),
        path,
    )
    loaded = {f.mangled: f for f in load_snapshot(path).declarations.functions}
    for fn in fns:
        got = loaded[fn.mangled].member_kind_fact
        if fn.member_kind_fact is None:
            assert got is None or got.value is None
            assert _sig(loaded[fn.mangled]).implicit_this is ImplicitThis.UNKNOWN
        else:
            assert got is not None and got.value is fn.member_kind_fact.value


# --------------------------------------------- real castxml and clang dumps

_HEADER_V1 = """struct S3 { long a, b, c; };
class C {
public:
  C(long a);
  ~C();
  long f(long a, long b);
  static long g(long a);
  virtual long v(long a, double d);
  long q(long a) const;
  S3 s(long a);
  long t(long a);
  friend long fr(C &c, long a) { return a; }
};
long free_fn(long a);
static inline long internal_fn(long a) { return a; }
template <class T> struct A { long u(long a); };
extern "C" {
struct E { long k(long a); };
long h(long a);
}
"""

# f drops b (preserved), g becomes non-static (this inserted: broken),
# v drops d (preserved), q gains a leading x (broken), s keeps its shape,
# t becomes static (this removed, a shifts: broken).
_HEADER_V2 = (
    _HEADER_V1.replace("long f(long a, long b);", "long f(long a);")
    .replace("static long g(long a);", "long g(long a);")
    .replace("virtual long v(long a, double d);", "virtual long v(long a);")
    .replace("long q(long a) const;", "long q(long x, long a) const;")
    .replace("long t(long a);", "static long t(long a);")
)

_SOURCE = """#include "a.h"
C::C(long) {}
C::~C() {}
long C::f(long a, long b) { return a + b; }
long C::g(long a) { return a; }
long C::v(long a, double) { return a; }
long C::q(long a) const { return a; }
S3 C::s(long a) { S3 r = {a, a, a}; return r; }
long C::t(long a) { return a; }
long free_fn(long a) { return a; }
long E::k(long a) { return a; }
long h(long a) { return a; }
"""


_SOURCE_V2 = (
    _SOURCE.replace(
        "long C::f(long a, long b) { return a + b; }", "long C::f(long a) { return a; }"
    )
    .replace(
        "long C::v(long a, double) { return a; }", "long C::v(long a) { return a; }"
    )
    .replace(
        "long C::q(long a) const { return a; }",
        "long C::q(long, long a) const { return a; }",
    )
)


def _dump(tmp_path: Path, header: str, frontend: str, monkeypatch) -> list[Function]:  # type: ignore[no-untyped-def]
    import subprocess

    from abicheck.serialization import load_snapshot
    from tests._abicheck_subprocess import abicheck_command

    d = tmp_path
    (d / "a.h").write_text(header)
    (d / "a.cpp").write_text(_SOURCE if header == _HEADER_V1 else _SOURCE_V2)
    lib, snap = d / "liba.so", d / "snap.json"
    subprocess.run(
        ["g++", "-g", "-shared", "-fPIC", str(d / "a.cpp"), "-o", str(lib)],
        check=True,
    )
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", frontend)
    subprocess.run(
        abicheck_command("dump", lib, "-H", d / "a.h", "-o", snap),
        check=True,
        capture_output=True,
    )
    return load_snapshot(snap).declarations.functions


def _require(frontend: str) -> None:
    import shutil

    if shutil.which("g++") is None or shutil.which(frontend) is None:
        pytest.skip(f"g++ and {frontend} required")


def _leaf(fn: Function) -> str:
    return fn.name.rsplit("::", 1)[-1]


@pytest.mark.integration
@pytest.mark.parametrize("frontend", ["castxml", "clang"])
def test_extractors_record_the_member_kind(
    tmp_path: Path, frontend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require(frontend)
    kinds: dict[str, set[MemberKind | None]] = {}
    for fn in _dump(tmp_path, _HEADER_V1, frontend, monkeypatch):
        fact = fn.member_kind_fact
        kinds.setdefault(_leaf(fn), set()).add(None if fact is None else fact.value)
    want = {
        "f": MemberKind.INSTANCE,
        "v": MemberKind.INSTANCE,
        "q": MemberKind.INSTANCE,
        "s": MemberKind.INSTANCE,
        "t": MemberKind.INSTANCE,
        "k": MemberKind.INSTANCE,  # a member inside extern "C"
        "g": MemberKind.STATIC,
        "fr": MemberKind.FREE,  # a friend defined in the class
        "free_fn": MemberKind.FREE,
        "internal_fn": MemberKind.FREE,  # is_static: internal linkage
        "h": MemberKind.FREE,
        "C": MemberKind.CONSTRUCTOR,
        "~C": MemberKind.DESTRUCTOR,
    }
    for name, kind in want.items():
        assert kinds[name] == {kind}, (name, kinds)
    if frontend == "clang":
        # clang records the uninstantiated template's method (castxml does not).
        assert kinds["u"] == {MemberKind.INSTANCE}


@pytest.mark.integration
@pytest.mark.parametrize("frontend", ["castxml", "clang"])
def test_member_edits_on_real_dumps(
    tmp_path: Path, frontend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require(frontend)
    (tmp_path / "v1").mkdir()
    (tmp_path / "v2").mkdir()
    old = _dump(tmp_path / "v1", _HEADER_V1, frontend, monkeypatch)
    new = _dump(tmp_path / "v2", _HEADER_V2, frontend, monkeypatch)

    def by_leaf(fns: list[Function]) -> dict[str, Function]:
        out: dict[str, Function] = {}
        for fn in fns:
            out.setdefault(_leaf(fn), fn)
        return out

    o, n = by_leaf(old), by_leaf(new)
    want = {
        "f": AxisStatus.PRESERVED,
        "g": AxisStatus.BROKEN,
        "v": AxisStatus.PRESERVED,
        "q": AxisStatus.BROKEN,
        "t": AxisStatus.BROKEN,
        "k": AxisStatus.PRESERVED,
        "h": AxisStatus.PRESERVED,
        "free_fn": AxisStatus.PRESERVED,
        "fr": AxisStatus.UNKNOWN,  # `C &` is a reference: not a modelled slot
        "C": AxisStatus.UNKNOWN,
        "~C": AxisStatus.UNKNOWN,
    }
    got = {
        name: assess_call_interface(
            call_signature_of(o[name], linkage_reliable=True),
            call_signature_of(n[name], linkage_reliable=True),
            SYSV_AMD64,
        ).status
        for name in want
    }
    assert got == want
