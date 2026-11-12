"""The edit axis: which record-layout edit sub-classes a run exercised.

The construct axis cannot tell two edits of one record apart: an operator
that appends a member and one that removes a member in padding both cover
``struct``. The edit axis splits ``struct`` into the layout sub-classes
(``STRUCT_EDIT_CLASSES``) so coverage reports which of them ran.
"""

from __future__ import annotations

import pytest

from tests._edit_corpus_config import Toolchain, default_config
from tests._edit_corpus_operators import OP_BY_NAME, OPERATORS
from tests._edit_corpus_space import (
    ANY_EDIT,
    CONSTRUCTS,
    LAYOUT_EDIT_CLASS,
    STRUCT_EDIT_CLASSES,
    Point,
    cell_points,
    coverage,
    edit_class,
    edit_values,
    infeasible,
    op_constructs,
)

GCC = Toolchain("gcc", 13, "gcc", "g++")


def _struct_ops() -> list[str]:
    return [op.name for op in OPERATORS if "struct" in op_constructs(op)]


def test_every_layout_operator_has_exactly_one_edit_class() -> None:
    names = set(OP_BY_NAME)
    for op_name, cls in LAYOUT_EDIT_CLASS.items():
        assert op_name in names, f"LAYOUT_EDIT_CLASS names no operator: {op_name}"
        assert cls in STRUCT_EDIT_CLASSES, (op_name, cls)
        # One class per operator: the mapping is a dict, and the axis reads it
        # back for the struct construct to the same single value.
        assert edit_class(OP_BY_NAME[op_name], "struct") == cls


def test_every_struct_operator_maps_into_the_struct_edit_classes() -> None:
    for name in _struct_ops():
        assert edit_class(OP_BY_NAME[name], "struct") in STRUCT_EDIT_CLASSES, name


@pytest.mark.parametrize("cls", STRUCT_EDIT_CLASSES)
def test_every_struct_edit_class_has_an_operator(cls: str) -> None:
    members = [
        name for name in _struct_ops() if edit_class(OP_BY_NAME[name], "struct") == cls
    ]
    assert members, f"edit sub-class {cls!r} has no operator"


def test_non_struct_constructs_are_not_split() -> None:
    for con in CONSTRUCTS:
        if con.name == "struct":
            continue
        assert edit_values(con.name) == (ANY_EDIT,), con.name


def test_a_point_on_an_edit_value_its_construct_lacks_is_infeasible() -> None:
    fn_split = Point(
        "function", "c", "c17", "baseline", "gcc", "gcc", "headers", "widen"
    )
    assert infeasible(fn_split) == "widen is not an edit sub-class of function"
    struct_any = Point("struct", "c", "c17", "baseline", "gcc", "gcc", "headers")
    assert infeasible(struct_any) == "any is not an edit sub-class of struct"


def test_a_padding_insertion_exercises_only_its_own_edit_class() -> None:
    op = OP_BY_NAME["field_inserted_in_padding"]
    cfg = default_config("c++", GCC)
    ran = frozenset(c.id for c in cfg.consumer_set)
    points = cell_points(op, cfg, ran)
    assert points, "the cell exercised no point"
    assert {p.construct for p in points} == {"struct"}
    assert {p.edit for p in points} == {"insert_absorbed"}


def test_coverage_counts_the_edit_sub_class_that_ran() -> None:
    op = OP_BY_NAME["field_removed_without_shrink"]
    cfg = default_config("c++", GCC)
    ran = frozenset(c.id for c in cfg.consumer_set)
    covered = cell_points(op, cfg, ran)
    rep = coverage(covered, top=1)
    rows = rep["per_axis"]["edit"]  # type: ignore[index]
    assert set(rows) == set(STRUCT_EDIT_CLASSES) | {ANY_EDIT}
    assert rows["remove_absorbed"]["covered"] == len(covered)
    for cls in set(STRUCT_EDIT_CLASSES) - {"remove_absorbed"}:
        assert rows[cls]["covered"] == 0, cls


def test_a_pre_edit_axis_point_id_is_refused_not_guessed() -> None:
    from tests._edit_corpus_space import AXES, point_from_id

    current = "/".join(f"v{i}" for i in range(len(AXES)))
    legacy = current.rsplit("/", 1)[0]
    assert point_from_id(current).edit == f"v{len(AXES) - 1}"
    with pytest.raises(ValueError, match="predates the edit axis"):
        point_from_id(legacy)
    with pytest.raises(ValueError, match="not a space point id"):
        point_from_id("a/b")
