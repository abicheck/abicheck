# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-063 6B, variable cohort: ``VAR_TYPE_CHANGED``/``VAR_BECAME_CONST``/
``VAR_LOST_CONST`` read through ``SemanticIR``.

The oracle for the equivalence property is a hand-written table of what each
spelling *is* (its type with the top-level ``const`` removed, and whether the
variable itself is ``const``), not a call into the canonicalizer or the old
helper, so a shared bug cannot make both sides agree.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import ChangeKind, compare
from abicheck.compare.variables import (
    variable_type_changes,
    variable_type_facts,
    variable_type_index,
)
from abicheck.diff_symbols_variables import variable_type_index_for
from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model import AbiSnapshot, Fact, Variable
from abicheck.model.identity import Namespace, entity_id_for_variable
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_legacy_adapter import (
    SYNTHETIC_IDENTITY_EXTRA,
    legacy_variable_occurrences,
)
from abicheck.model.semantic_ir_variable_payload import variable_canonical_entity

#: spelling -> (type without its top-level const, variable itself const);
#: ``None`` for a spelling that is not a resolved type.
_MEANING: dict[str, tuple[str, bool] | None] = {
    "int": ("int", False),
    "const int": ("int", True),
    "int const": ("int", True),
    "long": ("long", False),
    "volatile int": ("volatile int", False),
    "int *": ("ptr int", False),
    "int * const": ("ptr int", True),
    "const int *": ("ptr const int", False),
    "const int * const": ("ptr const int", True),
    "std::vector<int>": ("vector", False),
    "const std::vector<int>": ("vector", True),
    "?": None,
    "?*": None,
    "const ?": None,
}
_SPELLINGS = st.sampled_from(sorted(_MEANING))


def _var(type_: str, *, ident: bool = True, name: str = "g") -> Variable:
    mangled = f"_ZN2ns1{name}E"
    return Variable(
        name=f"ns::{name}",
        mangled=mangled,
        type=type_,
        # What both header-AST backends store: a word search over the whole
        # spelling -- deliberately not the top-level answer, so the property
        # below shows the cutover does not read it.
        is_const="const" in type_.split(),
        entity_id=(
            entity_id_for_variable((Namespace("ns"),), name, mangled_name=mangled)
            if ident
            else None
        ),
    )


def _snap(variables: list[Variable], *, with_ir: bool) -> AbiSnapshot:
    ir = (
        normalize_header_ast(
            types=[],
            enums=[],
            typedefs_qualified={},
            typedef_entity_ids={},
            producer="castxml",
            variables=variables,
        )
        if with_ir
        else None
    )
    return AbiSnapshot(
        library="l",
        version="1",
        variables=variables,
        semantic_ir=ir,
        ast_producer="castxml",
    )


#: Builtin, non-pointer spellings: a known-exact "no indirection" shape.
_EXACT_NON_POINTER = frozenset(
    {"int", "const int", "int const", "long", "volatile int"}
)


def _oracle(old: str, new: str) -> list[ChangeKind]:
    a, b = _MEANING[old], _MEANING[new]
    if a is None or b is None:
        # An unresolved pointee still proves a change when the other side is
        # exactly a non-pointer and this one is a pointer to *something*
        # (the reviewer's ``int -> ?*``); anything else stays unknown.
        if "?*" in (old, new) and {old, new} & _EXACT_NON_POINTER:
            return [ChangeKind.VAR_TYPE_CHANGED]
        return []
    if a[0] != b[0]:
        return [ChangeKind.VAR_TYPE_CHANGED]
    if a[1] == b[1]:
        return []
    return [ChangeKind.VAR_BECAME_CONST if b[1] else ChangeKind.VAR_LOST_CONST]


