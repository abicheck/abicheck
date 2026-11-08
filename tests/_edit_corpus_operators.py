"""Semantic edit operators for the generated ABI corpus.

Each operator is one source-level edit with the ground truth it must produce,
declared *from the edit's meaning* (C/C++ ABI rules), never from what abicheck
currently reports. ``expected`` ChangeKinds must all appear; each ``any_of``
group needs at least one member; the verdict must be no weaker than
``verdict_min`` and, for benign edits, no stronger than ``verdict_max``.

An operator applies to a seed when the seed provides every role in
``requires``. ``edit`` receives the seed's current role texts and returns the
roles it changes, as ``(header_text, source_text)`` pairs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from tests._edit_corpus_seeds import SEEDS, RoleText, Seed

Roles = Mapping[str, RoleText]
Edit = Callable[[Roles], dict[str, RoleText]]

# Verdict names in increasing severity (see abicheck.checker_types.Verdict).
VERDICT_RANK: dict[str, int] = {
    "NO_CHANGE": 0,
    "COMPATIBLE": 1,
    "COMPATIBLE_WITH_RISK": 2,
    "API_BREAK": 3,
    "BREAKING": 4,
}


@dataclass(frozen=True)
class EditOp:
    name: str
    requires: tuple[str, ...]
    edit: Edit
    why: str
    expected: frozenset[str] = frozenset()
    any_of: tuple[frozenset[str], ...] = ()
    verdict_min: str = "BREAKING"
    verdict_max: str | None = None
    # Roles this op re-spells; used only to check the edit actually changes text.
    touches: tuple[str, ...] = field(default=())

    def applies_to(self, seed: Seed) -> bool:
        return all(r in seed.roles for r in self.requires)


def _set(
    roles: Roles, role: str, header: str | None = None, source: str | None = None
) -> dict[str, RoleText]:
    """Replace one role's header and/or source text, keeping the other side."""
    old_h, old_c = roles[role]
    return {
        role: (old_h if header is None else header, old_c if source is None else source)
    }


def _merge(*parts: dict[str, RoleText]) -> dict[str, RoleText]:
    out: dict[str, RoleText] = {}
    for p in parts:
        out.update(p)
    return out


# --- struct / layout -------------------------------------------------------


def _struct_append_field(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "struct.rec",
        "struct rec { int id; double weight; char tag[8]; long extra; };",
    )


def _struct_remove_trailing_field(r: Roles) -> dict[str, RoleText]:
    return _set(r, "struct.rec", "struct rec { int id; double weight; };")


def _struct_swap_fields(r: Roles) -> dict[str, RoleText]:
    return _set(r, "struct.point", "struct point { int y; int x; };")


def _struct_field_widen(r: Roles) -> dict[str, RoleText]:
    return _set(r, "struct.point", "struct point { long x; int y; };")


def _struct_array_grow(r: Roles) -> dict[str, RoleText]:
    return _set(r, "struct.rec", "struct rec { int id; double weight; char tag[16]; };")


# --- enums -----------------------------------------------------------------


def _enum_append_member(r: Roles) -> dict[str, RoleText]:
    return _set(r, "enum.color", "enum color { RED, GREEN, BLUE, ALPHA };")


def _enum_remove_member(r: Roles) -> dict[str, RoleText]:
    return _set(r, "enum.color", "enum color { RED, GREEN };")


def _enum_change_value(r: Roles) -> dict[str, RoleText]:
    return _set(r, "enum.color", "enum color { RED, GREEN = 10, BLUE };")


def _enum_class_append(r: Roles) -> dict[str, RoleText]:
    return _set(r, "enum.mode", "enum class mode { fast, slow, turbo };")


def _enum_class_remove(r: Roles) -> dict[str, RoleText]:
    return _set(r, "enum.mode", "enum class mode { fast };")


def _enum_class_change_value(r: Roles) -> dict[str, RoleText]:
    return _set(r, "enum.mode", "enum class mode { fast, slow = 5 };")


# --- typedefs --------------------------------------------------------------


def _typedef_retarget(r: Roles) -> dict[str, RoleText]:
    return _set(r, "typedef.handle", "typedef long handle_t;")


# --- functions -------------------------------------------------------------


def _func_param_type_change(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "func.add",
        "int add_ints(long a, int b);",
        "int add_ints(long a, int b) { return a + b; }",
    )


def _func_param_append(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "func.add",
        "int add_ints(int a, int b, int c);",
        "int add_ints(int a, int b, int c) { return a + b + c; }",
    )


