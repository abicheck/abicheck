"""The edit axis: the model of edit classes, and which of them a run exercised.

The construct axis cannot tell two edits of one record apart: an operator
that appends a member and one that removes a member in padding both cover
``struct``. The edit axis splits ``struct``, ``inheritance`` and ``function``
into the classes ``abiforge.model.edit_model`` defines from the C/C++ layout
and declaration rules. The
model, not the operator registry, is the denominator: a class no operator
generates is an uncovered gap, reported in the coverage summary and the
ledger, never dropped.
"""

from __future__ import annotations

import ast
import json
from itertools import product
from pathlib import Path

import pytest

from abiforge import coverage as cov
from abiforge.model import edit_model as model, space
from abiforge.model.config import Toolchain, default_config
from abiforge.model.space import (
    ANY_EDIT,
    CONSTRUCTS,
    OP_EDIT_CLASS,
    Point,
    cell_points,
    coverage,
    edit_class,
    edit_values,
    infeasible,
    op_constructs,
)
from abiforge.operators import OP_BY_NAME, OPERATORS

GCC = Toolchain("gcc", 13, "gcc", "g++")
LAYOUT_TABLE = (
    Path(__file__).resolve().parents[1] / "formal" / "lean" / "layout_table.json"
)

# The model, written out here independently of both the model module's
# product and the operator table: (construct, class) pairs.
_MEMBER = {
    f"{op}_{pos}_{eff}"
    for op in ("insert", "remove")
    for pos in ("head", "middle", "tail")
    for eff in ("shifts", "absorbed")
} - {"insert_head_absorbed", "remove_head_absorbed"}
EXPECTED_STRUCT = _MEMBER | {
    "reorder",
    "retype_same_layout",
    "retype_grows",
    "retype_shrinks",
    "retype_align_only",
    "array_grow_shifts",
    "array_grow_absorbed",
    "array_shrink_shifts",
    "array_shrink_absorbed",
    "field_to_bitfield",
    "bitfield_to_field",
    "bitfield_width_absorbed",
    "bitfield_width_shifts",
    "packing_change",
    "record_align_change",
    "record_kind_change",
    "record_opaque",
    "record_defined",
    "record_removed",
    "record_added",
    "record_triviality_change",
    "member_rename",
    "pointee_rename",
    "member_cv_change",
    "member_access_change",
    "declaration_text_only",
}
EXPECTED_INHERITANCE = {
    "base_add_nonvirtual",
    "base_add_virtual",
    "base_remove",
    "base_reorder",
    "base_became_virtual",
    "base_became_nonvirtual",
    "base_layout_changed",
    "base_access_changed",
}
EXPECTED_FUNCTION = {
    "param_added",
    "param_removed",
    "param_retyped",
    "param_pointer_level",
    "param_pointee_cv",
    "param_renamed",
    "param_qualifier_change",
    "params_reordered",
    "default_argument_changed",
    "return_retyped",
    "return_pointer_level",
    "return_pointee_cv",
    "variadic_change",
    "calling_convention_change",
    "exception_spec_change",
    "attribute_change",
    "language_linkage_change",
    "became_inline",
    "became_out_of_line",
    "body_change",
    "overload_added",
    "function_added",
    "function_removed",
    "function_renamed",
    "export_declaration_mismatch",
    "binding_change",
    "visibility_change",
    "version_change",
    "symbol_type_change",
    "declaration_text_only",
}
SPLIT = {
    "struct": EXPECTED_STRUCT,
    "inheritance": EXPECTED_INHERITANCE,
    "function": EXPECTED_FUNCTION,
}


def _space_edits(construct: str, points) -> set[str]:  # type: ignore[no-untyped-def]
    return {p.edit for p in points if p.construct == construct}


# --- the model is independent of the generator --------------------------------


def test_the_model_is_the_independently_written_list() -> None:
    for con, want in SPLIT.items():
        assert set(model.model_classes(con)) == want, con
        # No class listed twice.
        assert len(model.model_classes(con)) == len(want), con
    assert set(model.EDIT_MODEL) == set(SPLIT)


