# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The declared-vs-exported contract, stated as invariants over the whole
input space rather than as one reproducer.

Bug class ``evidence.independent_facts_conflated_into_one_signal``
(``tests/regressions/manifest.py``). Two parts:

1. **Primitive-level property tests** for the selection primitive
   (``model/declaration_surface.py``), per AGENTS.md's "Primitive-level
   property tests" section: determinism, order-independence,
   side-symmetry, axis-independence, and -- the one that matters -- no
   negative answer from absent evidence.
2. **The quadrant table**, exhaustively: all 81 combinations of
   (declared: yes/no/unknown) x (exported: yes/no/unknown) on the old side
   crossed with the same on the new side, run through the *real* detectors,
   checked against a literal matrix written out below by hand. The oracle
   is that matrix -- not ``source_removal_supported``/``export_only_loss``,
   which are the code under test.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_namespaces import detect_experimental_namespace_changes
from abicheck.diff_symbols import _diff_functions
from abicheck.model import (
    AbiSnapshot,
    Fact,
    Function,
    ScopeOrigin,
    SurfaceAnswer,
    Variable,
    Visibility,
    declared_in_source,
    dynamically_exported,
    export_only_loss,
    in_exported_public_api,
    in_source_surface,
    source_removal_supported,
)

# --------------------------------------------------------------------------
# The nine cells, and the fixtures that realize them
# --------------------------------------------------------------------------

#: Every way a producer can leave a fact: a real answer either way, or one
#: of the five non-answers ``Fact`` distinguishes. All five must behave
#: identically -- "we could not tell" is one quadrant, however it arose.
_NO_EVIDENCE_FACTS = (
    None,
    Fact.not_collected(),
    Fact.unsupported(),
    Fact.failed("boom"),
    Fact.not_applicable(),
    Fact.present(None),  # PRESENT but valueless: still not an answer
)

_CELLS = tuple(
    itertools.product(
        (SurfaceAnswer.YES, SurfaceAnswer.NO, SurfaceAnswer.UNKNOWN), repeat=2
    )
)


def _fact(answer: SurfaceAnswer) -> Fact[bool] | None:
    if answer is SurfaceAnswer.YES:
        return Fact.present(True)
    if answer is SurfaceAnswer.NO:
        return Fact.present(False)
    return Fact.not_collected()


def _legacy_visibility(declared: SurfaceAnswer, exported: SurfaceAnswer) -> Visibility:
    """The ``Visibility`` a producer would have recorded for this cell.

    Written out rather than derived from the code under test, because the
    whole point of the fix is that this *conflation* is what the enum
    always was: exported and declared -> ``PUBLIC``; exported without a
    declaration -> ``ELF_ONLY``; anything else -> ``HIDDEN``, with the one
    documented fallback (no export table observed at all -> ``PUBLIC``,
    i.e. "no contrary evidence").
    """
    if exported is SurfaceAnswer.YES:
        return (
            Visibility.ELF_ONLY if declared is SurfaceAnswer.NO else Visibility.PUBLIC
        )
    if exported is SurfaceAnswer.UNKNOWN and declared is not SurfaceAnswer.NO:
        return Visibility.PUBLIC
    return Visibility.HIDDEN


def _function(
    declared: SurfaceAnswer,
    exported: SurfaceAnswer,
    *,
    name: str = "lib::experimental::widget",
    mangled: str = "_ZN3lib12experimental6widgetEv",
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=_legacy_visibility(declared, exported),
        declared_fact=_fact(declared),
        exported_fact=_fact(exported),
    )


def _snapshot(fn: Function) -> AbiSnapshot:
    return AbiSnapshot(library="libx.so", version="1", functions=[fn])


# --------------------------------------------------------------------------
# Part 1 -- primitive-level properties
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fact", _NO_EVIDENCE_FACTS)
@pytest.mark.parametrize("axis", ["declared", "exported"])
def test_absent_evidence_is_never_a_negative_answer(fact, axis):
    """The invariant the whole fix rests on, on both axes and for every
    shape of "no evidence" -- including the two a naive ``if fact:`` /
    ``fact.value_or(False)`` reader would silently turn into ``False``."""
    fn = Function(name="f", mangled="f", return_type="void", **{f"{axis}_fact": fact})
    reader = declared_in_source if axis == "declared" else dynamically_exported
    assert reader(fn) is SurfaceAnswer.UNKNOWN


