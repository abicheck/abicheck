# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``ScopePath``/``EntityId`` — the one identity primitive (ADR-063 Phase 2).

**This module is a first, isolated slice of Phase 2, not the whole
phase.** See ``docs/contribute/plans/one-semantic-pipeline.md``'s "Phase 2
— EntityId/ScopePath as the one identity primitive" section for the full
design, including two questions this slice deliberately leaves open:

1. *Where ``ScopePath`` gets built from.* This module's ``entity_id_for_*``
   constructors take an already-built :data:`ScopePath` as input — they do
   not derive one from a parser's internal scope-tracking state
   (``entry.scope`` in ``dumper_clang.py``/``dumper_castxml.py`` today is a
   bare ``list[str]``, structurally insufficient to build a typed
   ``ScopePath`` from; see the plan). Widening that parser state, and
   deciding whether the resulting ``EntityId`` is computed once and carried
   on the model object or recomputed on demand (the plan's "no carrier
   field" open question, options (a)/(b)), is separate follow-on work.
2. *The mangled-name-is-genuine determination.* ``entity_id_for_function``/
   ``entity_id_for_variable`` take a caller-supplied ``mangled_name`` and
   trust it is a real mangling, not a bare name that merely rode in the
   mangled field (the ``extern "C"`` case). That determination stays owned
   by ``finding_identity.is_real_mangled_name``/``normalize_mangled_name``
   for now — this slice does not migrate that ~450-line, independently
   reviewed Itanium-mangling-validation machinery, and
   ``finding_identity.py`` does not yet delegate to this module. A future
   slice is expected to move that algorithm here and make
   ``finding_identity.resolve_function_identity`` a thin wrapper, per the
   plan's "direction of reuse" note — not attempted here, to keep this
   slice reviewable on its own.

What *is* real and load-bearing in this slice: the ``ScopePath`` segment
types and their identity-vs-payload field split, the ``EntityId`` shape
itself (``scope``, ``kind``, ``leaf_name``, ``extra`` — never a bare
``(ScopePath, kind)``, which collides sibling declarations), and the
``EntityKind``/``ObservationKind`` relocation from ``storage.entity_ids``
(domain vocabulary belongs in ``model``, not the storage wire layer, per
ADR-061's ``storage -> model`` import direction — ``storage.entity_ids`` now
imports these two enums rather than redefining them).

Leaf module: no dependency on ``checker_types``/``diff_*``/anything above
``model``, per ADR-063 D10.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field, replace

from .signature_normalization import canonicalize_function_signature_param_type

__all__ = [
    "Anonymous",
    "EntityId",
    "EntityKind",
    "InlineNamespace",
    "LocalToFunction",
    "Namespace",
    "ObservationKind",
    "Record",
    "ScopePath",
    "ScopeSegment",
    "canonicalize_type_param_references",
    "entity_id_for_constant",
    "entity_id_for_enum",
    "entity_id_for_function",
    "entity_id_for_type",
    "entity_id_for_typedef",
    "entity_id_for_variable",
    "with_mangled_name",
]


class EntityKind(enum.Enum):
    """What kind of logical thing an :class:`EntityId` names.

    Relocated here from ``storage.entity_ids`` (ADR-063 Phase 2 note above):
    this is domain vocabulary, not a storage wire concern.
    ``storage.entity_ids.EntityKind`` is this same enum, imported rather
    than redefined — exactly one ``EntityKind`` exists in the repository.
    """

    FUNCTION = "function"
    VARIABLE = "variable"
    TYPE = "type"
    ENUM = "enum"
    TYPEDEF = "typedef"
    CONSTANT = "constant"
    SYMBOL = "symbol"
    FIELD = "field"
    BASE = "base"


class ObservationKind(enum.Enum):
    """Where an occurrence of an entity was observed.

    Relocated from ``storage.entity_ids`` alongside :class:`EntityKind`,
    for the same reason. See ``storage.entity_ids.OccurrenceId`` for what
    consumes it today.
    """

    AST = "ast"
    DWARF = "dwarf"
    PDB = "pdb"
    EXPORT_TABLE = "export_table"
    TRANSLATION_UNIT = "translation_unit"
    SOURCE_LOCATION = "source_location"
    BUILD_UNIT = "build_unit"


# --------------------------------------------------------------------------
# ScopePath segment types
#
# Each segment states which of its own fields are identity and which are
# payload -- a bare frozen dataclass would make every field identity by
# default, which is wrong for `Record.access`. `field(compare=False)`
# excludes a field from both `__eq__` and the frozen-dataclass-generated
# `__hash__` (dataclass's `hash=None` default follows `compare`), so no
# separate `__eq__`/`__hash__` override is needed anywhere below.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Namespace:
    """An ordinary (non-inline) namespace segment. Every field is identity."""

    name: str


@dataclass(frozen=True)
class Record:
    """A record (class/struct/union) nesting scope.

    ``access`` (public/protected/private) is carried on the segment because
    a nested record's access is a real fact a consumer may want, but it is
    not part of *where* the nesting scope is: two snapshots of the same
    class with a member's access level changed still name the identical
    containing scope. Making ``access`` part of identity would turn an
    access-level change into a spurious identity mismatch -- the matcher
    would see "removed, then added" at a different ``EntityId`` instead of
    "this declaration changed."
    """

    name: str
    access: str = field(default="", compare=False)


@dataclass(frozen=True)
class InlineNamespace:
    """An inline namespace segment. Every field is identity.

    ``version_tag`` is exactly the dimension ADR-025's versioned-inline-
    namespace-alias handling already keys matching on; excluding it here
    would silently re-widen the ``v1``/``v2``-shaped collision that
    machinery exists to avoid.
    """

    name: str
    version_tag: str = ""


@dataclass(frozen=True)
class Anonymous:
    """An anonymous struct/union/enum/namespace scope.

    Both fields are identity, deliberately: nothing else disambiguates two
    sibling anonymous scopes coexisting in the same parent. ``ordinal`` is a
    deterministic per-parent sequence number assigned at parse time --
    stable *within one parse*, which is what makes it a legitimate
    disambiguator for two anonymous siblings in one snapshot. It is **not**
    stable across revisions: inserting a new anonymous sibling ahead of
    existing ones shifts every later sibling's ordinal, and therefore its
    whole ``EntityId``, even though nothing about those later declarations
    changed. No stable across-snapshot discriminator is adopted here -- see
    the plan's Phase 2 Design section for why (a source-location anchor and
    a structural fingerprint of the anonymous scope's own members were both
    considered and are each independently documented elsewhere in this
    codebase's AGENTS.md as unreliable for this exact purpose). This is an
    accepted, documented limitation of ``Anonymous`` identity specifically,
    not a silent gap.
    """

    kind: str
    ordinal: int


@dataclass(frozen=True)
class LocalToFunction:
    """A scope local to one function body.

    Both fields are identity, deliberately, for exactly the reason
    :class:`Anonymous` gives for its own ``ordinal``: ``owner`` alone
    disambiguates two same-named locals across *different* functions, but
    not two same-named locals in *sibling compound blocks of the same
    function* (``void f() { { struct A {}; } { struct A {}; } }`` -- two
    distinct declarations, same ``owner``, same leaf name). ``block_ordinal``
    closes that gap the same way ``Anonymous.ordinal`` closes its sibling
    case: a deterministic per-function sequence number assigned at parse
    time, stable *within one parse*, not across revisions -- the identical
    accepted, documented limitation :class:`Anonymous` already states (an
    edit that adds or removes an earlier local block shifts every later
    sibling's ordinal and therefore its whole ``EntityId``, even though
    nothing about those later declarations changed). No default is given,
    matching :class:`Anonymous.ordinal` for the same reason: a caller must
    supply a real value rather than silently under-specifying identity by
    relying on an unwired default.

    ``owner`` is the *owning function's own* :class:`EntityId`, not a bare
    string -- a plain function name collides two overloads that each
    declare a same-named local in their (corresponding) block
    (``f(int) { struct A {}; }`` vs. ``f(double) { struct A {}; }`` --
    identical ``owner="f"`` string, identical leaf name, so the two locals
    would wrongly merge under an unconstrained-string owner; Codex review,
    PR #941). Recursion is intentional and safe: `EntityId` is frozen/
    hashable, so an `EntityId` naming a function whose own scope path
    happens to include an outer `LocalToFunction` nests exactly as deep as
    the real declaration does, with no special-casing needed here.
    """

    owner: EntityId
    block_ordinal: int


ScopeSegment = Namespace | Record | InlineNamespace | Anonymous | LocalToFunction

#: An immutable, ordered sequence of typed scope segments, outermost first,
#: naming only the *containing* scope -- never the leaf declaration itself.
ScopePath = tuple[ScopeSegment, ...]


# --------------------------------------------------------------------------
# EntityId
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityId:
    """A logical declaration's identity.

    Never a bare ``(scope, kind)`` pair -- that collides any two sibling
    declarations of the same kind in the same scope (two enums, two
    variables, two typedefs, and -- the case that first exposed this --
    two function overloads). ``leaf_name`` is the declaration's own
    (unqualified) name, carried explicitly for every kind. ``extra`` is
    kind-specific and empty for most kinds (a record/enum/typedef/
    constant); a variable's and a function's constructors below populate it
    with the discriminator each of those two kinds specifically needs.
    """

    scope: ScopePath
    kind: EntityKind
    leaf_name: str
    extra: tuple[str, ...] = ()


def _scope_path(scope: ScopePath) -> ScopePath:
    """Normalize *scope* to a real ``tuple`` regardless of what iterable a
    caller passed, so two calls built from value-equal-but-not-identical
    scope sequences (e.g. a list vs. a tuple) produce ``EntityId``s that
    compare equal -- "computed once" here means one algorithm producing one
    answer for one input, not object identity of the scope argument.
    """
    return tuple(scope)


def _anonymous_self_extra(
    leaf_name: str, anonymous_ordinal: int | None
) -> tuple[str, ...]:
    """``extra`` for an anonymous record/enum declaration's OWN identity --
    distinct from :class:`Anonymous`'s ``ordinal``, which disambiguates a
    *descendant's* containing scope, not the anonymous declaration itself.
    ``ScopePath`` explicitly names only the containing scope, never the
    leaf declaration (this module's own docstring), so two anonymous
    sibling records/enums both passing ``leaf_name=""`` would otherwise
    collide onto one identical ``EntityId`` regardless of which one is
    meant (Codex review, PR #941). Only meaningful when *leaf_name* is
    empty -- a named declaration already disambiguates via ``leaf_name``,
    so *anonymous_ordinal* is ignored there rather than adding a second,
    redundant discriminator. Same deterministic per-parent sequence-number
    semantics, and the identical within-one-parse-only accepted
    limitation, as :class:`Anonymous`'s own ``ordinal``.
    """
    if leaf_name or anonymous_ordinal is None:
        return ()
    return ("anonymous", str(anonymous_ordinal))


def entity_id_for_type(
    scope: ScopePath, leaf_name: str, *, anonymous_ordinal: int | None = None
) -> EntityId:
    """``EntityId`` for a record/class/struct/union type. No kind-specific
    discriminator beyond *anonymous_ordinal*: a bare name is unambiguous
    once ``ScopePath`` disambiguates the containing scope, since two named
    types cannot share one name in one scope in valid C/C++ -- but an
    *anonymous* struct/union has no name to disambiguate with at all; see
    :func:`_anonymous_self_extra` for why *anonymous_ordinal* exists and
    when it applies.
    """
    return EntityId(
        scope=_scope_path(scope),
        kind=EntityKind.TYPE,
        leaf_name=leaf_name,
        extra=_anonymous_self_extra(leaf_name, anonymous_ordinal),
    )


def entity_id_for_enum(
    scope: ScopePath, leaf_name: str, *, anonymous_ordinal: int | None = None
) -> EntityId:
    """``EntityId`` for an enum type. See :func:`entity_id_for_type`,
    including for *anonymous_ordinal* -- an anonymous enum is exactly as
    real a case as an anonymous struct/union."""
    return EntityId(
        scope=_scope_path(scope),
        kind=EntityKind.ENUM,
        leaf_name=leaf_name,
        extra=_anonymous_self_extra(leaf_name, anonymous_ordinal),
    )


def entity_id_for_typedef(scope: ScopePath, leaf_name: str) -> EntityId:
    """``EntityId`` for a typedef/alias. See :func:`entity_id_for_type`."""
    return EntityId(
        scope=_scope_path(scope), kind=EntityKind.TYPEDEF, leaf_name=leaf_name
    )


def entity_id_for_constant(scope: ScopePath, leaf_name: str) -> EntityId:
    """``EntityId`` for a manifest/macro constant. See
    :func:`entity_id_for_type`."""
    return EntityId(
        scope=_scope_path(scope), kind=EntityKind.CONSTANT, leaf_name=leaf_name
    )


def entity_id_for_variable(
    scope: ScopePath,
    leaf_name: str,
    *,
    mangled_name: str | None = None,
    is_extern_c: bool = False,
) -> EntityId:
    """``EntityId`` for a variable.

    A bare ``(scope, "variable", leaf_name, ())`` is not enough: two
    exported variables sharing scope and leaf name but differing mangled
    names (two distinct, non-overloadable template-instantiation statics,
    or a declaration-vs-definition spelling mismatch the mangler doesn't
    collapse) are two different exports, not one -- "variables enable no
    alias tier at all ... a display-name join would hide a real removal"
    (AGENTS.md's own ``finding_identity.py``/``SymbolIdentityIndex`` entry,
    which this constructor generalizes rather than contradicts). So
    ``extra`` carries the mangled spelling whenever one exists, falling back
    to ``()`` only for the genuinely mangling-free case (no linker symbol at
    all -- e.g. a variable known only from a header declaration with no
    corresponding binary evidence).

    *mangled_name* must already be established as a genuine mangling by the
    caller, never a bare name that merely rode in the mangled field (an
    ``extern "C"`` producer's ``mangled == name``) -- see this module's
    docstring for why that determination is not made here. *is_extern_c* is
    the same separate, caller-supplied linkage signal
    :func:`entity_id_for_function` accepts, for the identical reason: a
    caller following *mangled_name*'s own contract passes ``None`` for an
    ``extern "C"`` variable (Codex review, PR #941 -- the fresh gap this
    parameter closes).

    When either *mangled_name* or *is_extern_c* applies, the resulting
    ``EntityId.scope`` is always ``()``, regardless of the caller-supplied
    *scope*. A genuine mangled name already fully and deterministically
    encodes scope, so folding a caller-supplied *scope* in on top adds
    nothing when it is available and actively fragments identity when it
    is not: a header/DWARF-derived observation may supply a real
    ``ScopePath`` for the identical symbol an export-table-only snapshot
    observes with no scope at all (Codex review, PR #941 -- the same
    evidence-tier-fragmentation mechanism :func:`entity_id_for_function`'s
    own *is_extern_c* branch already guards against, generalized here to
    every name-based -- as opposed to signature-based -- discriminator,
    mangled included). Only the genuinely mangling-free, non-``extern
    "C"`` degenerate case keeps *scope* as given.

    When *mangled_name* is present, *leaf_name* is likewise ignored, for
    a confirmed, not merely hypothetical, reason: the ELF-only fallback
    path (``dumper_elf_fallback.py``) constructs an export-only
    ``Variable``/``Function`` with ``name=sym, mangled=sym`` -- the raw
    exported symbol reused for *both* fields -- while a header/DWARF
    observation of the identical symbol supplies the real demangled short
    name for ``name``. Keying the mangled branch on *leaf_name* too would
    therefore fail to merge the two observations of one symbol precisely
    when the mangled evidence agrees they are the same declaration (Codex
    review, PR #941). The mangled spelling alone already disambiguates
    every declaration in ``extra``, so nothing is lost by dropping
    *leaf_name* here.
    """
    if mangled_name:
        extra: tuple[str, ...] = ("mangled", mangled_name)
        resolved_scope: ScopePath = ()
        resolved_leaf_name = ""
    elif is_extern_c:
        extra = ("extern_c",)
        resolved_scope = ()
        resolved_leaf_name = leaf_name
    else:
        extra = ()
        resolved_scope = _scope_path(scope)
        resolved_leaf_name = leaf_name
    return EntityId(
        scope=resolved_scope,
        kind=EntityKind.VARIABLE,
        leaf_name=resolved_leaf_name,
        extra=extra,
    )


def canonicalize_type_param_references(
    spelling: str, type_param_names: tuple[str, ...]
) -> str:
    """Replace each name in *type_param_names* with its 0-based position.

    A dependent type reference spells itself using the referenced
    template parameter's own declared name (confirmed by direct
    compilation) -- so ``template<class T> void f(T);`` and
    ``template<class U> void f(U);`` are the identical declaration under
    a pure parameter rename, but a caller building a per-parse
    discriminator from the raw spelling alone would see ``"T"`` and
    ``"U"`` and fingerprint them as two different overloads. A whole-word
    (``\\b``) substitution, so a name that is a substring of another
    (``T`` inside ``TT``) is never partially replaced, and a compound
    spelling (``"T *"``) still resolves correctly (``"type-param-0 *"``).
    Shared by :func:`entity_id_for_function` (for the ordinary parameter
    list) and ``extract.headers.clang.functions.
    function_template_param_kinds`` (for a non-type template parameter's
    own declared type, referencing either a preceding type/template-
    template parameter OR a preceding NON-TYPE one, e.g.
    ``decltype(N)`` -- Codex review, PR #943, on a later round: both are
    the identical hazard, just for two different sources of a "type
    spelling that can reference a template parameter").

    All substitutions happen in ONE combined regex pass, rather than one
    ``re.sub`` call per name applied sequentially to the same string --
    confirmed by direct compilation that the sequential form has its own
    self-inflicted collision: if an EARLIER name (e.g. ``T``) is replaced
    first, producing ``"type-param-0"``, and a LATER parameter happens to
    be named literally ``type`` (a legal, unremarkable C++ identifier),
    that later substitution's own ``\\btype\\b`` pattern matches the
    ``"type"`` INSIDE the already-generated ``"type-param-0"`` token,
    corrupting it into ``"type-param-1-param-0"`` -- and since this
    corruption only fires when a later name happens to collide with the
    generated marker's own prefix, renaming an entirely unrelated,
    unused parameter changed the ``EntityId`` too (Codex review, PR #943,
    on the version of this function that had that sequential form). A
    single combined-alternation pass never re-scans replacement text at
    all (Python's ``re.sub`` resumes scanning immediately after each
    match, in the ORIGINAL string, never inside what it just substituted),
    so this class of collision cannot occur regardless of what any
    parameter happens to be named.
    """
    index_by_name = {name: index for index, name in enumerate(type_param_names) if name}
    if not index_by_name:
        return spelling
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(name) for name in index_by_name) + r")\b"
    )
    return pattern.sub(lambda m: f"type-param-{index_by_name[m.group(1)]}", spelling)


