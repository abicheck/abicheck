"""Hoisted invariants in compare-time hot loops must not change answers.

Two call sites were restructured purely for cost: ``find_by_value_types``
now derives each opaque candidate's leaf spelling once (it made 4.2M
``depth_aware_bare_name`` calls over 1,682 names), and
``qualified_declaration_name`` reads the demangle batch cache directly
instead of issuing one single-name batch per declaration. The oracle for
each is the *pre-change* formulation, restated here verbatim, run over
generated inputs.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck import demangle
from abicheck.compare.opaque_types import (
    _type_is_by_value_referenced,
    find_by_value_types,
)
from abicheck.model import AbiSnapshot, Function, Param, Variable, Visibility


def _reference_find_by_value_types(snap: AbiSnapshot, opaque: set[str]) -> set[str]:
    """The implementation before the hoist, kept as the oracle."""
    from abicheck.model.surface_facts import is_abi_visible

    out: set[str] = set()
    for func in snap.functions:
        if not is_abi_visible(func):
            continue
        rt = func.return_type.strip()
        for tname in opaque:
            if tname not in out and _type_is_by_value_referenced(tname, rt):
                out.add(tname)
        for param in func.params:
            pt = param.type.strip()
            for tname in opaque:
                if (
                    tname not in out
                    and _type_is_by_value_referenced(tname, pt)
                    and param.pointer_depth == 0
                ):
                    out.add(tname)
    for var in snap.variables:
        if not is_abi_visible(var):
            continue
        vt = var.type.strip()
        for tname in opaque:
            if tname not in out and _type_is_by_value_referenced(tname, vt):
                out.add(tname)
    return out


_leaves = st.sampled_from(["Handle", "Tag", "Impl", "Wrapper<dep::Tag>"])
_scopes = st.sampled_from(["", "ns::", "other::", "api::detail::"])
_names = st.builds(lambda s, leaf: s + leaf, _scopes, _leaves)
_decor = st.sampled_from(
    ["{}", "const {}", "{}*", "{} &", "std::vector<{}>", "{}**", " {} "]
)
_types = st.builds(lambda d, n: d.format(n), _decor, _names)
_vis = st.sampled_from([Visibility.PUBLIC, Visibility.HIDDEN])


@st.composite
def _snapshots(draw):
    functions = [
        Function(
            name=f"f{i}",
            mangled=f"f{i}",
            return_type=draw(_types),
            params=[
                Param(
                    name="p", type=draw(_types), pointer_depth=draw(st.integers(0, 2))
                )
                for _ in range(draw(st.integers(0, 3)))
            ],
            visibility=draw(_vis),
        )
        for i in range(draw(st.integers(0, 5)))
    ]
    variables = [
        Variable(
            name=f"v{i}", mangled=f"v{i}", type=draw(_types), visibility=draw(_vis)
        )
        for i in range(draw(st.integers(0, 3)))
    ]
    return AbiSnapshot(
        library="x", version="1", functions=functions, variables=variables
    )


@settings(max_examples=300, deadline=None)
@given(snap=_snapshots(), opaque=st.sets(_names, max_size=6))
def test_find_by_value_types_matches_the_unhoisted_loop(snap, opaque):
    assert find_by_value_types(snap, opaque) == _reference_find_by_value_types(
        snap, opaque
    )


@given(
    symbols=st.lists(
        st.sampled_from(
            ["_ZN2ns3fooEv", "_ZN1a1bE", "__ZN2ns3fooEv", "_Znotvalid", "plain", ""]
        ),
        max_size=6,
    )
)
def test_demangle_one_batched_matches_single_symbol_batch(symbols):
    for sym in symbols:
        assert demangle.demangle_one_batched(sym) == demangle.demangle_batch([sym]).get(
            sym
        )


def test_demangle_one_batched_does_not_answer_macho_from_permissive_entry():
    demangle._reset_demangle_batch_cache()
    permissive = demangle.demangle_batch(["__ZN2ns3fooEv"], accept_macho_prefix=True)
    assert demangle.demangle_one_batched("__ZN2ns3fooEv") is None
    assert demangle.demangle_batch(["__ZN2ns3fooEv"]) == {}
    del permissive
