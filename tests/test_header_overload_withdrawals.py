# SPDX-License-Identifier: Apache-2.0
"""Public header overloads for which clang records no distinct linker name."""

from itertools import permutations

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


@pytest.mark.parametrize("queue_first", [True, False])
def test_removed_event_default_attributed_once(queue_first):
    host = pattern("int")
    queue_old, queue_new = (pattern("queue&", "events") for _ in range(2))
    queue_old.params[-1].default = "{}"
    old = snap(*([queue_old, host] if queue_first else [host, queue_old]))
    new = snap(*([queue_new, host] if queue_first else [host, queue_new]))
    old.from_headers = new.from_headers = True
    report = compare(old, new)
    [finding] = [
        c for c in report.changes if c.kind == ChangeKind.PARAM_DEFAULT_VALUE_REMOVED
    ]
    assert finding.entity_id == queue_old.entity_id
    assert report.verdict.value == "API_BREAK"


@pytest.mark.parametrize("queue_first", [False, True])
def test_inconsistent_event_default_redeclarations_are_inconclusive(queue_first):
    host = pattern("int")
    old, new, redeclared = (pattern("queue&", "events") for _ in range(3))
    old.params[-1].default = redeclared.params[-1].default = "{}"
    before = snap(old, host) if queue_first else snap(host, old)
    after = snap(new, redeclared, host) if queue_first else snap(host, new, redeclared)
    before.from_headers = after.from_headers = True
    assert not any(
        c.kind == ChangeKind.PARAM_DEFAULT_VALUE_REMOVED
        for c in compare(before, after).changes
    )


@pytest.mark.parametrize("default_added", [False, True])
def test_new_required_argument_excludes_old_arity(default_added):
    host, prev, now = (
        pattern("int"),
        pattern("queue&", "T*"),
        pattern("queue&", "T*", "int"),
    )
    if default_added:
        now.params[-1].default = "0"
    findings = lost_header_overloads(snap(host, prev), snap(host, now), [])
    assert [c.kind for c in findings] == (
        [] if default_added else [ChangeKind.HEADER_ONLY_FUNCTION_PARAMS_CHANGED]
    )


@pytest.mark.parametrize("directory,removed", [("feature", True), ("other", False)])
def test_withdrawal_needs_same_include_relative_header(directory, removed):
    before = snap(
        pattern("int", header="/old/include/feature/api.hpp"),
        pattern("queue&", header="/old/include/feature/api.hpp"),
    )
    after = snap(pattern("int", header=f"/new/include/{directory}/api.hpp"))
    before.ast_compile_args = ("-I/old/include",)
    after.ast_compile_args = ("-I", "/new/include")
    assert bool(lost_header_overloads(before, after, [])) == removed


@pytest.mark.parametrize("queue_first", [True, False])
def test_explicit_deletion_is_scoped_to_the_queue_overload(queue_first):
    host = pattern("int")
    previous, current = (pattern("queue&", "T*") for _ in range(2))
    current.is_deleted = True
    old = snap(previous, host) if queue_first else snap(host, previous)
    new = snap(current, host) if queue_first else snap(host, current)
    result = compare(old, new)
    [change] = [c for c in result.changes if c.kind == ChangeKind.FUNC_DELETED]
    assert change.entity_id == previous.entity_id
    assert result.verdict.value == "API_BREAK"
    assert not any(c.kind == ChangeKind.FUNC_DELETED for c in compare(new, new).changes)


@pytest.mark.parametrize("conflict_side", ["old", "new"])
def test_conflicting_deletion_observations_do_not_prove_a_transition(conflict_side):
    host = pattern("int")
    previous, current, conflict = (pattern("queue&") for _ in range(3))
    current.is_deleted = True
    conflict.is_deleted = conflict_side == "old"
    old, new = [host, previous], [host, current]
    (old if conflict_side == "old" else new).append(conflict)
    assert not any(
        c.kind == ChangeKind.FUNC_DELETED
        for c in compare(snap(*old), snap(*new)).changes
    )


@pytest.mark.parametrize("observed", [False, True])
def test_arity_change_requires_observing_original_header(observed):
    old = snap(pattern("int"), pattern("queue&", "T*"))
    new = snap(
        pattern("int", header="table.hpp" if observed else "other.hpp"),
        pattern("queue&", "T*", "int", header="other.hpp"),
    )
    assert bool(lost_header_overloads(old, new, [])) is observed


class TestOverloadMatchingProperties:
    def test_order_and_duplicate_declarations_preserve_identity(self):
        host, queue = pattern("int"), pattern("queue&")
        for old in permutations([host, queue]):
            for new in permutations([host, queue, pattern("queue&")]):
                assert not lost_header_overloads(snap(*old), snap(*new), [])

    def test_conflicting_defaults_never_establish_a_loss(self):
        host = pattern("int")
        previous, kept, missing = (pattern("queue&", "events") for _ in range(3))
        previous.params[-1].default = kept.params[-1].default = "{}"
        for old in permutations([host, previous]):
            for new in permutations([host, kept, missing]):
                before, after = snap(*old), snap(*new)
                before.from_headers = after.from_headers = True
                assert ChangeKind.PARAM_DEFAULT_VALUE_REMOVED not in {
                    c.kind for c in compare(before, after).changes
                }
