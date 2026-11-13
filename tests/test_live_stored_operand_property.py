# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A comparison concludes identically whether each operand is live or stored.

Root ``AGENTS.md`` asks for a change to be proven "across live and stored
operands": a snapshot held in memory (what ``dump`` just extracted) and the
same snapshot written to disk and loaded back (what a baseline file is) must
yield the same report. This module states that as a differential property
over generated snapshot pairs, for all four operand combinations
(live/live, stored/stored, live/stored, stored/live), comparing the **whole**
JSON report -- findings, their order, every count and the ``exit`` block --
not a projection the property could be satisfied by while a reader still sees
two different reports.

Two pair strategies are used. ``snapshot_st`` from ``test_property_based.py``
draws two independent snapshots (mostly additions/removals);
``related_pair`` draws one surface and edits it, so the operands share symbols
and the *modification* detectors (return/type/typedef/constant changes) run.

Found by this property: the typedef detector emitted its findings in the
typedef map's insertion order, which a stored snapshot (written key-sorted)
does not preserve, so the report's ``changes`` order depended on whether an
operand was live or stored. ``compare/typedefs.py`` now walks aliases in
sorted order.

Kept out of ``test_property_based.py`` on purpose: that module is marked
``slow`` as a whole, and this differential belongs in the default lane.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings, strategies as st
from test_property_based import (
    _type_names,
    function_st,
    record_type_st,
    snapshot_st,
    variable_st,
)

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Function, Variable
from abicheck.report import diff_report as reporter
from abicheck.serialization import load_snapshot, save_snapshot, snapshot_to_json

_SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


def _stored(snapshot: AbiSnapshot, directory: Path, name: str) -> AbiSnapshot:
    """*snapshot* as a baseline file reads back: saved, then loaded."""
    path = directory / f"{name}.json"
    save_snapshot(snapshot, path)
    return load_snapshot(path)


def _report(old: AbiSnapshot, new: AbiSnapshot) -> dict[str, object]:
    return json.loads(reporter.to_json(compare(old, new)))


def _assert_operand_form_invariant(old: AbiSnapshot, new: AbiSnapshot) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        stored_old = _stored(old, Path(tmp), "old")
        stored_new = _stored(new, Path(tmp), "new")
    reference = _report(old, new)
    for label, (a, b) in {
        "stored/stored": (stored_old, stored_new),
        "live/stored": (old, stored_new),
        "stored/live": (stored_old, new),
    }.items():
        other = _report(a, b)
        differing = sorted(
            k
            for k in reference.keys() | other.keys()
            if reference.get(k) != other.get(k)
        )
        assert not differing, f"{label}: report differs from live/live in {differing}"


class _Pair(tuple):
    """An (old, new) snapshot pair that Hypothesis can print.

    Its dataclass pretty-printer walks every field, and ``AbiSnapshot``'s
    retired declaration fields raise on access, which would replace a real
    counterexample with an ``AttributeError``. The serialized form is what a
    reader needs to reproduce the case anyway.
    """

    def __repr__(self) -> str:
        return "_Pair(\n" + ",\n".join(snapshot_to_json(s) for s in self) + ")"


@st.composite
def independent_pair(draw: st.DrawFn) -> _Pair:
    """Two independently drawn snapshots.

    Built inside a composite, not as ``st.tuples(...).map(_Pair)``: a mapped
    strategy's counterexample is printed from the pre-map tuple of bare
    ``AbiSnapshot`` objects, whose retired fields raise on access, so a real
    failure (a ``UnicodeEncodeError`` from ``c++filt`` on Windows) was
    reported as an ``AttributeError`` from the printer instead.
    """
    return _Pair((draw(snapshot_st()), draw(snapshot_st())))


