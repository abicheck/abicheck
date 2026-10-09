"""``overload_ambiguity_introduced``: a new overload that makes a call ambiguous.

Regression for case111 (2026-10-08 re-audit: a real overload ambiguity read
COMPATIBLE). The invariant is checked over a domain of parameter-type pairs
against g++ itself as the oracle -- never the detector's own scalar rule:

* soundness: whenever the finding fires, a consumer calling with ``{}`` at
  the differing position compiles against the old overload set and is
  rejected against the new one;
* completeness on the scalar sub-domain: for two scalar parameter types the
  finding fires exactly when the compiler says the call became ambiguous.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.checker_policy import RISK_KINDS
from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    Param,
    RecordType,
    Visibility,
)

_KIND = ChangeKind.OVERLOAD_AMBIGUITY_INTRODUCED

#: (C++ spelling in the header, spelling a header backend records)
_TYPES = {
    "int": "int",
    "long": "long",
    "double": "double",
    "char *": "char *",
    "fnptr_t": "fnptr_t",  # typedef int (*fnptr_t)();
    "E": "E",  # enum E { A };
    "Widget": "Widget",  # struct Widget {};
    "const Widget &": "const Widget &",
    "const int &": "const int &",
}
_SCALARS = {"int", "long", "double", "char *", "fnptr_t", "E"}
_PRELUDE = "typedef int (*fnptr_t)();\nenum E { A };\nstruct Widget {};\n"


def _ctor(cls: str, ptype: str, tag: str) -> Function:
    return Function(
        name=cls,
        mangled=f"__abicheck_ctor__ns::{cls}({tag})",
        return_type="void",
        params=[Param(name="v", type=ptype)],
        visibility=Visibility.PUBLIC,
    )


def _snap(funcs: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib.so",
        version="1",
        from_headers=True,
        functions=funcs,
        typedefs={"fnptr_t": "int (*)()"},
        enums=[EnumType(name="E", members=[])],
        types=[RecordType(name="Widget", kind="struct")],
    )


def _fires(a: str, b: str) -> bool:
    old = _snap([_ctor("S", _TYPES[a], a)])
    new = _snap([_ctor("S", _TYPES[a], a), _ctor("S", _TYPES[b], b)])
    return _KIND in {c.kind for c in compare(old, new).changes}


def _compiles(tmp: Path, ctors: list[str]) -> bool:
    src = tmp / "probe.cpp"
    decls = "".join(f"  explicit S({t});\n" for t in ctors)
    src.write_text(f"{_PRELUDE}struct S {{\n{decls}}};\nvoid use() {{ S s({{}}); }}\n")
    r = subprocess.run(
        ["g++", "-std=c++17", "-fsyntax-only", str(src)], capture_output=True
    )
    return r.returncode == 0


_PAIRS = [(a, b) for a, b in itertools.permutations(_TYPES, 2)]


@pytest.mark.integration
@pytest.mark.parametrize(("a", "b"), _PAIRS)
def test_against_the_compiler(a: str, b: str, tmp_path: Path) -> None:
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    became_ambiguous = _compiles(tmp_path, [a]) and not _compiles(tmp_path, [a, b])
    fires = _fires(a, b)
    if fires:
        assert became_ambiguous, (a, b)
    if a in _SCALARS and b in _SCALARS:
        assert fires == became_ambiguous, (a, b)


def test_verdict_is_risk() -> None:
    assert _KIND in RISK_KINDS


@pytest.mark.parametrize(
    ("old_params", "new_params", "fires"),
    [
        (["int", "int"], ["int", "char *"], True),  # one differing scalar position
        (["int", "Widget"], ["char *", "Widget"], True),  # equal class position
        (["int", "int"], ["char *", "Widget"], False),  # class position differs
        (["int"], ["int", "int"], False),  # arity differs
        (["int &"], ["long &"], False),  # references rank differently
    ],
)
def test_free_function_witness(
    old_params: list[str], new_params: list[str], fires: bool
) -> None:
    def fn(params: list[str], tag: str) -> Function:
        return Function(
            name="ns::f",
            mangled=f"_ZN2ns1fE{tag}",
            return_type="void",
            params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
            visibility=Visibility.PUBLIC,
        )

    old = _snap([fn(old_params, "a")])
    new = _snap([fn(old_params, "a"), fn(new_params, "b")])
    assert (_KIND in {c.kind for c in compare(old, new).changes}) is fires


def test_already_ambiguous_set_is_not_reported() -> None:
    """``{}`` was ambiguous before (int vs long), so adding double changes nothing."""
    base = [_ctor("S", "int", "int"), _ctor("S", "long", "long")]
    new = _snap([*base, _ctor("S", "double", "double")])
    assert _KIND not in {c.kind for c in compare(_snap(base), new).changes}


def test_header_less_side_declines() -> None:
    old = _snap([_ctor("S", "int", "int")])
    old.from_headers = False
    new = _snap([_ctor("S", "int", "int"), _ctor("S", "char *", "p")])
    assert _KIND not in {c.kind for c in compare(old, new).changes}
