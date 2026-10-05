# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``model/type_indirection.py``: a partially unresolved spelling (``"?*"``)
still proves an indirection change, never a fabricated one.

The oracle is a brute-force *model*, not the module's prefix rule: every
spelling is built from a declarator level sequence over a core, and an open
core (``?``, a typedef/class name) is expanded into every possible inner
level sequence up to a bound. Two spellings provably differ iff no pair of
expansions yields the same full level sequence. The sweep enumerates every
shape up to three levels, in both directions, then the same contract is
checked end to end through the variable, function-return, function-parameter
and pointer-level detectors.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

from abicheck.compare.function_signature import (
    function_signature_changes,
    function_signature_index,
)
from abicheck.compare.parameter_facts import pointer_level_changes
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.type_indirection import (
    indirection_provably_differs,
    indirection_shape,
    pointer_depth_provably_differs,
)

_LEVELS = ("*", "&", "[]")
#: (core text, open?) -- ``int`` is exact; ``?`` and a typedef name may hide
#: further levels.
_CORES = (("int", False), ("?", True), ("T", True), ("const ?", True))
_MAX = 3


def _render(core: str, levels: tuple[str, ...]) -> str:
    """Spell *levels* (outermost first) around *core* the C++ way: arrays
    trail left-to-right as the outermost levels, pointers/references read
    right-to-left."""
    arrays = 0
    while arrays < len(levels) and levels[arrays] == "[]":
        arrays += 1
    inner = levels[arrays:]
    if "[]" in inner:
        return ""  # needs parentheses, not a spelling this module parses
    text = core + "".join(reversed(inner))
    return text + "[4]" * arrays


def _well_formed(levels: tuple[str, ...]) -> bool:
    """A reference is only ever the outermost level (no pointer to,
    array of, or reference to a reference)."""
    return all(lv not in ("&", "&&") for lv in levels[1:])


def _shapes():
    for n in range(_MAX + 1):
        for levels in itertools.product(_LEVELS + ("&&",), repeat=n):
            if not _well_formed(levels):
                continue
            for core, is_open in _CORES:
                text = _render(core, levels)
                if text:
                    yield text, levels, is_open


_ALL = list(_shapes())


def _expansions(levels: tuple[str, ...], is_open: bool) -> set[tuple[str, ...]]:
    if not is_open:
        return {levels}
    return {
        levels + extra
        for n in range(_MAX + 2)
        for extra in itertools.product(_LEVELS + ("&&",), repeat=n)
        if _well_formed(levels + extra)
    }


def _oracle(a, b) -> bool:
    return not (_expansions(a[1], a[2]) & _expansions(b[1], b[2]))


def test_shape_parses_every_rendered_spelling() -> None:
    for text, levels, is_open in _ALL:
        shape = indirection_shape(text)
        assert shape is not None, text
        assert (shape.levels, shape.closed) == (levels, not is_open), text


def test_provably_differs_matches_the_brute_force_model() -> None:
    bad = [
        (a[0], b[0])
        for a, b in itertools.product(_ALL, _ALL)
        if indirection_provably_differs(a[0], b[0]) != _oracle(a, b)
    ]
    assert not bad, bad[:20]


def test_provably_differs_is_symmetric_and_irreflexive() -> None:
    for (a, *_), (b, *_) in itertools.product(_ALL, _ALL):
        assert indirection_provably_differs(a, b) == indirection_provably_differs(b, a)
    for a, *_ in _ALL:
        assert not indirection_provably_differs(a, a)


def test_oracle_is_not_vacuous() -> None:
    by = {t: (t, lv, op) for t, lv, op in _ALL}
    assert _oracle(by["int"], by["?*"])
    assert not _oracle(by["int**"], by["?*"])
    assert not _oracle(by["?*"], by["?*"])
    assert _oracle(by["?&"], by["?*"])


@pytest.mark.parametrize(
    "spelling",
    ["int (*)(int)", "_Atomic(?)", "", "S<decltype(a ? b : c)>*x?"],
)
def test_unparseable_spelling_never_differs(spelling) -> None:
    assert not indirection_provably_differs(spelling, "int")
    assert not indirection_provably_differs("?*", spelling)


