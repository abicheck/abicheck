"""The model of C/C++ edits the edit axis splits a construct into.

This module defines *what edit classes exist*, from the layout rules of C and
C++ (SysV-style sequential member layout with alignment padding, the Itanium
C++ ABI's base-subobject layout), not from what the generator happens to
implement. It imports nothing from the operator registry: the coverage
denominator is this model, and an edit class no operator generates is an
uncovered gap in the report and the ledger
(``abiforge.model.space.edit_classes_without_operator``), never a class that is
left out. ``tests/test_edit_corpus_edit_axis.py`` checks both directions.

The model splits three constructs:

``struct`` (record layout)
    A member edit is described by *operation* x *position* x *effect*:

    - operation: ``insert`` or ``remove`` one member;
    - position: ``head`` (the first member), ``middle``, ``tail`` (the last);
    - effect: ``shifts`` (some other member's offset, or sizeof, changes) or
      ``absorbed`` (the bytes come from or go back to padding: every other
      offset and sizeof are unchanged).

    ``head`` x ``absorbed`` is excluded by a layout rule, not by omission:
    the first member sits at offset 0, so inserting before it moves it, and
    removing it moves the next member (at offset > 0) to 0. The Lean layout
    table checks this rule (``test_edit_corpus_edit_axis``).

    The other record-layout classes: reorder; retype by how size and
    alignment change; array bound change by direction and effect; bit-field
    edits; record-level edits (packing, alignment, kind, opacity, removal,
    addition); and member edits with no layout effect (rename, cv).

``inheritance`` (base-class subobjects, C++)
    Add a non-virtual or a virtual base, remove a base, reorder bases, make
    a base virtual or non-virtual, change a base's own layout, change a
    base's access.

``function`` (a declared function and its exported symbol)
    Signature edits (each parameter, the return type, variadic-ness, calling
    convention, exception specification, attributes, language linkage),
    definition edits (inline/out of line, body imports, overloads), the
    function set (added, removed, renamed, declared-vs-exported) and the
    exporting symbol (binding, visibility, version, type).

Every other construct is not split: its single class ``any`` is the whole
construct (``UNSPLIT``). That is a statement about the model's resolution,
not about the generator: such a construct is covered by any one of its
operators, and the docs say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class EditClass:
    construct: str
    name: str
    doc: str
    # The languages the edit exists in, when narrower than its construct's
    # (an overload or a default argument has no C spelling).
    langs: frozenset[str] | None = None

    @property
    def id(self) -> str:
        return f"{self.construct}/{self.name}"


ANY_EDIT = "any"

# --- struct: record layout ---------------------------------------------------

MEMBER_OPERATIONS: tuple[str, ...] = ("insert", "remove")
MEMBER_POSITIONS: tuple[str, ...] = ("head", "middle", "tail")
LAYOUT_EFFECTS: tuple[str, ...] = ("shifts", "absorbed")

# (position, effect) pairs a layout rule makes impossible, and why.
IMPOSSIBLE_MEMBER_EDITS: dict[tuple[str, str], str] = {
    ("head", "absorbed"): (
        "the first member is at offset 0: inserting before it moves it, and "
        "removing it moves the next member to 0"
    ),
}


def _member_edits() -> tuple[EditClass, ...]:
    out = []
    for op, pos, eff in product(MEMBER_OPERATIONS, MEMBER_POSITIONS, LAYOUT_EFFECTS):
        if (pos, eff) in IMPOSSIBLE_MEMBER_EDITS:
            continue
        verb = "inserting" if op == "insert" else "removing"
        what = (
            "moves a later member or changes sizeof"
            if eff == "shifts"
            else "leaves every other offset and sizeof unchanged (padding)"
        )
        out.append(
            EditClass("struct", f"{op}_{pos}_{eff}", f"{verb} the {pos} member {what}")
        )
    return tuple(out)


_STRUCT_OTHER: tuple[tuple[str, str], ...] = (
    ("reorder", "two members swap declaration order"),
    ("retype_same_layout", "a member's type changes, size and alignment unchanged"),
    ("retype_grows", "a member's type changes to a larger size"),
    ("retype_shrinks", "a member's type changes to a smaller size"),
    ("retype_align_only", "a member's alignment changes, its size unchanged"),
    (
        "array_grow_shifts",
        "an array member's bound grows; later offsets or sizeof change",
    ),
    ("array_grow_absorbed", "an array member's bound grows into padding"),
    (
        "array_shrink_shifts",
        "an array member's bound shrinks; later offsets or sizeof change",
    ),
    (
        "array_shrink_absorbed",
        "an array member's bound shrinks; the bytes become padding",
    ),
    ("field_to_bitfield", "a plain member becomes a bit-field"),
    ("bitfield_to_field", "a bit-field becomes a plain member"),
    ("bitfield_width_absorbed", "a bit-field's width changes within its storage unit"),
    ("bitfield_width_shifts", "a bit-field's width change crosses a storage unit"),
    ("packing_change", "the record's packing changes (pragma, attribute or flag)"),
    ("record_align_change", "the record's own alignment changes (alignas/aligned)"),
    ("record_kind_change", "struct becomes union or the reverse"),
    ("record_opaque", "the record's definition is withdrawn (made opaque)"),
    ("record_defined", "an opaque record's definition becomes visible"),
    ("record_removed", "the record is removed from the interface"),
    ("record_added", "a new record is added to the interface"),
    (
        "record_triviality_change",
        "the record becomes non-trivial for calls, or trivial again (a "
        "user-provided destructor or copy constructor), which changes how it is "
        "passed by value without changing its layout",
    ),
    ("member_rename", "a member is renamed, layout unchanged"),
    (
        "pointee_rename",
        "the record a pointer or reference member points to is renamed; "
        "every layout is unchanged",
    ),
    ("member_cv_change", "a member gains or loses const/volatile"),
    (
        "declaration_text_only",
        "the record's header text moves (line position, order) and its "
        "definition does not",
    ),
    (
        "member_access_change",
        "a member's access (public/protected/private) changes, layout unchanged",
    ),
)

STRUCT_EDIT_CLASSES: tuple[EditClass, ...] = _member_edits() + tuple(
    EditClass("struct", n, d) for n, d in _STRUCT_OTHER
)

# --- inheritance: base-class subobjects --------------------------------------

INHERITANCE_EDIT_CLASSES: tuple[EditClass, ...] = tuple(
    EditClass("inheritance", n, d)
    for n, d in (
        ("base_add_nonvirtual", "a non-virtual base is added"),
        ("base_add_virtual", "a virtual base is added"),
        ("base_remove", "a base is removed"),
        ("base_reorder", "two bases swap declaration order"),
        ("base_became_virtual", "a non-virtual base becomes virtual"),
        ("base_became_nonvirtual", "a virtual base becomes non-virtual"),
        ("base_layout_changed", "a base's own size or layout changes"),
        ("base_access_changed", "a base's access (public/protected/private) changes"),
    )
)

# --- function: a declared function and the symbol it exports ----------------
#
# A function is its declaration (return type, parameter list, variadic-ness,
# calling convention, exception specification, attributes, language linkage),
# its definition (inline or out of line, what its body imports) and the ELF
# symbol that exports it (name, binding, visibility, version, type).

_CXX_ONLY = frozenset({"c++"})
_CXX_FUNCTION_EDITS = frozenset(
    {
        "default_argument_changed",
        "exception_spec_change",
        "language_linkage_change",
        "overload_added",
    }
)

FUNCTION_EDIT_CLASSES: tuple[EditClass, ...] = tuple(
    EditClass("function", n, d, _CXX_ONLY if n in _CXX_FUNCTION_EDITS else None)
    for n, d in (
        # signature: parameters
        ("param_added", "a parameter is added"),
        ("param_removed", "a parameter is removed"),
        (
            "param_retyped",
            "a parameter's type changes (value class, size or va_list-ness)",
        ),
        ("param_pointer_level", "a parameter gains or loses a level of indirection"),
        ("param_pointee_cv", "a parameter's pointee gains or loses const/volatile"),
        ("param_renamed", "a parameter is renamed, its type unchanged"),
        (
            "param_qualifier_change",
            "a parameter's top-level qualifier (const, restrict) changes; the "
            "function type and its symbol do not",
        ),
        ("params_reordered", "two parameters of different types swap places"),
        (
            "default_argument_changed",
            "a parameter's default argument is added, removed or changed (C++)",
        ),
        # signature: return
        ("return_retyped", "the return type changes"),
        ("return_pointer_level", "the return type gains or loses indirection"),
        ("return_pointee_cv", "the returned pointee gains or loses const/volatile"),
        # signature: the call itself
        ("variadic_change", "the function becomes variadic or stops being variadic"),
        ("calling_convention_change", "the calling convention changes"),
        ("exception_spec_change", "the exception specification changes (C++)"),
        ("attribute_change", "a contract attribute (nonnull, ...) is added or removed"),
        (
            "language_linkage_change",
            'the declaration gains or loses extern "C" (C++), renaming its symbol',
        ),
        # definition
        ("became_inline", "an exported function becomes inline in the header"),
        ("became_out_of_line", "an inline function gets an exported definition"),
        (
            "body_change",
            "the body changes what the library imports or how it is relocated, "
            "the declaration unchanged",
        ),
        ("overload_added", "an overload is added beside the function (C++)"),
        # the interface's function set
        ("function_added", "a function is added to the interface"),
        ("function_removed", "a function is removed from the interface"),
        ("function_renamed", "a function's symbol name changes"),
        (
            "export_declaration_mismatch",
            "a function is declared but not exported, or exported but not declared",
        ),
        # the exporting symbol
        ("binding_change", "the symbol's binding changes (global, weak)"),
        (
            "visibility_change",
            "the symbol's visibility changes (default, protected, hidden)",
        ),
        ("version_change", "the symbol's version node changes"),
        ("symbol_type_change", "the symbol's type changes (function, ifunc, unique)"),
        (
            "declaration_text_only",
            "the header text changes (comments, declaration order) and no "
            "declaration does",
        ),
    )
)

EDIT_MODEL: dict[str, tuple[EditClass, ...]] = {
    "struct": STRUCT_EDIT_CLASSES,
    "inheritance": INHERITANCE_EDIT_CLASSES,
    "function": FUNCTION_EDIT_CLASSES,
}


def edit_langs(construct: str, name: str) -> frozenset[str] | None:
    """The languages edit class *name* of *construct* is limited to, or None."""
    for c in EDIT_MODEL.get(construct, ()):
        if c.name == name:
            return c.langs
    return None


def model_classes(construct: str) -> tuple[str, ...]:
    """The edit classes the model splits *construct* into (``any`` if unsplit)."""
    split = EDIT_MODEL.get(construct)
    return tuple(c.name for c in split) if split else (ANY_EDIT,)


def all_model_classes() -> tuple[EditClass, ...]:
    return tuple(c for classes in EDIT_MODEL.values() for c in classes)


__all__ = [
    "ANY_EDIT",
    "EDIT_MODEL",
    "EditClass",
    "FUNCTION_EDIT_CLASSES",
    "IMPOSSIBLE_MEMBER_EDITS",
    "INHERITANCE_EDIT_CLASSES",
    "LAYOUT_EFFECTS",
    "MEMBER_OPERATIONS",
    "MEMBER_POSITIONS",
    "STRUCT_EDIT_CLASSES",
    "all_model_classes",
    "edit_langs",
    "model_classes",
]