def _func_param_remove(r: Roles) -> dict[str, RoleText]:
    return _set(
        r, "func.add", "int add_ints(int a);", "int add_ints(int a) { return a; }"
    )


def _func_return_type_change(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "func.count",
        "long get_count(void);",
        "long get_count(void) { return g_counter; }",
    )


def _func_remove(r: Roles) -> dict[str, RoleText]:
    return _set(r, "func.count", "", "")


def _func_add(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "func.make",
        "struct rec make_rec(int id);\nint add3(int a);",
        "struct rec make_rec(int id) { struct rec r = {0}; r.id = id; return r; }\n"
        "int add3(int a) { return a + 3; }",
    )


def _func_out_of_line_to_inline(r: Roles) -> dict[str, RoleText]:
    return _set(r, "func.twice", "inline int twice(int x) { return 2 * x; }", "")


def _func_param_rename_header_only(r: Roles) -> dict[str, RoleText]:
    return _set(r, "func.add", "int add_ints(int x, int y);", None)


# --- variables -------------------------------------------------------------


def _var_type_change(r: Roles) -> dict[str, RoleText]:
    return _set(r, "var.counter", "extern long g_counter;", "long g_counter = 0;")


# --- benign edits (must not be reported as breaking) -----------------------


def _comment_only(r: Roles) -> dict[str, RoleText]:
    return _set(
        r, "func.scale", "double scale(struct point p, double f); /* scaled */", None
    )


def _reorder_declarations(r: Roles) -> dict[str, RoleText]:
    add_h, _ = r["func.add"]
    scale_h, _ = r["func.scale"]
    return _merge(
        _set(r, "func.add", scale_h, None), _set(r, "func.scale", add_h, None)
    )


def _static_inline_helper(r: Roles) -> dict[str, RoleText]:
    h, _ = r["func.handle"]
    return _set(
        r, "func.handle", h + "\nstatic inline int helper_x(int v) { return v; }", None
    )


# --- C++ class / virtual / template ----------------------------------------


def _virtual_method_add(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "shape.area",
        "  virtual int area() const;\n  virtual int perimeter() const;",
        "int Shape::area() const { return w; }\nint Shape::perimeter() const { return 0; }",
    )


def _virtual_reorder(r: Roles) -> dict[str, RoleText]:
    area_h, _ = r["shape.area"]
    kind_h, _ = r["shape.kind"]
    return _merge(
        _set(r, "shape.area", kind_h, None), _set(r, "shape.kind", area_h, None)
    )


def _virtual_remove(r: Roles) -> dict[str, RoleText]:
    return _set(r, "shape.kind", "", "")


def _virtual_become_pure(r: Roles) -> dict[str, RoleText]:
    return _set(r, "shape.kind", "  virtual int kind() const = 0;", "")


def _method_const_drop(r: Roles) -> dict[str, RoleText]:
    return _set(
        r, "shape.area", "  virtual int area();", "int Shape::area() { return w; }"
    )


def _method_noexcept_add(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "shape.area",
        "  virtual int area() const noexcept;",
        "int Shape::area() const noexcept { return w; }",
    )


def _base_class_add(r: Roles) -> dict[str, RoleText]:
    return _set(r, "shape.open", "class Shape : public Base {", None)


def _template_default_arg(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "buf.decl",
        "template <typename T, int N = 8> struct Buf { T data[N]; };",
        None,
    )