def test_the_model_module_imports_nothing_from_the_generator() -> None:
    tree = ast.parse(Path(model.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {m for m in imported if m.startswith("tests")}, imported


@pytest.mark.parametrize("construct", sorted(SPLIT))
def test_every_model_class_enters_the_denominator(construct: str) -> None:
    assert _space_edits(construct, space.all_points()) == SPLIT[construct]
    assert _space_edits(construct, space.feasible_points()) == SPLIT[construct]
    rows = coverage((), top=0)["per_axis"]["edit"]  # type: ignore[index]
    for cls in SPLIT[construct]:
        assert rows[cls]["feasible"] > 0, cls


@pytest.mark.parametrize("construct", sorted(SPLIT))
def test_a_class_with_no_operator_is_not_dropped_from_the_denominator(
    construct: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove every operator mapping for the construct: the denominator must
    not shrink, and every one of its classes must turn into a reported gap."""
    before = _space_edits(construct, space.feasible_points())
    n_before = sum(1 for p in space.feasible_points() if p.construct == construct)
    monkeypatch.setitem(space.OP_EDIT_CLASS, construct, {})
    after = space.feasible_points()
    assert _space_edits(construct, after) == before == SPLIT[construct]
    assert sum(1 for p in after if p.construct == construct) == n_before
    gaps = {g for g in space.edit_classes_without_operator() if g.startswith(construct)}
    assert gaps == {f"{construct}/{c}" for c in SPLIT[construct]}
    rep = coverage((), top=0)
    assert rep["per_axis"]["edit"].keys() >= SPLIT[construct]  # type: ignore[index]


def test_non_split_constructs_have_the_single_any_class() -> None:
    for con in CONSTRUCTS:
        if con.name in SPLIT:
            continue
        assert edit_values(con.name) == (ANY_EDIT,), con.name


# --- the operator -> class mapping ---------------------------------------------


def test_the_operator_mapping_names_only_model_classes_and_real_operators() -> None:
    for con, ops in OP_EDIT_CLASS.items():
        assert con in SPLIT, (
            f"OP_EDIT_CLASS maps a construct the model does not split: {con}"
        )
        for op_name, cls in ops.items():
            assert cls in SPLIT[con], f"{op_name} -> {con}/{cls}: not a model class"
            assert op_name in OP_BY_NAME, f"OP_EDIT_CLASS names no operator: {op_name}"
            assert con in op_constructs(OP_BY_NAME[op_name]), (op_name, con)
            assert edit_class(OP_BY_NAME[op_name], con) == cls


def test_every_operator_of_a_split_construct_is_placed() -> None:
    unplaced = [
        (op.name, con)
        for op in OPERATORS
        for con in op_constructs(op)
        if con in SPLIT and op.name not in OP_EDIT_CLASS.get(con, {})
    ]
    assert not unplaced, f"place these operators in OP_EDIT_CLASS: {unplaced}"


def test_an_unplaced_operator_of_a_split_construct_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(space.OP_EDIT_CLASS, "struct", {})
    with pytest.raises(KeyError, match="has no edit class"):
        edit_class(OP_BY_NAME["struct_append_field"], "struct")


def test_gaps_are_the_model_classes_no_operator_maps_to() -> None:
    mapped = {(c, cls) for c, ops in OP_EDIT_CLASS.items() for cls in ops.values()}
    want = sorted(
        f"{con}/{cls}"
        for con, classes in SPLIT.items()
        for cls in classes
        if (con, cls) not in mapped
    )
    assert sorted(space.edit_classes_without_operator()) == want
    # Every model class has an operator (the layout operators closed the last
    # 19 gaps); a class added to the model without one shows up in ``want``.


# --- the gaps are visible -------------------------------------------------------


def test_the_ledger_lists_every_gap() -> None:
    ledger = cov.load_ledger()
    assert ledger["edit_classes_without_operator"] == list(
        space.edit_classes_without_operator()
    ), "regenerate: python -m abiforge.coverage ledger --write"


def test_the_summary_names_every_gap() -> None:
    rep = cov.report([])
    text = cov.summary_markdown(rep)
    for gap in space.edit_classes_without_operator():
        assert f"| `{gap}` | **none (gap)** |" in text, gap
    n = sum(len(c) for c in SPLIT.values())
    n_gaps = len(space.edit_classes_without_operator())
    assert (
        f"Edit classes in the model (`abiforge/model/edit_model.py`): **{n}**" in text
    )
    assert f"with none (uncovered gaps, still in the denominator): **{n_gaps}**" in text


# --- the member-edit product agrees with the Lean layout table ------------------


def test_the_impossible_member_edits_are_exactly_what_the_layout_table_never_shows() -> (
    None
):
    """Each (position, effect) the model keeps is realised by some row of the
    Lean layout table, and each it excludes by none. A removal is the same row
    read backwards, so this checks insert and remove at once."""
    rows = json.loads(LAYOUT_TABLE.read_text(encoding="utf-8"))["rows"]
    seen: set[tuple[str, str]] = set()
    for r in rows:
        k, n = r["insert_at"], len(r["base"])
        pos = "head" if k == 0 else "tail" if k == n else "middle"
        others = r["new_offsets"][:k] + r["new_offsets"][k + 1 :]
        moved = others != r["old_offsets"] or r["old_size"] != r["new_size"]
        seen.add((pos, "shifts" if moved else "absorbed"))
    every = set(product(model.MEMBER_POSITIONS, model.LAYOUT_EFFECTS))
    assert seen == every - set(model.IMPOSSIBLE_MEMBER_EDITS)


# --- points ----------------------------------------------------------------------


def test_a_point_on_an_edit_value_its_construct_lacks_is_infeasible() -> None:
    fn_split = Point(
        "function", "c", "c17", "baseline", "gcc", "gcc", "headers", "retype_grows"
    )
    assert infeasible(fn_split) == "retype_grows is not an edit sub-class of function"
    struct_any = Point("struct", "c", "c17", "baseline", "gcc", "gcc", "headers")
    assert infeasible(struct_any) == "any is not an edit sub-class of struct"


def test_a_padding_insertion_exercises_only_its_own_edit_class() -> None:
    op = OP_BY_NAME["field_inserted_in_padding"]
    cfg = default_config("c++", GCC)
    ran = frozenset(c.id for c in cfg.consumer_set)
    points = cell_points(op, cfg, ran)
    assert points, "the cell exercised no point"
    assert {p.construct for p in points} == {"struct"}
    assert {p.edit for p in points} == {"insert_middle_absorbed"}


def test_coverage_counts_the_edit_class_that_ran() -> None:
    op = OP_BY_NAME["field_removed_without_shrink"]
    cfg = default_config("c++", GCC)
    ran = frozenset(c.id for c in cfg.consumer_set)
    covered = cell_points(op, cfg, ran)
    rep = coverage(covered, top=1)
    rows = rep["per_axis"]["edit"]  # type: ignore[index]
    assert set(rows) == EXPECTED_STRUCT | EXPECTED_INHERITANCE | EXPECTED_FUNCTION | {
        ANY_EDIT
    }
    assert rows["remove_middle_absorbed"]["covered"] == len(covered)
    for cls in (EXPECTED_STRUCT | EXPECTED_INHERITANCE | EXPECTED_FUNCTION) - {
        "remove_middle_absorbed"
    }:
        assert rows[cls]["covered"] == 0, cls
    per_class = rep["edit_classes"]["struct"]["remove_middle_absorbed"]  # type: ignore[index]
    assert per_class["covered"] == len(covered)
    assert per_class["operators"] == ["field_removed_without_shrink"]


def test_a_pre_edit_axis_point_id_is_refused_not_guessed() -> None:
    from abiforge.model.space import AXES, point_from_id

    current = "/".join(f"v{i}" for i in range(len(AXES)))
    legacy = current.rsplit("/", 1)[0]
    assert point_from_id(current).edit == f"v{len(AXES) - 1}"
    with pytest.raises(ValueError, match="predates the edit axis"):
        point_from_id(legacy)
    with pytest.raises(ValueError, match="not a space point id"):
        point_from_id("a/b")


def test_construct_placements_name_real_operators_and_constructs() -> None:
    names = {c.name for c in CONSTRUCTS}
    for op_name, cons in space.OP_CONSTRUCT.items():
        assert op_name in OP_BY_NAME, op_name
        assert set(cons) <= names, (op_name, cons)
        assert op_constructs(OP_BY_NAME[op_name]) == cons, op_name


def test_function_edits_name_the_signature_part_they_change() -> None:
    """Spot-check placements against the edit each operator's text makes."""
    want = {
        "func_param_append": "param_added",
        "params_reordered": "params_reordered",
        "func_became_extern_c": "language_linkage_change",
        "func_became_weak": "binding_change",
        "comment_only": "declaration_text_only",
    }
    for op_name, cls in want.items():
        assert edit_class(OP_BY_NAME[op_name], "function") == cls, op_name


@pytest.mark.parametrize(
    "edit",
    [
        "default_argument_changed",
        "exception_spec_change",
        "language_linkage_change",
        "overload_added",
    ],
)
def test_a_cxx_only_function_edit_has_no_c_point(edit: str) -> None:
    """C has no default arguments, exception specifications, language linkage
    or overloads: such a point is infeasible in C and feasible in C++."""
    c_points = {
        p
        for p in space.feasible_points()
        if p.construct == "function" and p.lang == "c"
    }
    cxx_points = {
        p
        for p in space.feasible_points()
        if p.construct == "function" and p.lang == "c++"
    }
    assert edit not in {p.edit for p in c_points}
    assert edit in {p.edit for p in cxx_points}
    # Every other function class keeps its C points.
    assert {p.edit for p in c_points} == EXPECTED_FUNCTION - {
        "default_argument_changed",
        "exception_spec_change",
        "language_linkage_change",
        "overload_added",
    }
