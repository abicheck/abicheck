"""The standard-layout resolver on hand-built summaries: the edge cases DWARF
rarely produces from a real compile (unresolved references, incomplete types,
cycles, the depth limit, unions, empty classes).

Each expected value is derived here from [class.prop]/3 (N4659) by hand, not
read from the implementation. A layout that is not a proven answer must stay
``None``: never a break, never a clean ``True``.
"""

from __future__ import annotations

from abicheck.extract.dwarf_standard_layout import (
    _MAX_DEPTH,
    _SCALAR,
    _Resolver,
    _Summary,
)


def k(n: int) -> tuple[int, int]:
    return (0, n)


def rec(n: int) -> tuple[str, tuple[int, int]]:
    return ("record", k(n))


def summary(
    *,
    members=(),
    bases=(),
    local_false: bool = False,
    incomplete: bool = False,
    union: bool = False,
) -> _Summary:
    return _Summary(
        local_false=local_false,
        incomplete=incomplete,
        members=tuple(members),
        base_edges=tuple(bases),
        union=union,
    )


def trait(summaries: dict, n: int):
    return _Resolver(summaries).trait(k(n))


# --- a plain scalar class, and the local [class.prop]/3.2/3.3 rules ---


def test_scalar_members_are_standard_layout() -> None:
    # struct { int a; int b; } -- every rule holds.
    assert trait({k(1): summary(members=[_SCALAR, _SCALAR])}, 1) is True


def test_a_local_failure_is_false_whatever_the_members() -> None:
    # Virtual function or mixed access: decided by the record's own children.
    assert trait({k(1): summary(members=[_SCALAR], local_false=True)}, 1) is False


def test_an_incomplete_record_is_unknown() -> None:
    # A declaration is not a complete type, so no rule can be checked.
    assert trait({k(1): summary(incomplete=True)}, 1) is None


def test_an_absent_record_is_unknown() -> None:
    assert trait({}, 1) is None


# --- [class.prop]/3.4: every by-value member and base must itself qualify ---


def test_a_member_that_is_not_standard_layout_makes_the_holder_false() -> None:
    summaries = {
        k(1): summary(members=[rec(2)]),
        k(2): summary(local_false=True),
    }
    assert trait(summaries, 1) is False


def test_an_unknown_member_leaves_the_holder_unknown() -> None:
    summaries = {k(1): summary(members=[("unknown", None)])}
    assert trait(summaries, 1) is None


def test_an_incomplete_member_type_leaves_the_holder_unknown() -> None:
    summaries = {
        k(1): summary(members=[rec(2)]),
        k(2): summary(incomplete=True),
    }
    assert trait(summaries, 1) is None


def test_an_unresolved_base_leaves_the_holder_unknown() -> None:
    summaries = {k(1): summary(bases=[(None, False)], members=[_SCALAR])}
    assert trait(summaries, 1) is None


def test_a_cycle_through_by_value_members_terminates_unknown() -> None:
    # An ill-formed self-containing pair: the provisional answer breaks the
    # cycle, so the result is unknown rather than a recursion error.
    summaries = {
        k(1): summary(members=[rec(2)]),
        k(2): summary(members=[rec(1)]),
    }
    assert trait(summaries, 1) is None


def test_a_chain_past_the_depth_limit_is_unknown() -> None:
    # Each record holds the next by value; past _MAX_DEPTH the answer is unknown.
    length = _MAX_DEPTH + 4
    summaries = {k(n): summary(members=[rec(n + 1)]) for n in range(1, length)}
    summaries[k(length)] = summary(members=[_SCALAR])
    assert trait(summaries, 1) is None


# --- [class.prop]/3.5: data members in at most one class of the hierarchy ---


def test_data_in_base_and_derived_is_false() -> None:
    # struct B { int x; }; struct D : B { int y; };
    summaries = {
        k(1): summary(bases=[(k(2), False)], members=[_SCALAR]),
        k(2): summary(members=[_SCALAR]),
    }
    assert trait(summaries, 1) is False


def test_an_unknown_base_leaves_the_data_rule_unknown() -> None:
    # The base's data is unknown, so whether the hierarchy holds data is too.
    summaries = {
        k(1): summary(bases=[(k(2), False)], members=[_SCALAR]),
        k(2): summary(incomplete=True),
    }
    assert trait(summaries, 1) is None


def test_two_bases_with_data_and_none_in_the_derived_class_is_false() -> None:
    # struct X { int a; }; struct Y { int b; }; struct Z : X, Y {};
    summaries = {
        k(1): summary(bases=[(k(2), False), (k(3), False)]),
        k(2): summary(members=[_SCALAR]),
        k(3): summary(members=[_SCALAR]),
    }
    assert trait(summaries, 1) is False


def test_an_empty_class_with_an_empty_base_is_standard_layout() -> None:
    # struct A {}; struct B : A {};
    summaries = {
        k(1): summary(bases=[(k(2), False)]),
        k(2): summary(),
    }
    assert trait(summaries, 1) is True


def test_a_base_with_unknown_data_leaves_an_empty_derived_class_unknown() -> None:
    summaries = {
        k(1): summary(bases=[(k(2), False), (k(3), False)]),
        k(2): summary(incomplete=True),
        k(3): summary(members=[_SCALAR]),
    }
    assert trait(summaries, 1) is None


# --- [class.prop]/3.6: no base of the type of the first member ---


