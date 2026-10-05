# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Contract of the per-snapshot qualified-name table and the shared alias index.

Both exist only to stop re-deriving the same name (``perf-findings.md``:
``qualified_declaration_name`` ~13x per declaration). Neither may change an
answer: a memoised read must equal the direct derivation for every
declaration, inside a comparison scope and outside one, for declarations the
snapshot owns and for foreign ones. The oracle is
``qualified_declaration_name`` itself applied per declaration -- not the
table, which is what is under test.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.compare.detection_memo import detection_memo_scope
from abicheck.compare.surface_reconcile import reconcile_declaration_lists
from abicheck.compare.template_surface import (
    alias_identity,
    qualified_declaration_name,
    qualified_name_lookup,
)
from abicheck.model.comparison_memo import comparison_memo_scope
from abicheck.model.declarations import Function, Variable
from abicheck.model.snapshot import AbiSnapshot

# Mixed spellings: plain C names, Itanium manglings in two namespaces (the
# collision the qualified name exists to separate), operators, a template.
_SPELLINGS = [
    ("foo", "foo"),
    ("foo", "_ZN3ns13fooEv"),
    ("foo", "_ZN3ns23fooEv"),
    ("bar", "_ZN1A3barEi"),
    ("operator<", "_ZN1AltERKS_"),
    ("max<int>", "_Z3maxIiET_S0_S0_"),
    ("baz", "_Z3bazv"),
]

_decl_specs = st.lists(
    st.tuples(st.sampled_from(_SPELLINGS), st.booleans()), min_size=0, max_size=12
)


def _snapshot(specs: list[tuple[tuple[str, str], bool]]) -> AbiSnapshot:
    funcs = []
    variables = []
    for i, ((name, mangled), is_var) in enumerate(specs):
        if is_var:
            variables.append(Variable(name=name, mangled=mangled, type="int"))
        else:
            # Same spelling may repeat: identity, not name, keys the table.
            funcs.append(Function(name=name, mangled=mangled, return_type=f"r{i}"))
    return AbiSnapshot(library="lib", version="1", functions=funcs, variables=variables)


def _all_decls(snap: AbiSnapshot) -> list[Function | Variable]:
    return [*snap.declarations.functions, *snap.declarations.variables]


@given(specs=_decl_specs)
@settings(deadline=None, max_examples=60)
def test_lookup_equals_direct_derivation_in_and_out_of_scope(specs) -> None:
    snap = _snapshot(specs)
    expected = [qualified_declaration_name(d.name, d.mangled) for d in _all_decls(snap)]

    outside = qualified_name_lookup(snap)
    assert [outside(d) for d in _all_decls(snap)] == expected

    with comparison_memo_scope():
        inside = qualified_name_lookup(snap)
        assert [inside(d) for d in _all_decls(snap)] == expected
        # A second reader in the same scope agrees too (the shared table).
        again = qualified_name_lookup(snap)
        assert [again(d) for d in _all_decls(snap)] == expected


@given(specs=_decl_specs, foreign=st.sampled_from(_SPELLINGS))
@settings(deadline=None, max_examples=40)
def test_a_foreign_declaration_is_derived_not_looked_up(specs, foreign) -> None:
    """A declaration the snapshot does not own -- even one spelled exactly
    like an owned one -- gets its own derivation, never another object's
    table entry."""
    snap = _snapshot(specs)
    stranger = Function(name=foreign[0], mangled=foreign[1], return_type="void")
    with comparison_memo_scope():
        assert qualified_name_lookup(snap)(stranger) == qualified_declaration_name(
            *foreign
        )


@given(old_specs=_decl_specs, new_specs=_decl_specs)
@settings(deadline=None, max_examples=40)
def test_shared_alias_index_does_not_change_reconciliation(
    old_specs, new_specs
) -> None:
    """The alias index is shared across reconciliation slots inside a
    detector pass; the reconciled result must equal the unshared one, even
    when two slots reconcile against the same full lists back to back."""
    old = _snapshot(old_specs)
    new = _snapshot(new_specs)

    def run() -> tuple[list[str], list[str]]:
        o, n = reconcile_declaration_lists(
            old.declarations.functions[::2],
            new.declarations.functions[::2],
            old_all=old.declarations.functions,
            new_all=new.declarations.functions,
            key=lambda f: f.mangled,
            alias_key=alias_identity,
        )
        return [f.return_type for f in o], [f.return_type for f in n]

    baseline = run()
    with detection_memo_scope():
        assert run() == baseline
        assert run() == baseline
