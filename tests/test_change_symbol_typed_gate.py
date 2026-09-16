# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The `test-change-symbol-typed` AI-readiness gate, stated as an invariant.

`checker_types.Change.symbol` is annotated `str` and every producer under
`abicheck/` honours it. `mypy` never sees `tests/`, though, so a fixture could
construct the state the annotation forbids and nothing would say so -- which
is how a `None` a test had fabricated came to be recorded in
`docs/contribute/known-gaps.md` as a *production* defect that does not exist.

The oracle here is deliberately not the gate's own rule restated. It is
CPython's own argument binding against the two real signatures
(`inspect.Signature.bind`): a call site is a violation exactly when Python
itself would bind `symbol` to the `None` literal. That answers "which
parameter does this argument land on" from the same source the runtime does,
so a gate that mis-indexes a positional, forgets that `make_change`'s
`symbol` is keyword-only, or lets a keyword lose to a positional disagrees
with it immediately.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import itertools
import sys
from pathlib import Path

import pytest

from abicheck.checker_types import Change
from abicheck.diff_helpers import make_change

# Mutation-verified: mis-indexing the positional slot (`node.args[1]` ->
# `[0]`) fails 6 cases here. Two other mutations survive and were checked
# rather than chased -- dropping the `name == "Change"` guard on the
# positional slot, and letting a positional `None` win over a later `symbol=`
# keyword. Both differ only on call text Python itself rejects (`make_change`
# takes `symbol` keyword-only; supplying it twice is a `TypeError`), so they
# are equivalent mutants over every site that can actually run.

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "_ai_readiness_for_symbol_gate", REPO_ROOT / "scripts" / "check_ai_readiness.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = _load_gate()

_SIGNATURES = {
    # `self` is not part of the call site's own argument list.
    "Change": inspect.signature(Change.__init__).replace(
        parameters=list(inspect.signature(Change.__init__).parameters.values())[1:]
    ),
    "make_change": inspect.signature(make_change),
}

_SENTINEL = object()


def _oracle_binds_symbol_to_none(call: ast.Call, name: str) -> bool | None:
    """Would Python bind this call's `symbol` parameter to a `None` literal?

    Evaluated by handing the real signature the call's own shape, with each
    argument standing in as either the `None` literal or an opaque sentinel.
    `bind_partial`, not `bind`: the question is which parameter an argument
    lands on, not whether every other parameter was supplied.

    Returns `None` for a call Python itself would reject (`Change(K, None,
    symbol="")` supplies `symbol` twice). Such a site raises before it can
    fabricate anything, so the gate owes it no particular answer -- but the
    sweep must not silently consist only of those, which is what
    `test_the_sweep_covers_both_bindable_and_rejected_calls` guards.
    """

    def _value(node: ast.expr) -> object:
        return (
            None if isinstance(node, ast.Constant) and node.value is None else _SENTINEL
        )

    try:
        bound = _SIGNATURES[name].bind_partial(
            *[_value(a) for a in call.args],
            **{kw.arg: _value(kw.value) for kw in call.keywords if kw.arg},
        )
    except TypeError:
        return None
    return "symbol" in bound.arguments and bound.arguments["symbol"] is None


def _call_of(source: str) -> ast.Call:
    return next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call))


# Every shape a call site can give `symbol`, crossed with both callables and
# both of the two values that matter. Small enough to enumerate exhaustively,
# which is the point: no reported input is privileged.
_ARG_SHAPES = (
    "",
    "{v}",
    "KIND, {v}",
    "KIND, {v}, DESC",
    "symbol={v}",
    "KIND, symbol={v}",
    "KIND, {v}, symbol={v2}",
    "KIND, {v2}, symbol={v}",
    "kind=KIND, symbol={v}",
    "**kwargs",
)
_VALUES = ("None", '""', "SOME_NAME")


@pytest.mark.parametrize(
    ("name", "shape", "value", "other"),
    [
        (n, s, v, o)
        for n, s, v, o in itertools.product(
            ("Change", "make_change"), _ARG_SHAPES, _VALUES, _VALUES
        )
    ],
)
def test_the_gate_agrees_with_python_argument_binding(
    name: str, shape: str, value: str, other: str
) -> None:
    source = f"{name}({shape.format(v=value, v2=other)})"
    expected = _oracle_binds_symbol_to_none(_call_of(source), name)
    if expected is None:
        pytest.skip(f"Python itself rejects this call: {source}")
    actual = bool(GATE.change_symbol_none_sites(ast.parse(source)))
    assert actual == expected, (
        f"gate says {actual}, Python binding says {expected}, for: {source}"
    )


def test_the_sweep_covers_both_bindable_and_rejected_calls() -> None:
    """The skip above must not be able to hollow out the whole sweep."""
    answers = [
        _oracle_binds_symbol_to_none(
            _call_of(f"{name}({shape.format(v=v, v2=o)})"), name
        )
        for name in ("Change", "make_change")
        for shape in _ARG_SHAPES
        for v in _VALUES
        for o in _VALUES
    ]
    assert answers.count(True) > 10, answers.count(True)
    assert answers.count(False) > 10, answers.count(False)


def test_a_starred_argument_is_reported_on_its_explicit_keyword() -> None:
    """`*args` makes the positional slots statically unknowable.

    The gate still answers from the one thing that is explicit -- the
    `symbol=` keyword -- rather than declining, and that is deliberate: a
    site spelling `symbol=None` in the call text is the fabrication this
    gate exists to catch, whatever precedes it.
    """
    assert GATE.change_symbol_none_sites(ast.parse("Change(*args, symbol=None)"))
    assert not GATE.change_symbol_none_sites(ast.parse('Change(*args, symbol="")'))


def test_the_oracle_is_not_constant() -> None:
    """Vacuity guard: an oracle reduced to a constant passes the sweep above."""
    answers = {
        _oracle_binds_symbol_to_none(_call_of(src), name)
        for name, src in (
            ("Change", "Change(KIND, None, DESC)"),
            ("Change", 'Change(KIND, "", DESC)'),
            ("make_change", "make_change(KIND, symbol=None)"),
            ("make_change", 'make_change(KIND, symbol="")'),
        )
    }
    assert answers == {True, False}


def test_unrelated_callables_and_parameters_are_left_alone() -> None:
    """The gate is narrow on purpose; it must not police every `None`."""
    for source in (
        "Change(KIND, SYM, DESC, old_value=None)",
        "make_change(KIND, symbol=SYM, old=None)",
        "SomethingElse(symbol=None)",
        "obj.Change(KIND, None, DESC)",
    ):
        assert not GATE.change_symbol_none_sites(ast.parse(source)), source


def test_the_live_tree_carries_no_fabricated_none() -> None:
    """A contributor learns locally, not from CI, that they added one."""
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno} ({name})"
        for path in sorted((REPO_ROOT / "tests").rglob("*.py"))
        for name, lineno in GATE.change_symbol_none_sites(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
    ]
    assert not offenders, (
        "tests construct a `Change` in a state its own annotation forbids: "
        + ", ".join(offenders)
    )
