"""``diff_symbols._check_removed_function``: the finding it builds.

Pins the whole contract of one removed-or-hidden function's finding -- its
kind, its description, and the split export facts it carries for the side
it is about -- with each expectation derived from the facts this test
constructs, never from the module's own helpers. Before this file the
``surface_facts`` payload, the description's visibility label, the
"still declared but no longer exported" guard and the ELF-only exception
were all executed by the suite but asserted by nothing (PR #1519's
mutation gate).
"""

from __future__ import annotations

import pytest

from abicheck.diff_symbols import _check_removed_function
from abicheck.model import Function, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.fact import Fact

_T, _F = Fact.present(True), Fact.present(False)


def _fn(
    *, exported: Fact[bool] | None, vis: Visibility = Visibility.PUBLIC, **kw: object
) -> Function:
    return Function(
        name="foo",
        mangled="_Z3foov",
        return_type="void",
        visibility=vis,
        declared_in_headers_fact=kw.pop("declared", _T),  # type: ignore[arg-type]
        in_public_contract_fact=kw.pop("contract", _T),  # type: ignore[arg-type]
        binary_exported_fact=exported,
        **kw,  # type: ignore[arg-type]
    )


def _tri(fact: Fact[bool] | None) -> str:
    return "unknown" if fact is None else ("true" if fact.value else "false")


def test_hidden_on_the_new_side_reports_the_new_sides_facts() -> None:
    old = _fn(exported=_T)
    new = _fn(exported=_F, vis=Visibility.HIDDEN, contract=_F)
    change = _check_removed_function("_Z3foov", old, {"_Z3foov": new}, False)
    assert change.kind == ChangeKind.FUNC_VISIBILITY_CHANGED
    assert change.old_value == "public" and change.new_value == "hidden"
    # The facts are the *new* side's: declared, out of the contract, unexported.
    assert change.surface_facts == {
        "declared_in_headers": "true",
        "in_public_contract": "false",
        "binary_exported": "false",
    }


@pytest.mark.parametrize("still_exported", [_T, None])
def test_a_new_side_record_that_is_not_confirmed_unexported_is_a_removal(
    still_exported: Fact[bool] | None,
) -> None:
    """Only a confirmed-absent export makes it a visibility change; a record
    that is still (or not known not to be) exported is not that finding."""
    old = _fn(exported=_T)
    new = _fn(exported=still_exported)
    change = _check_removed_function("_Z3foov", old, {"_Z3foov": new}, False)
    assert change.kind != ChangeKind.FUNC_VISIBILITY_CHANGED


def test_export_table_only_record_in_elf_only_mode_is_a_removal_even_if_hidden() -> (
    None
):
    old = _fn(exported=_T, vis=Visibility.ELF_ONLY, declared=None, contract=None)
    new = _fn(exported=_F, vis=Visibility.HIDDEN)
    change = _check_removed_function("_Z3foov", old, {"_Z3foov": new}, True)
    assert change.kind == ChangeKind.FUNC_REMOVED_ELF_ONLY


@pytest.mark.parametrize(
    ("exported", "vis", "label"),
    [(_T, Visibility.PUBLIC, "Public"), (_T, Visibility.ELF_ONLY, "Elf_only")],
)
def test_removal_reports_the_old_sides_facts_and_visibility(
    exported: Fact[bool] | None, vis: Visibility, label: str
) -> None:
    old = _fn(exported=exported, vis=vis)
    change = _check_removed_function("_Z3foov", old, {}, False)
    assert change.kind == ChangeKind.FUNC_REMOVED
    assert change.description.startswith(f"{label} function removed: foo")
    assert change.surface_facts == {
        "declared_in_headers": "true",
        "in_public_contract": "true",
        "binary_exported": _tri(exported),
    }