def _run(old: AbiSnapshot, new: AbiSnapshot) -> list[ChangeKind]:
    (o,), (n,) = old.declarations.variables, new.declarations.variables
    got = variable_type_changes(
        o.mangled,
        o.name,
        (o.type, n.type),
        variable_type_index_for(old, [o]).entity_for(o),
        variable_type_index_for(new, [n]).entity_for(n),
        entity_id=o.entity_id,
    )
    return [c.kind for c in got]


@settings(max_examples=400, deadline=None)
@given(
    old_t=_SPELLINGS,
    new_t=_SPELLINGS,
    old_ir=st.booleans(),
    new_ir=st.booleans(),
    old_ident=st.booleans(),
    new_ident=st.booleans(),
)
def test_ir_and_adapter_paths_match_the_documented_contract(
    old_t, new_t, old_ir, new_ir, old_ident, new_ident
) -> None:
    old = _snap([_var(old_t, ident=old_ident)], with_ir=old_ir)
    new = _snap([_var(new_t, ident=new_ident)], with_ir=new_ir)
    assert _run(old, new) == _oracle(old_t, new_t)


def test_oracle_is_not_vacuous() -> None:
    produced = {k for a in _MEANING for b in _MEANING for k in _oracle(a, b)}
    assert produced == {
        ChangeKind.VAR_TYPE_CHANGED,
        ChangeKind.VAR_BECAME_CONST,
        ChangeKind.VAR_LOST_CONST,
    }


@pytest.mark.parametrize(
    ("old_t", "new_t", "expected"),
    [
        # Both spellings contain the word "const", so Variable.is_const read
        # True on both sides and the old path reported a type change; the
        # variable itself lost its top-level const.
        ("const int * const", "const int *", ChangeKind.VAR_LOST_CONST),
        ("const int *", "const int * const", ChangeKind.VAR_BECAME_CONST),
        ("const std::vector<int>", "std::vector<int>", ChangeKind.VAR_LOST_CONST),
        ("int *", "const int *", ChangeKind.VAR_TYPE_CHANGED),
    ],
)
def test_end_to_end_compare_reads_top_level_const(old_t, new_t, expected) -> None:
    old = _snap([_var(old_t)], with_ir=True)
    new = _snap([_var(new_t)], with_ir=True)
    kinds = {c.kind for c in compare(old, new).changes}
    var_kinds = kinds & {
        ChangeKind.VAR_TYPE_CHANGED,
        ChangeKind.VAR_BECAME_CONST,
        ChangeKind.VAR_LOST_CONST,
    }
    assert var_kinds == {expected}


def test_the_declaration_wins_over_a_stale_ir_copy_only_where_it_speaks() -> None:
    """A named occurrence is a boundary copy of its declaration: an
    established projected fact replaces a disagreeing copy, a fact the
    projection leaves ``NOT_COLLECTED`` keeps the occurrence's own value."""
    var = _var("int")
    occ = OccurrenceId(var.entity_id)
    ir = SemanticIR(
        occurrences={
            occ: CanonicalEntity(
                canonical_spelling=Fact.present("long"),
                cv_qualification=Fact.present(("const",)),
            )
        }
    )
    fresh = CanonicalEntity(
        canonical_spelling=Fact.present("int"),
        cv_qualification=Fact.not_collected(),
    )
    index = variable_type_index(ir, [var], lambda v: fresh)
    assert variable_type_facts(index.entity_for(var)) == ("int", True)


def test_an_unnamed_variable_on_an_ir_side_is_projected() -> None:
    named, unnamed = _var("int"), _var("const int", ident=False, name="h")
    snap = _snap([named, unnamed], with_ir=True)
    index = variable_type_index_for(snap, [named, unnamed])
    assert variable_type_facts(index.entity_for(named)) == ("int", False)
    assert variable_type_facts(index.entity_for(unnamed))[1] is True


