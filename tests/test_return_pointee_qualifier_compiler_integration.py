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


_PAIRS = _pairs()


@pytest.fixture(scope="module")
def gxx_rejected(tmp_path_factory: pytest.TempPathFactory) -> set[int]:
    """Indices of :data:`_PAIRS` whose ``T_old y = x;`` g++ rejects."""
    src = tmp_path_factory.mktemp("conv") / "probe.cpp"
    lines = [
        f"void t{i}({new} x) {{ {old} y = x; (void)y; }}"
        for i, (old, new) in enumerate(_PAIRS)
    ]
    src.write_text("\n".join(lines) + "\n")
    result = subprocess.run(
        ["g++", "-std=c++17", "-fsyntax-only", "-fmax-errors=0", str(src)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    rejected = {
        int(m.group(1)) - 1
        for m in re.finditer(rf"{re.escape(src.name)}:(\d+):\d+: error", result.stderr)
    }
    assert rejected, result.stderr  # the oracle must see some breaks
    assert len(rejected) < len(_PAIRS)  # and some conversions
    return rejected


def _snap(ret: str) -> AbiSnapshot:
    fn = Function(
        name="get_name",
        mangled="get_name",
        return_type=ret,
        params=[],
        visibility=Visibility.PUBLIC,
    )
    return AbiSnapshot(library="lib.so", version="1", from_headers=True, functions=[fn])


def test_matrix_agrees_with_gxx(gxx_rejected: set[int]) -> None:
    wrong: list[str] = []
    for i, (old, new) in enumerate(_PAIRS):
        got = {c.kind for c in compare(_snap(old), _snap(new)).changes} & _KINDS
        if len(got) != 1:
            wrong.append(f"{old!r} -> {new!r}: {sorted(got)}")
            continue
        (kind,) = got
        breaks = i in gxx_rejected
        if breaks != (kind in API_BREAK_KINDS) or (
            not breaks and kind not in RISK_KINDS
        ):
            wrong.append(f"{old!r} -> {new!r}: {kind.value}, g++ rejects={breaks}")
    assert not wrong, "\n".join(wrong)