def test_the_first_member_type_being_a_base_is_false() -> None:
    # struct E {}; struct F : E { E first; };  E is no data, so only 3.6 applies.
    summaries = {
        k(1): summary(bases=[(k(2), False)], members=[rec(2)]),
        k(2): summary(),
    }
    assert trait(summaries, 1) is False


def test_an_unknown_first_member_leaves_the_derived_class_unknown() -> None:
    summaries = {
        k(1): summary(bases=[(k(2), False)], members=[("unknown", None)]),
        k(2): summary(),
    }
    assert trait(summaries, 1) is None


def test_a_union_first_member_set_includes_every_alternative() -> None:
    # struct E {}; union U { E e; int i; }; struct F : E { U first; };  M(U) contains E.
    summaries = {
        k(1): summary(bases=[(k(2), False)], members=[rec(3)]),
        k(2): summary(),
        k(3): summary(members=[rec(2), _SCALAR], union=True),
    }
    assert trait(summaries, 1) is False


def test_a_union_with_an_incomplete_alternative_leaves_the_first_member_unknown() -> (
    None
):
    summaries = {
        k(1): summary(bases=[(k(2), False)], members=[rec(3)]),
        k(2): summary(),
        k(3): summary(members=[rec(4)], union=True),
        k(4): summary(incomplete=True),
    }
    assert trait(summaries, 1) is None


# --- the collector hands back the same answers ---


def test_the_resolver_memoizes_a_record_once() -> None:
    summaries = {k(1): summary(members=[_SCALAR])}
    resolver = _Resolver(summaries)
    assert resolver.trait(k(1)) is True
    # A second query returns the memoized answer without re-deriving it.
    assert resolver.trait(k(1)) is True


def test_a_record_without_a_child_list_stays_unknown_and_the_collector_is_reused() -> (
    None
):
    from types import SimpleNamespace

    from abicheck.extract.dwarf_standard_layout import collector_for

    class Builder:
        pass

    builder = Builder()
    collector = collector_for(builder)
    assert collector_for(builder) is collector
    holder = SimpleNamespace(is_standard_layout=None, is_standard_layout_fact=None)
    collector.add(object(), None, holder)  # no DIE is read: nothing is collected
    collector.finalize()
    assert holder.is_standard_layout is None


def test_a_diamond_ladder_with_a_shared_base_is_false_without_expanding_paths() -> None:
    # L_n derives from X_n and Y_n, both of which derive from L_(n+1); the base
    # L_(n+1) therefore occurs twice in L_n ([class.prop]/3.7). The paths double
    # per level, so the answer must come from the per-record multiplicity, not
    # from listing every subobject.
    levels = 12
    summaries: dict = {}
    for n in range(1, levels + 1):
        below = k(3 * (n + 1))
        summaries[k(3 * n)] = summary(
            bases=[(k(3 * n + 1), False), (k(3 * n + 2), False)]
        )
        summaries[k(3 * n + 1)] = summary(bases=[(below, False)])
        summaries[k(3 * n + 2)] = summary(bases=[(below, False)])
    summaries[k(3 * (levels + 1))] = summary()
    assert trait(summaries, 3) is False


def test_a_base_chain_past_the_depth_limit_is_unknown() -> None:
    # Empty classes derive from one another, and only the bottom one has data.
    # Whether the hierarchy holds data is reached through the chain, past
    # _MAX_DEPTH, so the top record is unknown rather than decided.
    length = _MAX_DEPTH + 4
    summaries = {k(n): summary(bases=[(k(n + 1), False)]) for n in range(1, length)}
    summaries[k(length)] = summary(members=[_SCALAR])
    assert trait(summaries, 1) is None


def test_the_first_member_set_of_a_record_is_the_same_on_a_second_query() -> None:
    # M(X) of a record with no members is the record itself, on every query.
    resolver = _Resolver({k(2): summary()})
    first = resolver._first_member_types(rec(2))
    assert first == frozenset({k(2)})
    assert resolver._first_member_types(rec(2)) == first


def test_a_declaration_left_unresolved_keeps_its_holder_unset() -> None:
    from types import SimpleNamespace as NS

    from abicheck.extract.dwarf_standard_layout import collector_for

    class Builder:
        pass

    die = NS(
        tag="DW_TAG_structure_type",
        offset=7,
        cu=NS(cu_offset=0),
        attributes={"DW_AT_declaration": NS(value=True)},
    )
    holder = NS(is_standard_layout=None, is_standard_layout_fact=None)
    collector = collector_for(Builder())
    collector.add(die, [], holder)
    collector.finalize()
    assert holder.is_standard_layout is None
    assert holder.is_standard_layout_fact is None


def test_the_subobject_walk_stops_at_the_depth_limit() -> None:
    # Past _MAX_DEPTH the walk gives up: unknown, never a clean answer.
    resolver = _Resolver({k(1): summary(bases=[(k(2), False)]), k(2): summary()})
    assert resolver._base_multiplicity(k(1), _MAX_DEPTH + 1) is None


def test_a_complete_record_gets_its_resolved_trait() -> None:
    from types import SimpleNamespace as NS

    from abicheck.extract.dwarf_standard_layout import collector_for

    class Builder:
        pass

    die = NS(
        tag="DW_TAG_structure_type",
        offset=8,
        cu=NS(cu_offset=0),
        attributes={},
    )
    holder = NS(is_standard_layout=None, is_standard_layout_fact=None)
    collector = collector_for(Builder())
    collector.add(die, [], holder)
    collector.finalize()
    # An empty complete class is standard-layout: [class.prop]/3 holds vacuously.
    assert holder.is_standard_layout is True