OPERATORS: tuple[EditOp, ...] = (
    # layout
    EditOp(
        "struct_append_field",
        ("struct.rec",),
        _struct_append_field,
        "appending a member grows sizeof(struct rec)",
        any_of=(frozenset({"TYPE_SIZE_CHANGED", "STRUCT_SIZE_CHANGED"}),),
        touches=("struct.rec",),
    ),
    EditOp(
        "struct_remove_trailing_field",
        ("struct.rec",),
        _struct_remove_trailing_field,
        "removing a member is a layout and API break",
        any_of=(
            frozenset({"STRUCT_FIELD_REMOVED", "TYPE_FIELD_REMOVED"}),
            frozenset({"TYPE_SIZE_CHANGED", "STRUCT_SIZE_CHANGED"}),
        ),
        touches=("struct.rec",),
    ),
    EditOp(
        "struct_swap_fields",
        ("struct.point",),
        _struct_swap_fields,
        "swapping members moves field offsets",
        any_of=(
            frozenset({"STRUCT_FIELD_OFFSET_CHANGED", "TYPE_FIELD_OFFSET_CHANGED"}),
        ),
        touches=("struct.point",),
    ),
    EditOp(
        "struct_field_widen",
        ("struct.point",),
        _struct_field_widen,
        "int -> long changes the member type and layout",
        any_of=(frozenset({"STRUCT_FIELD_TYPE_CHANGED", "TYPE_FIELD_TYPE_CHANGED"}),),
        touches=("struct.point",),
    ),
    EditOp(
        "struct_array_grow",
        ("struct.rec",),
        _struct_array_grow,
        "char[8] -> char[16] grows the struct",
        any_of=(frozenset({"TYPE_SIZE_CHANGED", "STRUCT_SIZE_CHANGED"}),),
        touches=("struct.rec",),
    ),
    # enums
    EditOp(
        "enum_append_member",
        ("enum.color",),
        _enum_append_member,
        "appending an enumerator keeps existing values",
        expected=frozenset({"ENUM_MEMBER_ADDED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        touches=("enum.color",),
    ),
    EditOp(
        "enum_remove_member",
        ("enum.color",),
        _enum_remove_member,
        "removing an enumerator breaks users of that name",
        expected=frozenset({"ENUM_MEMBER_REMOVED"}),
        touches=("enum.color",),
    ),
    EditOp(
        "enum_change_value",
        ("enum.color",),
        _enum_change_value,
        "changing an enumerator's value changes meaning of stored values",
        expected=frozenset({"ENUM_MEMBER_VALUE_CHANGED"}),
        touches=("enum.color",),
    ),
    EditOp(
        "enum_class_append",
        ("enum.mode",),
        _enum_class_append,
        "appending an enum class enumerator keeps existing values",
        expected=frozenset({"ENUM_MEMBER_ADDED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        touches=("enum.mode",),
    ),
    EditOp(
        "enum_class_remove",
        ("enum.mode",),
        _enum_class_remove,
        "removing an enum class enumerator breaks users of that name",
        expected=frozenset({"ENUM_MEMBER_REMOVED"}),
        touches=("enum.mode",),
    ),
    EditOp(
        "enum_class_change_value",
        ("enum.mode",),
        _enum_class_change_value,
        "changing an enum class enumerator's value changes stored meaning",
        expected=frozenset({"ENUM_MEMBER_VALUE_CHANGED"}),
        touches=("enum.mode",),
    ),
    # typedefs
    EditOp(
        "typedef_retarget",
        ("typedef.handle",),
        _typedef_retarget,
        "retargeting a typedef used in a public signature",
        any_of=(frozenset({"TYPEDEF_BASE_CHANGED", "PUBLIC_TYPEDEF_TARGET_CHANGED"}),),
        verdict_min="API_BREAK",
        touches=("typedef.handle",),
    ),
    # functions
    EditOp(
        "func_param_type_change",
        ("func.add",),
        _func_param_type_change,
        "parameter type changes the mangled/ABI signature",
        expected=frozenset({"FUNC_PARAMS_CHANGED"}),
        touches=("func.add",),
    ),
    EditOp(
        "func_param_append",
        ("func.add",),
        _func_param_append,
        "adding a parameter changes the signature",
        expected=frozenset({"FUNC_PARAMS_CHANGED"}),
        touches=("func.add",),
    ),
    EditOp(
        "func_param_remove",
        ("func.add",),
        _func_param_remove,
        "removing a parameter changes the signature",
        expected=frozenset({"FUNC_PARAMS_CHANGED"}),
        touches=("func.add",),
    ),
    EditOp(
        "func_return_type_change",
        ("func.count",),
        _func_return_type_change,
        "return type change alters the function's ABI",
        expected=frozenset({"FUNC_RETURN_CHANGED"}),
        touches=("func.count",),
    ),
    EditOp(
        "func_remove",
        ("func.count",),
        _func_remove,
        "removing an exported function is a binary and API break",
        expected=frozenset({"FUNC_REMOVED"}),
        touches=("func.count",),
    ),
    EditOp(
        "func_add",
        ("func.make",),
        _func_add,
        "adding an exported function is compatible",
        expected=frozenset({"FUNC_ADDED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE",
        touches=("func.make",),
    ),
    EditOp(
        "func_out_of_line_to_inline",
        ("func.twice",),
        _func_out_of_line_to_inline,
        "moving an out-of-line function inline removes its exported symbol",
        # The exported symbol disappears; abicheck may name that as the inline
        # change or as the visibility change. Either describes the same fact.
        any_of=(
            frozenset(
                {"FUNC_BECAME_INLINE", "FUNC_VISIBILITY_CHANGED", "FUNC_REMOVED"}
            ),
        ),
        verdict_min="COMPATIBLE_WITH_RISK",
        touches=("func.twice",),
    ),
    # PARAM_RENAMED is the taxonomy's source-level risk (COMPATIBLE_WITH_RISK),
    # not a binary change: the ceiling is the risk verdict, never a breaking one.
    EditOp(
        "param_rename_header_only",
        ("func.add",),
        _func_param_rename_header_only,
        "parameter names do not affect the ABI; the taxonomy reports the rename as source risk",
        expected=frozenset({"PARAM_RENAMED"}),
        verdict_min="NO_CHANGE",
        verdict_max="COMPATIBLE_WITH_RISK",
        touches=("func.add",),
    ),
    # variables
    EditOp(
        "var_type_change",
        ("var.counter",),
        _var_type_change,
        "changing an exported variable's type changes its ABI",
        expected=frozenset({"VAR_TYPE_CHANGED"}),
        touches=("var.counter",),
    ),
    # benign
    EditOp(
        "comment_only",
        ("func.scale",),
        _comment_only,
        "a comment changes no ABI or API",
        verdict_min="NO_CHANGE",
        verdict_max="NO_CHANGE",
        touches=("func.scale",),
    ),
    EditOp(
        "reorder_declarations",
        ("func.add", "func.scale"),
        _reorder_declarations,
        "declaration order in a header changes no ABI",
        verdict_min="NO_CHANGE",
        verdict_max="NO_CHANGE",
        touches=("func.add", "func.scale"),
    ),
    EditOp(
        "static_inline_helper_added",
        ("func.handle",),
        _static_inline_helper,
        "a new static inline helper exports no symbol",
        verdict_min="NO_CHANGE",
        verdict_max="NO_CHANGE",
        touches=("func.handle",),
    ),
    # C++ classes, virtuals, templates
    EditOp(
        "virtual_method_add",
        ("shape.area",),
        _virtual_method_add,
        "a new virtual method changes the vtable layout",
        any_of=(
            frozenset(
                {"VIRTUAL_METHOD_ADDED", "FUNC_VIRTUAL_ADDED", "TYPE_VTABLE_CHANGED"}
            ),
        ),
        touches=("shape.area",),
    ),
    EditOp(
        "virtual_reorder",
        ("shape.area", "shape.kind"),
        _virtual_reorder,
        "swapping virtual declarations changes vtable slot order",
        expected=frozenset({"TYPE_VTABLE_CHANGED"}),
        touches=("shape.area", "shape.kind"),
    ),
    EditOp(
        "virtual_remove",
        ("shape.kind",),
        _virtual_remove,
        "removing a virtual method removes its vtable slot",
        expected=frozenset({"FUNC_VIRTUAL_REMOVED"}),
        touches=("shape.kind",),
    ),
    EditOp(
        "virtual_become_pure",
        ("shape.kind",),
        _virtual_become_pure,
        "a virtual becoming pure changes its exported definition",
        # The edit makes the class abstract: either kind names that fact.
        any_of=(frozenset({"FUNC_VIRTUAL_BECAME_PURE", "TYPE_BECAME_ABSTRACT"}),),
        touches=("shape.kind",),
    ),
    EditOp(
        "method_const_drop",
        ("shape.area",),
        _method_const_drop,
        "dropping const changes the method's mangled name and type",
        expected=frozenset({"FUNC_CV_CHANGED"}),
        touches=("shape.area",),
    ),
    EditOp(
        "method_noexcept_add",
        ("shape.area",),
        _method_noexcept_add,
        "adding noexcept changes the function type (C++17)",
        expected=frozenset({"FUNC_NOEXCEPT_ADDED"}),
        touches=("shape.area",),
    ),
    EditOp(
        "base_class_add",
        ("shape.open",),
        _base_class_add,
        "adding a base class changes the layout and base set",
        expected=frozenset({"TYPE_BASE_CHANGED"}),
        touches=("shape.open",),
    ),
    EditOp(
        "template_default_arg_change",
        ("buf.decl",),
        _template_default_arg,
        "a changed template default alters the header-only instantiation",
        any_of=(
            frozenset(
                {"DEFAULT_TEMPLATE_ARG_CHANGED", "TEMPLATE_PARAM_DEFAULT_CHANGED"}
            ),
        ),
        verdict_min="API_BREAK",
        touches=("buf.decl",),
    ),
)


def applicable(seed: Seed) -> tuple[EditOp, ...]:
    return tuple(op for op in OPERATORS if op.applies_to(seed))


def matrix() -> tuple[tuple[EditOp, Seed], ...]:
    return tuple(
        (op, seed) for op in OPERATORS for seed in SEEDS if op.applies_to(seed)
    )
