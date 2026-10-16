"""``overload_ambiguity_introduced``: a new overload that makes a call ambiguous.

Regression for case111 (2026-10-08 re-audit: a real overload ambiguity read
COMPATIBLE). The invariant is checked over a domain of parameter-type pairs
against g++ itself as the oracle -- never the detector's own scalar rule:

* soundness: whenever the finding fires, a consumer calling with ``{}`` at
  the differing position compiles against the old overload set and is
  rejected against the new one;
* completeness on the scalar sub-domain: for two scalar parameter types the
  finding fires exactly when the compiler says the call became ambiguous.

The claim is about the whole overload set (2026-10-11 re-audit: with
``f(int)`` and ``f(std::initializer_list<int>)`` present, adding
``f(double)`` leaves ``f({})`` calling the ``initializer_list`` overload), so
soundness and scalar completeness are also checked over every
(old pair, added overload) triple of the domain, in one batched g++ run.
"""

from __future__ import annotations

import itertools
import re
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
    "long &&": "long &&",
    "std::initializer_list<int>": "std::initializer_list<int>",
    "const std::initializer_list<int> &": "const std::initializer_list<int> &",
}
_SCALARS = {"int", "long", "double", "char *", "fnptr_t", "E"}
_PRELUDE = (
    "#include <initializer_list>\n"
    "typedef int (*fnptr_t)();\nenum E { A };\nstruct Widget {};\n"
)


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


def _fires_set(old_types: list[str], added: str) -> bool:
    base = [_ctor("S", _TYPES[t], t) for t in old_types]
    new = _snap([*base, _ctor("S", _TYPES[added], added)])
    return _KIND in {c.kind for c in compare(_snap(base), new).changes}


def _no_error_limit() -> str:
    """g++'s flag lifting the error cap; Apple's ``g++`` is clang, which
    ignores ``-fmax-errors`` and stops after 20 errors."""
    version = subprocess.run(
        ["g++", "--version"], capture_output=True, text=True, check=False
    ).stdout
    return "-ferror-limit=0" if "clang" in version.lower() else "-fmax-errors=0"


_TRIPLES = list(itertools.permutations(_TYPES, 3))


@pytest.mark.integration
def test_whole_overload_set_against_the_compiler(tmp_path: Path) -> None:
    """For old set {a, c} plus added b: the finding fires only when ``S s({})``
    compiled before and is rejected after; on all-scalar sets, exactly then."""
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    lines = _PRELUDE.rstrip("\n").split("\n")
    first = len(lines) + 1  # 1-based line of the first probe
    for i, (a, c, b) in enumerate(_TRIPLES):
        for tag, types in (("O", (a, c)), ("N", (a, c, b))):
            decls = " ".join(f"explicit {tag}{i}({t});" for t in types)
            lines.append(
                f"struct {tag}{i} {{ {decls} }}; void u{tag}{i}() {{ {tag}{i} s({{}}); }}"
            )
    src = tmp_path / "probe.cpp"
    src.write_text("\n".join(lines) + "\n")
    r = subprocess.run(
        ["g++", "-std=c++17", "-fsyntax-only", _no_error_limit(), str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    bad = {
        int(m.group(1)) for m in re.finditer(r"probe\.cpp:(\d+):\d+: error", r.stderr)
    }
    wrong: list[str] = []
    fired = 0
    for i, (a, c, b) in enumerate(_TRIPLES):
        old_ok, new_ok = first + 2 * i not in bad, first + 2 * i + 1 not in bad
        became_ambiguous = old_ok and not new_ok
        fires = _fires_set([a, c], b)
        fired += fires
        if fires and not became_ambiguous:
            wrong.append(f"unsound: {{{a}, {c}}} + {b}")
        if {a, b, c} <= _SCALARS and fires != became_ambiguous:
            wrong.append(f"incomplete: {{{a}, {c}}} + {b}: compiler={became_ambiguous}")
    assert fired, "the domain must exercise the finding"
    assert not wrong, "\n".join(wrong)


def test_initializer_list_overload_wins_the_braced_call() -> None:
    """The re-audit's set: ``f({})`` keeps selecting the initializer_list
    overload, so adding ``f(double)`` makes nothing ambiguous."""
    for il in ("std::initializer_list<int>", "const std::initializer_list<int> &"):
        assert not _fires_set(["int", il], "double")
        assert not _fires_set([il, "int"], "double")
    assert _fires_set(["int", "Widget"], "double")  # a class type does not win


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


def _free(
    params: list[tuple[str, str | None]], tag: str, *, variadic: bool = False
) -> Function:
    return Function(
        name="ns::g",
        mangled=f"_ZN2ns1gE{tag}",
        return_type="void",
        params=[
            Param(name=f"p{i}", type=t, default=d) for i, (t, d) in enumerate(params)
        ],
        visibility=Visibility.PUBLIC,
        is_variadic=variadic,
    )


@pytest.mark.parametrize(
    ("rival", "fires"),
    [
        # Rival with a defaulted extra parameter is viable and ties: no claim.
        (_free([("long", None), ("int", "0")], "r1"), False),
        # Extra parameter without a default: not viable for the call.
        (_free([("long", None), ("int", None)], "r2"), True),
        # Variadic rival: ranking unknown, no claim.
        (_free([("long", None)], "r3", variadic=True), False),
        # Rvalue reference to a scalar binds {} by identity: ties.
        (_free([("long &&", None)], "r4"), False),
        # Reference to a class type: a user-defined conversion, worse.
        (_free([("const Widget &", None)], "r5"), True),
    ],
)
def test_old_rival_decides_whether_the_call_was_valid(
    rival: Function, fires: bool
) -> None:
    """The old overload must have been the unique best candidate for the
    witness call; a rival that could tie withdraws the claim."""
    base = [_free([("int", None)], "a"), rival]
    new = _snap([*base, _free([("double", None)], "b")])
    assert (_KIND in {c.kind for c in compare(_snap(base), new).changes}) is fires


def test_identity_position_rival_with_another_type_is_worse() -> None:
    base = [
        _free([("int", None), ("int", None)], "a"),
        _free([("long", None), ("long", None)], "r"),
    ]
    new = _snap([*base, _free([("double", None), ("int", None)], "b")])
    assert _KIND in {c.kind for c in compare(_snap(base), new).changes}
