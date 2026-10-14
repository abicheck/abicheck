"""Scope-aware leaf widening in ``find_by_value_types`` and spelling-aware
asymmetric existence in ``find_opaque_struct_types``.

Bug class: an opaque-type criterion decides "same entity" from one rendered
spelling, so a reference or declaration spelled another way (bare leaf in a
different scope; ``name`` vs ``qualified_name`` across DWARF and header
producers) is attributed to the wrong entity.

Oracle: the expected outcome of each case is written down from the C++
unqualified-lookup rule (the innermost enclosing scope that declares the
leaf wins) and from which entity each declaration really is, not computed by
the implementation's helpers.
"""

from __future__ import annotations

import pytest
from test_opaque_struct_identity_tiers import _record, _struct_size_change

from abicheck.compare.opaque_leaf_lookup import leaf_hidden, scope_of
from abicheck.compare.opaque_struct_types import find_opaque_struct_types
from abicheck.compare.opaque_types import find_by_value_types
from abicheck.diff_filtering import _downgrade_opaque_struct_changes
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Variable


def _snap(types=(), functions=(), variables=()) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so.1",
        version="1",
        types=list(types),
        functions=list(functions),
        variables=list(variables),
    )


def _decl(name: str, qualified: str | None = None) -> RecordType:
    return RecordType(name=name, kind="struct", qualified_name=qualified)


def _opaque() -> RecordType:
    return RecordType(name="ns::Handle", kind="struct", is_opaque=True)


# (function name, other declarations in the snapshot, expected exposed)
_FUNCTION_CASES = [
    # other::f's bare Handle finds other::Handle first.
    ("other::f", [("other::Handle", None)], False),
    # Header-backend spelling: bare name, scope in qualified_name.
    ("other::f", [("Handle", "other::Handle")], False),
    # Lookup walks outward: other::sub::f still finds other::Handle.
    ("other::sub::f", [("other::Handle", None)], False),
    # A deeper scope inside ns hides ns::Handle.
    ("ns::inner::f", [("ns::inner::Handle", None)], False),
    # Member function of a class in other.
    ("other::Widget::run(int)", [("other::Handle", None)], False),
    # Inside ns, ns::Handle is found before other::Handle.
    ("ns::f", [("other::Handle", None)], True),
    # A sibling namespace's Handle does not enclose other::f.
    ("other::f", [("third::Handle", None)], True),
    # No same-leaf declaration at all: nothing proves another entity.
    ("other::f", [], True),
    # Unknown (global) scope proves nothing.
    ("f", [("other::Handle", None)], True),
    # A declaration with no known scope proves nothing.
    ("other::f", [("Handle", None)], True),
    # ns::inner::f with only an unrelated other::Handle.
    ("ns::inner::f", [("other::Handle", None)], True),
]


@pytest.mark.parametrize(("fname", "others", "exposed"), _FUNCTION_CASES)
def test_bare_parameter_reference_follows_unqualified_lookup(fname, others, exposed):
    snap = _snap(
        types=[_opaque(), *(_decl(n, q) for n, q in others)],
        functions=[
            Function(
                name=fname,
                mangled="_Z1fv",
                return_type="void",
                params=[Param(name="h", type="Handle", pointer_depth=0)],
            )
        ],
    )
    assert (find_by_value_types(snap, {"ns::Handle"}) == {"ns::Handle"}) is exposed


@pytest.mark.parametrize(("fname", "others", "exposed"), _FUNCTION_CASES)
def test_bare_return_and_variable_reference_follow_the_same_rule(
    fname, others, exposed
):
    types = [_opaque(), *(_decl(n, q) for n, q in others)]
    ret = _snap(
        types=types,
        functions=[Function(name=fname, mangled="_Z1gv", return_type="Handle")],
    )
    var = _snap(
        types=types,
        variables=[Variable(name=fname.split("(")[0], mangled="_Z1v", type="Handle")],
    )
    for snap in (ret, var):
        assert (find_by_value_types(snap, {"ns::Handle"}) == {"ns::Handle"}) is exposed


def test_qualified_reference_is_never_hidden_by_scope():
    snap = _snap(
        types=[_opaque(), _decl("other::Handle")],
        functions=[
            Function(
                name="other::f",
                mangled="_Z1fv",
                return_type="void",
                params=[Param(name="h", type="ns::Handle", pointer_depth=0)],
            )
        ],
    )
    assert find_by_value_types(snap, {"ns::Handle"}) == {"ns::Handle"}


def test_same_text_in_two_scopes_is_scanned_per_scope():
    """The per-text scan cache must not let a hidden occurrence in one scope
    mask a real exposure of the identical text in another."""
    fns = [
        Function(name="other::f", mangled="_Z1f", return_type="Handle"),
        Function(name="ns::g", mangled="_Z1g", return_type="Handle"),
    ]
    snap = _snap(types=[_opaque(), _decl("other::Handle")], functions=fns)
    assert find_by_value_types(snap, {"ns::Handle"}) == {"ns::Handle"}


def test_helpers_edge_cases():
    assert scope_of("a::b::f(int, x::y)") == ("a", "b")
    assert scope_of("f") == ()
    assert not leaf_hidden("ns::Handle", [("other",)], ())


# -- find_opaque_struct_types: asymmetric existence across spellings --------


@pytest.mark.parametrize(
    ("old_types", "new_types", "symbol", "suppressed"),
    [
        # DWARF old forward decl (qualified name) vs header new full
        # definition (bare name + qualified_name): the same entity exists.
        (
            [("ns::Handle", True, None)],
            [("Handle", False, "ns::Handle")],
            "ns::Handle",
            False,
        ),
        # Header old forward decl vs DWARF new full definition.
        (
            [("Handle", True, "ns::Handle")],
            [("ns::Handle", False, None)],
            "Handle",
            False,
        ),
        # Mirror direction: new forward decl, old definition.
        (
            [("Handle", False, "ns::Handle")],
            [("ns::Handle", True, None)],
            "ns::Handle",
            False,
        ),
        # Genuinely absent on the other side: still suppressed.
        ([("ns::Handle", True, None)], [], "ns::Handle", True),
        # A different entity under another scope does not count as present.
        (
            [("ns::Handle", True, None)],
            [("Handle", False, "other::Handle")],
            "ns::Handle",
            True,
        ),
    ],
)
def test_forward_declaration_pairs_with_definition_under_any_spelling(
    old_types, new_types, symbol, suppressed
):
    def build(specs):
        return _snap(
            types=[_record(n, is_opaque=o, qualified_name=q) for n, o, q in specs]
        )

    old, new = build(old_types), build(new_types)
    out, filtered = _downgrade_opaque_struct_changes(
        [_struct_size_change(symbol)], old, new
    )
    assert bool(filtered) is suppressed
    assert bool(out) is not suppressed
    spellings = {k.spelling for k in find_opaque_struct_types(old, new).local}
    assert (symbol in spellings) is suppressed
