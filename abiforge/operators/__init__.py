"""Semantic edit operators for the generated ABI corpus.

Each operator is one source-level edit (or one *rebuild*: the same source
built with a changed flag) with the ground truth it must produce, declared
*from the edit's meaning* (C/C++ ABI rules), never from what abicheck
currently reports.

- ``applies_to(seed)``: the seed provides every role in ``roles``.
- ``requires(config)``: ``None`` when the edit is meaningful under *config*,
  else why not (a language feature the standard lacks, a flag the edit needs).
- ``expect(config)``: the ``Expectation`` for *config*: the base fields below,
  then the first matching ``Rule`` in ``rules``, then the evidence floor
  (below ``min_evidence`` the edit is unobservable).

``expected`` ChangeKinds must all appear; each ``any_of`` group needs at least
one member; the verdict must be no weaker than ``verdict_min`` and, for benign
edits, no stronger than ``verdict_max``. ``edit`` receives the seed's role
texts and returns the roles it changes, as ``(header_text, source_text)``.
"""

from __future__ import annotations

from collections.abc import Callable

from abiforge.model.config import Config
from abiforge.model.expect import VERDICT_RANK, Rule
from abiforge.model.opkit import (
    _ALIGN,
    _ENUM_SIZE,
    _FIELD_TYPE,
    _OFFSET,
    _REMOVED,
    _SIZE,
    EditOp,
    Roles,
    _binary,
    _binary_mangled,
    _cxx_binary_else_dwarf,
    _merge,
    _no_edit,
    _set,
    _with,
)
from abiforge.model.seeds import SEEDS, RoleText, Seed
from abiforge.operators.aliases import ALIAS_OPERATORS
from abiforge.operators.build_data import BUILD_DATA_OPERATORS
from abiforge.operators.cxx import CXX_OPERATORS
from abiforge.operators.dependencies import DEPENDENCY_OPERATORS
from abiforge.operators.elf import ELF_OPERATORS
from abiforge.operators.enums import ENUM_OPERATORS
from abiforge.operators.kinds import KIND_OPERATORS
from abiforge.operators.layout import LAYOUT_OPERATORS
from abiforge.operators.platform import PLATFORM_OPERATORS
from abiforge.operators.runtime_floor import RUNTIME_FLOOR_OPERATORS
from abiforge.operators.source_evidence import SOURCE_EVIDENCE_OPERATORS
from abiforge.operators.source_tree import SOURCE_TREE_OPERATORS
from abiforge.operators.symbols import SYMBOL_OPERATORS
from abiforge.operators.variables import VARIABLE_OPERATORS
from abiforge.operators.vtable import VTABLE_OPERATORS

__all__ = ["OP_BY_NAME", "OPERATORS", "VERDICT_RANK", "EditOp", "applicable", "matrix"]


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
    return _set(r, "struct.point", "struct point { long long x; int y; };")


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
    return _set(r, "typedef.handle", "typedef long long handle_t;")


# --- functions -------------------------------------------------------------


def _func_param_type_change(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "func.add",
        "int add_ints(long long a, int b);",
        "int add_ints(long long a, int b) { return a + b; }",
    )


