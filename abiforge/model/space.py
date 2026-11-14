"""The language space the generated edit corpus samples, and which of it is feasible.

A *point* in the space is one combination of six axes::

    construct x language x standard x config feature x build toolchain
      x consumer toolchain x evidence x edit

- **construct** (``CONSTRUCTS``): the language construct an edit operator
  changes, read off the operator's roles (``ROLE_CONSTRUCT``) or, when it needs
  a language feature, off that feature (``FEATURE_CONSTRUCT``);
- **edit** (``edit_values``): the class of the edit within its construct, as
  ``abiforge.model.edit_model`` defines it from C/C++ layout rules, not from the
  operators. Record layout (``struct``) and base-class layout
  (``inheritance``) are split; every other construct has the single value
  ``any``. A model class no operator generates stays in the denominator and
  is listed by ``edit_classes_without_operator``;
- **language** / **standard**: ``abiforge.model.config.STANDARDS``;
- **config feature** (``CONFIG_FEATURES``): one ABI-relevant setting the cell
  turns on (``baseline`` = none of them), on either side of a rebuild;
- **build** / **consumer** toolchain: the family that builds the library and
  the one that builds its consumers (``abiforge.model.config.FAMILIES``); a
  (build, consumer) pair that cannot work is infeasible through
  ``PAIR_RULES``;
- **evidence**: ``abiforge.model.config.EVIDENCE``, with ``headers`` split by
  the backend that dumps them (``SPACE_EVIDENCE``: ``headers.castxml``,
  ``headers.clang``, ``headers.gcc``), so a header backend no cell ran shows
  as an uncovered axis value rather than vanishing from the denominator.

``infeasible(point)`` says why a point cannot be a meaningful cell (concepts
before C++20, a C++-only construct in C, ``-fabi-version`` on clang, a family
that does not target Linux ELF), or ``None``. ``cell_points`` maps one corpus
cell (operator, seed, config) onto the points it exercises. The generator
(``abiforge.run.cells.seed_conflict``) and the coverage report
(``abiforge.coverage``) both read this module, so a rule changes in one
place.

What a covered fraction of this space does NOT mean: a covered point is one
that some cell *ran*, not one where abicheck was right (that is the ChangeKind
and mismatch report). A construct's edit sub-classes are covered only where
the edit axis splits it; a construct with a single ``any`` value is covered by
any one of its operators. The axes are the corpus's
own; a dimension the corpus cannot vary (OS, architecture beyond -m32, link
order) is not in the denominator at all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from abiforge.model import edit_model as _model
from abiforge.model.config import (
    EVIDENCE,
    FAMILIES,
    FEATURES,
    HEADER_BACKENDS,
    STANDARDS,
    Config,
    Consumer,
    Flags,
    ResolvedConsumer,
    Standard,
)

if TYPE_CHECKING:
    from abiforge.model.seeds import Seed
    from abiforge.operators import EditOp

AXES: tuple[str, ...] = (
    "construct",
    "lang",
    "std",
    "config",
    "build",
    "consumer",
    "evidence",
    "edit",
)

#: The evidence axis: ``headers`` once per header backend.
SPACE_EVIDENCE: tuple[str, ...] = tuple(
    ev
    for e in EVIDENCE
    for ev in ([f"headers.{b}" for b in HEADER_BACKENDS] if e == "headers" else [e])
)

# The corpus builds and runs Linux ELF shared libraries only.
TARGET_FAMILIES: frozenset[str] = frozenset({"gcc", "clang"})
LANGS: tuple[str, ...] = ("c", "c++")


@dataclass(frozen=True)
class Construct:
    name: str
    langs: frozenset[str]
    # The ``abiforge.model.config.FEATURES`` entry the construct needs, if any.
    feature: str | None = None


_BOTH = frozenset(LANGS)
_CXX = frozenset({"c++"})
_C = frozenset({"c"})

CONSTRUCTS: tuple[Construct, ...] = (
    Construct("struct", _BOTH),
    Construct("enum", _BOTH),
    Construct("enum_fixed_underlying", _BOTH, "enum_underlying"),
    Construct("enum_class", _CXX, "enum_class"),
    Construct("typedef", _BOTH),
    Construct("function", _BOTH),
    Construct("inline_function", _BOTH, "inline"),
    Construct("variable", _BOTH),
    Construct("visibility_block", _BOTH),
    Construct("class_virtual", _CXX),
    Construct("template", _CXX),
    Construct("concept", _CXX, "concepts"),
    Construct("no_unique_address", _CXX, "no_unique_address"),
    Construct("deduced_return", _CXX, "deduced_return"),
    Construct("noexcept_type", _CXX, "noexcept_in_type"),
    Construct("std_string", _CXX, "std_string"),
    Construct("restrict_pointer", _C, "restrict"),
    Construct("flexible_array", _C, "flexible_array"),
    Construct("atomic", _C, "atomic"),
    Construct("bit_int", _C, "bit_int"),
    Construct("char8_t", _CXX, "char8_t"),
    Construct("union", _BOTH),
    Construct("header_constant", _BOTH),
    Construct("deprecation", _BOTH),
    Construct("tls_variable", _BOTH),
    Construct("symbol_version", _BOTH),
    Construct("soname", _BOTH),
    Construct("link_option", _BOTH),
    Construct("compile_option", _BOTH),
    Construct("class", _CXX),
    Construct("inheritance", _CXX),
    Construct("inline_namespace", _CXX),
    Construct("abi_tag", _CXX),
    Construct("hidden_friend", _CXX),
    Construct("using_declaration", _CXX),
)
CONSTRUCT_BY_NAME: dict[str, Construct] = {c.name: c for c in CONSTRUCTS}

# Seed role -> construct. An exact role wins over its ``prefix.``; a role the
# map cannot place fails ``test_edit_corpus_space`` (a new seed role must say
# what it is).
ROLE_CONSTRUCT: dict[str, str] = {
    "struct": "struct",
    "enum.color": "enum",
    "enum.mode": "enum_class",
    "typedef": "typedef",
    "func": "function",
    "func.greet": "std_string",
    "func.name_len": "std_string",
    "var": "variable",
    "hidden": "visibility_block",
    "base": "class_virtual",
    "shape": "class_virtual",
    "buf": "template",
    "tmpl": "template",
    # c_elf
    "build.vscript": "symbol_version",
    "build.soname": "soname",
    "build.ldflags": "link_option",
    "build.cflags": "compile_option",
    "elf.const": "header_constant",
    "elf.union": "union",
    "elf.flags": "struct",
    "elf.buf": "struct",
    "elf.ctx": "struct",
    "elf.level": "enum",
    "elf.count_t": "typedef",
    "elf.legacy": "deprecation",
    "elf.tls": "tls_variable",
    "elf.obj": "variable",
    "elf.tab": "variable",
    "elf.k": "variable",
    "elf.al": "variable",
    "elf.banner": "variable",
    "elf": "function",
    # c_elf_alt (alt.gate admits an operator to the seed: no construct)
    "alt.obj": "variable",
    "alt.tls": "tls_variable",
    "alt.pair": "struct",
    "alt.int": "typedef",
    "alt.time": "typedef",
    "alt.inl": "inline_function",
    "alt": "function",
    # cxx_alt
    "xa.uniq": "inline_function",
    "xa.str": "std_string",
    "xa.tv": "struct",
    "xa.leak": "struct",
    "xa.inl": "inline_function",
    "xa.tmpl": "template",
    "xa": "function",
    # cxx_more
    "x.widget": "class",
    "x.gadget": "class",
    "x.sealed": "class",
    "x.calc": "class",
    "x.plain": "class",
    "x.poly": "class_virtual",
    "x.vbase": "inheritance",
    "x.multi": "inheritance",
    "x.ns": "inline_namespace",
    "x.tagged": "abi_tag",
    "x.cmp": "hidden_friend",
    "x.tone": "enum",
    "x.shade": "enum_class",
    "x.quals": "class",
    "x.shape": "class_virtual",
    "x.ovr": "class_virtual",
    "x.defs": "class",
    "x.vnx": "class_virtual",
    "x.ctor2": "class",
    "x.vtt": "inheritance",
    "x.tailpad": "struct",
    "x.inspad": "struct",
    "x.rmpad": "struct",
    "x.helper": "function",
    "lay": "struct",
    "layb": "inheritance",
    "x": "function",
    # cxx_more: one baseline per ChangeKind operator (operators/kinds.py)
    "k.vbo": "inheritance",
    "k.svg": "inheritance",
    "k.tpr": "struct",
    "k.rsv": "struct",
    "k.tvs": "typedef",
    "k.hnd": "typedef",
    "k.opq": "struct",
    "k.isa": "function",
    "k.inst": "template",
    "k.ovl": "function",
    "k.inv": "inline_namespace",
    "k.exp": "function",
    "k.tag": "template",
    "k.cpo": "function",
    "k.ret": "function",
    "k.tpp": "function",
    "k.slk": "struct",
    "k.mtp": "template",
    "k.aeq": "struct",
    "k.del": "function",
    "k.ibr": "inline_function",
    "k.urt": "deduced_return",
    "k.rex": "using_declaration",
    "k.pnv": "class_virtual",
    "k.stl": "std_string",
    "k.anon": "function",
}

# Roles that only admit an operator to a seed (``Seed.gate_role``).
GATE_ROLES = frozenset({"alt.gate", "xa.gate"})

# Language feature an operator needs -> the construct it exercises.
FEATURE_CONSTRUCT: dict[str, str] = {
    c.feature: c.name for c in CONSTRUCTS if c.feature is not None
}


def role_construct(role: str) -> str:
    if role in ROLE_CONSTRUCT:
        return ROLE_CONSTRUCT[role]
    prefix = role.split(".", 1)[0]
    if prefix in ROLE_CONSTRUCT:
        return ROLE_CONSTRUCT[prefix]
    raise KeyError(f"seed role {role!r} has no construct in ROLE_CONSTRUCT")


# Operators whose roles name a carrier (``elf.extra``, a ``func.*`` or class
# seed role holding several declarations) rather than what the edit changes:
# the role-derived construct is not the one the edit touches. A key must be a real operator (``test_edit_corpus_edit_axis``).
OP_CONSTRUCT: dict[str, tuple[str, ...]] = {
    # declarations of another construct added next to the seed's functions
    "object_added": ("variable",),
    "source_only_var_export_added": ("variable",),
    "header_constant_added": ("header_constant",),
    "record_added": ("struct",),
    # record, typedef and enum edits whose second role is a carrier function
    "field_renamed": ("struct",),
    "record_became_opaque": ("struct",),
    "struct_became_union": ("struct",),
    "record_removed": ("struct",),
    "typedef_removed": ("typedef",),
    "enum_member_renamed": ("enum",),
    "serialization_tag_swapped": ("enum",),
    "internal_table_grown": ("variable",),
    # free functions declared beside the seed's class
    "extern_c_linkage_removed": ("function",),
    "param_default_value_changed": ("function",),
    "param_default_value_removed": ("function",),
    "param_restrict_removed": ("function",),
    # the same sources rebuilt with other flags
    "rebuild_hidden_visibility": ("compile_option",),
    "rebuild_optimized": ("compile_option",),
    "rebuild_m32": ("compile_option",),
}


def op_constructs(op: EditOp) -> tuple[str, ...]:
    """The constructs *op* exercises: as declared, else ``OP_CONSTRUCT``'s,
    else its features', else its roles'."""
    if op.constructs:
        names = list(op.constructs)
    elif op.name in OP_CONSTRUCT:
        names = list(OP_CONSTRUCT[op.name])
    elif op.features:
        names = [FEATURE_CONSTRUCT[f] for f in op.features]
    else:
        names = [role_construct(r) for r in op.roles if r not in GATE_ROLES]
    return tuple(dict.fromkeys(names))


# --- edit sub-classes -------------------------------------------------------
#
# A construct says what an edit touches, not how. The classes an edit can
# fall into are defined by ``abiforge.model.edit_model`` from C/C++ layout rules,
# independently of the operators: every class there is a value of the edit
# axis, and so enters the denominator, whether or not an operator generates
# it. ``formal/EXPERIMENTS.md`` (E4) shows why a single representative per
# construct is unsound: the same edit can be absorbed or not depending on the
# layout it lands in.
#
# ``OP_EDIT_CLASS`` is the other direction: which model class each operator
# of a split construct exercises. It may only name classes the model has, and
# every operator of a split construct must be in it (no catch-all class). A
# model class no operator maps to is reported by
# ``edit_classes_without_operator`` as an explicit uncovered gap.

ANY_EDIT = _model.ANY_EDIT

OP_EDIT_CLASS: dict[str, dict[str, str]] = {
    "struct": {
        "struct_append_field": "insert_tail_shifts",
        "field_added_in_tail_padding": "insert_tail_absorbed",
        "field_inserted_in_padding": "insert_middle_absorbed",
        "struct_remove_trailing_field": "remove_tail_shifts",
        "field_removed_without_shrink": "remove_middle_absorbed",
        "struct_swap_fields": "reorder",
        "struct_field_widen": "retype_grows",
        "struct_array_grow": "array_grow_shifts",
        "struct_array_shrink_in_padding": "array_shrink_absorbed",
        "rebuild_pack_struct": "packing_change",
        "record_became_packed": "packing_change",
        "field_became_bitfield": "field_to_bitfield",
        "field_renamed": "member_rename",
        "field_became_const": "member_cv_change",
        "field_became_volatile": "member_cv_change",
        "record_became_opaque": "record_opaque",
        "struct_became_union": "record_kind_change",
        "record_removed": "record_removed",
        "record_added": "record_added",
        "returned_struct_grew_past_registers": "insert_tail_shifts",
        # Holder embeds detail::Impl by value; Impl grows, moving Holder::x: a
        # member whose type grew, not a member appended at the tail.
        "internal_type_grew": "retype_grows",
        "by_value_struct_became_nontrivial": "record_triviality_change",
        "field_inserted_at_head": "insert_head_shifts",
        "field_inserted_in_middle": "insert_middle_shifts",
        "field_removed_at_head": "remove_head_shifts",
        "field_removed_in_middle": "remove_middle_shifts",
        "field_removed_into_tail_padding": "remove_tail_absorbed",
        "field_retyped_same_layout": "retype_same_layout",
        "field_retyped_smaller": "retype_shrinks",
        "field_retyped_alignment_only": "retype_align_only",
        "array_shrunk_shifting": "array_shrink_shifts",
        "array_grown_into_padding": "array_grow_absorbed",
        "bitfield_width_within_unit": "bitfield_width_absorbed",
        "bitfield_width_crosses_unit": "bitfield_width_shifts",
        "bitfield_became_field": "bitfield_to_field",
        "record_alignment_raised": "record_align_change",
        "cxx_record_added": "record_added",
        "record_lost_trivially_copyable": "record_triviality_change",
        "record_lost_standard_layout": "member_access_change",
        "reserved_field_used": "member_rename",
        "nonpod_field_added_in_tail_padding": "insert_tail_absorbed",
        "opaque_record_defined": "record_defined",
        "struct_became_class": "record_kind_change",
        "field_retyped_abi_equivalent": "retype_same_layout",
    },
    "inheritance": {
        "base_became_virtual": "base_became_virtual",
        "bases_reordered": "base_reorder",
        "primary_base_grew": "base_layout_changed",
        "vtt_grew": "base_add_virtual",
        "base_removed": "base_remove",
        "base_added_nonvirtual": "base_add_nonvirtual",
        "base_became_nonvirtual": "base_became_nonvirtual",
        "base_access_narrowed": "base_access_changed",
        "virtual_bases_reordered": "base_reorder",
        "secondary_base_became_polymorphic": "base_layout_changed",
    },
    "function": {
        "func_param_append": "param_added",
        "func_param_remove": "param_removed",
        "func_param_type_change": "param_retyped",
        "param_became_va_list": "param_retyped",
        "param_lost_va_list": "param_retyped",
        "param_pointer_level_changed": "param_pointer_level",
        "param_pointee_const_added": "param_pointee_cv",
        "param_pointee_const_to_volatile": "param_pointee_cv",
        "param_rename_header_only": "param_renamed",
        "params_reordered": "params_reordered",
        "default_argument_added": "default_argument_changed",
        "param_default_value_changed": "default_argument_changed",
        "param_default_value_removed": "default_argument_changed",
        "param_restrict_removed": "param_qualifier_change",
        "func_return_type_change": "return_retyped",
        "return_pointer_level_changed": "return_pointer_level",
        "return_pointee_const_added": "return_pointee_cv",
        "return_pointee_const_removed": "return_pointee_cv",
        "return_deep_pointee_const_removed": "return_pointee_cv",
        "func_became_variadic": "variadic_change",
        "func_lost_variadic": "variadic_change",
        "func_became_ms_abi": "calling_convention_change",
        "func_exception_spec_widened": "exception_spec_change",
        "func_contract_attribute_added": "attribute_change",
        "func_contract_attribute_removed": "attribute_change",
        "func_became_extern_c": "language_linkage_change",
        "extern_c_linkage_removed": "language_linkage_change",
        "func_out_of_line_to_inline": "became_inline",
        "inline_function_outlined": "became_out_of_line",
        "imported_symbol_added": "body_change",
        "imported_symbol_removed": "body_change",
        "glibc_version_required_added": "body_change",
        "glibc_older_version_required": "body_change",
        "glibc_version_requirement_dropped": "body_change",
        "fortified_call_dropped": "body_change",
        "text_relocation_introduced": "body_change",
        "text_relocation_removed": "body_change",
        "writable_executable_segment": "body_change",
        "overload_added": "overload_added",
        "func_add": "function_added",
        "export_surface_exploded": "function_added",
        "allocator_replacement_added": "function_added",
        "std_template_instance_exported": "function_added",
        "func_remove": "function_removed",
        "allocator_replacement_removed": "function_removed",
        "symbols_prefixed_batch": "function_renamed",
        "api_version_suffix_bumped": "function_renamed",
        "declared_but_not_exported": "export_declaration_mismatch",
        "exported_but_undeclared": "export_declaration_mismatch",
        "source_only_export_added": "export_declaration_mismatch",
        "declared_exports_defined": "export_declaration_mismatch",
        "func_became_weak": "binding_change",
        "weak_became_global": "binding_change",
        "func_became_protected": "visibility_change",
        "func_lose_default_visibility": "visibility_change",
        "internal_helper_hidden": "visibility_change",
        "version_node_added_for_new_symbol": "version_change",
        "symbol_moved_version_node_keeping_alias": "version_change",
        "unversioned_export_added": "version_change",
        "func_became_ifunc": "symbol_type_change",
        "ifunc_removed": "symbol_type_change",
        # adds functions (and a STB_GNU_UNIQUE static inside one): no existing
        # symbol changes type
        "gnu_unique_static_added": "function_added",
        "comment_only": "declaration_text_only",
        "cpu_dispatch_variants_dropped": "function_removed",
        "overload_retyped": "param_retyped",
        "experimental_api_graduated": "function_added",
        "experimental_api_removed": "function_removed",
        "function_became_customization_object": "function_removed",
        "template_return_argument_changed": "return_retyped",
        "template_param_argument_changed": "param_retyped",
        "declared_function_lost_definition": "export_declaration_mismatch",
        "declared_function_export_became_c_namesake": "export_declaration_mismatch",
        "unnamed_type_api_added": "function_added",
        "reorder_declarations": "declaration_text_only",
    },
}


def edit_values(construct: str) -> tuple[str, ...]:
    """The edit classes a construct's points are split into: the model's."""
    return _model.model_classes(construct)


def edit_class(op: EditOp, construct: str) -> str:
    """The edit class *op* exercises under *construct*.

    Raises ``KeyError`` for an operator of a split construct that
    ``OP_EDIT_CLASS`` does not place: a new operator must say which class it
    generates (``test_edit_corpus_edit_axis`` names it).
    """
    if construct not in _model.EDIT_MODEL:
        return ANY_EDIT
    try:
        return OP_EDIT_CLASS[construct][op.name]
    except KeyError:
        raise KeyError(
            f"operator {op.name!r} edits {construct!r} but has no edit class in "
            "OP_EDIT_CLASS"
        ) from None


def edit_classes_without_operator() -> tuple[str, ...]:
    """Model classes (``construct/class``) no operator generates: the gaps."""
    mapped = {(con, cls) for con, ops in OP_EDIT_CLASS.items() for cls in ops.values()}
    return tuple(
        c.id for c in _model.all_model_classes() if (c.construct, c.name) not in mapped
    )


def edit_class_report() -> dict[str, dict[str, list[str]]]:
    """Per split construct, each model class and the operators that generate it
    (an empty list is a gap)."""
    out: dict[str, dict[str, list[str]]] = {}
    for con, classes in _model.EDIT_MODEL.items():
        ops = OP_EDIT_CLASS.get(con, {})
        out[con] = {
            c.name: sorted(o for o, cls in ops.items() if cls == c.name)
            for c in classes
        }
    return out


# --- config features --------------------------------------------------------


@dataclass(frozen=True)
class ConfigFeature:
    name: str
    on: Callable[[Flags], bool]
    # Why the feature cannot apply to (lang, family), or None.
    gate: Callable[[str, str], str | None] = lambda _lang, _family: None


def _cxx_only(lang: str, _family: str) -> str | None:
    return None if lang == "c++" else "_GLIBCXX_USE_CXX11_ABI is a libstdc++ C++ macro"


def _gcc_only(_lang: str, family: str) -> str | None:
    return None if family == "gcc" else "-fabi-version is a gcc flag"


# One entry per ABI-relevant setting a cell can turn on, on either side.
CONFIG_FEATURES: tuple[ConfigFeature, ...] = (
    ConfigFeature("O2", lambda f: f.opt == "O2"),
    ConfigFeature("hidden_visibility", lambda f: f.visibility == "hidden"),
    ConfigFeature("m32", lambda f: f.bits == 32),
    ConfigFeature("short_enums", lambda f: f.short_enums),
    ConfigFeature("packed", lambda f: f.pack is not None),
    ConfigFeature("cxx11_abi_0", lambda f: f.cxx11_abi == 0, _cxx_only),
    ConfigFeature("fabi_version", lambda f: f.abi_version is not None, _gcc_only),
)
# A setting of the consumer rather than of the flags: consumer flags that
# differ from the producer's. (The consumer's toolchain is its own axis.)
CONSUMER_FEATURES: tuple[str, ...] = ("consumer_flags",)
CONFIG_AXIS: tuple[str, ...] = (
    "baseline",
    *(f.name for f in CONFIG_FEATURES),
    *CONSUMER_FEATURES,
)
_FEATURE_BY_NAME = {f.name: f for f in CONFIG_FEATURES}

# Constructs a config feature cannot be combined with, and why. The generator
# refuses these cells (``abiforge.run.cells.seed_conflict`` and ``pair_blocked``
# call ``seed_flags_conflict``).
CONSTRUCT_CONFIG_CONFLICTS: dict[tuple[str, str], str] = {
    ("std_string", "packed"): (
        "-fpack-struct makes std::string incompatible with the installed libstdc++"
    ),
    ("std_string", "short_enums"): (
        "-fshort-enums makes std::string incompatible with the installed libstdc++"
    ),
}


def config_features(
    config: Config,
    new_flags: Flags | None = None,
    consumer: ResolvedConsumer | None = None,
) -> tuple[str, ...]:
    """The config features a cell turns on: on the build (either side of a
    rebuild) or on *consumer* (every consumer of the build when None)."""
    consumers = (consumer,) if consumer is not None else config.consumer_set
    sides = [config.flags, *(c.flags for c in consumers)]
    if new_flags is not None:
        sides.append(new_flags)
    out = [f.name for f in CONFIG_FEATURES if any(f.on(s) for s in sides)]
    # Flags differing from what that consumer's family would build the seed with.
    if any(
        c.flags != config.resolve(Consumer(toolchain=c.toolchain)).flags
        for c in consumers
    ):
        out.append("consumer_flags")
    return tuple(out) or ("baseline",)


# --- points -----------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Point:
    construct: str
    lang: str
    std: str
    config: str
    build: str
    consumer: str
    evidence: str
    # The edit sub-class (``edit_values``); ``any`` for constructs not split.
    edit: str = ANY_EDIT

    def axis(self, name: str) -> str:
        return str(getattr(self, name))

    @property
    def id(self) -> str:
        return "/".join(self.axis(a) for a in AXES)


_STD_BY_NAME: dict[str, Standard] = {s.name: s for s in STANDARDS}


# (build, consumer) pairs that cannot work, whatever the edit: each rule
# returns why, or None. The toolchain axes (C++ standard library, dual ABI,
# word size, a consumer standard lacking a feature) plug their pair rules in
# here, so the generator and the report agree on what is feasible.
PAIR_RULES: tuple[Callable[[Point], str | None], ...] = ()


def infeasible(p: Point) -> str | None:
    """Why *p* cannot be a meaningful corpus cell, or ``None`` when it can."""
    std = _STD_BY_NAME[p.std]
    if std.lang != p.lang:
        return f"{p.std} is not a {p.lang} standard"
    for side in (p.build, p.consumer):
        if side not in TARGET_FAMILIES:
            return f"{side} does not target Linux ELF, the corpus's only platform"
    for rule in PAIR_RULES:
        if (why := rule(p)) is not None:
            return why
    con = CONSTRUCT_BY_NAME[p.construct]
    if p.lang not in con.langs:
        return f"{p.construct} is not a {p.lang} construct"
    if con.feature is not None:
        first = FEATURES[con.feature].get(p.lang)
        if first is None or std.year < first:
            return f"{p.construct} needs {con.feature}, which {p.std} lacks"
    feat = _FEATURE_BY_NAME.get(p.config)
    if feat is not None and (why := feat.gate(p.lang, p.build)):
        return why
    if (why := CONSTRUCT_CONFIG_CONFLICTS.get((p.construct, p.config))) is not None:
        return why
    if p.edit not in edit_values(p.construct):
        return f"{p.edit} is not an edit sub-class of {p.construct}"
    langs = _model.edit_langs(p.construct, p.edit)
    if langs is not None and p.lang not in langs:
        return f"{p.construct}/{p.edit} has no {p.lang} spelling"
    return None


def all_points() -> Iterator[Point]:
    for con in CONSTRUCTS:
        for std in STANDARDS:
            for cfg in CONFIG_AXIS:
                for build in FAMILIES:
                    for consumer in FAMILIES:
                        for ev in SPACE_EVIDENCE:
                            for edit in edit_values(con.name):
                                yield Point(
                                    con.name,
                                    std.lang,
                                    std.name,
                                    cfg,
                                    build,
                                    consumer,
                                    ev,
                                    edit,
                                )


def feasible_points() -> frozenset[Point]:
    return frozenset(p for p in all_points() if infeasible(p) is None)


def cell_points(
    op: EditOp, config: Config, ran_consumers: frozenset[str] = frozenset()
) -> tuple[Point, ...]:
    """The points one corpus cell exercises: its constructs x its config
    features, once per (build, consumer) pair that ran (``ran_consumers``, by
    ``ResolvedConsumer.id``). An allow-list: a pair that was infeasible or
    unavailable, or a cell with no execution evidence at all (a non-ELF host,
    no oracle), exercises nothing."""
    new = op.new_flags(config.flags) if op.rebuild is not None else None
    points = (
        Point(
            con,
            config.lang,
            config.std.name,
            cfg,
            config.producer.family,
            c.toolchain.family,
            config.space_evidence,
            edit_class(op, con),
        )
        for c in config.consumer_set
        if c.id in ran_consumers
        for con in op_constructs(op)
        for cfg in config_features(config, new, c)
    )
    return tuple(dict.fromkeys(points))


def seed_constructs(seed: Seed, op: EditOp | None = None) -> tuple[str, ...]:
    """Every construct the translation units of a cell contain.

    The seed's baseline contributes the roles it actually spells (a role
    whose baseline text is empty, filled only by an operator, contains
    nothing); *op*, when given, adds what the edit introduces, since the new
    side is built and consumed too.
    """
    names = [role_construct(r) for r, (h, c) in seed.roles.items() if h or c]
    if op is not None:
        names.extend(op_constructs(op))
    return tuple(dict.fromkeys(names))


def seed_flags_conflict(
    seed: Seed, flags: Flags, op: EditOp | None = None
) -> str | None:
    """Why a cell of *seed* (edited by *op*) cannot be built or consumed under *flags*.

    The rule is a property of what the translation units contain: one that
    uses ``std::string`` anywhere, in the baseline or through the edit,
    cannot be built with a flag that breaks the installed libstdc++'s layout,
    even when the edited role is a plain function. The generator applies it
    to the build and to every consumer.
    """
    feats = [f.name for f in CONFIG_FEATURES if f.on(flags)]
    for con in seed_constructs(seed, op):
        for cfg in feats:
            why = CONSTRUCT_CONFIG_CONFLICTS.get((con, cfg))
            if why is not None:
                return why
    return None


# --- the report -------------------------------------------------------------


def coverage(covered: Iterable[Point], top: int = 20) -> dict[str, object]:
    """Covered against feasible points: overall, per axis value, and the gaps.

    ``top_uncovered`` ranks feasible uncovered points by how neglected their
    axis values are (lowest summed per-value coverage first), so the list
    points at the thinnest regions rather than at an arbitrary corner.
    """
    feasible = feasible_points()
    got = set(covered)
    impossible = sorted(p.id for p in got - feasible)
    got &= feasible
    per_axis: dict[str, dict[str, dict[str, int]]] = {}
    for axis in AXES:
        rows: dict[str, dict[str, int]] = {}
        for p in feasible:
            row = rows.setdefault(p.axis(axis), {"covered": 0, "feasible": 0})
            row["feasible"] += 1
            row["covered"] += p in got
        per_axis[axis] = dict(sorted(rows.items()))

    def ratio(axis: str, value: str) -> float:
        row = per_axis[axis][value]
        return row["covered"] / row["feasible"]

    def per_axis_edit(con: str, cls: str) -> dict[str, int]:
        pts = [p for p in feasible if p.construct == con and p.edit == cls]
        return {"covered": sum(p in got for p in pts), "feasible": len(pts)}

    gaps = sorted(
        feasible - got,
        key=lambda p: (sum(ratio(a, p.axis(a)) for a in AXES), p),
    )
    return {
        "feasible": len(feasible),
        "covered": len(got),
        "total": sum(1 for _ in all_points()),
        "per_axis": per_axis,
        "top_uncovered": [p.id for p in gaps[:top]],
        # A covered point the model calls infeasible: the model is wrong.
        "covered_but_infeasible": impossible,
        # Per split construct, each model edit class: its generating operators
        # and covered / feasible points. A class with no operator is still in
        # the denominator, and listed in ``edit_classes_without_operator``.
        "edit_classes": {
            con: {
                cls: {
                    "operators": ops,
                    **per_axis_edit(con, cls),
                }
                for cls, ops in classes.items()
            }
            for con, classes in edit_class_report().items()
        },
        "edit_classes_without_operator": list(edit_classes_without_operator()),
    }


def point_from_id(ident: str) -> Point:
    parts = ident.split("/")
    if len(parts) == len(AXES) - 1:
        # A pre-edit-axis record cannot be mapped: `any` is infeasible for a
        # struct, so guessing it would report covered-but-infeasible points.
        raise ValueError(
            f"point id {ident!r} predates the edit axis; re-record the coverage"
        )
    if len(parts) != len(AXES):
        raise ValueError(f"not a space point id: {ident!r}")
    return Point(*parts)


__all__ = [
    "ANY_EDIT",
    "AXES",
    "CONFIG_AXIS",
    "CONSTRUCTS",
    "OP_CONSTRUCT",
    "OP_EDIT_CLASS",
    "Point",
    "edit_class",
    "edit_class_report",
    "edit_classes_without_operator",
    "edit_values",
    "SPACE_EVIDENCE",
    "all_points",
    "cell_points",
    "config_features",
    "seed_flags_conflict",
    "seed_constructs",
    "coverage",
    "feasible_points",
    "infeasible",
    "op_constructs",
    "point_from_id",
]