@st.composite
def related_pair(draw: st.DrawFn) -> _Pair:
    """One drawn surface and an edited copy: some entries kept, some removed,
    some modified in place, some added."""
    functions = draw(st.lists(function_st(), max_size=6))
    variables = draw(st.lists(variable_st(), max_size=4))
    types = draw(st.lists(record_type_st(), max_size=3))
    typedefs = draw(
        st.dictionaries(
            st.text(min_size=1, max_size=8, alphabet="abcxyz_"), _type_names, max_size=4
        )
    )
    constants = draw(
        st.dictionaries(
            st.text(min_size=1, max_size=8, alphabet="ABCXYZ_"),
            st.sampled_from(["0", "1", "42", "-1"]),
            max_size=4,
        )
    )
    fate = st.sampled_from(["keep", "drop", "edit"])

    new_functions: list[Function] = []
    for f in functions:
        what = draw(fate)
        if what == "keep":
            new_functions.append(f)
        elif what == "edit":
            new_functions.append(
                Function(
                    name=f.name,
                    mangled=f.mangled,
                    return_type=draw(_type_names),
                    params=f.params,
                    visibility=f.visibility,
                    is_virtual=f.is_virtual,
                    is_noexcept=draw(st.booleans()),
                )
            )
    new_variables: list[Variable] = []
    for v in variables:
        what = draw(fate)
        if what == "keep":
            new_variables.append(v)
        elif what == "edit":
            new_variables.append(
                Variable(
                    name=v.name,
                    mangled=v.mangled,
                    type=draw(_type_names),
                    visibility=v.visibility,
                )
            )
    new_typedefs = {
        k: draw(_type_names) if draw(st.booleans()) else v
        for k, v in typedefs.items()
        if draw(fate) != "drop"
    }
    new_constants = {
        k: draw(st.sampled_from(["0", "7", v]))
        for k, v in constants.items()
        if draw(fate) != "drop"
    }
    new_types = [t for t in types if draw(fate) != "drop"]

    new_functions += draw(st.lists(function_st(), max_size=2))
    new_variables += draw(st.lists(variable_st(), max_size=1))
    new_typedefs.update(
        draw(
            st.dictionaries(
                st.text(min_size=1, max_size=8, alphabet="mnop"),
                _type_names,
                max_size=2,
            )
        )
    )

    old = AbiSnapshot(
        library="librel.so",
        version="1.0",
        functions=functions,
        variables=variables,
        types=types,
        typedefs=typedefs,
        constants=constants,
    )
    new = AbiSnapshot(
        library="librel.so",
        version="2.0",
        functions=new_functions,
        variables=new_variables,
        types=new_types,
        typedefs=new_typedefs,
        constants=new_constants,
    )
    return _Pair((old, new))


@_SETTINGS
@given(pair=independent_pair())
def test_independent_pair_report_is_operand_form_invariant(pair: _Pair) -> None:
    _assert_operand_form_invariant(*pair)


@_SETTINGS
@given(pair=related_pair())
def test_related_pair_report_is_operand_form_invariant(pair: _Pair) -> None:
    _assert_operand_form_invariant(*pair)


def test_typedef_findings_order_does_not_follow_insertion_order() -> None:
    """The found defect, as a fixed case: typedefs inserted out of key order
    are reported in the same order live and stored."""
    old = AbiSnapshot(
        library="l",
        version="1",
        typedefs={"zeta_t": "int", "alpha_t": "int", "mid_t": "long"},
    )
    new = AbiSnapshot(library="l", version="2", typedefs={})
    _assert_operand_form_invariant(old, new)
    symbols = [
        c["symbol"]
        for c in _report(old, new)["changes"]
        if c["kind"] == "typedef_removed"
    ]
    assert symbols == ["alpha_t", "mid_t", "zeta_t"]


def test_related_pair_reaches_modification_detectors() -> None:
    """Guard against a vacuous strategy: ``related_pair`` must produce
    in-place modifications, not only additions and removals."""
    seen: set[str] = set()

    @settings(
        max_examples=150,
        deadline=None,
        database=None,
        suppress_health_check=list(HealthCheck),
    )
    @given(pair=related_pair())
    def collect(pair: _Pair) -> None:
        seen.update(c.kind.value for c in compare(*pair).changes)

    collect()
    assert {
        "func_return_changed",
        "var_type_changed",
        "typedef_base_changed",
    } <= seen, sorted(seen)