def _params_reordered(r: Roles) -> dict[str, RoleText]:
    # A pointer and an int swap places: both travel in integer registers, so
    # an old caller's record pointer lands in ``id`` and its id in ``r``.
    return _set(
        r,
        "func.fill",
        "void fill_rec(int id, struct rec *r);",
        "void fill_rec(int id, struct rec *r) "
        "{ memset(r, 0x11, sizeof *r); r->id = id; r->weight = 2.5; }",
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
        "long long get_count(void);",
        "long long get_count(void) { return g_counter; }",
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
    return _set(
        r, "var.counter", "extern long long g_counter;", "long long g_counter = 0;"
    )


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


# --- config-sensitive edits -------------------------------------------------


def _enum_append_wide_value(r: Roles) -> dict[str, RoleText]:
    return _set(r, "enum.color", "enum color { RED, GREEN, BLUE, WIDE = 300 };")


def _struct_array_shrink_in_padding(r: Roles) -> dict[str, RoleText]:
    # char[8] -> char[6]: the two bytes become tail padding unless packed.
    return _set(r, "struct.rec", "struct rec { int id; double weight; char tag[6]; };")


def _func_lose_default_visibility(r: Roles) -> dict[str, RoleText]:
    h, _ = r["func.count"]
    return _merge(_set(r, "func.count", "", None), _set(r, "hidden.slot", h, ""))


def _enum_underlying_narrow(r: Roles) -> dict[str, RoleText]:
    return _set(r, "enum.color", "enum color : unsigned char { RED, GREEN, BLUE };")


def _enum_class_underlying_narrow(r: Roles) -> dict[str, RoleText]:
    return _set(r, "enum.mode", "enum class mode : unsigned char { fast, slow };")


def _no_unique_address_added(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "struct.tagged",
        "struct Empty {};\nstruct Tagged { [[no_unique_address]] Empty e; int v; };",
    )


def _concept_constraint_added(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "tmpl.clamp",
        "template <typename T> requires (sizeof(T) >= 4) T clampv(T v) { return v < 0 ? T(0) : v; }",
    )


def _concept_constraint_relaxed(r: Roles) -> dict[str, RoleText]:
    return _set(
        r,
        "tmpl.req",
        "#ifdef __cpp_concepts\ntemplate <typename T> T reqv(T v) { return v; }\n#endif",
    )


def _not_already(attr: str, value: object) -> Callable[[Config], str | None]:
    def check(c: Config) -> str | None:
        if getattr(c.flags, attr) == value:
            return f"the producer already builds with {attr}={value!r}"
        return None

    return check


def _gnu(c: Config) -> str | None:
    return None if c.producer.gnu_like else f"{c.producer.family} has no such flag"


def _all(*checks: Callable[[Config], str | None]) -> Callable[[Config], str | None]:
    def check(c: Config) -> str | None:
        for chk in checks:
            reason = chk(c)
            if reason:
                return reason
        return None

    return check


def _libcxx_rebuild(c: Config) -> str | None:
    if c.objfmt != "elf" or c.producer.family != "clang":
        return "-stdlib=libc++ is a clang switch; the corpus pairs it with ELF"
    if c.flags.stdlib is not None:
        return "the producer already selects a standard library"
    return None


def _libstdcxx(c: Config) -> str | None:
    if c.objfmt != "elf" or c.producer.family not in ("gcc", "clang"):
        return "the dual std::string ABI is a libstdc++ (ELF gcc/clang) property"
    if c.flags.cxx11_abi == 0:
        return "the producer already builds the old std::string ABI"
    return None


def _visibility_attr(c: Config) -> str | None:
    return (
        None
        if c.objfmt in ("elf", "macho")
        else "symbol visibility is an ELF/Mach-O notion"
    )


def _short_enums(c: Config) -> bool:
    return c.flags.short_enums


def _packed_tight(c: Config) -> bool:
    # struct rec { int; double; char tag[8] } is 20 bytes at pack 1..4. Losing
    # two tag bytes gives 18 at pack 1 or 2; at pack 4 (or more) it rounds back
    # up to 20, so only pack <= 2 shrinks the struct.
    return c.flags.pack is not None and c.flags.pack <= 2


def _hidden(c: Config) -> bool:
    return c.flags.visibility == "hidden"


def _msvc_abi(c: Config) -> bool:
    # MSVC and clang targeting MSVC ignore [[no_unique_address]]; MinGW gcc on
    # PE uses the Itanium ABI and honors it.
    return c.objfmt == "pe" and c.producer.family in ("msvc", "clang")


CORE_OPERATORS: tuple[EditOp, ...] = (
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
        "int -> long long changes the member type and layout; on x86-64 the "
        "struct's alignment rises from 4 to 8 (i386 aligns long long to 4 in a struct)",
        any_of=(frozenset({"STRUCT_FIELD_TYPE_CHANGED", "TYPE_FIELD_TYPE_CHANGED"}),),
        rules=(
            Rule(
                # -fpack-struct=N caps every member's alignment at N, so the
                # struct's alignment rises only when packing allows 8.
                lambda c: (
                    c.flags.bits == 64
                    and c.evidence == "headers"
                    and (c.flags.pack is None or c.flags.pack >= 8)
                ),
                {
                    "any_of": (
                        frozenset(
                            {"STRUCT_FIELD_TYPE_CHANGED", "TYPE_FIELD_TYPE_CHANGED"}
                        ),
                        _ALIGN,
                    )
                },
                "x86-64, unpacked: alignof(point) 4 -> 8, which the header layout records",
            ),
        ),
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
        min_evidence=_cxx_binary_else_dwarf,
        rules=(_binary_mangled("FUNC_PARAMS_CHANGED"),),
        touches=("func.add",),
    ),
    EditOp(
        "params_reordered",
        ("func.fill",),
        _params_reordered,
        "two parameters of different types swap places: the signature (and a "
        "C++ mangled name) changes, and an old caller's arguments arrive in the "
        "wrong parameters",
        expected=frozenset({"FUNC_PARAMS_CHANGED"}),
        min_evidence=_cxx_binary_else_dwarf,
        rules=(_binary_mangled("FUNC_PARAMS_CHANGED"),),
        touches=("func.fill",),
    ),
    EditOp(
        "func_param_append",
        ("func.add",),
        _func_param_append,
        "adding a parameter changes the signature",
        expected=frozenset({"FUNC_PARAMS_CHANGED"}),
        min_evidence=_cxx_binary_else_dwarf,
        rules=(_binary_mangled("FUNC_PARAMS_CHANGED"),),
        touches=("func.add",),
    ),
    EditOp(
        "func_param_remove",
        ("func.add",),
        _func_param_remove,
        "removing a parameter changes the signature",
        expected=frozenset({"FUNC_PARAMS_CHANGED"}),
        min_evidence=_cxx_binary_else_dwarf,
        rules=(_binary_mangled("FUNC_PARAMS_CHANGED"),),
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
        any_of=(_REMOVED,),
        min_evidence="binary",
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
        min_evidence="binary",
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
            frozenset({"FUNC_BECAME_INLINE", "FUNC_VISIBILITY_CHANGED"}) | _REMOVED,
        ),
        verdict_min="BREAKING",
        min_evidence="binary",
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
        min_evidence="headers",
        touches=("func.add",),
    ),
    # variables
    EditOp(
        "var_type_change",
        ("var.counter",),
        _var_type_change,
        "changing an exported variable's type changes its ABI",
        expected=frozenset({"VAR_TYPE_CHANGED"}),
        min_evidence="binary",
        rules=(
            Rule(
                _binary,
                {
                    "expected": frozenset(),
                    "any_of": (frozenset({"SYMBOL_SIZE_CHANGED", "VAR_TYPE_CHANGED"}),),
                },
                "binary evidence sees the exported object's st_size grow",
            ),
        ),
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
        min_evidence="headers",
        touches=("func.scale",),
    ),
    EditOp(
        "reorder_declarations",
        ("func.add", "func.scale"),
        _reorder_declarations,
        "declaration order in a header changes no ABI",
        verdict_min="NO_CHANGE",
        verdict_max="NO_CHANGE",
        min_evidence="headers",
        touches=("func.add", "func.scale"),
    ),
    EditOp(
        "static_inline_helper_added",
        ("func.handle",),
        _static_inline_helper,
        # The binary is unchanged; against the headers' public-header set
        # (``abicheck dump -H``) the helper is a new source-level entry
        # point, a compatible API addition.
        "a new static inline helper exports no symbol; at most it adds to the API",
        verdict_min="NO_CHANGE",
        verdict_max="COMPATIBLE",
        min_evidence="headers",
        features=("inline",),
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
        min_evidence="binary",
        rules=(
            Rule(
                _binary,
                {
                    "any_of": (
                        frozenset(
                            {
                                "SYMBOL_SIZE_CHANGED",
                                "TYPE_VTABLE_CHANGED",
                                "VTABLE_SLOT_COUNT_CHANGED",
                            }
                        ),
                    )
                },
                "binary evidence sees the vtable object (_ZTV) grow",
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
        min_evidence="binary",
        rules=(_binary_mangled("FUNC_VIRTUAL_REMOVED"),),
        touches=("shape.kind",),
    ),
    EditOp(
        "virtual_become_pure",
        ("shape.kind",),
        _virtual_become_pure,
        "a virtual becoming pure changes its exported definition",
        # The edit makes the class abstract: either kind names that fact.
        any_of=(frozenset({"FUNC_VIRTUAL_BECAME_PURE", "TYPE_BECAME_ABSTRACT"}),),
        min_evidence="binary",
        rules=(_binary_mangled("FUNC_VIRTUAL_BECAME_PURE", "TYPE_BECAME_ABSTRACT"),),
        touches=("shape.kind",),
    ),
    EditOp(
        "method_const_drop",
        ("shape.area",),
        _method_const_drop,
        "dropping const changes the method's mangled name and type",
        expected=frozenset({"FUNC_CV_CHANGED"}),
        min_evidence="binary",
        rules=(_binary_mangled("FUNC_CV_CHANGED"),),
        touches=("shape.area",),
    ),
    EditOp(
        "method_noexcept_add",
        ("shape.area",),
        _method_noexcept_add,
        # A member function's mangled name carries no exception specification,
        # so binaries keep linking; an overrider without noexcept no longer
        # compiles ([except.spec]/5), so it is a source break, not a binary one.
        "adding noexcept to a virtual makes overriders without it ill-formed",
        expected=frozenset({"FUNC_NOEXCEPT_ADDED"}),
        verdict_min="API_BREAK",
        verdict_max="API_BREAK",
        min_evidence="headers",
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
        min_evidence="headers",
        touches=("buf.decl",),
    ),
    # --- config-sensitive edits: the expectation is a function of the config
    EditOp(
        "enum_append_wide_value",
        ("enum.color",),
        _enum_append_wide_value,
        "appending an enumerator keeps existing values; 300 needs more than one byte",
        expected=frozenset({"ENUM_MEMBER_ADDED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        rules=(
            Rule(
                _short_enums,
                {
                    "any_of": (_ENUM_SIZE,),
                    "verdict_min": "BREAKING",
                    "verdict_max": None,
                },
                "-fshort-enums sizes the enum by its range: 0..2 fits a byte, 300 does not",
            ),
        ),
        touches=("enum.color",),
    ),
    EditOp(
        "struct_array_shrink_in_padding",
        ("struct.rec",),
        _struct_array_shrink_in_padding,
        "a public member's array type shrinks; unpacked, the bytes become tail padding",
        any_of=(_FIELD_TYPE,),
        verdict_min="API_BREAK",
        rules=(
            Rule(
                _packed_tight,
                {"any_of": (_FIELD_TYPE, _SIZE), "verdict_min": "BREAKING"},
                "packed to 1 or 2, there is no tail padding: sizeof(struct rec) shrinks by two",
            ),
        ),
        touches=("struct.rec",),
    ),
    EditOp(
        "func_lose_default_visibility",
        ("func.count", "hidden.slot"),
        _func_lose_default_visibility,
        "moving a declaration out of the default-visibility block",
        verdict_min="NO_CHANGE",
        verdict_max="NO_CHANGE",
        min_evidence="headers",
        precondition=_visibility_attr,
        rules=(
            Rule(
                _hidden,
                {
                    "any_of": (
                        frozenset(
                            {"FUNC_VISIBILITY_CHANGED", "SYMBOL_ELF_VISIBILITY_CHANGED"}
                        )
                        | _REMOVED,
                    ),
                    "verdict_min": "BREAKING",
                    "verdict_max": None,
                },
                "under -fvisibility=hidden the moved function stops being exported",
                min_evidence="binary",
            ),
        ),
        touches=("func.count", "hidden.slot"),
    ),
    EditOp(
        "enum_underlying_narrow",
        ("enum.color",),
        _enum_underlying_narrow,
        "fixing the underlying type to unsigned char shrinks an int-sized enum",
        any_of=(_ENUM_SIZE,),
        features=("enum_underlying",),
        rules=(
            Rule(
                _short_enums,
                {
                    "any_of": (),
                    "verdict_min": "NO_CHANGE",
                    "verdict_max": "COMPATIBLE_WITH_RISK",
                },
                "-fshort-enums already gave 0..2 an unsigned char: the size is unchanged",
            ),
        ),
        touches=("enum.color",),
    ),
    EditOp(
        "enum_class_underlying_narrow",
        ("enum.mode",),
        _enum_class_underlying_narrow,
        "a scoped enum's implicit int becomes unsigned char (-fshort-enums never applies)",
        any_of=(_ENUM_SIZE,),
        touches=("enum.mode",),
    ),
    EditOp(
        "no_unique_address_added",
        ("struct.tagged",),
        _no_unique_address_added,
        "[[no_unique_address]] on an empty member lets it overlap: sizeof 8 -> 4",
        any_of=(_SIZE | _OFFSET,),
        features=("no_unique_address",),
        rules=(
            Rule(
                _msvc_abi,
                {
                    "any_of": (),
                    "verdict_min": "NO_CHANGE",
                    "verdict_max": "COMPATIBLE_WITH_RISK",
                },
                "the MSVC ABI ignores [[no_unique_address]] (msvc::no_unique_address is the spelling)",
            ),
        ),
        touches=("struct.tagged",),
    ),
    EditOp(
        "concept_constraint_added",
        ("tmpl.clamp",),
        _concept_constraint_added,
        "a new requires-clause rejects calls the old template accepted: the "
        "constraint set strictly grows (none -> one), a tightening",
        expected=frozenset({"CONCEPT_TIGHTENED"}),
        verdict_min="API_BREAK",
        min_evidence="headers",
        features=("concepts",),
        touches=("tmpl.clamp",),
    ),
    EditOp(
        "concept_constraint_relaxed",
        ("tmpl.req",),
        _concept_constraint_relaxed,
        "the requires-clause is dropped: every old call still compiles, but a "
        "relaxed constraint can change which overload subsumption picks, so "
        "it is a risk, not a tightening",
        expected=frozenset({"CONCEPT_CONSTRAINT_CHANGED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        verdict_max="COMPATIBLE_WITH_RISK",
        min_evidence="headers",
        features=("concepts",),
        touches=("tmpl.req",),
    ),
    # --- rebuild edits: same source, one flag changed on the new side
    EditOp(
        "rebuild_short_enums",
        ("enum.color",),
        _no_edit,
        "rebuilding with -fshort-enums shrinks every unscoped enum to its range",
        any_of=(_ENUM_SIZE,),
        rules=(
            Rule(
                lambda c: c.build_data,
                {"expected": frozenset({"ENUM_SIZE_FLAG_CHANGED"})},
                "the compile commands record -fshort-enums itself",
            ),
        ),
        precondition=_all(_gnu, _not_already("short_enums", True)),
        rebuild=_with(short_enums=True),
    ),
    EditOp(
        "rebuild_pack_struct",
        ("struct.rec",),
        _no_edit,
        "rebuilding with -fpack-struct=1 removes padding: offsets and sizes move",
        any_of=(_SIZE | _OFFSET,),
        rules=(
            Rule(
                lambda c: (
                    c.flags.bits == 32
                    or (c.flags.pack is not None and c.flags.pack <= 4)
                ),
                {"any_of": (_SIZE | _OFFSET | _ALIGN,)},
                "rec's double already sits at offset 4 (i386 aligns it to 4; an old side "
                "packed to 2 or 4 caps it there): packing to 1 keeps every size and "
                "offset and lowers only alignof",
                # DWARF records no alignment for an unannotated record and the
                # sizes and offsets it does record are unchanged: only the header
                # dump (built with the same -fpack-struct) can show alignof drop.
                min_evidence="headers",
            ),
            Rule(
                lambda c: c.evidence == "headers",
                {"any_of": (_SIZE | _OFFSET, _ALIGN)},
                "x86-64, naturally aligned: alignof(rec) 8 -> 1 as well, which the "
                "header layout records",
            ),
        ),
        precondition=_not_already("pack", 1),
        rebuild=_with(pack=1),
    ),
    EditOp(
        "rebuild_cxx11_abi_off",
        ("func.greet",),
        _no_edit,
        "_GLIBCXX_USE_CXX11_ABI=0 switches std::string to the old ABI: every mangled name changes",
        # The flip itself is the finding (ADR-050 amendment 2026-11-09): the
        # re-mangled greet export pairs with its old-ABI twin at every level.
        expected=frozenset({"GLIBCXX_DUAL_ABI_CHANGED"}),
        # Symbols alone show the old names gone; a tool that pairs by demangled
        # name sees std::__cxx11::basic_string become std::basic_string instead.
        any_of=(_REMOVED | {"FUNC_PARAMS_CHANGED", "FUNC_RETURN_CHANGED"},),
        min_evidence="binary",
        precondition=_libstdcxx,
        rebuild=_with(cxx11_abi=0),
        needs_libstdcxx=True,
    ),
    EditOp(
        "rebuild_against_libcxx",
        ("func.greet",),
        _no_edit,
        "the library is rebuilt against libc++ instead of libstdc++: std::string "
        "is std::__1::basic_string with another layout, so greet's mangled name "
        "changes and every std:: object crossing the boundary is a different type. "
        "The header still declares the libstdc++ spelling a consumer links "
        "against, so with headers its lost export reads as a visibility change",
        expected=frozenset({"STDLIB_IMPLEMENTATION_CHANGED"}),
        any_of=(
            _REMOVED
            | {"FUNC_PARAMS_CHANGED", "FUNC_RETURN_CHANGED", "FUNC_VISIBILITY_CHANGED"},
        ),
        min_evidence="binary",
        precondition=_libcxx_rebuild,
        rebuild=_with(stdlib="libc++"),
        # The baseline must be libstdc++; libc++ itself is probed through the
        # rebuilt flags (``capability_gap``): absent, the cell is unavailable.
        needs_libstdcxx=True,
    ),
    EditOp(
        "rebuild_hidden_visibility",
        ("func.add",),
        _no_edit,
        "-fvisibility=hidden changes nothing when every public declaration is in a default-visibility block",
        verdict_min="NO_CHANGE",
        verdict_max="NO_CHANGE",
        min_evidence="binary",
        precondition=_all(_gnu, _visibility_attr, _not_already("visibility", "hidden")),
        rebuild=_with(visibility="hidden"),
    ),
    EditOp(
        "rebuild_optimized",
        ("func.add",),
        _no_edit,
        # The interface is unchanged; the optimized library may legitimately
        # stop importing a helper (NEEDED/imported-symbol changes are compatible).
        "-O2 changes code, not the interface",
        verdict_min="NO_CHANGE",
        verdict_max="COMPATIBLE",
        min_evidence="binary",
        precondition=_not_already("opt", "O2"),
        rebuild=_with(opt="O2"),
    ),
)


# Operators for the c_elf and cxx_more seeds live in their own modules.
OPERATORS: tuple[EditOp, ...] = (
    *CORE_OPERATORS,
    *ELF_OPERATORS,
    *PLATFORM_OPERATORS,
    *CXX_OPERATORS,
    *LAYOUT_OPERATORS,
    *SYMBOL_OPERATORS,
    *KIND_OPERATORS,
    *VTABLE_OPERATORS,
    *ENUM_OPERATORS,
    *VARIABLE_OPERATORS,
    *ALIAS_OPERATORS,
    *BUILD_DATA_OPERATORS,
    *DEPENDENCY_OPERATORS,
    *RUNTIME_FLOOR_OPERATORS,
    *SOURCE_EVIDENCE_OPERATORS,
    *SOURCE_TREE_OPERATORS,
)

OP_BY_NAME: dict[str, EditOp] = {op.name: op for op in OPERATORS}


def applicable(seed: Seed) -> tuple[EditOp, ...]:
    return tuple(op for op in OPERATORS if op.applies_to(seed))


def matrix() -> tuple[tuple[EditOp, Seed], ...]:
    """Every (operator, seed) pair whose seed provides the operator's roles."""
    return tuple(
        (op, seed) for op in OPERATORS for seed in SEEDS if op.applies_to(seed)
    )
