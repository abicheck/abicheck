# SPDX-License-Identifier: Apache-2.0
"""Public header overloads for which clang records no distinct linker name."""

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.compare.header_overloads import lost_header_overloads
from abicheck.model import AbiSnapshot, Function, Param
from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, entity_id_for_function


def pattern(*types, scope="api", variadic=False, header="table.hpp"):
    return Function(
        name="wrap",
        mangled="wrap",
        return_type="void",
        params=[Param("arg", t) for t in types],
        is_variadic=variadic,
        source_header=header,
        entity_id=entity_id_for_function(
            (Namespace(scope),), "wrap", param_types=types
        ),
        in_public_contract_fact=Fact.present(True, producer="clang"),
        binary_exported_fact=Fact.present(False, producer="clang"),
    )


def snap(*fns):
    return AbiSnapshot(library="libapi.so", version="1", functions=list(fns))


def test_event_argument_withdrawal_reaches_compare():
    host = pattern("int")
    old = pattern("queue&", "T*", "events")
    new = pattern("queue&", "T*")
    report = compare(snap(host, old), snap(host, new))
    assert report.verdict.value == "API_BREAK"
    [finding] = [
        c
        for c in report.changes
        if c.kind == ChangeKind.HEADER_ONLY_FUNCTION_PARAMS_CHANGED
    ]
    assert (finding.symbol, finding.entity_id) == ("api::wrap", old.entity_id)


def test_removals_are_identified_separately():
    host, a, b = pattern("int"), pattern("queue&", "T*"), pattern("queue&", "const T*")
    [first, second] = lost_header_overloads(snap(host, a, b), snap(host), [])
    assert {first.entity_id, second.entity_id} == {a.entity_id, b.entity_id}
    assert all(c.kind == ChangeKind.INLINE_FUNCTION_REMOVED for c in (first, second))


@pytest.mark.parametrize(
    "other",
    [
        pattern("Args&&..."),
        pattern("int", variadic=True),
        pattern("int", variadic=None),
        pattern("long", "int", "int"),
    ],
)
def test_no_arity_proof_with_another_candidate(other):
    prev, now = pattern("queue&", "T*", "events"), pattern("queue&", "T*")
    assert not lost_header_overloads(snap(other, prev), snap(other, now), [])


def test_reordering_and_missing_header_do_not_prove_withdrawal():
    host, q = pattern("int"), pattern("queue&")
    assert not compare(snap(host, q), snap(q, host)).changes
    unrelated = pattern("int", scope="other", header="other.hpp")
    assert not lost_header_overloads(snap(unrelated, q), snap(unrelated), [])


def test_mangled_sibling_with_sufficient_arity_prevents_contraction():
    sibling = pattern("int", "int", "int")
    sibling.mangled = "_ZN3api4wrapEiii"
    sibling.entity_id = entity_id_for_function((), "", mangled_name=sibling.mangled)
    prev, now = pattern("queue&", "T*", "events"), pattern("queue&", "T*")
    assert not lost_header_overloads(snap(sibling, prev), snap(sibling, now), [])