@pytest.mark.parametrize(
    ("old_t", "new_t", "expected"),
    [
        # Non-pointer -> pointer to an unresolved pointee: provable.
        ("int", "?*", [ChangeKind.VAR_TYPE_CHANGED]),
        ("?*", "int", [ChangeKind.VAR_TYPE_CHANGED]),
        ("int &", "?*", [ChangeKind.VAR_TYPE_CHANGED]),
        ("int", "?**", [ChangeKind.VAR_TYPE_CHANGED]),
        ("?&", "?*", [ChangeKind.VAR_TYPE_CHANGED]),
        # Compatible structure: the ``?`` may be what makes them equal.
        ("int *", "?*", []),
        ("int **", "?*", []),
        ("?*", "?*", []),
        ("?**", "?*", []),
        ("std::vector<int>", "?*", []),
        ("int", "const ?", []),
        ("int", "?", []),
    ],
)
def test_unresolved_spelling_reports_only_a_provable_change(
    old_t, new_t, expected
) -> None:
    for with_ir in (True, False):
        old = _snap([_var(old_t)], with_ir=with_ir)
        new = _snap([_var(new_t)], with_ir=with_ir)
        assert _run(old, new) == expected


def test_unreliable_cv_suppresses_a_cv_only_difference() -> None:
    old = variable_canonical_entity(_var("int"), "castxml")
    new = variable_canonical_entity(_var("volatile int"), "castxml")
    kw = {"entity_id": None}
    assert variable_type_changes("g", "g", ("int", "volatile int"), old, new, **kw)
    assert not variable_type_changes(
        "g", "g", ("int", "volatile int"), old, new, cv_facts_reliable=False, **kw
    )


class TestLegacyVariableOccurrences:
    def test_keyed_by_entity_id_or_synthetic_and_kept_apart(self) -> None:
        a, b = _var("int", ident=False), _var("long", ident=False)
        ir, order = legacy_variable_occurrences(
            [a, b], lambda v: variable_canonical_entity(v, "")
        )
        assert len(set(order)) == 2
        assert all(o.entity_id.extra == SYNTHETIC_IDENTITY_EXTRA for o in order)
        assert [ir.occurrences[o].canonical_spelling.value for o in order] == [
            "int",
            "long",
        ]

    def test_producer_identity_is_reused(self) -> None:
        var = _var("int")
        _ir, (occ,) = legacy_variable_occurrences(
            [var], lambda v: variable_canonical_entity(v, "")
        )
        assert occ.entity_id == var.entity_id

    def test_projection_matches_the_normalizer(self) -> None:
        for spelling in _MEANING:
            var = _var(spelling)
            (real,) = normalize_header_ast(
                types=[],
                enums=[],
                typedefs_qualified={},
                typedef_entity_ids={},
                producer="castxml",
                variables=[var],
            ).occurrences.values()
            projected = variable_canonical_entity(var, "castxml")
            assert projected.canonical_spelling == real.canonical_spelling or (
                projected.canonical_spelling.status is real.canonical_spelling.status
                and not real.canonical_spelling.is_present
            )
            assert projected.cv_qualification.value == real.cv_qualification.value


