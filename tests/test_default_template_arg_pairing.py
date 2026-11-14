"""DEFAULT_TEMPLATE_ARG_CHANGED pairs a removed instantiation only with a
newly added one whose kept leading arguments prove a defaulted tail.

Bug class: the shape-A detector explained instantiation churn as a changed
default without evidence of one -- it paired a dropped ``ktwice<double>``
with the *surviving* ``ktwice<int>``, and a renamed tag
``kfam<ktag_a>`` -> ``kfam<ktag_b>`` (one argument, nothing kept) as a
default switch. Oracle, stated on the inputs: a pair is a default change iff
the new instantiation did not exist before AND the two argument lists have
the same length, share a non-empty leading prefix and differ after it.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compare.template_param_defaults import (
    detect_default_template_arg_changed,
)
from abicheck.model import AbiSnapshot, Function, Visibility

#: Itanium builtin codes, so each mangled name demangles to its own spelling.
_CODE = {
    "int": "i",
    "long": "l",
    "char": "c",
    "short": "s",
    "double": "d",
    "float": "f",
}


def _fn(args: tuple[str, ...]) -> Function:
    spelled = ", ".join(args)
    codes = "".join(_CODE[a] for a in args)
    return Function(
        name=f"ns::f<{spelled}>",
        mangled=f"_ZN2ns1fI{codes}EEvv",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )


def _oracle(old: tuple[str, ...], new: tuple[str, ...], added: bool) -> bool:
    if not added or len(old) != len(new) or old == new:
        return False
    first_diff = next(i for i, (a, b) in enumerate(zip(old, new)) if a != b)
    return first_diff >= 1


_ARGS = [
    ("int",),
    ("long",),
    ("int", "short"),
    ("int", "double"),
    ("char", "double"),
    ("int", "short", "float"),
]


@pytest.mark.parametrize("survives", [False, True])
@pytest.mark.parametrize(("old", "new"), list(itertools.permutations(_ARGS, 2)))
def test_pairing_requires_an_added_instantiation_and_a_kept_prefix(
    old: tuple[str, ...], new: tuple[str, ...], survives: bool
) -> None:
    removed, partner = _fn(old), _fn(new)
    # `survives`: the partner existed in OLD too -- a sibling, not a re-mangle.
    old_fns = [removed, partner] if survives else [removed]
    snap_old = AbiSnapshot(library="l.so", version="1", functions=old_fns)
    snap_new = AbiSnapshot(library="l.so", version="2", functions=[partner])
    found = detect_default_template_arg_changed(snap_old, snap_new)
    expected = _oracle(old, new, added=not survives)
    assert bool(found) is expected
    if expected:
        assert [c.symbol for c in found] == [removed.mangled]


@pytest.mark.parametrize(
    ("old_mangled", "new_mangled"),
    [
        # U kdta<T, U = int>(T) -> U = long: the return type IS the default.
        ("_Z4kdtaIiiET0_T_", "_Z4kdtaIilET0_T_"),
        # void kdtv<T, U = int>(T): the return type does not depend on it.
        ("_Z4kdtvIiiEvT_", "_Z4kdtvIilEvT_"),
        # Namespaced, return type the default.
        ("_ZN2ns4kdtaIiiEET0_T_", "_ZN2ns4kdtaIilEET0_T_"),
    ],
)
def test_pairing_ignores_a_return_type_that_depends_on_the_default(
    old_mangled: str, new_mangled: str
) -> None:
    """The callable both instantiations share is keyed without its return
    type: ``int kdta<int, int>(int)`` and ``long kdta<int, long>(int)`` are
    one template (found by the edit corpus's
    ``exported_instantiation_default_changed``)."""
    name = "kdta" if "kdta" in old_mangled else "kdtv"

    def fn(mangled: str) -> Function:
        return Function(
            name=name, mangled=mangled, return_type="", visibility=Visibility.PUBLIC
        )

    old = AbiSnapshot(library="l.so", version="1", functions=[fn(old_mangled)])
    new = AbiSnapshot(library="l.so", version="2", functions=[fn(new_mangled)])
    found = detect_default_template_arg_changed(old, new)
    assert [c.symbol for c in found] == [old_mangled]


@pytest.mark.parametrize(
    ("spelled", "expected"),
    [
        ("int kdta<int, int>(int)", "kdta"),
        ("std::pair<int, int> ns::mk<int>(int)", "ns::mk"),
        ("ns::Foo<float, A>::compute(int)", "ns::Foo::compute"),
        ("ns::C<int>::operator new(unsigned long)", "ns::C::operator new"),
        ("ns::C<int>::operator<<(int)", "ns::C::operator<<"),
        ("unsigned long ns::f<int>()", "ns::f"),
        ("ns::f<int>", "ns::f"),
    ],
)
def test_callable_name_drops_return_type_args_and_params(
    spelled: str, expected: str
) -> None:
    from abicheck.compare.template_surface import callable_name

    assert callable_name(spelled) == expected
