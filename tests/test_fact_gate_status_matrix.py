# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-063 5B: the seven header facts gate on ``FactStatus``, not provenance.

Every snapshot here is a single-backend ``castxml`` header dump, so the old
``fact_provenance`` gate would have answered "known" for every declaration
and compared resting defaults as real values. The oracle is written from
the status contract alone: a transition is reported iff both sides state
the fact ``PRESENT``; every other status pair produces no finding.

The sweep crosses all seven facts with every pair of statuses (6 x 6), and
a vacuity guard asserts the both-``PRESENT`` cell really fires for each
fact, so an oracle or fixture that silently never fires cannot pass.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.compare.declined_comparisons import declined_scope
from abicheck.compare.fact_gate import both_facts_present
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Fact,
    FactStatus,
    Function,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

STATUSES = tuple(FactStatus)


def _fact(status: FactStatus, value: object) -> Fact:
    if status is FactStatus.PRESENT:
        return Fact.present(value)
    if status is FactStatus.PARTIAL:
        return Fact.partial(value)
    if status is FactStatus.FAILED:
        return Fact.failed("extractor error")
    return {
        FactStatus.NOT_COLLECTED: Fact.not_collected,
        FactStatus.UNSUPPORTED: Fact.unsupported,
        FactStatus.NOT_APPLICABLE: Fact.not_applicable,
    }[status]()


def _snap(version: str, **kw: object) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version=version,
        from_headers=True,
        ast_producer="castxml",
        **kw,  # type: ignore[arg-type]
    )


def _func(fact: Fact) -> dict:
    f = Function(
        name="f",
        mangled="_Z1fv",
        return_type="int",
        visibility=Visibility.PUBLIC,
        deprecated_fact=fact,
    )
    return {"functions": [f]}


def _var(fact: Fact) -> dict:
    v = Variable(
        name="g",
        mangled="g",
        type="int",
        visibility=Visibility.PUBLIC,
        deprecated_fact=fact,
    )
    return {"variables": [v]}


def _record(fact: Fact) -> dict:
    return {
        "types": [
            RecordType(
                name="R",
                kind="struct",
                size_bits=32,
                fields=[TypeField("x", "int", 0)],
                deprecated_fact=fact,
            )
        ]
    }


def _field(field: str) -> Callable[[Fact], dict]:
    def build(fact: Fact) -> dict:
        tf = TypeField("x", "int", 0, **{f"{field}_fact": fact})
        return {
            "types": [RecordType(name="R", kind="struct", size_bits=32, fields=[tf])]
        }

    return build


def _enum(field: str) -> Callable[[Fact], dict]:
    def build(fact: Fact) -> dict:
        return {
            "enums": [
                EnumType(
                    name="E", members=[EnumMember("A", 0)], **{f"{field}_fact": fact}
                )
            ]
        }

    return build


# (fact, builder, old value, new value, kind the transition reports)
CASES = [
    ("Function.deprecated", _func, "use h", None, ChangeKind.FUNC_DEPRECATED_REMOVED),
    ("Variable.deprecated", _var, "use h", None, ChangeKind.VAR_DEPRECATED_REMOVED),
    (
        "RecordType.deprecated",
        _record,
        "use S",
        None,
        ChangeKind.TYPE_DEPRECATED_REMOVED,
    ),
    (
        "TypeField.deprecated",
        _field("deprecated"),
        "old",
        None,
        ChangeKind.FIELD_DEPRECATED_REMOVED,
    ),
    (
        "TypeField.default",
        _field("default"),
        "30",
        None,
        ChangeKind.FIELD_DEFAULT_INITIALIZER_REMOVED,
    ),
    (
        "EnumType.deprecated",
        _enum("deprecated"),
        "old",
        None,
        ChangeKind.ENUM_DEPRECATED_REMOVED,
    ),
    (
        "EnumType.is_scoped",
        _enum("is_scoped"),
        False,
        True,
        ChangeKind.ENUM_BECAME_SCOPED,
    ),
]


def _fires(case, old_status: FactStatus, new_status: FactStatus) -> bool:
    _, build, old_v, new_v, kind = case
    old = _snap("1", **build(_fact(old_status, old_v)))
    new = _snap("2", **build(_fact(new_status, new_v)))
    return kind in {c.kind for c in compare(old, new).changes}


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_transition_reported_iff_both_sides_present(case) -> None:
    disagreements = [
        (o.value, n.value)
        for o, n in itertools.product(STATUSES, STATUSES)
        if _fires(case, o, n) != (o is n is FactStatus.PRESENT)
    ]
    assert disagreements == []


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_vacuity_guard_both_present_fires(case) -> None:
    assert _fires(case, FactStatus.PRESENT, FactStatus.PRESENT)


def _obj(status: FactStatus) -> Function:
    return Function(
        name="f", mangled="_Z1fv", return_type="int", deprecated_fact=_fact(status, "m")
    )


@pytest.mark.parametrize(("old", "new"), list(itertools.product(STATUSES, STATUSES)))
def test_decline_recorded_only_when_informative(
    old: FactStatus, new: FactStatus
) -> None:
    with declined_scope() as got:
        ok = both_facts_present(_obj(old), _obj(new), "deprecated", "_Z1fv")
    assert ok == (old is new is FactStatus.PRESENT)
    loud = {FactStatus.FAILED, FactStatus.PARTIAL}
    informative = not ok and (
        FactStatus.PRESENT in (old, new) or bool({old, new} & loud)
    )
    assert bool(got) == informative
    if informative:
        assert [d.entity for d in got] == ["_Z1fv"]
