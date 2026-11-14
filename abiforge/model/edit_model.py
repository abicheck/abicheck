"""The model of C/C++ edits the edit axis splits a construct into.

This module defines *what edit classes exist*, from the layout rules of C and
C++ (SysV-style sequential member layout with alignment padding, the Itanium
C++ ABI's base-subobject layout), not from what the generator happens to
implement. It imports nothing from the operator registry: the coverage
denominator is this model, and an edit class no operator generates is an
uncovered gap in the report and the ledger
(``abiforge.model.space.edit_classes_without_operator``), never a class that is
left out. ``tests/test_edit_corpus_edit_axis.py`` checks both directions.

The model splits ten constructs:

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

``class_virtual`` (virtual functions and the vtable, Itanium C++ ABI 2.5)
    A slot appended, inserted, removed or reordered; a virtual's signature,
    covariant return, noexcept, purity, override and final specifiers; a
    method becoming virtual or not, a derived class overriding or not, a
    virtual destructor; the key function; the vptr appearing or going; a new
    polymorphic class; the vtable/type_info symbol.

``enum``, ``enum_fixed_underlying``, ``enum_class`` (C11 6.7.2.2, [dcl.enum])
    An enumerator appended, inserted, removed, revalued, swapped, renamed or
    aliased; the underlying type and its range; scoping; the enum added,
    removed or made opaque. ``IMPOSSIBLE_ENUM_EDITS`` states which construct
    excludes which class, and the rule.

``variable`` (a declared object and its exported symbol)
    Its type by size effect, cv, initializer, TLS, visibility, binding,
    alignment and version; the object added, removed, renamed,
    declared-vs-exported, or made a C++17 inline variable.

``typedef``
    Retargeted (by size, same size, pointee), removed, renamed, added, or
    respelled to the same type.

``template`` (C++)
    A parameter's default; a parameter added (defaulted or not), removed or
    retyped; an instantiation added or dropped; an explicit specialization
    added or removed; a template argument type renamed.

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

# --- class_virtual: virtual functions and the vtable (Itanium C++ ABI 2.5) ---
#
# A polymorphic class's vtable holds, after the offset-to-top and RTTI
# entries, one slot per virtual function in declaration order (two for a
# virtual destructor), the primary base's slots first. An edit is described
# by what it does to that slot list, to the overrider a slot names, to the
# vptr's existence, or to where the vtable is emitted (the key function).

CLASS_VIRTUAL_EDIT_CLASSES: tuple[EditClass, ...] = tuple(
    EditClass("class_virtual", n, d)
    for n, d in (
        ("vfunc_appended", "a virtual function is added after every existing slot"),
        ("vfunc_inserted", "a virtual function is added before an existing slot"),
        ("vfunc_removed", "a virtual function and its slot are removed"),
        ("vfunc_reordered", "two virtual functions swap declaration (slot) order"),
        (
            "vfunc_signature_changed",
            "a virtual function's parameters or cv/ref qualifiers change: the "
            "slot keeps its index and names a re-mangled function",
        ),
        (
            "vfunc_return_covariant",
            "an overrider's return type changes to a pointer or reference to a "
            "class derived from the overridden one's (covariant return)",
        ),
        ("vfunc_exception_spec_change", "a virtual function's noexcept changes"),
        ("vfunc_became_pure", "a defined virtual function becomes pure"),
        ("vfunc_lost_pure", "a pure virtual function gets a definition"),
        ("pure_vfunc_added", "a new pure virtual function is declared"),
        (
            "method_became_virtual",
            "a non-virtual member function of a polymorphic class becomes "
            "virtual, adding a slot",
        ),
        (
            "method_became_nonvirtual",
            "a virtual member function becomes non-virtual, removing its slot",
        ),
        (
            "override_added",
            "a derived class starts overriding a base virtual: the inherited "
            "slot names a new function",
        ),
        (
            "override_removed",
            "a derived class stops overriding a base virtual: the slot falls "
            "back to the base's function",
        ),
        ("override_specifier_change", "an override specifier is added or removed"),
        ("final_specifier_change", "a virtual function becomes final, or stops being"),
        ("virtual_dtor_added", "the destructor of a polymorphic class becomes virtual"),
        ("virtual_dtor_removed", "a virtual destructor becomes non-virtual"),
        (
            "key_function_change",
            "the key function (the first non-inline, non-pure virtual) changes or "
            "disappears, which moves where the vtable and type_info are emitted",
        ),
        ("became_polymorphic", "a class's first virtual function adds a vptr"),
        ("lost_polymorphism", "a class's last virtual function goes, and its vptr"),
        ("polymorphic_class_added", "a new polymorphic class is added"),
        (
            "vtable_symbol_visibility",
            "the vtable or type_info symbol's visibility or binding changes",
        ),
    )
)

# --- enums: enumerators and the underlying type (C11 6.7.2.2, C++ [dcl.enum]) -
#
# The same classes for the three enum constructs, minus what a rule excludes:
# a fixed underlying type cannot be outgrown (a value it cannot represent is
# ill-formed), only an enum with a fixed underlying type has an opaque
# declaration, and only C++ has scoped enums.

_ENUM_CLASSES: tuple[tuple[str, str], ...] = (
    ("enumerator_appended", "an enumerator is added after the last one"),
    (
        "enumerator_inserted",
        "an enumerator is added before an existing one, shifting the implicit "
        "values after it",
    ),
    ("enumerator_removed", "an enumerator is removed"),
    ("enumerator_value_changed", "an enumerator's value changes"),
    ("enumerator_values_swapped", "two enumerators exchange values"),
    ("enumerator_renamed", "an enumerator is renamed, its value unchanged"),
    (
        "enumerator_alias_added",
        "an enumerator is added with the value of an existing one",
    ),
    (
        "range_exceeds_underlying",
        "an added value no longer fits the implied underlying type, so the "
        "enum's size changes",
    ),
    ("underlying_type_change", "the underlying type (or its implied size) changes"),
    ("scoped_change", "the enum becomes scoped (enum class) or unscoped"),
    ("enum_added", "a new enum is added to the interface"),
    ("enum_removed", "an enum is removed from the interface"),
    (
        "became_opaque",
        "the definition is withdrawn to an opaque declaration (its enumerators go)",
    ),
)

#: Enum classes a construct cannot have, and the rule that excludes them.
IMPOSSIBLE_ENUM_EDITS: dict[str, dict[str, str]] = {
    "enum": {
        "became_opaque": "an enum without a fixed underlying type has no opaque "
        "declaration ([dcl.enum]/3; C23 6.7.2.2)",
    },
    "enum_fixed_underlying": {
        "range_exceeds_underlying": "a value the fixed underlying type cannot "
        "represent is ill-formed",
    },
    "enum_class": {
        "range_exceeds_underlying": "a scoped enum always has a fixed underlying "
        "type (int when unstated)",
    },
}


def _enum_edits(construct: str) -> tuple[EditClass, ...]:
    excluded = IMPOSSIBLE_ENUM_EDITS[construct]
    return tuple(
        EditClass(construct, n, d, _CXX_ONLY if n == "scoped_change" else None)
        for n, d in _ENUM_CLASSES
        if n not in excluded
    )


# --- variable: a declared object and the ELF symbol that exports it ---------
#
# A consumer that references an exported object copy-relocates it: its size
# and alignment are baked into the consumer (STT_OBJECT st_size), its
# initial value lives in the library, and the symbol's binding, visibility,
# type (STT_TLS) and version decide what the reference resolves to.

VARIABLE_EDIT_CLASSES: tuple[EditClass, ...] = tuple(
    EditClass("variable", n, d, _CXX_ONLY if n == "became_inline" else None)
    for n, d in (
        ("retyped_same_size", "the object's type changes, its size unchanged"),
        ("size_grows", "the object's type or array bound grows"),
        ("size_shrinks", "the object's type or array bound shrinks"),
        ("cv_change", "the object gains or loses const/volatile"),
        ("initial_value_change", "the object's initializer changes"),
        ("tls_change", "the object becomes thread-local, or stops being"),
        ("visibility_change", "the symbol's visibility changes"),
        ("binding_change", "the symbol's binding changes (global, weak)"),
        ("alignment_change", "the object's alignment changes"),
        ("version_change", "the symbol's version node changes"),
        ("variable_added", "an object is added to the interface"),
        ("variable_removed", "an object is removed from the interface"),
        ("variable_renamed", "an object's symbol name changes"),
        (
            "export_declaration_mismatch",
            "an object is exported but not declared, or declared but not exported",
        ),
        (
            "became_inline",
            "the object becomes a C++17 inline variable, defined in the header "
            "with vague linkage",
        ),
    )
)

# --- typedef: a name for a type --------------------------------------------
#
# A typedef is in no mangled name or symbol: what it names is. So an edit is
# described by what happens to the named type, and by the name's own
# presence in the API.

TYPEDEF_EDIT_CLASSES: tuple[EditClass, ...] = tuple(
    EditClass("typedef", n, d, _CXX_ONLY if n == "respelled" else None)
    for n, d in (
        ("retarget_size_change", "the typedef names a type of another size"),
        (
            "retarget_same_size",
            "the typedef names another type of the same size (int -> unsigned)",
        ),
        ("retarget_pointee", "a pointer typedef names a pointer to another type"),
        ("typedef_removed", "the typedef is removed"),
        ("typedef_renamed", "the typedef is renamed, its target unchanged"),
        ("typedef_added", "a new typedef is added"),
        (
            "respelled",
            "the typedef becomes an alias-declaration (using) naming the same type",
        ),
    )
)

# --- template: parameters, instantiations, specializations (C++) ------------
#
# A specialization's mangled name carries every template argument, defaulted
# or not (Itanium 5.1.5), and a function template specialization is exported
# only where the library instantiates or explicitly specializes it.

TEMPLATE_EDIT_CLASSES: tuple[EditClass, ...] = tuple(
    EditClass("template", n, d)
    for n, d in (
        ("default_argument_change", "a template parameter's default argument changes"),
        (
            "definition_changed",
            "a header template's definition changes: each consumer instantiates "
            "its own copy, so only rebuilt consumers see the new body",
        ),
        ("template_added", "a new template is declared in the interface"),
        ("template_removed", "a template is removed from the interface"),
        (
            "param_added_defaulted",
            "a template parameter with a default is added: every specialization "
            "re-mangles with the extra argument",
        ),
        ("param_added_mandatory", "a template parameter without a default is added"),
        ("param_removed", "a template parameter is removed"),
        (
            "param_retyped",
            "a non-type template parameter's type, or a parameter's kind, changes",
        ),
        (
            "instantiation_added",
            "the library instantiates and exports a further specialization",
        ),
        (
            "instantiation_dropped",
            "the library stops instantiating a specialization consumers reference",
        ),
        (
            "specialization_added",
            "an explicit specialization is added beside the primary template",
        ),
        ("specialization_removed", "an explicit specialization is removed"),
        (
            "argument_renamed",
            "a type used as a template argument is renamed, re-mangling the "
            "specializations that name it",
        ),
    )
)

EDIT_MODEL: dict[str, tuple[EditClass, ...]] = {
    "struct": STRUCT_EDIT_CLASSES,
    "inheritance": INHERITANCE_EDIT_CLASSES,
    "function": FUNCTION_EDIT_CLASSES,
    "class_virtual": CLASS_VIRTUAL_EDIT_CLASSES,
    "enum": _enum_edits("enum"),
    "enum_fixed_underlying": _enum_edits("enum_fixed_underlying"),
    "enum_class": _enum_edits("enum_class"),
    "variable": VARIABLE_EDIT_CLASSES,
    "typedef": TYPEDEF_EDIT_CLASSES,
    "template": TEMPLATE_EDIT_CLASSES,
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
    "CLASS_VIRTUAL_EDIT_CLASSES",
    "EDIT_MODEL",
    "EditClass",
    "FUNCTION_EDIT_CLASSES",
    "IMPOSSIBLE_ENUM_EDITS",
    "IMPOSSIBLE_MEMBER_EDITS",
    "INHERITANCE_EDIT_CLASSES",
    "LAYOUT_EFFECTS",
    "MEMBER_OPERATIONS",
    "MEMBER_POSITIONS",
    "STRUCT_EDIT_CLASSES",
    "TEMPLATE_EDIT_CLASSES",
    "TYPEDEF_EDIT_CLASSES",
    "VARIABLE_EDIT_CLASSES",
    "all_model_classes",
    "edit_langs",
    "model_classes",
]
