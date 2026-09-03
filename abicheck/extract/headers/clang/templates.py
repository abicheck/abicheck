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

"""Template-specialization parsing for the clang header-AST backend (ADR-061
Phase 5 item 1's fourth and final entity module, closing out the item on
this backend).

Split out of ``dumper_clang_vtable.py``, which — despite its name — always
held two only loosely related halves: record/vtable layout reconstruction
(``is_record_definition``/``build_vtable``/``_collect_virtual_slots`` and
friends, which stay there) and template-specialization scope/spelling
reconstruction (``build_specialization_index`` and everything it depends
on, which moved here).

This module has NO import of ``dumper_clang_vtable`` at all, module-level
or function-local, even though ``build_specialization_index`` needs its own
forward-decl-vs-definition tie-break — the same one ``is_record_definition``
already provides. Reading it back directly would be a genuine cycle:
``dumper_clang_vtable.py`` re-exports every name this module owns from its
own tail (back-compat for existing direct imports of them — see below), so
whichever direction the OTHER edge went, the two modules would import each
other, which ``scripts/check_ai_readiness.py``'s static ``import-cycle-
growth`` scan flags regardless of whether the importing statement sits at
module level or inside a function body (an earlier revision of this module
tried the function-local dodge — it resolves fine at runtime, since neither
module needs the other's names until it actually executes, but the static
scan still sees the edge and still calls it a cycle). Fixed the way this
package already resolves an identical shape of cross-layer need elsewhere
(``enums.py``'s ``evaluate_int``, ``functions.py``'s ``default_value``,
``records.py``'s ``evaluate_bitfield_int``/``field_default_value``):
:func:`build_specialization_index` takes *is_record_definition* as an
explicit, required keyword-only parameter instead of importing it, and its
one real caller (``context.py``'s ``specialization_record_index()``, which
already imports ``is_record_definition`` from ``dumper_clang_vtable`` for
its own ``record_index()`` use) passes the same function straight through.

Unlike ``enums.py``/``functions.py``/``records.py``, there is no
``parse_templates()`` entry point parallel to ``parse_enums``/
``parse_functions``/``parse_types``: a ``ClassTemplateSpecializationDecl``
is never appended to one of ``_ClangAstParser``'s own categorized ``_Decl``
lists in the first place (only functions/variables/records/enums/typedefs
are — see ``dumper_clang.py``'s ``_categorize``), so there is no
post-walk collection for a template-entity parser to consume. A concrete
specialization's own members surface as ordinary ``_records``/``_functions``
entries scoped under the specialization's reconstructed spelling instead
(``records.py`` already reads the resulting ``in_template``/scope data,
via ``RecordType.is_template_pattern``). What genuinely IS template-entity
parsing — reconstructing a ``ClassTemplateDecl``'s own parameter kinds/
defaults/names, and a concrete specialization's ``Name<Arg1, Arg2>``
spelling and qualname-indexed lookup — already existed as free functions
taking their AST root/node explicitly, just physically in the flat
``dumper_clang_vtable.py`` sibling rather than this package; moving them
here needed no context-shape change either; none of them reads or writes
``CastxmlParserContext``/clang's own ``context.py`` state; two of the
callers already reached them as free functions before this move
(``dumper_clang.py``'s ``_walk``, for its own ``ClassTemplateSpecializationDecl``
scope-continuation branch, via :func:`_specialization_spelling`, and
``context.py``'s ``RecordVtableIndex``, for ``specialization_record_index()``,
via :func:`build_specialization_index`).

``dumper_clang.py``'s ``_walk``/``_categorize`` — the shared traversal/
categorization dispatch every entity kind (not just templates) goes
through — stays exactly where it is, per this package's own established
precedent (see ``records.py``'s own account of why the walk itself was set
aside rather than split): the ``ClassTemplateSpecializationDecl`` branch
inside ``_walk`` that computes a specialization's own members' scope stays
there too, now importing :func:`build_specialization_index`'s sibling
:func:`_specialization_spelling` from here instead of from the old flat
module.

``dumper_clang_vtable.py`` keeps every migrated name as a one-line
re-export (not merely referenced — genuinely imported, so
``from abicheck.dumper_clang_vtable import _index_template_param_defaults``
and friends, which several existing tests use directly, keep resolving).

``_SCOPE_NODE_KINDS`` moved here too, from ``dumper_clang_expr.py``, for the
same reason: ``extract`` may not import that module (it pulls in
``diff_cxx_rules``, classified ``compare``), but this constant is exactly
the kind of pure, dependency-free data this package's own leaf modules are
allowed to own. ``dumper_clang_expr.py`` and ``dumper_clang.py``'s own
``_ClangAstParser._walk`` both read it back from here now, keeping it the
ONE definition (never two independently-drifting copies) their own prior
comments already required.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Decl contexts we descend into, tracking the enclosing scope name so a
#: namespace/class-qualified constant key is built (``ns::C::kLimit``).
#: Shared with ``dumper_clang._ClangAstParser._walk``'s own public-surface
#: qualified-name building and ``dumper_clang_expr.py``'s own scope
#: tracking (both import it back from here) — kept as ONE definition
#: rather than independently-drifting copies.
_SCOPE_NODE_KINDS = frozenset(
    {"NamespaceDecl", "CXXRecordDecl", "RecordDecl", "LinkageSpecDecl"}
)

#: Non-type template parameter types whose Clang JSON ``value`` (an
#: evaluated integer, e.g. ``3``) is CONFIRMED to print identically to how a
#: base reference spells the same argument (``"A<3>"``) -- verified against
#: a real clang build. Deliberately narrow: ``bool``'s own encoding does
#: NOT round-trip this way (``template <bool B> struct A; ... A<true>``
#: reports ``value: -1``, not ``1``, and the base reference still spells it
#: ``"A<true>"`` -- confirmed empirically, Codex review, fresh evidence), and
#: neither an enum (spelled by its enumerator name, e.g. ``"A<Color::Red>"``,
#: never its integer value) nor a pointer/floating-point/structural
#: (C++20) non-type argument has any reason to share this property. Only a
#: parameter whose declared type is exactly one of these plain builtin
#: integer spellings is trusted; every other non-type parameter makes the
#: whole specialization unindexable (see :func:`_specialization_spelling`).
_SAFE_NONTYPE_INT_TYPES = frozenset(
    {
        "int",
        "unsigned int",
        "long",
        "unsigned long",
        "long long",
        "unsigned long long",
        "short",
        "unsigned short",
    }
)


def _template_param_kinds(class_template_decl: dict[str, Any]) -> list[str | None]:
    """Per-position "trusted plain-integer non-type parameter" marker for a
    ``ClassTemplateDecl``'s own parameter list, in declaration order --
    matching the order ``TemplateArgument`` children of one of its
    specializations are emitted in (confirmed with a real clang build).

    Each entry is the parameter's own type spelling when it's a
    non-type parameter declared with one of :data:`_SAFE_NONTYPE_INT_TYPES`,
    or ``None`` for a type parameter (a type argument carries its own
    ``type.qualType`` directly -- this list is never consulted for it) or an
    untrusted non-type parameter (``bool``, an enum, a pointer, ...). Stops
    at the first non-parameter child: the parameter list always precedes
    the pattern's own body/specializations in ``inner`` order.
    """
    kinds: list[str | None] = []
    for child in class_template_decl.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = child.get("kind")
        if kind == "NonTypeTemplateParmDecl":
            type_obj = child.get("type")
            spelling = (
                str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
            )
            kinds.append(spelling if spelling in _SAFE_NONTYPE_INT_TYPES else None)
        elif kind in ("TemplateTypeParmDecl", "TemplateTemplateParmDecl"):
            kinds.append(None)
        else:
            break
    return kinds


def _register_template_param_metadata(
    idx: dict[str, list[str | None]],
    ambiguous: set[str],
    node_ids: dict[str, str],
    qualname: str,
    node: dict[str, Any],
    value: list[str | None],
) -> None:
    """Register *value* for *qualname* in *idx*, treating a conflicting
    second registration as ambiguous UNLESS clang's own ``previousDecl``
    link says it's a legal redeclaration of the SAME entity, not a
    coincidentally same-named different one.

    Shared by :func:`_index_template_param_kinds`/:func:`_index_template_
    param_defaults`/:func:`_index_template_param_names` (Codex review,
    fresh evidence, fourth round): the *names* list specifically can
    legally differ across a redeclaration of the identical template --
    ``template<class T, class U=T> struct A;`` followed later by
    ``template<class X, class Y> struct A {...};`` is one C++ entity, and
    clang always spells a dependent default's inherited text using the
    ORIGINAL declaration's parameter name (``"T"``, never renamed to
    ``"X"``/``"Y"`` on the later declaration) -- confirmed empirically, so
    treating the differing NAME lists as a genuine conflict (the third
    round's fix did, unconditionally) broke dependent-default substitution
    for this ordinary, legal C++ shape. Distinguished from a genuine
    conflict (two nested templates in two different outer specializations
    coincidentally sharing a bare qualname, e.g. ``Outer<int>``'s and
    ``Outer<double>``'s own nested ``struct A``) via ``previousDecl``:
    confirmed empirically that clang stamps it on every legal
    redeclaration and NEVER on two genuinely unrelated declarations.

    The tracked ``node_ids[qualname]`` -- the id ``previousDecl`` is
    matched against for the NEXT registration -- is only ever advanced on
    a CONFIRMED same-entity link (the first registration, or a
    ``previousDecl`` match), never merely because a later registration's
    *value* happens to equal the stored one (Codex review, fresh evidence,
    fifth round). Two genuinely unrelated declarations sharing a bare
    qualname (the ``Outer<int>``/``Outer<double>`` case above) can easily
    have byte-identical kinds/defaults/names by coincidence -- e.g. both
    nested ``template<class T, class U=T> struct A`` before either is ever
    redeclared -- and advancing ``node_ids`` to the second, unrelated
    node's id there would corrupt the chain for the FIRST entity's own
    later, legal redeclaration: its real ``previousDecl`` (pointing at the
    first node) would then mismatch the corrupted tracked id and get
    wrongly deleted as ambiguous, confirmed with a real end-to-end repro.
    A value-equal-but-unconfirmed registration is still safely absorbed
    (the stored value doesn't change either way), it just doesn't get to
    reassign whose id is being tracked.

    On a CONFIRMED redeclaration, the stored value is positionally MERGED
    with the new one -- not simply kept as-is -- because C++ allows a
    later declaration to legally ADD a default a parameter didn't have
    before (never repeat one already given elsewhere): ``template<class
    T, class U> struct A;`` followed by ``template<class T, class U=T>
    struct A {...};`` is one entity whose effective default for ``U`` only
    becomes known on the SECOND declaration (Codex review, fresh evidence,
    sixth round). Keeping only the first declaration's value unconditionally
    (the fourth round's fix did) silently dropped that added default,
    leaving the dependent-default substitution unable to trim a trailing
    argument and mis-indexing the specialization -- confirmed end to end.
    Merging is safe for every position: where the tracked value already
    has data (a default, or a parameter's own name), it wins, preserving
    the ORIGINAL declaration's spelling this module already relies on for
    dependent-default substitution; only a position the tracked value has
    nothing for adopts the new declaration's own value there. Length
    mismatch (parameter count cannot legally change across a redeclaration
    of the same template) falls back to keeping the existing value as-is,
    the identical safe default this branch already used before merging was
    added.
    """
    existing = idx.get(qualname)
    node_id = node.get("id")
    if existing is None:
        idx[qualname] = value
        if node_id:
            node_ids[qualname] = node_id
        return
    previous_decl = node.get("previousDecl")
    if previous_decl and previous_decl == node_ids.get(qualname):
        if len(existing) == len(value):
            idx[qualname] = [e if e is not None else v for e, v in zip(existing, value)]
        if node_id:
            node_ids[qualname] = node_id
        return
    if existing == value:
        return
    ambiguous.add(qualname)
    del idx[qualname]
    node_ids.pop(qualname, None)


def _index_template_param_kinds(root: dict[str, Any]) -> dict[str, list[str | None]]:
    """``qualified template name -> per-position param-kind list`` (see
    :func:`_template_param_kinds`) over every ``ClassTemplateDecl`` in the
    AST, scope-tracked the identical way :func:`build_specialization_index`
    tracks it for a specialization -- so a specialization found under a
    given scope+name looks its owning template's parameter list up under
    that SAME scope+name (a specialization is always either nested inside
    its own ``ClassTemplateDecl`` or a sibling of it at the same scope
    depth, confirmed with a real clang build for both an implicit and an
    explicit specialization). A redeclaration of the same template (e.g.
    seen again through a second ``#include``) shares an identical parameter
    list, so keeping the first registration is safe THERE -- but a bare,
    unspelled qualname (this function never extends scope through a
    ``ClassTemplateSpecializationDecl``, see :func:`build_specialization_index`'s
    own *lookup_scope* split) collapses a member template nested in one
    explicit outer specialization with a SAME-NAMED, genuinely DIFFERENT
    member template nested in a sibling outer specialization -- e.g.
    ``Outer<int>``'s own nested ``template<class U=int> struct A`` and
    ``Outer<double>``'s own nested ``template<class U=double> struct A``
    both register under the bare key ``"A"`` (Codex review, fresh evidence,
    real end-to-end repro: with first-registration-wins, ``Outer<double>::
    A<>``'s trailing default couldn't be trimmed against the WRONG
    (``Outer<int>``-borrowed) default, leaving the base unresolvable and an
    added virtual method on the derived class completely undetected). A
    conflicting SECOND registration under the same qualname is therefore
    treated as genuinely ambiguous and dropped entirely (equivalent to no
    entry at all, degrading to this module's usual unresolvable-base false
    negative) rather than trusting either candidate -- only an EXACTLY
    matching redeclaration is safe to keep.
    """
    idx: dict[str, list[str | None]] = {}
    ambiguous: set[str] = set()
    node_ids: dict[str, str] = {}

    def walk(node: Any, scope: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = str(node.get("name") or "")
        if kind == "ClassTemplateDecl" and name and (
            qualname := ("::".join((*scope, name)) if scope else name)
        ) not in ambiguous:
            _register_template_param_metadata(
                idx, ambiguous, node_ids, qualname, node, _template_param_kinds(node)
            )
        child_scope = (*scope, name) if kind in _SCOPE_NODE_KINDS and name else scope
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(root, ())
    return idx


def _template_param_defaults(class_template_decl: dict[str, Any]) -> list[str | None]:
    """Per-position default-argument spelling for a ``ClassTemplateDecl``'s
    own parameter list, in the SAME order and indexing as
    :func:`_template_param_kinds` -- consulted by :func:`_specialization_spelling`
    to know which trailing arguments to drop.

    Only a TYPE parameter's default is captured (its own
    ``defaultArg.type.qualType``, the identical representation
    :func:`_specialization_spelling` builds for a type argument, so the two
    compare directly) -- ``None`` for a parameter with no default, or a
    non-type/template-template parameter's default (conservatively
    excluded: this module already only trusts a non-type ARGUMENT's own
    value for a narrow, confirmed-safe set of types via
    :data:`_SAFE_NONTYPE_INT_TYPES`, and a non-type DEFAULT carries the
    identical unreliable-printing risk this doesn't attempt to solve here).
    """
    defaults: list[str | None] = []
    for child in class_template_decl.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = child.get("kind")
        if kind == "TemplateTypeParmDecl":
            default = child.get("defaultArg")
            spelling = None
            if isinstance(default, dict):
                type_obj = default.get("type")
                if isinstance(type_obj, dict) and type_obj.get("qualType"):
                    spelling = str(type_obj["qualType"])
            defaults.append(spelling)
        elif kind in ("NonTypeTemplateParmDecl", "TemplateTemplateParmDecl"):
            defaults.append(None)
        else:
            break
    return defaults


def _index_template_param_defaults(root: dict[str, Any]) -> dict[str, list[str | None]]:
    """``qualified template name -> per-position default-spelling list``
    (see :func:`_template_param_defaults`), scope-tracked identically to
    :func:`_index_template_param_kinds` (same reasoning applies here for
    why a specialization's scope+name always matches its owning template's,
    and the same conflicting-registration-is-ambiguous discipline for a
    same-named member template nested in two DIFFERENT explicit outer
    specializations -- see that function's own docstring).

    A dependent default's raw spelling (``"T"`` in ``template<class T,
    class U=T> struct A;``) names an EARLIER parameter of the SAME
    declaration it appears on -- :func:`_specialization_spelling` resolves
    it by looking up that text in the *tracked* names index
    (:func:`_index_template_param_names`'s own fully-merged output). When
    a CONFIRMED redeclaration (the sixth round's merge) contributes a
    NEWLY adopted default and that redeclaration also renamed its
    parameters, the raw text it carries names one of ITS OWN (renamed)
    parameters, not the tracked ones -- e.g. ``template<class T, class U>
    struct A;`` followed by ``template<class X, class Y=X> struct
    A {...};`` spells the added default as literal ``"X"``, which the
    tracked names index (still ``["T", "U"]`` here) never contains, so the
    substitution silently fails (Codex review, fresh evidence, eighth
    round; confirmed end to end this left the base unresolvable and a
    real virtual-method addition undetected). Fixed by translating a
    newly adopted default's dependent reference through THIS
    declaration's own positional name list into the TRACKED name at that
    same position (parameter order/count can't legally change across a
    redeclaration of the same template, so position always lines up)
    before merging it in.

    The translation target is :func:`_index_template_param_names`'s own
    output for the WHOLE tree, computed once up front, not a locally
    hand-rolled "first name seen for this qualname" copy -- an earlier
    version of this fix kept its own local shadow, updated only at a
    qualname's very first sighting here, and it silently went stale
    whenever an INTERMEDIATE redeclaration was the one that actually
    named a previously-unnamed parameter: ``template<class, class>
    struct A;`` (both unnamed) then ``template<class X, class Y> struct
    A;`` (a redeclaration that names them) then ``template<class T, class
    U=T> struct A {...};`` (a further redeclaration that adds a default)
    -- the real, fully-merged names index correctly resolves to
    ``["X", "Y"]``, but the stale local shadow here still held
    ``[None, None]`` from the very first sighting, so the translation
    target for the added default's position was falsy and the raw,
    untranslated text was kept -- reproducing the identical unresolvable-
    base failure (Codex review, fresh evidence, ninth round). Reusing
    :func:`_index_template_param_names`'s own already-correct, fully-
    merged result sidesteps the whole class of "which registration counts
    as authoritative" bug rather than re-deriving (and risking
    re-diverging) it a second time here.
    """
    idx: dict[str, list[str | None]] = {}
    ambiguous: set[str] = set()
    node_ids: dict[str, str] = {}
    tracked_names_by_qualname = _index_template_param_names(root)

    def walk(node: Any, scope: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = str(node.get("name") or "")
        if kind == "ClassTemplateDecl" and name and (
            qualname := ("::".join((*scope, name)) if scope else name)
        ) not in ambiguous:
            this_names = _template_param_names(node)
            defaults = _template_param_defaults(node)
            tracked_names = tracked_names_by_qualname.get(qualname)
            if tracked_names is not None:
                own_positions = {n: i for i, n in enumerate(this_names) if n}
                defaults = [
                    tracked_names[own_positions[d]]
                    if d is not None
                    and d in own_positions
                    and own_positions[d] < len(tracked_names)
                    and tracked_names[own_positions[d]]
                    else d
                    for d in defaults
                ]
            _register_template_param_metadata(
                idx, ambiguous, node_ids, qualname, node, defaults
            )
        child_scope = (*scope, name) if kind in _SCOPE_NODE_KINDS and name else scope
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(root, ())
    return idx


def _template_param_names(class_template_decl: dict[str, Any]) -> list[str | None]:
    """Per-position parameter NAME for a ``ClassTemplateDecl``'s own
    parameter list, in the SAME order/indexing as :func:`_template_param_kinds`
    and :func:`_template_param_defaults` -- lets :func:`_specialization_spelling`
    recognize a DEPENDENT default (``template <class T, class U = T> struct
    A;``) that names an earlier parameter by its bare identifier, so it can
    substitute in that earlier parameter's own resolved argument before
    comparing (a literal, unsubstituted ``"T"`` never equals a resolved
    argument like ``"double"``).
    """
    names: list[str | None] = []
    for child in class_template_decl.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = child.get("kind")
        if kind in (
            "TemplateTypeParmDecl",
            "NonTypeTemplateParmDecl",
            "TemplateTemplateParmDecl",
        ):
            pname = child.get("name")
            names.append(str(pname) if pname else None)
        else:
            break
    return names


def _index_template_param_names(root: dict[str, Any]) -> dict[str, list[str | None]]:
    """``qualified template name -> per-position parameter-name list`` (see
    :func:`_template_param_names`), scope-tracked identically to
    :func:`_index_template_param_kinds`, including its same conflicting-
    registration-is-ambiguous discipline.
    """
    idx: dict[str, list[str | None]] = {}
    ambiguous: set[str] = set()
    node_ids: dict[str, str] = {}

    def walk(node: Any, scope: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = str(node.get("name") or "")
        if kind == "ClassTemplateDecl" and name and (
            qualname := ("::".join((*scope, name)) if scope else name)
        ) not in ambiguous:
            _register_template_param_metadata(
                idx, ambiguous, node_ids, qualname, node, _template_param_names(node)
            )
        child_scope = (*scope, name) if kind in _SCOPE_NODE_KINDS and name else scope
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(root, ())
    return idx


def _specialization_spelling(
    node: dict[str, Any],
    name: str,
    param_kinds: list[str | None] | None,
    param_defaults: list[str | None] | None = None,
    param_names: list[str | None] | None = None,
) -> str | None:
    """A ``ClassTemplateSpecializationDecl``'s own ``Name<Arg1, Arg2>``
    spelling, reconstructed from its direct ``TemplateArgument`` children --
    matching what clang's own type printer produces on a base-reference
    site's ``type.qualType``/``desugaredQualType`` (confirmed with real
    clang builds, see :func:`build_specialization_index`'s docstring for the
    exact repros this reproduces).

    ``None`` when any argument isn't reproducible with confidence: a type
    argument's own ``type.qualType`` is always trusted, but a non-type
    argument's raw ``value`` is trusted ONLY when *param_kinds* confirms
    (by position) that the corresponding template parameter is one of
    :data:`_SAFE_NONTYPE_INT_TYPES` -- a ``bool``/enum/pointer/other
    non-type argument's ``value`` does not reliably print the same way the
    referring site spells it (see that set's own docstring), so the whole
    specialization is left unindexed rather than guessed at. Also ``None``
    for a template-template argument or a pack expansion (no ``type`` or
    ``value`` field on that argument at all) or when there are no arguments
    at all. The caller skips indexing entirely in every ``None`` case,
    degrading to the same already-accepted "unresolvable base"
    false-negative every other unresolvable-base shape in this module
    already degrades to -- never a false positive.

    A specialization ALWAYS carries a ``TemplateArgument`` for every
    parameter, including one a base reference omitted because it equals
    its own default (``template <class T, class U = int> struct A; struct
    D : A<double> {...};`` reports arguments for BOTH ``T`` and ``U``) --
    confirmed with a real clang build that joining all of them
    unconditionally produces ``"A<double, int>"``, which never matches the
    referring site's own ``"A<double>"`` (Codex review, fresh evidence). So
    once *args* is fully built, trailing entries that exactly equal their
    own parameter's default spelling (*param_defaults*, by position) are
    popped -- confirmed this reproduces the referring site's spelling
    EXACTLY, for both an omitted default AND one written out explicitly
    with the identical value: ``struct D : A<double, int> {...};`` reports
    ``type.qualType == "A<double, int>"`` (as literally written) but
    ``type.desugaredQualType == "A<double>"`` (defaults collapsed) --
    ``_base_qualnames`` already prefers ``desugaredQualType`` whenever
    present, so both the omitted-default and the explicit-matching-value
    spellings resolve to the identical `"A<double>"` this collapses to.

    A default can also be DEPENDENT on an earlier parameter
    (``template <class T, class U = T> struct A;``) -- its
    *param_defaults* entry is the literal, unsubstituted text of the
    default (``"T"``), which never equals a real resolved argument
    (``"double"``) by plain string comparison (Codex review, fresh
    evidence: confirmed this left `struct D : A<double> {...};`
    unresolvable, the identical false-positive shape as the undropped-
    default gap above). When a default spelling exactly matches an
    EARLIER parameter's own bare name (*param_names*), it's substituted
    with that earlier parameter's own already-resolved argument before
    comparing -- always safe (a dependent default can only name an
    earlier parameter, never itself or a later one, so the substitution
    source is always already resolved in *args* by the time it's needed).
    Anything more complex (a default that only partially depends on an
    earlier parameter, e.g. ``std::vector<T>``) is conservatively left
    unsubstituted, matching this module's "false negative over false
    positive" degradation elsewhere: it just won't compare equal, so that
    trailing argument stays rather than risking a wrong drop.
    """
    args: list[str] = []
    idx = 0
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict) or child.get("kind") != "TemplateArgument":
            continue
        type_obj = child.get("type")
        if isinstance(type_obj, dict) and type_obj.get("qualType"):
            args.append(str(type_obj["qualType"]))
            idx += 1
            continue
        if "value" in child:
            safe = param_kinds[idx] if param_kinds and idx < len(param_kinds) else None
            if safe is None:
                return None
            args.append(str(child["value"]))
            idx += 1
            continue
        return None
    if not args:
        return None
    if param_defaults:
        name_positions = (
            {n: i for i, n in enumerate(param_names) if n} if param_names else {}
        )
        while args:
            pos = len(args) - 1
            if pos >= len(param_defaults):
                break
            default = param_defaults[pos]
            if default is not None and default in name_positions:
                dep_pos = name_positions[default]
                if dep_pos < len(args):
                    default = args[dep_pos]
            if default != args[-1]:
                break
            args.pop()
        if not args:
            # EVERY argument was popped as matching its own default --
            # clang still prints an explicit, empty angle-bracket pair
            # (`"A<>"`), never a bare `"A"` (Codex review, fresh evidence;
            # confirmed with a real clang build:
            # `template<class T=int> struct A {...}; struct D : A<> {...};`
            # gives `bases[0].type.qualType == "A<>"` on the base
            # reference). Returning `None` here (as the earlier
            # no-arguments-at-all case above correctly does, for a
            # template-template argument/pack-expansion/zero-parameter
            # shape) would degrade this to an unresolvable base even
            # though every argument is safely known -- unlike that case,
            # this one has real, fully-resolved argument data; it's only
            # the resulting spelling that happens to be the empty list.
            return f"{name}<>"
    return f"{name}<{', '.join(args)}>"


def build_specialization_index(
    root: dict[str, Any],
    param_kinds_by_qualname: dict[str, list[str | None]] | None = None,
    param_defaults_by_qualname: dict[str, list[str | None]] | None = None,
    param_names_by_qualname: dict[str, list[str | None]] | None = None,
    *,
    is_record_definition: Callable[[dict[str, Any]], bool],
) -> dict[str, dict[str, Any]]:
    """``qualified spelling -> node`` index over every concrete
    ``ClassTemplateSpecializationDecl`` reachable from the parsed AST, for
    :func:`build_vtable`'s base-lookup recursion (via
    ``dumper_clang.py``'s ``_ClangAstParser._base_lookup_index``, which
    merges this with its own ``_record_index()``).

    ``dumper_clang.py``'s own categorizing walk (``self._records``, and
    therefore its ``_record_index()``) only ever collects
    ``CXXRecordDecl``/``RecordDecl`` nodes -- a concrete template
    specialization used as a base (``struct D : A<int> {...};``) is a
    DIFFERENT clang node kind entirely (``ClassTemplateSpecializationDecl``,
    confirmed with a real clang build: nested inside its own
    ``ClassTemplateDecl``, sibling to the uninstantiated pattern's own
    ``CXXRecordDecl``, and never visited by that walk's ``kind in
    ("CXXRecordDecl", "RecordDecl")`` check), so it was never reachable from
    the base-lookup index at all. An old ``D`` deriving from ``A<int>``
    resolved to an empty `vtable`/no vptr, and adding a no-keyword override
    in a new ``D`` then made the vtable appear to gain its FIRST entry -- a
    false breaking ``VPTR_INTRODUCED`` (Codex review, fresh evidence).

    Unlike an ordinary record, a specialization's own ``name`` is always the
    BARE primary-template name (``"A"``, never ``"A<int>"``, confirmed with
    a real clang build) -- the template-argument spelling only exists on the
    REFERRING site's own ``type.qualType``/``desugaredQualType`` (what
    ``_base_qualnames`` already reads for an ordinary base). So this
    reconstructs the matching spelling itself from each direct
    ``TemplateArgument`` child, via :func:`_specialization_spelling` -- a
    type argument's own ``type.qualType``, or a non-type argument's own
    ``value`` -- joined with ``", "``, confirmed against real clang output
    to exactly reproduce the base-reference spelling for both a namespaced
    two-type-argument specialization (``"ns::A<int, double>"``) and a
    non-type-argument one (``"A<3>"``). A specialization carrying any OTHER
    kind of argument (template-template, pack expansion -- no ``type`` or
    ``value`` field on that argument) is skipped entirely rather than
    guessed at: an unindexed specialization degrades to the same
    already-accepted "unresolvable base" false-negative every other
    unresolvable-base shape in this module already degrades to (see the
    module docstring's own "known limitation" section) -- never a false
    positive.

    Scope-tracked the same way ``dumper_clang.py``'s own ``_walk`` tracks it
    for an ordinary record (extended on ``NamespaceDecl``/``CXXRecordDecl``/
    ``RecordDecl``/``LinkageSpecDecl``, via the shared ``_SCOPE_NODE_KINDS``
    -- a ``ClassTemplateDecl``/``ClassTemplateSpecializationDecl`` itself is
    deliberately NOT scope-forming here, matching ``_SCOPE_NODE_KINDS``'s
    own membership) so a namespaced template's specialization indexes under
    its fully-qualified spelling, not a bare one. A dedicated whole-AST walk
    (independent of ``dumper_clang.py``'s own categorizing walk, the same
    shape as ``dumper_clang_expr._index_decl_id_qualified_names``) since a
    specialization is never collected by that walk at all.

    A forward-declared explicit specialization (``template<> struct
    A<int>;``) followed later by its complete definition (``template<>
    struct A<int> { ... };``) emits TWO ``ClassTemplateSpecializationDecl``
    nodes sharing the identical spelling -- confirmed with a real clang
    build, the same forward-decl-shadows-definition shape
    ``dumper_clang._ClangAstParser._record_index`` already guards against
    for an ordinary record. A first-registration-wins policy here would
    permanently keep the empty forward-decl stub whenever it's walked
    first (Codex review, fresh evidence), so a complete definition always
    wins over a forward-declaration stub for the same spelling, regardless
    of walk order, via the caller-supplied *is_record_definition*.

    *param_kinds_by_qualname*/*param_defaults_by_qualname*/*param_names_by_qualname*
    let a caller that already computed these (``dumper_clang.py``'s own
    ``_ClangAstParser``, which needs the identical indices for its
    ``_walk``'s specialization scoping too) pass them in rather than paying
    for a second whole-AST pass -- computed internally when omitted, so
    this function stays independently callable/testable.

    *is_record_definition* is keyword-only and required rather than a
    default import of ``dumper_clang_vtable.is_record_definition`` -- this
    module is read back BY ``dumper_clang_vtable.py`` itself (see this
    module's own docstring: that flat module re-exports every name here for
    back-compat), so a module-level (or even function-local, per
    ``scripts/check_ai_readiness.py``'s own static ``import-cycle-growth``
    scan, which sees a nested import the same as a top-level one) import
    the other way would be a genuine two-way edge between the same two
    modules -- exactly the entanglement this package's own established
    convention (``enums.py``'s ``evaluate_int``, ``functions.py``'s
    ``default_value``, ``records.py``'s ``evaluate_bitfield_int``/
    ``field_default_value``) already resolves by taking the dependency as
    an explicit parameter instead. The one real caller
    (``context.py``'s ``specialization_record_index()``) already imports
    ``is_record_definition`` from ``dumper_clang_vtable`` for its own
    ``record_index()`` use, so passing the same function through costs it
    nothing extra.
    """
    if param_kinds_by_qualname is None:
        param_kinds_by_qualname = _index_template_param_kinds(root)
    if param_defaults_by_qualname is None:
        param_defaults_by_qualname = _index_template_param_defaults(root)
    if param_names_by_qualname is None:
        param_names_by_qualname = _index_template_param_names(root)
    idx: dict[str, dict[str, Any]] = {}

    def walk(
        node: Any, scope: tuple[str, ...], lookup_scope: tuple[str, ...]
    ) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = str(node.get("name") or "")
        if kind == "ClassTemplateSpecializationDecl" and name:
            # *lookup_scope* -- deliberately the ORIGINAL, unextended scope
            # convention (grows only through `_SCOPE_NODE_KINDS`, exactly
            # like `_index_template_param_kinds`/`_index_template_param_
            # defaults`/`_index_template_param_names`'s own walks) -- is
            # used ONLY for the param_kinds/defaults/names lookup below,
            # kept deliberately separate from *scope* (Codex review, fresh
            # evidence, second round): those three index functions register
            # a NESTED template's own `ClassTemplateDecl` under ITS
            # natural, unspelled scope (confirmed empirically: nested `A`
            # inside `Outer<int>`'s specialization body registers under
            # bare `"A"`, since `ClassTemplateSpecializationDecl` doesn't
            # extend scope in THEIR walks at all), never under the outer
            # specialization's SPELLED qualname (`"Outer<int>::A"`).
            # Looking it up with the spelled *scope* (as an earlier fix
            # here did) missed every entry, silently degrading a nested
            # specialization with DEFAULTED arguments back to the same
            # unresolvable-base false negative this whole mechanism exists
            # to close (`Outer<int>::A<>`'s own trailing default couldn't
            # be trimmed without a param_defaults hit) -- confirmed with a
            # real clang build reproducing the exact mismatch.
            template_qualname = (
                "::".join((*lookup_scope, name)) if lookup_scope else name
            )
            spelling = _specialization_spelling(
                node,
                name,
                param_kinds_by_qualname.get(template_qualname),
                param_defaults_by_qualname.get(template_qualname),
                param_names_by_qualname.get(template_qualname),
            )
            if spelling is not None:
                qualname = "::".join((*scope, spelling)) if scope else spelling
                existing = idx.get(qualname)
                if existing is None or (
                    not is_record_definition(existing) and is_record_definition(node)
                ):
                    idx[qualname] = node
            # A specialization containing its own NESTED specialization
            # (`struct D : Outer<int>::A<double>`) must descend under the
            # OUTER specialization's own spelled qualname (`"Outer<int>"`),
            # not its bare `name` (`"Outer"`) -- `ClassTemplateSpecialization
            # Decl` is deliberately not in `_SCOPE_NODE_KINDS` (it isn't an
            # ordinary namespace/class/linkage-spec scope), so falling
            # through to the generic branch below would silently drop the
            # outer specialization's template arguments from the nested
            # one's qualname, indexing it as bare `"A<double>"` instead of
            # `"Outer<int>::A<double>"` (Codex review, fresh evidence;
            # mirrors `dumper_clang.py`'s own `_walk` handling of the
            # identical shape for scoping a specialization's own members).
            # Falls back to the unscoped behavior (bare `name`) when the
            # spelling can't be reconstructed, same as every other
            # unresolvable-specialization degradation in this module. Only
            # *scope* (the registration/descent scope) extends this way --
            # *lookup_scope* never does, per the note above.
            child_scope = (*scope, spelling) if spelling else scope
            child_lookup_scope = lookup_scope
        elif kind in _SCOPE_NODE_KINDS and name:
            child_scope = (*scope, name)
            child_lookup_scope = (*lookup_scope, name)
        else:
            child_scope = scope
            child_lookup_scope = lookup_scope
        for child in node.get("inner", []) or []:
            walk(child, child_scope, child_lookup_scope)

    walk(root, (), ())
    return idx
