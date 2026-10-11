"""Return-pointee qualifier classification agrees with g++ (2026-10-11 re-audit).

The previous unit oracle assumed that losing a qualifier behind a returned
pointer always leaves the result implicitly convertible to the old type, so
``const char **f()`` -> ``char **f()`` was a risk while
``const char **p = f();`` stops compiling. Here the compiler is the oracle:
for every (old, new) return type in the matrix, ``T_old y = x;`` with ``x``
of type ``T_new`` is compiled, and

* compiles  -> the change may only be ``func_return_pointee_qualifier_removed``
  (risk: only function-pointer consumers of the old type break);
* rejected  -> it must be an API break (``_added`` or ``_changed``).

The matrix covers 1/2/3 pointer levels, const and volatile at each pointee
level, and references to pointers.
"""

from __future__ import annotations

import itertools
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.checker_policy import API_BREAK_KINDS, RISK_KINDS
from abicheck.model import AbiSnapshot, Function, Visibility

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++"),
]

_KINDS = {
    ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_ADDED,
    ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_REMOVED,
    ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_CHANGED,
}
_CV4 = [(), ("const",), ("volatile",), ("const", "volatile")]
_CV2 = [(), ("const",)]


def _spell(levels: tuple[tuple[str, ...], ...], ref: bool = False) -> str:
    """``levels[0] char * levels[1] * ...`` (innermost pointee first)."""
    out = [*levels[0], "char"]
    for cv in levels[1:]:
        out += ["*", *cv]
    out.append("&" if ref else "*")
    return " ".join(out)


def _pairs() -> list[tuple[str, str]]:
    shapes: list[str] = []
    shapes += [_spell((a,)) for a in _CV4]
    shapes += [_spell((a, b)) for a, b in itertools.product(_CV4, repeat=2)]
    shapes += [_spell(t) for t in itertools.product(_CV2, repeat=3)]
    shapes += [_spell((a, b), ref=True) for a, b in itertools.product(_CV4, repeat=2)]
    by_depth: dict[tuple[int, bool], list[str]] = {}
    for s in shapes:
        by_depth.setdefault((s.count("*"), s.endswith("&")), []).append(s)
    return [
        (o, n)
        for group in by_depth.values()
        for o, n in itertools.product(group, repeat=2)
        if o != n
    ]


def _no_error_limit() -> str:
    """g++'s flag lifting the error cap; Apple's ``g++`` is clang, which
    ignores ``-fmax-errors`` and stops after 20 errors."""
    version = subprocess.run(
        ["g++", "--version"], capture_output=True, text=True, check=False
    ).stdout
    return "-ferror-limit=0" if "clang" in version.lower() else "-fmax-errors=0"


_PAIRS = _pairs()


#: C consumers: no references, and the C rule (only the first pointee's
#: qualifiers may be added) is enforced as an error.
_C_PAIRS = [(o, n) for o, n in _PAIRS if "&" not in o]


def _rejected(pairs: list[tuple[str, str]], src: Path, cmd: list[str]) -> set[int]:
    """Indices of *pairs* whose ``T_old y = x;`` the compiler rejects."""
    src.write_text(
        "\n".join(
            f"void t{i}({new} x) {{ {old} y = x; (void)y; }}"
            for i, (old, new) in enumerate(pairs)
        )
        + "\n"
    )
    result = subprocess.run(
        [*cmd, str(src)], capture_output=True, text=True, timeout=300, check=False
    )
    rejected = {
        int(m.group(1)) - 1
        for m in re.finditer(rf"{re.escape(src.name)}:(\d+):\d+: error", result.stderr)
    }
    assert rejected, result.stderr  # the oracle must see some breaks
    assert len(rejected) < len(pairs)  # and some conversions
    return rejected


@pytest.fixture(scope="module")
def gxx_rejected(tmp_path_factory: pytest.TempPathFactory) -> set[int]:
    src = tmp_path_factory.mktemp("conv") / "probe.cpp"
    return _rejected(
        _PAIRS, src, ["g++", "-std=c++17", "-fsyntax-only", _no_error_limit()]
    )


@pytest.fixture(scope="module")
def gcc_rejected(tmp_path_factory: pytest.TempPathFactory) -> set[int]:
    src = tmp_path_factory.mktemp("conv") / "probe.c"
    limit = _no_error_limit()
    return _rejected(
        _C_PAIRS,
        src,
        ["gcc", "-x", "c", "-std=c11", "-fsyntax-only", "-Werror", limit],
    )


def _snap(ret: str, mangled: str) -> AbiSnapshot:
    fn = Function(
        name="get_name",
        mangled=mangled,
        return_type=ret,
        params=[],
        visibility=Visibility.PUBLIC,
    )
    return AbiSnapshot(library="lib.so", version="1", from_headers=True, functions=[fn])


def _disagreements(
    pairs: list[tuple[str, str]], rejected: set[int], mangled: str, oracle: str
) -> list[str]:
    wrong: list[str] = []
    for i, (old, new) in enumerate(pairs):
        changes = compare(_snap(old, mangled), _snap(new, mangled)).changes
        got = {c.kind for c in changes} & _KINDS
        if len(got) != 1:
            wrong.append(f"{old!r} -> {new!r}: {sorted(got)}")
            continue
        (kind,) = got
        breaks = i in rejected
        if breaks != (kind in API_BREAK_KINDS) or (
            not breaks and kind not in RISK_KINDS
        ):
            wrong.append(f"{old!r} -> {new!r}: {kind.value}, {oracle} rejects={breaks}")
    return wrong


def test_matrix_agrees_with_gxx(gxx_rejected: set[int]) -> None:
    """A C++-only function (Itanium-mangled): g++ decides."""
    wrong = _disagreements(_PAIRS, gxx_rejected, "_Z8get_namev", "g++")
    assert not wrong, "\n".join(wrong)


def test_c_callable_matrix_agrees_with_gcc(gcc_rejected: set[int]) -> None:
    """A C-linkage function also has C consumers: gcc in C mode decides
    (stricter than C++: ``const char * const *`` -> ``char * const *``)."""
    wrong = _disagreements(_C_PAIRS, gcc_rejected, "get_name", "gcc -x c")
    assert not wrong, "\n".join(wrong)
