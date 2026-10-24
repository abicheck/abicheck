"""``pair_parameter_replacements``: a removed symbol and the added one that
replaces it under a changed parameter type become one ``FUNC_PARAMS_CHANGED``
only when the pairing is provable, and every other change is kept as it was.

Oracle: the expected kinds are read off the scenario (one signature change, or
a removal and an addition that cannot be paired), not computed by the rule.
The mangled names are written out from the Itanium ABI grammar:

* ``_Z5scale3vecIf3abiILi8E2t8EEf``  -> ``scale(vec<float, abi<8, t8> >, float)``
* ``_Z5scale3vecIf3abiILi16E3t16EEf`` -> ``scale(vec<float, abi<16, t16> >, float)``
* ``_ZN2ns1C1fEi`` / ``_ZN2ns1C1fEl`` -> ``ns::C::f(int)`` / ``ns::C::f(long)``
* ``_ZNK2ns1C1fEi``                 -> ``ns::C::f(int) const``
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.compare.parameter_replacement import pair_parameter_replacements
from abicheck.diff_helpers import make_change
from abicheck.model import AbiSnapshot, Function
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind

OLD_SCALE = "_Z5scale3vecIf3abiILi8E2t8EEf"
NEW_SCALE = "_Z5scale3vecIf3abiILi16E3t16EEf"


def _fn(name: str, mangled: str) -> Function:
    return Function(name=name, mangled=mangled, return_type="void", params=[])


def _snap(functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(library="lib", version="1.0", functions=functions)


def _removed(mangled: str) -> Change:
    return make_change(
        ChangeKind.FUNC_REMOVED, symbol=mangled, name="x", description="removed"
    )


def _added(mangled: str) -> Change:
    return make_change(
        ChangeKind.FUNC_ADDED, symbol=mangled, new="x", description="added"
    )


def test_replaced_non_template_is_one_params_change() -> None:
    old = _snap([_fn("scale", OLD_SCALE)])
    new = _snap([_fn("scale", NEW_SCALE)])
    (change,) = pair_parameter_replacements(
        [_removed(OLD_SCALE), _added(NEW_SCALE)], old, new
    )
    assert change.kind == ChangeKind.FUNC_PARAMS_CHANGED
    assert change.symbol == OLD_SCALE
    assert change.old_value == "(vec<float, abi<8, t8> >, float)"
    assert change.new_value == "(vec<float, abi<16, t16> >, float)"
    assert NEW_SCALE in change.description


def test_namespaced_member_pair_is_one_params_change() -> None:
    old = _snap([_fn("ns::C::f", "_ZN2ns1C1fEi")])
    new = _snap([_fn("ns::C::f", "_ZN2ns1C1fEl")])
    changes = pair_parameter_replacements(
        [_removed("_ZN2ns1C1fEi"), _added("_ZN2ns1C1fEl")], old, new
    )
    assert [c.kind for c in changes] == [ChangeKind.FUNC_PARAMS_CHANGED]


def test_unrelated_changes_pass_through_in_order() -> None:
    unrelated = make_change(
        ChangeKind.FUNC_ADDED, symbol="_Z7computev", new="c", description="added"
    )
    old = _snap([_fn("scale", OLD_SCALE)])
    new = _snap([_fn("scale", NEW_SCALE)])
    changes = pair_parameter_replacements(
        [unrelated, _removed(OLD_SCALE), _added(NEW_SCALE)], old, new
    )
    assert [c.symbol for c in changes] == ["_Z7computev", OLD_SCALE]


def test_removed_overload_beside_surviving_overload_is_not_paired() -> None:
    # f(int) and f(double) both existed; f(int) went and f(long) appeared.
    # Overloads coexist, so the removal stays a removal of f(int).
    old = _snap([_fn("ns::C::f", "_ZN2ns1C1fEi"), _fn("ns::C::f", "_ZN2ns1C1fEd")])
    new = _snap([_fn("ns::C::f", "_ZN2ns1C1fEl"), _fn("ns::C::f", "_ZN2ns1C1fEd")])
    removed, added = _removed("_ZN2ns1C1fEi"), _added("_ZN2ns1C1fEl")
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]


def test_addition_of_second_overload_is_not_paired() -> None:
    # Only one f existed before, but the new side has two: the added f(long)
    # is a new overload, not the replacement of f(int).
    old = _snap([_fn("ns::C::f", "_ZN2ns1C1fEi")])
    new = _snap([_fn("ns::C::f", "_ZN2ns1C1fEl"), _fn("ns::C::f", "_ZN2ns1C1fEd")])
    removed, added = _removed("_ZN2ns1C1fEi"), _added("_ZN2ns1C1fEl")
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]


def test_cv_qualifier_change_is_not_paired() -> None:
    # The const change is the cv detector's finding, not a parameter change.
    old = _snap([_fn("ns::C::f", "_ZNK2ns1C1fEi")])
    new = _snap([_fn("ns::C::f", "_ZN2ns1C1fEl")])
    removed, added = _removed("_ZNK2ns1C1fEi"), _added("_ZN2ns1C1fEl")
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]


@pytest.mark.parametrize(
    ("old_mangled", "new_mangled"),
    [
        # A function template's instantiations are a family, not one function.
        ("_Z3getIiET_S0_", "_Z3getIlET_S0_"),
        # A constructor has no unique declared name to pair on.
        ("_ZN3BoxC1Ei", "_ZN3BoxC1El"),
    ],
)
def test_template_or_constructor_is_not_paired(
    old_mangled: str, new_mangled: str
) -> None:
    old = _snap([_fn("get", old_mangled)])
    new = _snap([_fn("get", new_mangled)])
    removed, added = _removed(old_mangled), _added(new_mangled)
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]


def test_no_removal_or_no_addition_returns_input_unchanged() -> None:
    changes = [_added(NEW_SCALE)]
    old = _snap([])
    new = _snap([_fn("scale", NEW_SCALE)])
    assert pair_parameter_replacements(changes, old, new) is changes


@pytest.mark.parametrize("header_side", ["old", "new"])
def test_header_parsed_side_keeps_removal_and_addition(header_side: str) -> None:
    # A header-parsed declaration is contract evidence: a parameter retyped to
    # an internal type stays func_removed (case189's documented finding).
    old = _snap([_fn("scale", OLD_SCALE)])
    new = _snap([_fn("scale", NEW_SCALE)])
    (old if header_side == "old" else new).from_headers = True
    removed, added = _removed(OLD_SCALE), _added(NEW_SCALE)
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]


def test_removal_absent_from_the_snapshot_is_not_paired() -> None:
    # A removal the old snapshot does not hold has no declaration to read a
    # name from, so it stays a removal while a real pair beside it is paired.
    missing = "_Z7missingv"
    old = _snap([_fn("scale", OLD_SCALE)])
    new = _snap([_fn("scale", NEW_SCALE)])
    changes = pair_parameter_replacements(
        [_removed(missing), _removed(OLD_SCALE), _added(NEW_SCALE)], old, new
    )
    assert [(c.kind, c.symbol) for c in changes] == [
        (ChangeKind.FUNC_REMOVED, missing),
        (ChangeKind.FUNC_PARAMS_CHANGED, OLD_SCALE),
    ]


def test_unparseable_removal_is_not_paired() -> None:
    # A plain C name is not an Itanium symbol: it has no parameter list to
    # compare, so the pass makes no claim.
    old = _snap([_fn("scale", "scale")])
    new = _snap([_fn("scale", NEW_SCALE)])
    removed, added = _removed("scale"), _added(NEW_SCALE)
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]


# Generated-input contract for the pairing primitive. Each symbol's declared
# name is written out here by hand, not read back from the implementation.
_DECLARED = {
    OLD_SCALE: "scale",
    NEW_SCALE: "scale",
    "_ZN2ns1C1fEi": "ns::C::f",
    "_ZN2ns1C1fEl": "ns::C::f",
    "_ZN2ns1C1fEd": "ns::C::f",
    "_Z7computev": "compute",
}
_SYMBOLS = sorted(_DECLARED)
_RETURN_TYPES = st.sampled_from(["int", "double", ""])


def _generated_pair(
    old_syms: set[str], new_syms: set[str], old_ret: str, new_ret: str
) -> tuple[AbiSnapshot, AbiSnapshot, list[Change]]:
    old = AbiSnapshot(
        library="lib",
        version="1.0",
        functions=[
            Function(name=_DECLARED[s], mangled=s, return_type=old_ret, params=[])
            for s in sorted(old_syms)
        ],
    )
    new = AbiSnapshot(
        library="lib",
        version="1.0",
        functions=[
            Function(name=_DECLARED[s], mangled=s, return_type=new_ret, params=[])
            for s in sorted(new_syms)
        ],
    )
    changes = [_removed(s) for s in sorted(old_syms - new_syms)] + [
        _added(s) for s in sorted(new_syms - old_syms)
    ]
    return old, new, changes


class TestPairingPrimitiveProperties:
    @given(
        old_syms=st.sets(st.sampled_from(_SYMBOLS)),
        new_syms=st.sets(st.sampled_from(_SYMBOLS)),
        old_ret=_RETURN_TYPES,
        new_ret=_RETURN_TYPES,
    )
    @settings(max_examples=200, deadline=None)
    def test_each_pair_consumes_two_changes_and_leaves_the_rest_untouched(
        self, old_syms: set[str], new_syms: set[str], old_ret: str, new_ret: str
    ) -> None:
        old, new, changes = _generated_pair(old_syms, new_syms, old_ret, new_ret)
        out = pair_parameter_replacements(changes, old, new)
        replacements = [c for c in out if c.kind == ChangeKind.FUNC_PARAMS_CHANGED]
        assert len(out) == len(changes) - len(replacements)
        survivors = [c for c in out if c.kind != ChangeKind.FUNC_PARAMS_CHANGED]
        assert all(any(c is original for original in changes) for c in survivors)

    @given(
        old_syms=st.sets(st.sampled_from(_SYMBOLS)),
        new_syms=st.sets(st.sampled_from(_SYMBOLS)),
        data=st.data(),
    )
    @settings(max_examples=200, deadline=None)
    def test_input_order_cannot_change_the_result(
        self, old_syms: set[str], new_syms: set[str], data: st.DataObject
    ) -> None:
        old, new, changes = _generated_pair(old_syms, new_syms, "int", "int")
        shuffled = data.draw(st.permutations(changes))

        def signature(result: list[Change]) -> list[tuple[str, str]]:
            return sorted((c.kind.value, c.symbol) for c in result)

        assert signature(pair_parameter_replacements(shuffled, old, new)) == signature(
            pair_parameter_replacements(changes, old, new)
        )

    @given(
        old_syms=st.sets(st.sampled_from(_SYMBOLS)),
        new_syms=st.sets(st.sampled_from(_SYMBOLS)),
    )
    @settings(max_examples=200, deadline=None)
    def test_an_ambiguous_declared_name_is_never_paired(
        self, old_syms: set[str], new_syms: set[str]
    ) -> None:
        old, new, changes = _generated_pair(old_syms, new_syms, "int", "int")
        out = pair_parameter_replacements(changes, old, new)
        replaced = {c.symbol for c in out if c.kind == ChangeKind.FUNC_PARAMS_CHANGED}
        for name in set(_DECLARED.values()):
            members_old = {s for s in old_syms if _DECLARED[s] == name}
            members_new = {s for s in new_syms if _DECLARED[s] == name}
            if len(members_old) != 1 or len(members_new) != 1:
                assert not (replaced & members_old), name

    @given(
        old_syms=st.sets(st.sampled_from(_SYMBOLS)),
        new_syms=st.sets(st.sampled_from(_SYMBOLS)),
        old_ret=_RETURN_TYPES,
        new_ret=_RETURN_TYPES,
    )
    @settings(max_examples=200, deadline=None)
    def test_known_disagreeing_return_types_are_never_paired(
        self, old_syms: set[str], new_syms: set[str], old_ret: str, new_ret: str
    ) -> None:
        old, new, changes = _generated_pair(old_syms, new_syms, old_ret, new_ret)
        out = pair_parameter_replacements(changes, old, new)
        replaced = {c.symbol for c in out if c.kind == ChangeKind.FUNC_PARAMS_CHANGED}
        if old_ret and new_ret and old_ret != new_ret:
            assert not replaced
