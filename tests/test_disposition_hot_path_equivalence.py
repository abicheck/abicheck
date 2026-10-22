# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The disposition hot-path rewrites answer exactly what the scans they
replaced answered.

* ``finding_redundancy._RootIndex`` -- root-type matching searches only the
  roots a finding's spellings can name. Oracle: an independent whole-token
  scan over every root, in order (never the regex under test).
* ``SuppressionList.evaluate`` -- tests each rule's selectors once instead
  of once per gate. Oracle: the rule's own public ``matches`` /
  ``would_withhold`` / ``would_withhold_unknown_reachability`` composed the
  way ``evaluate`` documents.
* ``execution_cache.per_object`` (template_surface) -- identity derived once per
  declaration, same values.
* ``selectors._ns_forms`` -- the cached namespace forms equal the uncached
  derivation.
"""

from __future__ import annotations

import itertools

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.compare.finding_redundancy import _RootIndex, match_root_type
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.evidence_status import ReachabilityState
from abicheck.model.execution_cache import per_object as _per_declaration
from abicheck.policy.selectors import _ns_forms, _strip_template_args
from abicheck.policy.suppression.rules import Suppression, SuppressionList

_IDENT = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _whole_token_occurs(name: str, text: str) -> bool:
    start = text.find(name)
    while start != -1:
        before = text[start - 1] if start else ""
        after = text[start + len(name)] if start + len(name) < len(text) else ""
        if before not in _IDENT and after not in _IDENT:
            return True
        start = text.find(name, start + 1)
    return False


def _oracle(c: Change, roots: list[str], exclude: str | None) -> str | None:
    for name in roots:
        if name == exclude:
            continue
        if c.old_value and c.new_value:
            if _whole_token_occurs(name, c.old_value) and _whole_token_occurs(
                name, c.new_value
            ):
                return name
            continue
        for text in (c.old_value, c.new_value, c.description):
            if text and _whole_token_occurs(name, text):
                return name
    return None


# Names that start with a word character (pre-screened by their first word
# run) and ones that do not (always searched), sharing first runs on purpose.
_POOL = ["Foo", "Foo2", "Foo::Bar", "ns::Foo", "Ctx", "a.b", "T1", "_x", "op+",
         "*p", "<T>", "::Ctx", "Foo<int>", "Bar"]  # fmt: skip
_PIECES = _POOL + ["", " ", "*", "&", "::", "<", ">", ",", "x", "2", "_", "Foo_"]
_text = st.lists(st.sampled_from(_PIECES), max_size=6).map("".join)
_maybe_text = st.one_of(st.none(), _text)


@given(
    roots=st.lists(st.sampled_from(_POOL), unique=True, min_size=1, max_size=8),
    extra=st.lists(st.sampled_from(_POOL), unique=True, max_size=4),
    old=_maybe_text,
    new=_maybe_text,
    desc=_text,
    exclude_pick=st.integers(min_value=-1, max_value=7),
)
@settings(max_examples=400, deadline=None)
def test_root_index_answers_the_exhaustive_scan(
    roots, extra, old, new, desc, exclude_pick
) -> None:
    c = Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="f", description=desc,
        old_value=old, new_value=new,
    )  # fmt: skip
    exclude = roots[exclude_pick] if 0 <= exclude_pick < len(roots) else None
    root_types = {name: c for name in roots}
    # The index may be built over a superset (roots popped after building).
    superset = {**root_types, **{n: c for n in extra}}
    order = list(superset)
    live = {n: c for n in order if n in root_types}
    expected = _oracle(c, list(live), exclude)
    assert (
        match_root_type(c, live, root_index=_RootIndex(superset), exclude=exclude)
        == expected
    )
    assert match_root_type(c, live, exclude=exclude) == expected


_KINDS = [ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_ADDED, ChangeKind.TYPE_SIZE_CHANGED]
_RULES = [
    Suppression(symbol="_Z3foov", reason="r"),
    Suppression(symbol_pattern="_Z3.*", reason="r"),
    Suppression(namespace="ns", reason="r"),
    Suppression(namespace="ns", allow_public_break=True, reason="r"),
    Suppression(symbol="_Z3foov", reachability="proven-unreachable-only", reason="r"),
    Suppression(
        symbol="_Z3foov",
        reachability="proven-unreachable-only",
        allow_unknown_reachability=True,
        reason="r",
    ),  # fmt: skip
    Suppression(namespace="ns", reachability="public-only", reason="r"),
    Suppression(symbol_pattern="nomatch.*", reason="r"),
]


def _changes() -> list[Change]:
    out = []
    for kind, symbol, reachable, state in itertools.product(
        _KINDS,
        ["_Z3foov", "ns::foo", "other"],
        [False, True],
        list(ReachabilityState),
    ):
        c = Change(kind=kind, symbol=symbol, description=symbol)
        c.public_reachable = reachable
        c.reachability_state = state
        out.append(c)
    return out


def _evaluate_oracle(rules, change):
    withheld = unknown = None
    for s in rules:
        if s.matches(change):
            return (True, s, None, None)
        if withheld is None and s.would_withhold(change):
            withheld = s
        if unknown is None and s.would_withhold_unknown_reachability(change):
            unknown = s
    return (False, None, withheld, unknown)


@pytest.mark.parametrize("width", [1, 2, 3])
def test_suppression_evaluate_matches_the_per_gate_composition(width) -> None:
    for rules in itertools.permutations(_RULES, width):
        sl = SuppressionList(list(rules))
        for change in _changes():
            out = sl.evaluate(change)
            got = (
                out.suppressed,
                out.matched_rule,
                out.withheld_rule,
                out.withheld_unknown_rule,
            )
            assert got == _evaluate_oracle(rules, change), (rules, change)


def test_per_declaration_identity_is_computed_once_and_unchanged() -> None:
    calls: list[object] = []

    def identity(decl: object) -> str:
        calls.append(decl)
        return f"id:{decl!r}"

    decls = [object() for _ in range(5)]
    memo = _per_declaration(identity)
    for _ in range(3):
        assert [memo(d) for d in decls] == [f"id:{d!r}" for d in decls]
    assert calls == decls


@pytest.mark.parametrize(
    "name",
    ["ns::foo", "ns::Box<int>::get", "a<b<c>>::d", "_Z3foov", "_ZN2ns3fooEv", "x"],
)
def test_ns_forms_equals_the_uncached_derivation(name) -> None:
    from abicheck.demangle import demangle

    forms = [name]
    if name.startswith("_Z") and (dm := demangle(name)):
        forms.append(dm)
    assert _ns_forms(name) == tuple(_strip_template_args(f) for f in forms)