@pytest.mark.parametrize("declared,exported", _CELLS)
def test_each_axis_ignores_the_other(declared, exported):
    """Neither fact may be inferred from the other: the declared answer is
    identical across all three export states, and vice versa."""
    fn = _function(declared, exported)
    assert declared_in_source(fn) is declared
    assert dynamically_exported(fn) is exported


@pytest.mark.parametrize("declared,exported", _CELLS)
def test_variable_and_function_answer_identically(declared, exported):
    """Side-symmetry across the two declaration kinds: one contract, not
    two implementations that can drift."""
    fn = _function(declared, exported)
    var = Variable(
        name="g",
        mangled="g",
        type="int",
        visibility=_legacy_visibility(declared, exported),
        declared_fact=_fact(declared),
        exported_fact=_fact(exported),
    )
    assert declared_in_source(var) is declared_in_source(fn)
    assert dynamically_exported(var) is dynamically_exported(fn)
    assert in_source_surface(var) == in_source_surface(fn)
    assert in_exported_public_api(var) == in_exported_public_api(fn)


@pytest.mark.parametrize("declared,exported", _CELLS)
def test_selection_is_deterministic_and_order_independent(declared, exported):
    """Collecting the source surface out of a list may not depend on how
    the list is ordered, nor on how many neighbours a declaration has."""
    subject = _function(declared, exported)
    others = [
        _function(d, e, name=f"lib::other{i}", mangled=f"_ZN3lib6other{i}Ev")
        for i, (d, e) in enumerate(_CELLS)
    ]
    alone = in_source_surface(subject)
    for arrangement in ([subject, *others], [*others, subject], [*reversed(others), subject]):
        selected = [f for f in arrangement if in_source_surface(f)]
        assert (subject in selected) is alone


@pytest.mark.parametrize("declared,exported", _CELLS)
def test_legacy_proxy_is_used_exactly_when_there_is_no_evidence(declared, exported):
    """``in_source_surface`` degrades to the pre-fix predicate on a
    snapshot with no declaration evidence, and never consults it when
    there is -- which is what makes a pre-v46 snapshot unchanged."""
    fn = _function(declared, exported)
    if declared is SurfaceAnswer.UNKNOWN:
        assert in_source_surface(fn) == (fn.visibility == Visibility.PUBLIC)
    elif declared is SurfaceAnswer.YES:
        assert in_source_surface(fn) is True
    else:
        assert in_source_surface(fn) is False


@pytest.mark.parametrize(
    "origin", [ScopeOrigin.PRIVATE_HEADER, ScopeOrigin.SYSTEM_HEADER, ScopeOrigin.GENERATED]
)
def test_a_declaration_outside_the_public_header_set_is_not_source_surface(origin):
    fn = _function(SurfaceAnswer.YES, SurfaceAnswer.YES)
    fn.origin = origin
    assert in_source_surface(fn) is False


@pytest.mark.parametrize("declared,exported", _CELLS)
def test_exported_public_api_matches_the_legacy_predicate_everywhere(
    declared, exported
):
    """``in_exported_public_api`` is a *renaming*, not a behaviour change:
    it must agree with ``visibility == Visibility.PUBLIC`` on every cell,
    so the 30-odd guard sites moved onto it cannot shift under anyone."""
    fn = _function(declared, exported)
    assert in_exported_public_api(fn) == (fn.visibility == Visibility.PUBLIC)


def test_a_missing_new_side_never_produces_an_export_event():
    old = _function(SurfaceAnswer.YES, SurfaceAnswer.YES)
    assert source_removal_supported(None) is True
    assert export_only_loss(old, None) is False


# --------------------------------------------------------------------------
# Part 2 -- the quadrant table, exhaustively, through the real detectors
# --------------------------------------------------------------------------

