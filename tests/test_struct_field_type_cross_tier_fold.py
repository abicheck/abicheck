"""The DWARF ``struct_field_type_changed`` folds into the AST lane's twin.

Bug class: one member retype was reported twice -- the DWARF layout diff's
``struct_field_type_changed`` beside the header lane's finding -- in two
shapes the edit corpus found: a *union* member (the AST kind is
``union_field_type_changed``, which the fold table did not list, and which
carried no ``field_name``), and an array member under the native gcc header
backend (``char [8]`` there, ``char[8]`` in DWARF). Oracle, on the inputs:
the DWARF finding is dropped iff an AST finding of an equivalent kind names
the same record and field with the same transition, spelling aside; a
genuinely different transition keeps both.
"""

from __future__ import annotations

import pytest

from abicheck.compare.finding_dedup import deduplicate_ast_dwarf
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind


def _dwarf(old: str, new: str) -> Change:
    return Change(
        kind=ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        symbol="rec::tag",
        description="dwarf",
        field_name="tag",
        old_value=old,
        new_value=new,
    )


def _ast(kind: ChangeKind, old: str, new: str) -> Change:
    return Change(
        kind=kind,
        symbol="rec",
        description="ast",
        field_name="tag",
        old_value=old,
        new_value=new,
    )


@pytest.mark.parametrize(
    "ast_kind",
    [ChangeKind.TYPE_FIELD_TYPE_CHANGED, ChangeKind.UNION_FIELD_TYPE_CHANGED],
)
@pytest.mark.parametrize(
    ("dwarf_t", "ast_t", "folds"),
    [
        (("float", "double"), ("float", "double"), True),
        (("char[8]", "char[16]"), ("char [8]", "char [16]"), True),
        (("char[8]", "char[16]"), ("char [ 8 ]", "char [ 16 ]"), True),
        (("char[8]", "char[16]"), ("char [8]", "char [32]"), False),
        (("float", "double"), ("float", "long double"), False),
    ],
)
def test_dwarf_twin_folds_iff_the_ast_finding_states_the_same_transition(
    ast_kind: ChangeKind,
    dwarf_t: tuple[str, str],
    ast_t: tuple[str, str],
    folds: bool,
) -> None:
    dwarf, ast = _dwarf(*dwarf_t), _ast(ast_kind, *ast_t)
    kept = deduplicate_ast_dwarf([dwarf, ast])
    assert ast in kept
    assert (dwarf not in kept) is folds