def entity_id_for_function(
    scope: ScopePath,
    leaf_name: str,
    *,
    mangled_name: str | None = None,
    is_extern_c: bool = False,
    param_types: tuple[str, ...] = (),
    is_const: bool = False,
    is_volatile: bool = False,
    ref_qualifier: str = "",
    is_variadic: bool | None = None,
    template_param_kinds: tuple[str, ...] = (),
    type_param_names: tuple[str, ...] = (),
    return_type: str = "",
) -> EntityId:
    """``EntityId`` for a function.

    ``scope`` plus a bare name is not enough: ``f(int)`` and ``f(double)``
    share the same ``ScopePath`` and the same ``function`` kind, so without
    a third component two genuinely distinct overloads collapse into one
    id. Mirrors the existing tiered resolution
    ``finding_identity.resolve_function_identity``/``SymbolIdentityIndex``
    and ADR-048's normalized identity already establish: mangled name first
    when one exists (the common case, already globally unique per
    overload) -- ``extra`` becomes ``("mangled", mangled_name)``. Next,
    *is_extern_c* (a genuinely mangling-free C-linkage function -- ``extra``
    becomes the bare ``("extern_c",)`` tag, deliberately dropping every
    signature dimension: C has no overload resolution, so a changed
    parameter list is a modification of the one function named
    ``leaf_name``, not a different overload -- the same rule
    ``finding_identity.resolve_function_identity`` documents and applies via
    ``func.is_extern_c``). Only for the remaining case -- a non-``extern
    "C"`` function with no real mangling (a DWARF-only snapshot, which
    *can* be legally overloaded) -- does ``extra`` fall back to the
    normalized signature discriminator, ``("sig", *param_types,
    f"const:{is_const}", f"volatile:{is_volatile}", ref_qualifier,
    is_variadic)``. *is_const*/*is_volatile* are two independent booleans,
    not a ``cv_qualifiers`` string tuple -- deliberately mirroring
    ``resolve_function_identity``'s own ``func.is_const``/
    ``func.is_volatile`` representation exactly, rather than a tuple of
    qualifier tokens whose *order* a caller could supply inconsistently
    for the identical member-cv qualification (``"const volatile"`` vs.
    ``"volatile const"`` spell the same qualification but would collide as
    two different tuples; Codex review, PR #941). *ref_qualifier*
    (``"&"``/``"&&"``/``""``) and *is_variadic* mirror
    ``resolve_function_identity``'s own remaining fallback dimensions --
    without them ``C::f() &`` vs. ``C::f() &&``, or ``void f(int)`` vs.
    ``void f(int, ...)``, would collide on identical
    scope/name/param_types/const/volatile. ``is_variadic`` is ``bool |
    None`` rather than defaulting to ``False`` so a producer that
    genuinely doesn't know stays distinguishable from one that confirmed
    non-variadic, the same reason ``resolve_function_identity`` keeps that
    tri-state. Unlike
    ``finding_identity.normalized_signature``'s own fallback tuple, the
    callable's qualified name does *not* need to be repeated inside
    ``extra`` here -- ``scope``/``leaf_name`` already carry it losslessly,
    with no string-joining involved, so there is nothing left for the
    fallback tuple to lose by omitting it.

    All three branches are tagged (``"mangled"``/``"extern_c"``/``"sig"``)
    rather than left as a bare tuple, so a mangled name that happens to
    equal some function's literal signature-tuple spelling can never
    collide with it -- the branches occupy disjoint regions of ``extra``'s
    value space by construction, not by coincidence of what real mangled
    names or type spellings look like.

    *mangled_name* must already be established as a genuine mangling by the
    caller -- see this module's docstring for why that determination is not
    made here. *is_extern_c* is a separate, caller-supplied linkage signal:
    an ``extern "C"`` producer's ``mangled_name`` is typically ``None``
    (its raw export spelling is not a genuine mangling, so a caller
    following *mangled_name*'s own contract passes ``None`` for it) --
    *is_extern_c* is what tells this constructor to still take the
    signature-free branch in that case, rather than silently falling
    through to the signature-based fallback the way a caller relying on
    ``mangled_name`` alone would. When *mangled_name* is genuinely present,
    it wins outright and *is_extern_c*/*param_types*/*is_const*/
    *is_volatile*/*ref_qualifier*/*is_variadic*/*template_param_kinds* are
    all ignored -- there is nothing left for a signature-free tag to add
    once the mangled name already disambiguates the declaration.

    *template_param_kinds* is the per-position parameter-KIND signature
    (``"type"``, ``"template"``, ``"nontype:<type-spelling>"``) of an
    uninstantiated function/method template's own template parameter list,
    in declaration order -- e.g. ``("type",)`` for
    ``template<class T> void f()`` vs. ``("nontype:int",)`` for
    ``template<int N> void f()``. Two such templates can share scope, leaf
    name, and an identical (possibly empty) ordinary parameter list while
    still being genuinely distinct overloads, and neither gets a real
    mangled name from an AST-only producer (uninstantiated templates aren't
    mangled), so without this the *sig* fallback tuple would collide them
    (Codex review, PR #943). Folded in only when non-empty and tagged
    (``"tmpl", *template_param_kinds``) so an ordinary, non-template
    function's ``extra`` tuple is unchanged byte-for-byte.

    Both the *mangled* and *is_extern_c* branches' resulting
    ``EntityId.scope`` are always ``()``, regardless of *scope*.
    ``resolve_symbol_identity`` deliberately bases a real-mangled or
    extern-"C" identity on the raw name alone (``mangled:...``) rather
    than a qualified name, precisely because scope availability varies by
    evidence tier -- a header/DWARF-derived observation of a namespaced
    function (mangled or ``extern "C"``) may supply a real ``ScopePath``,
    while an export-table-only snapshot of the identical binary symbol
    knows only the bare exported/mangled name. Folding a caller-supplied
    *scope* into the id would fragment one entity's identity across those
    two evidence tiers even though the name already, on its own,
    unambiguously identifies the declaration -- for a genuine mangled
    name this holds losslessly (the mangling itself fully encodes scope);
    for ``extern "C"`` it holds because the symbol *is* that bare name at
    the ABI level, so no namespace is even recoverable from an export
    table alone. (An earlier revision of this docstring claimed the
    *mangled* branch's redundant `scope` was merely harmless rather than
    actively fragmenting -- Codex review on PR #941 caught that this is
    the identical mechanism the *is_extern_c* branch already guards
    against, not a different, safe case; corrected here.) The *sig*
    fallback is the one branch that keeps *scope* as given -- a DWARF-only,
    mangling-free, non-``extern "C"`` function has no authoritative,
    scope-independent name to fall back on, and scope is exactly what
    makes two same-named, same-signature sibling declarations in
    different scopes distinct.

    *param_types* are canonicalized via the sibling
    ``signature_normalization.canonicalize_function_signature_param_type``
    before joining into ``extra`` -- CastXML's ``"char const*"`` and Clang's
    ``"char const *"`` spell an otherwise-identical parameter type
    differently, and without canonicalization the same declaration
    observed by the two backends would get two different ``EntityId``s,
    fragmenting identity across header-AST backends the same way an
    uncanonicalized qualified name would. That function *also* drops a
    top-level BY-VALUE cv-qualifier a plain ``canonicalize_type_name``
    would keep (``"int"`` vs. ``"const int"``): per the C++ standard that
    qualifier is absent from the function's own type for linkage/mangling
    purposes, so ``void f(int)``/``void f(const int)`` name the same
    function and must not collide as two overloads -- while a *pointee*
    cv-qualifier on a pointer/reference parameter (``"char *"`` vs.
    ``"const char *"``) is deliberately left alone, since that genuinely
    is a standard-mandated, independently-mangled overload discriminator;
    see that function's own docstring for why the more permissive
    ``_strip_cv_qualifiers`` diff-reporting helpers use is wrong to reuse
    here. Mirrors ``resolve_function_identity``'s own canonicalization of
    ``func.params`` for the identical cross-backend-spelling reason, and
    its own ``func.is_extern_c``-gated omission of by-value param cv for
    the identical linkage reason (Codex review, PR #941).

    *param_types* are ALSO run through
    :func:`canonicalize_type_param_references` against *type_param_names*
    -- the enclosing function template's own type/template-template
    parameter names, in declaration order -- before the signature
    canonicalization above: ``template<class T> void f(T);`` and
    ``template<class U> void f(U);`` are the identical declaration under
    a pure rename, but an ordinary parameter's raw spelling names the
    template parameter literally (``"T"``/``"U"``), the same hazard
    ``template_param_kinds``'s own non-type-parameter entries already
    guard against -- this is the ordinary-parameter-list sibling of that
    fix (Codex review, PR #943). A no-op (``type_param_names == ()``) for
    every non-template function, so an ordinary function's ``extra``
    tuple is unchanged byte-for-byte.

    *return_type* is folded into ``extra`` (as ``("ret", <canonicalized
    spelling>)``, placed before the ``"tmpl"`` block so it never shifts
    the fixed tail position every ``template_param_kinds`` consumer reads)
    ONLY when *template_param_kinds* is non-empty -- an ordinary function
    can never legally overload solely by return type, but a function
    TEMPLATE's return type can itself depend on a template parameter
    (``template<class T> typename T::x f(T);``), so two such templates can
    share every other dimension in ``extra`` and still be genuinely
    distinct, legally-coexisting declarations (Codex review, PR #943;
    confirmed by direct compilation that clang accepts both that
    declaration and its ``typename T::y`` sibling with no redefinition
    error). Canonicalized identically to a dependent ordinary parameter
    type -- ``canonicalize_function_signature_param_type`` then
    ``canonicalize_type_param_references`` -- so a pure template-parameter
    rename that only the return type's own dependent spelling reflects
    still resolves to the same ``EntityId``.

    When *mangled_name* is present, *leaf_name* is likewise ignored -- see
    :func:`entity_id_for_variable`'s docstring for the confirmed reason
    (the ELF-only fallback path reuses the raw exported symbol for both
    ``Function.name`` and ``Function.mangled``, so a header/DWARF
    observation's demangled ``name`` and an export-only observation's raw
    name would otherwise disagree despite an identical, genuine mangling;
    Codex review, PR #941). The mangled spelling alone already
    disambiguates every declaration in ``extra``.
    """
    if mangled_name:
        extra: tuple[str, ...] = ("mangled", mangled_name)
        resolved_scope: ScopePath = ()
        resolved_leaf_name = ""
    elif is_extern_c:
        extra = ("extern_c",)
        resolved_scope = ()
        resolved_leaf_name = leaf_name
    else:
        extra = (
            "sig",
            *(
                canonicalize_type_param_references(
                    canonicalize_function_signature_param_type(p), type_param_names
                )
                for p in param_types
            ),
            f"const:{is_const}",
            f"volatile:{is_volatile}",
            ref_qualifier,
            str(is_variadic),
            # A function template's return type CAN depend on its own
            # template parameters (`template<class T> typename T::x f(T);`),
            # so two such templates can share scope/leaf_name/param_types/
            # template_param_kinds while genuinely being distinct, legal
            # overloads distinguished only by that dependent return type --
            # confirmed by direct compilation (Codex review, PR #943):
            # clang accepts BOTH `template<class T> typename T::x f(T);`
            # and `template<class T> typename T::y f(T);` with no
            # redefinition error, two real `FunctionTemplateDecl`s. Folded
            # in only for a template (`template_param_kinds` non-empty) --
            # an ORDINARY function can never legally overload solely by
            # return type (the same reason `finding_identity.
            # normalized_signature` never folds return type in at all), so
            # including it there would add nothing and only risk widening
            # a genuine return-type EDIT into a spurious remove+add for a
            # function this branch already fully identifies by its other
            # dimensions. Placed AFTER the variadic marker but BEFORE the
            # ``"tmpl"`` block (not appended at the very end) so this
            # doesn't shift the fixed `extra[-1]`/`extra[-2]` positions
            # every existing `template_param_kinds` consumer already reads
            # off the tail of `extra`. Canonicalized identically to an
            # ordinary parameter's dependent type (cross-producer spelling
            # normalization, then the same rename-blind substitution).
            *(
                (
                    "ret",
                    canonicalize_type_param_references(
                        canonicalize_function_signature_param_type(return_type),
                        type_param_names,
                    ),
                )
                if template_param_kinds
                else ()
            ),
            *(("tmpl", *template_param_kinds) if template_param_kinds else ()),
        )
        resolved_scope = _scope_path(scope)
        resolved_leaf_name = leaf_name
    return EntityId(
        scope=resolved_scope,
        kind=EntityKind.FUNCTION,
        leaf_name=resolved_leaf_name,
        extra=extra,
    )