def test_cv_never_changes_the_shape() -> None:
    for a, b in [
        ("int * const", "int *"),
        ("const ? *", "?*"),
        ("volatile int", "int"),
    ]:
        assert indirection_shape(a) == indirection_shape(b)


def test_parameter_array_decays_to_pointer() -> None:
    assert not indirection_provably_differs("int[4]", "?*", decay_arrays=True)
    assert indirection_provably_differs("int[4]", "?*")


def _star_bounds(levels, is_open):
    n = levels.count("*")
    return (n, None) if is_open else (n, n)


def test_pointer_depth_matches_the_brute_force_model() -> None:
    """Depth (count of ``*`` levels) provably differs iff no expansion pair
    agrees on it."""
    bad = []
    for a, b in itertools.product(_ALL, _ALL):
        da = {e.count("*") for e in _expansions(a[1], a[2])}
        db = {e.count("*") for e in _expansions(b[1], b[2])}
        if pointer_depth_provably_differs(a[0], b[0]) != (not da & db):
            bad.append((a[0], b[0]))
    assert not bad, bad[:20]


# -- end to end through the function detectors --------------------------------

sys.path.insert(0, str(Path(__file__).parent))
from test_function_signature_cutover import _fn, _snap  # noqa: E402

#: A function-facing subset (castxml renders these; arrays decay in params).
_FN_SPELLINGS = ["int", "int *", "int **", "int &", "?", "?*", "?**", "?&", "const ?"]
_EXACT = {"int": (), "int *": ("*",), "int **": ("*", "*"), "int &": ("&",)}
_OPEN = {"?": (), "?*": ("*",), "?**": ("*", "*"), "?&": ("&",), "const ?": ()}


def _fn_oracle(a: str, b: str) -> bool:
    ea = {_EXACT[a]} if a in _EXACT else _expansions(_OPEN[a], True)
    eb = {_EXACT[b]} if b in _EXACT else _expansions(_OPEN[b], True)
    return not ea & eb


def _signature_kinds(ret: tuple[str, str], param: tuple[str, str]) -> set[ChangeKind]:
    o, n = _fn(ret[0], [param[0]], "", None), _fn(ret[1], [param[1]], "", None)
    old, new = _snap(o, with_ir=True), _snap(n, with_ir=True)
    oi = function_signature_index(old.canonical_ir, [o])
    ni = function_signature_index(new.canonical_ir, [n])
    return {
        c.kind
        for c in function_signature_changes(
            o.mangled, o.name, oi.entity_for(o), ni.entity_for(n), entity_id=None
        )
    }


def test_function_return_and_params_follow_the_model() -> None:
    bad = []
    for a, b in itertools.product(_FN_SPELLINGS, _FN_SPELLINGS):
        if "?" not in a + b:
            continue  # fully resolved pairs are the existing detector's domain
        got = _signature_kinds((a, b), (a, b))
        want = _fn_oracle(a, b)
        if (ChangeKind.FUNC_RETURN_CHANGED in got) != want or (
            ChangeKind.FUNC_PARAMS_CHANGED in got
        ) != want:
            bad.append((a, b, got))
    assert not bad, bad


def _bounded_depths(spelling: str) -> set[int]:
    if spelling in _EXACT:
        return {_EXACT[spelling].count("*")}
    return {e.count("*") for e in _expansions(_OPEN[spelling], True)}


def _depth_oracle(a: str, b: str) -> bool:
    if "?" in (a.strip(), b.strip()):
        return False  # wholly unknown: never compared (RD2-5)
    return not _bounded_depths(a) & _bounded_depths(b)


def test_param_pointer_level_follows_the_model() -> None:
    from test_parameter_facts_cutover import _fn as _pfn, _view

    bad = []
    for a, b in itertools.product(_FN_SPELLINGS, _FN_SPELLINGS):
        o = _view(_pfn([("p", a, None, None, None)], None), True)
        n = _view(_pfn([("p", b, None, None, None)], None), True)
        got = pointer_level_changes(
            "f", "f", o, n, entity_id=None, params_unconfirmed=False
        )
        if bool(got) != _depth_oracle(a, b):
            bad.append((a, b, [c.kind for c in got]))
    assert not bad, bad