#: Hand-written oracle. Rows are the OLD side's cell, columns the NEW
#: side's, both in ``_CELLS`` order:
#:
#:     (Y,Y) (Y,N) (Y,U) (N,Y) (N,N) (N,U) (U,Y) (U,N) (U,U)
#:
#: where the first letter is *declared* and the second *exported*.
#: ``S`` = a source-removal event is emitted, ``X`` = an export-axis event
#: is emitted, ``-`` = neither. The two are mutually exclusive by
#: construction: an export event asserts the new side still declares it,
#: a source removal asserts it does not.
#:
#: Reading of the table, stated independently of the implementation:
#:   * ``S`` exactly where the OLD side is in the source surface and the
#:     NEW side is not. Note the two ways the new side can be out: a
#:     positive "not declared" (the ``N`` columns) and a no-evidence cell
#:     whose legacy proxy already said non-public (``(U,N)``) -- the
#:     latter is unchanged pre-fix behaviour, not a new claim.
#:   * ``X`` exactly in the ``(Y,N)`` column (still declared, no longer
#:     exported) and only from a row whose export was positively observed.
#:   * every cell whose deciding fact is unknown on the side that decides
#:     it is ``-``: no event is invented from absent evidence.
_ORACLE = (
    #        (Y,Y) (Y,N) (Y,U) (N,Y) (N,N) (N,U) (U,Y) (U,N) (U,U)
    ("(Y,Y)", "-", "X", "-", "S", "S", "S", "-", "S", "-"),
    ("(Y,N)", "-", "-", "-", "S", "S", "S", "-", "S", "-"),
    ("(Y,U)", "-", "-", "-", "S", "S", "S", "-", "S", "-"),
    ("(N,Y)", "-", "X", "-", "-", "-", "-", "-", "-", "-"),
    ("(N,N)", "-", "-", "-", "-", "-", "-", "-", "-", "-"),
    ("(N,U)", "-", "-", "-", "-", "-", "-", "-", "-", "-"),
    ("(U,Y)", "-", "X", "-", "S", "S", "S", "-", "S", "-"),
    ("(U,N)", "-", "-", "-", "-", "-", "-", "-", "-", "-"),
    ("(U,U)", "-", "-", "-", "S", "S", "S", "-", "S", "-"),
)

_SOURCE_REMOVAL_KINDS = {ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT}
_EXPORT_AXIS_KINDS = {
    ChangeKind.FUNC_EXPORT_REMOVED_STILL_DECLARED,
    ChangeKind.VAR_EXPORT_REMOVED_STILL_DECLARED,
}


def _label(old_cell, new_cell) -> str:
    old = _snapshot(_function(*old_cell))
    new = _snapshot(_function(*new_cell))
    kinds = {c.kind for c in detect_experimental_namespace_changes(old, new)}
    kinds |= {c.kind for c in _diff_functions(old, new)}
    source = bool(kinds & _SOURCE_REMOVAL_KINDS)
    export = bool(kinds & _EXPORT_AXIS_KINDS)
    assert not (source and export), (
        "a source removal and an export-axis event are mutually exclusive"
    )
    return "S" if source else "X" if export else "-"


@pytest.mark.parametrize("row_index,old_cell", list(enumerate(_CELLS)))
def test_quadrant_table(row_index, old_cell):
    row = _ORACLE[row_index]
    assert row[0] == f"({old_cell[0].name[0]},{old_cell[1].name[0]})", (
        "oracle row order drifted from _CELLS"
    )
    for column_index, new_cell in enumerate(_CELLS):
        assert _label(old_cell, new_cell) == row[column_index + 1], (
            f"old={old_cell} new={new_cell}"
        )


def test_both_sides_unknown_emits_neither_event():
    """Called out separately because it is the load-bearing case: a
    pre-v46 snapshot on both sides must not gain (or lose) an event."""
    unknown = (SurfaceAnswer.UNKNOWN, SurfaceAnswer.UNKNOWN)
    assert _label(unknown, unknown) == "-"


def test_the_reproducing_defect_is_not_a_source_removal():
    """Identical headers, export dropped: declared/exported both observed."""
    assert _label(
        (SurfaceAnswer.YES, SurfaceAnswer.YES), (SurfaceAnswer.YES, SurfaceAnswer.NO)
    ) == "X"