def with_mangled_name(
    entity_id: EntityId | None, new_mangled_name: str
) -> EntityId | None:
    """*entity_id* with its ``"mangled"`` tag re-spelled to *new_mangled_name*.

    A declaration's own mangled spelling can legitimately change AFTER its
    ``EntityId`` was already resolved by a producer -- e.g. a hybrid
    dumper reconciling a castxml synthetic ctor/dtor placeholder key to
    clang's real mangled name, or normalizing a Mach-O linker symbol's
    leading underscore before cross-producer matching. Rebuilding the
    identity from scratch at that point is not an option: by then the
    caller no longer has the original ``ScopePath`` the resolver needs
    (that data is parser-internal, per this module's own carrier-field
    design). This is the narrow, safe alternative -- it only ever touches
    an identity genuinely tagged ``("mangled", ...)``; an
    ``extern_c``-/``sig``-tagged identity, or no identity at all, is
    returned unchanged, since neither of those was derived from the
    mangled spelling in the first place, and rewriting either would
    silently fabricate a tag the resolver never produced.

    >>> eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
    >>> with_mangled_name(eid, "_Z1fi").extra
    ('mangled', '_Z1fi')
    >>> extern_c_eid = entity_id_for_function((), "f", is_extern_c=True)
    >>> with_mangled_name(extern_c_eid, "_Z1fv") is extern_c_eid
    True
    >>> with_mangled_name(None, "_Z1fv") is None
    True
    """
    if entity_id is None or entity_id.extra[:1] != ("mangled",):
        return entity_id
    return replace(entity_id, extra=("mangled", new_mangled_name))
