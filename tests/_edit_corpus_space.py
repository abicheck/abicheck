"""The language space the generated edit corpus samples, and which of it is feasible.

A *point* in the space is one combination of six axes::

    construct x language x standard x config feature x build toolchain
      x consumer toolchain x evidence x edit

- **construct** (``CONSTRUCTS``): the language construct an edit operator
  changes, read off the operator's roles (``ROLE_CONSTRUCT``) or, when it needs
  a language feature, off that feature (``FEATURE_CONSTRUCT``);
- **edit** (``STRUCT_EDIT_CLASSES``, ``edit_values``): the sub-class of the
  edit within its construct. Record layout splits by its effect on offsets and
  sizeof (insert/append/remove, absorbed by padding or not); every other
  construct has the single value ``any``, so its denominator is unchanged;
- **language** / **standard**: ``_edit_corpus_config.STANDARDS``;
- **config feature** (``CONFIG_FEATURES``): one ABI-relevant setting the cell
  turns on (``baseline`` = none of them), on either side of a rebuild;
- **build** / **consumer** toolchain: the family that builds the library and
  the one that builds its consumers (``_edit_corpus_config.FAMILIES``); a
  (build, consumer) pair that cannot work is infeasible through
  ``PAIR_RULES``;
- **evidence**: ``_edit_corpus_config.EVIDENCE``, with ``headers`` split by
  the backend that dumps them (``SPACE_EVIDENCE``: ``headers.castxml``,
  ``headers.clang``, ``headers.gcc``), so a header backend no cell ran shows
  as an uncovered axis value rather than vanishing from the denominator.

``infeasible(point)`` says why a point cannot be a meaningful cell (concepts
before C++20, a C++-only construct in C, ``-fabi-version`` on clang, a family
that does not target Linux ELF), or ``None``. ``cell_points`` maps one corpus
cell (operator, seed, config) onto the points it exercises. The generator
(``_edit_corpus_cells.seed_conflict``) and the coverage report
(``_edit_corpus_coverage``) both read this module, so a rule changes in one
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

from tests._edit_corpus_config import (
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
    from tests._edit_corpus_operators import EditOp
    from tests._edit_corpus_seeds import Seed

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
    # The ``_edit_corpus_config.FEATURES`` entry the construct needs, if any.
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
    "x": "function",
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


def op_constructs(op: EditOp) -> tuple[str, ...]:
    """The constructs *op* exercises: as declared, else its features', else its roles'."""
    if op.constructs:
        names = list(op.constructs)
    elif op.features:
        names = [FEATURE_CONSTRUCT[f] for f in op.features]
    else:
        names = [role_construct(r) for r in op.roles if r not in GATE_ROLES]
    return tuple(dict.fromkeys(names))


# --- edit sub-classes -------------------------------------------------------
#
# A construct says what an edit touches, not how. Record layout is the one
# construct whose edits split by their layout effect: whether a member is
# added or removed, and whether the bytes it takes come from padding (the
# offsets and sizeof that follow are then unchanged) or move or resize
# everything after them. ``formal/EXPERIMENTS.md`` (E4) shows why a single
# representative per class is unsound: the same edit can be absorbed or not
# depending on the layout it lands in.
#
# Every other construct has one edit value, ``ANY_EDIT``: its edits are not
# split, so its denominator is unchanged.

ANY_EDIT = "any"

STRUCT_EDIT_CLASSES: tuple[str, ...] = (
    "append_grows",
    "append_absorbed",
    "insert_absorbed",
    "remove_shrinks",
    "remove_absorbed",
    "reorder_moves",
    "widen",
    "array_grow",
    "array_shrink_absorbed",
    # Struct edits that change no member layout: packing, qualifiers, bit-fields,
    # renames, record kind. Named so that every struct operator has one class.
    "member_other",
)

# Record-layout operator -> its sub-class. Every ``struct`` operator not listed
# is ``member_other``. ``insert_moves`` has no operator yet, so it is not a
# value: a class with no operator would be a coverage claim with nothing behind it.
LAYOUT_EDIT_CLASS: dict[str, str] = {
    "struct_append_field": "append_grows",
    "field_added_in_tail_padding": "append_absorbed",
    "field_inserted_in_padding": "insert_absorbed",
    "struct_remove_trailing_field": "remove_shrinks",
    "field_removed_without_shrink": "remove_absorbed",
    "struct_swap_fields": "reorder_moves",
    "struct_field_widen": "widen",
    "struct_array_grow": "array_grow",
    "struct_array_shrink_in_padding": "array_shrink_absorbed",
}

EDIT_CLASSES_BY_CONSTRUCT: dict[str, tuple[str, ...]] = {
    "struct": STRUCT_EDIT_CLASSES,
}


def edit_values(construct: str) -> tuple[str, ...]:
    """The edit sub-classes a construct's points are split into."""
    return EDIT_CLASSES_BY_CONSTRUCT.get(construct, (ANY_EDIT,))


def edit_class(op: EditOp, construct: str) -> str:
    """The edit sub-class *op* exercises under *construct*."""
    if construct not in EDIT_CLASSES_BY_CONSTRUCT:
        return ANY_EDIT
    return LAYOUT_EDIT_CLASS.get(op.name, "member_other")


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
# refuses these cells (``_edit_corpus_cells.seed_conflict`` and ``pair_blocked``
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


def seed_constructs(seed: Seed) -> tuple[str, ...]:
    """Every construct a seed's translation unit contains, edited or not."""
    return tuple(dict.fromkeys(role_construct(r) for r in seed.roles))


def seed_flags_conflict(seed: Seed, flags: Flags) -> str | None:
    """Why *seed* cannot be built (or consumed) under *flags*, whatever the edit.

    The rule is a property of what the translation unit contains: a seed that
    uses ``std::string`` anywhere cannot be built with a flag that breaks the
    installed libstdc++'s layout, even when the edited role is a plain
    function. The generator applies it to the build and to every consumer.
    """
    feats = [f.name for f in CONFIG_FEATURES if f.on(flags)]
    for con in seed_constructs(seed):
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
    "LAYOUT_EDIT_CLASS",
    "Point",
    "STRUCT_EDIT_CLASSES",
    "edit_class",
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