class TestCutoverGate:
    def _gate(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import semantic_ir_cutover

        return semantic_ir_cutover

    def test_registered_and_clean(self) -> None:
        gate = self._gate()
        from findings_report import Findings

        assert "variables" in {c.name for c in gate.MIGRATED_COHORTS}
        findings = Findings()
        gate.check_semantic_ir_cutover(findings)
        assert findings.errors == []

    @pytest.mark.parametrize(
        "source",
        [
            "x = v_old.type",
            "x = var.is_const",
            "x = snap.variable_map",
            "x = snap.declarations.variables",
            'x = getattr(v, "type")',
        ],
    )
    def test_fires_on_a_legacy_variable_read(self, source: str) -> None:
        forbidden = frozenset({"type", "is_const", "variables", "variable_map"})
        assert self._gate().legacy_collection_reads(ast.parse(source), forbidden)


#: Canonical spelling -> the same type with only the variable's own
#: top-level const removed (hand-written, not derived from the helper).
_TOP_LEVEL_CONST_CASES = {
    "int": "int",
    "const int": "int",
    "int const": "int",
    "int *const": "int *",
    "int * const": "int *",
    "const int *": "const int *",
    "int const *": "int const *",
    "const std::vector<int>": "std::vector<int>",
    "std::vector<int *> const": "std::vector<int *>",
    "const std::vector<int> *": "const std::vector<int> *",
    "void ( *const)(int *)": "void ( *)(int *)",
    "void ( *const volatile)()": "void ( *volatile)()",
    "void ( *volatile const)()": "void ( *volatile)()",
    "int ( *const)[5]": "int ( *)[5]",
    "void (C:: *)(int) const": "void (C:: *)(int) const",
    "void ( *)(int) const": "void ( *)(int) const",
}


@pytest.mark.parametrize(
    ("spelling", "expected"), sorted(_TOP_LEVEL_CONST_CASES.items())
)
def test_without_top_level_const(spelling: str, expected: str) -> None:
    from abicheck.compare.variables import _without_top_level_const

    assert _without_top_level_const(spelling) == expected


def test_facts_of_a_missing_entity_are_unknown() -> None:
    assert variable_type_facts(None) == (None, None)


def test_unestablished_spelling_is_recorded_as_declined() -> None:
    from abicheck.compare.declined_comparisons import declined_scope

    known = variable_canonical_entity(_var("int"), "castxml")
    unknown = variable_canonical_entity(_var("?"), "castxml")
    with declined_scope() as one_side:
        assert (
            variable_type_changes(
                "g", "g", ("?", "int"), unknown, known, entity_id=None
            )
            == []
        )
    with declined_scope() as both:
        assert (
            variable_type_changes(
                "g", "g", ("?", "?"), unknown, unknown, entity_id=None
            )
            == []
        )
    assert [d.reason for d in one_side] == [
        "variable type spelling not established on one side"
    ]
    assert [d.reason for d in both] == [
        "variable type spelling not established on both sides"
    ]


class TestDeclinedComparisonNotes:
    def _det(self, name: str, n: int):
        from types import SimpleNamespace

        return SimpleNamespace(
            name=name, declined=tuple((f"e{i}", "r") for i in range(n))
        )

    def test_one_note_per_declining_detector_in_order(self) -> None:
        from abicheck.policy.analysis_assurance_declined import (
            declined_comparison_notes,
        )

        notes = declined_comparison_notes(
            [self._det("a", 1), self._det("quiet", 0), self._det("b", 3)]
        )
        assert len(notes) == 2
        assert notes[0].startswith("detector 'a' declined 1 comparison:")
        assert notes[1].startswith("detector 'b' declined 3 comparisons:")

    def test_reaches_the_run_assurance_end_to_end(self) -> None:
        old = _snap([_var("?*")], with_ir=True)
        # Compatible structure (the ``?`` may be ``int``): a decline, not a
        # finding.
        new = _snap([_var("int *")], with_ir=True)
        notes = compare(old, new).analysis_assurance.notes
        assert any("'variables' declined 1 comparison" in n for n in notes)


@pytest.mark.parametrize(
    ("producer", "type_", "is_const", "expected"),
    [
        # Header-AST backends: the spelling is the evidence, is_const ignored.
        ("castxml", "int", True, ()),
        ("clang", "const int *", True, ()),
        # DWARF: the structural is_const fact, partial (no volatile fact).
        ("dwarf", "int", True, ("const",)),
        # Any other producer whose spelling names no qualifier: is_const.
        ("pdb", "int", True, ("const",)),
        ("", "int", False, ()),
        # ...but a spelling that names one is read structurally.
        ("pdb", "const int *", True, ()),
    ],
)
def test_cv_source_per_producer(producer, type_, is_const, expected) -> None:
    var = Variable(name="g", mangled="g", type=type_, is_const=is_const)
    cv = variable_canonical_entity(var, producer).cv_qualification
    assert cv.is_present and cv.value == expected
