# Copyright 2026 Nikolay Petrov
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

"""Declaration indexes and name resolution for the type/reference graph (ADR-041 P0).

One AST pass records every record's qualified name and declaring file
(:func:`_index_declared_entities`) into :class:`_AstIndexes`. A second pass
resolves an unqualified spelling against the nearest enclosing scope
(:func:`_resolve_type_name`) or a reference against its declaration
(:func:`_resolve_ref_identity`). The whole-TU index is memoised per AST inside
an :func:`ast_derived_scope`; :func:`index_declared_type_files` and
:func:`index_declared_entity_files` expose it to the header graph projection.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from ..model.execution_cache import request_key
from ..model.execution_cache_scoped import ScopedCache
from ..model.name_decoration import macho_underscore as macho_decoration

_RECORD_DECL_KINDS = frozenset(
    {"CXXRecordDecl", "RecordDecl", "ClassTemplateSpecializationDecl"}
)
#: Non-record type declarations indexed as resolvable type targets too (Codex
#: review: a private enum/typedef field/param type was previously left
#: unqualified and un-provenanced, same as an un-indexed record).
_OTHER_TYPE_DECL_KINDS = frozenset({"EnumDecl", "TypedefDecl", "TypeAliasDecl"})
#: All declaration kinds the first pass indexes as named type targets.
_INDEXED_TYPE_DECL_KINDS = _RECORD_DECL_KINDS | _OTHER_TYPE_DECL_KINDS
_FUNCTION_DECL_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)
#: clang AST decl kinds that open a named scope contributing to a qualified name.
_SCOPE_DECL_KINDS = frozenset({"NamespaceDecl", *_RECORD_DECL_KINDS})
#: clang AST decl kinds that wrap a templated entity, carrying that entity's
#: own template *parameter* declarations as their direct children alongside
#: the templated declaration itself (G29 Phase 5 item 5). Each maps its
#: parameters' type dependencies onto the templated entity's own node, so a
#: public template constrained by a private type is a real dependency edge.
#: A (partial or explicit) *specialization* is deliberately absent: its
#: injected-class-name resolves to the same qualified name as the generic
#: template, so attributing one specialization's parameters to that shared
#: node is the exact misattribution ``_walk_types`` already refuses to make
#: for ``ClassTemplateSpecializationDecl``'s own base/field edges.
_TEMPLATE_DECL_KINDS = frozenset(
    {
        "ClassTemplateDecl",
        "FunctionTemplateDecl",
        "VarTemplateDecl",
        "TypeAliasTemplateDecl",
    }
)
#: Template *parameter* decl kinds. A ``NonTypeTemplateParmDecl`` additionally
#: carries its own ``type`` (the ``template <detail::Handle H>`` case); all
#: three may carry a ``defaultArg``.
_TEMPLATE_PARM_DECL_KINDS = frozenset(
    {
        "TemplateTypeParmDecl",
        "NonTypeTemplateParmDecl",
        "TemplateTemplateParmDecl",
    }
)
#: referencedDecl kinds that make a DeclRefExpr a non-call reference worth
#: recording — a call target (FunctionDecl/CXXMethodDecl/...) is already
#: covered by ``call_graph.py``'s ``DECL_CALLS_DECL`` edges.
_REFERENCE_DECL_KINDS = frozenset({"VarDecl", "EnumConstantDecl"})


def _decl_identity(node: dict[str, Any]) -> str:
    """Stable identity for a decl node: mangled name when clang emits one."""
    return macho_decoration.decode_itanium(
        str(node.get("mangledName") or node.get("name") or "")
    )


def _node_file(node: dict[str, Any]) -> str:
    """The source file a node names, if any (clang emits ``file`` only when it
    *changes* — sticky — so the caller tracks the last-seen value; mirrors
    ``call_graph._node_file``)."""
    loc = node.get("loc")
    if isinstance(loc, dict) and loc.get("file"):
        return str(loc["file"])
    rng = node.get("range")
    if isinstance(rng, dict):
        beg = rng.get("begin")
        if isinstance(beg, dict) and beg.get("file"):
            return str(beg["file"])
    return ""


def _looks_like_record_definition(node: dict[str, Any]) -> bool:
    """Whether a CXXRecordDecl-like node carries a real definition (has members),
    as opposed to a bare forward declaration."""
    return any(
        isinstance(c, dict)
        and c.get("kind") in ("FieldDecl", "CXXMethodDecl", "CXXConstructorDecl")
        for c in node.get("inner", []) or []
    )


@dataclass(frozen=True)
class _AstIndexes:
    """The four whole-TU indexes both AST passes below read and populate.

    Bundled so the two mutually recursive walks take one argument instead of
    four. Every field is a mutable container filled in place and shared by
    reference across the whole walk, so the dataclass itself is frozen: what
    must not change is *which* dicts a walk writes into, not their contents.
    """

    #: bare type name -> every qualified name it can resolve to in this TU.
    name_index: dict[str, list[str]]
    #: entity identity -> the file it was declared in.
    decl_file: dict[str, str]
    #: bare var/enum-constant name -> every identity it can resolve to.
    ref_name_index: dict[str, list[str]]
    #: clang node id -> (identity, file), the unambiguous reference lookup.
    id_index: dict[str, tuple[str, str]]


def _new_ast_indexes() -> _AstIndexes:
    """A fresh, empty :class:`_AstIndexes` for one AST walk."""
    return _AstIndexes({}, {}, {}, {})


def _index_type_target(
    qname: str,
    name: str,
    cur_file: str,
    idx: _AstIndexes,
    *,
    prefer_definition: bool,
) -> None:
    """Index one resolvable *type* target: its bare-name alias and its file.

    ``prefer_definition`` is the records-only rule that a later, complete
    definition overwrites a file recorded from an earlier forward declaration;
    enums/typedefs keep the first file seen.
    """
    candidates = idx.name_index.setdefault(name, [])
    if qname not in candidates:
        candidates.append(qname)
    if not cur_file:
        return
    if prefer_definition:
        idx.decl_file[qname] = cur_file
    else:
        idx.decl_file.setdefault(qname, cur_file)


def _index_reference_target(
    node: Any, name: str, cur_file: str, idx: _AstIndexes
) -> None:
    """Index one var/enum-constant as a resolvable *reference* target.

    Three lookups are populated, weakest last: the identity's declaring file,
    the ambiguous bare-name alias, and clang's own per-node id. The id map is
    the only unambiguous one — two same-named declarations in different scopes
    collapse to one bare-name entry, so routing a stub's resolution through
    ``decl_file``/``ref_name_index`` alone would pick whichever was indexed
    first (Codex review). Mapping the id to *both* identity and file lets a
    stub carrying only an ``id`` (no ``mangledName``) resolve its identity
    precisely too — e.g. telling a reference to ``a::k`` from one to ``b::k``.
    """
    ident = _decl_identity(node)
    if ident and cur_file:
        idx.decl_file.setdefault(ident, cur_file)
    if ident and name:
        candidates = idx.ref_name_index.setdefault(name, [])
        if ident not in candidates:
            candidates.append(ident)
    node_id = str(node.get("id") or "")
    if node_id and ident:
        idx.id_index.setdefault(node_id, (ident, cur_file))


def _index_children(
    node: Any,
    scope: list[str],
    cur_file: str,
    idx: _AstIndexes,
    in_body: bool,
) -> str:
    """Index every child of *node* in order, threading the sticky file state.

    clang emits ``loc.file`` only on the *first* declaration in a file — later
    siblings carry line/column only (Codex review) — so each sibling's return
    value feeds the next call rather than every child re-receiving the parent's
    value, which would lose the file for every sibling after the first.
    """
    for child in node.get("inner", []) or []:
        cur_file = _index_declared_entities(child, scope, cur_file, idx, in_body)
    return cur_file


def _index_declared_entities(
    node: Any,
    scope: list[str],
    cur_file: str,
    idx: _AstIndexes,
    in_body: bool = False,
) -> str:
    """First pass: record every type declaration's qualified name (+declaring
    file) and every var/enum-constant's identity (+declaring file, +bare-name
    index, +clang node id) seen anywhere in the TU, so the second pass can
    resolve an unqualified type spelling against the nearest enclosing scope
    (Codex review: clang's ``qualType`` is *written*, not fully qualified — a
    field typed ``Base`` inside ``namespace ns`` prints as ``"Base"``, not
    ``"ns::Base"``) and mark AST-only dependency nodes with their declaring
    file for ``defined_in_project`` provenance. Records, enums, and
    typedef/type-alias declarations are all indexed as resolvable type
    targets (Codex review: a private enum/typedef used as a field/param type
    was previously left un-indexed, same gap as an un-indexed record).

    Returns the last-seen file after visiting *node* and its whole subtree;
    :func:`_index_children` documents why that state is threaded sibling to
    sibling.
    """
    if not isinstance(node, dict):
        return cur_file
    f = _node_file(node)
    if f:
        cur_file = f
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")

    if kind in _RECORD_DECL_KINDS and name:
        _index_type_target(
            "::".join([*scope, name]),
            name,
            cur_file,
            idx,
            prefer_definition=_looks_like_record_definition(node),
        )
        return _index_children(node, [*scope, name], cur_file, idx, in_body)

    if kind in _OTHER_TYPE_DECL_KINDS and name:
        _index_type_target(
            "::".join([*scope, name]), name, cur_file, idx, prefer_definition=False
        )
        # Enum constants (and any nested decls) still need indexing; no new
        # scope is opened — an unscoped/scoped enum's own name is not part of
        # its enumerators' spelling in clang's AST dump.
        return _index_children(node, scope, cur_file, idx, in_body)

    if kind == "NamespaceDecl" and name:
        return _index_children(node, [*scope, name], cur_file, idx, in_body)

    if kind == "FieldDecl" and name and not in_body:
        # A field's own qualified identity (``Widget::x``), mirroring
        # ``_walk_types``'s own ``field_ident`` computation — so a
        # DECL_REFERENCES_DECL edge whose *source* is a field (a default
        # member initializer, ``int x = detail::k;``) has a declaring file
        # to resolve, not just its target (Codex review: without this, such
        # a field was never seeded as a `source_decl` and never backfilled
        # either, so it carried no visibility at all and a public struct's
        # dependency on a private constant through it was silently dropped).
        # Deliberately NOT added to ``name_index``/``ref_name_index`` —
        # fields are not resolvable *type* targets or reference targets in
        # their own right, only entities with their own file to look up.
        if cur_file:
            idx.decl_file.setdefault("::".join([*scope, name]), cur_file)
        return _index_children(node, scope, cur_file, idx, in_body)

    if kind in _FUNCTION_DECL_KINDS:
        # Everything declared inside a function/method body — including a
        # plain block-scope local (``int api() { int x; return x; }``) — is
        # a private implementation detail, not a meaningful dependency
        # target: it can never be reached from outside the function, so
        # indexing it as a resolvable reference the same way a namespace-
        # scope global is indexed would let a public function's *ordinary
        # local variables* get marked ``defined_in_project`` and reported by
        # ``public_to_internal_dependency`` as a hidden internal dependency
        # (Codex review).
        return _index_children(node, scope, cur_file, idx, True)

    if kind in _REFERENCE_DECL_KINDS and not in_body:
        _index_reference_target(node, name, cur_file, idx)

    return _index_children(node, scope, cur_file, idx, in_body)


#: :func:`_resolve_type_name` resolution labels (ADR-041 richer-confidence
#: addendum), ordered strongest first.
RESOLUTION_SCOPE = "scope"
RESOLUTION_UNIQUE_CANDIDATE = "unique_candidate"
RESOLUTION_UNRESOLVED = "unresolved"

#: :func:`_resolve_ref_identity` resolution labels (ADR-041 P1 #4): a
#: distinct vocabulary from the three above, since the underlying mechanism
#: differs (a ``DeclRefExpr`` stub's own completeness / the id-index's
#: unambiguous per-node match, vs. C++ scope-qualified name lookup) --
#: `RESOLUTION_REF_EXACT` covers both "the stub was already complete" and
#: "the id-index pinned an unambiguous match", since neither involves any
#: guessing, unlike `RESOLUTION_SCOPE`'s single decisive path.
RESOLUTION_REF_EXACT = "exact"
RESOLUTION_REF_UNIQUE_CANDIDATE = "unique_candidate"
RESOLUTION_REF_UNRESOLVED = "unresolved"


def _resolve_type_name(
    raw: str, scope: list[str], name_index: dict[str, list[str]]
) -> tuple[str, str]:
    """Resolve a possibly-unqualified or partially-qualified type spelling to
    a fully qualified name.

    clang's ``qualType`` prints a type *as written*, not fully qualified — a
    field typed ``Base`` inside ``namespace ns { struct Widget { ... }; }``
    prints as ``"Base"``, not ``"ns::Base"`` (Codex review). The same holds
    for a *partially* qualified spelling: a field typed ``detail::Impl``
    written inside ``namespace ns { namespace detail { ... } }`` prints as
    ``"detail::Impl"``, not ``"ns::detail::Impl"`` — a naive "already has
    ``::`` so it must be fully qualified" shortcut misses this (Codex
    review). Both cases are handled uniformly: index lookups key on the
    spelling's *last* component (``"Impl"`` for either ``"Impl"`` or
    ``"detail::Impl"``), candidates are filtered to those whose full
    qualified name ends with the raw spelling (so ``"detail::Impl"`` only
    matches a candidate ending ``"::detail::Impl"``, never an unrelated
    ``"other::Impl"``), then the nearest enclosing scope is tried first,
    each enclosing scope outward next, and a unique remaining candidate last.

    Returns ``(name, resolution)`` — *resolution* is one of
    :data:`RESOLUTION_SCOPE` (a qualifying declaration was found via the
    scope walk — real C++ lookup order; this includes a **global**
    declaration whose resolved spelling happens to equal the raw spelling,
    e.g. ``"Base"`` at namespace scope, which is still a real scope match,
    not "unresolved" — Codex review), :data:`RESOLUTION_UNIQUE_CANDIDATE` (no
    scope in the walk matched, but exactly one same-bare-name declaration
    exists anywhere in the TU — a last-resort guess, weaker than a scope
    match even though a caller may still choose to trust it), or
    :data:`RESOLUTION_UNRESOLVED` (no candidate matched at all; the raw
    spelling is returned unchanged). Best effort, not a real semantic
    lookup.
    """
    if not raw:
        return raw, RESOLUTION_UNRESOLVED
    # A leading "::" is C++'s global-scope qualifier ("::ns::detail::Impl"),
    # not part of the name itself — the index stores declarations without it
    # (Codex review: matching on the unstripped spelling built "::::..." and
    # never joined the indexed "ns::detail::Impl").
    lookup = raw[2:] if raw.startswith("::") else raw
    leaf = lookup.rsplit("::", 1)[-1]
    candidates = name_index.get(leaf)
    if not candidates:
        return raw, RESOLUTION_UNRESOLVED
    suffix = "::" + lookup
    matching = [c for c in candidates if c == lookup or c.endswith(suffix)]
    if not matching:
        return raw, RESOLUTION_UNRESOLVED
    matching_set = set(matching)
    for k in range(len(scope), -1, -1):
        prefix = "::".join(scope[:k])
        target = f"{prefix}::{lookup}" if prefix else lookup
        if target in matching_set:
            return target, RESOLUTION_SCOPE
    if len(matching) == 1:
        return matching[0], RESOLUTION_UNIQUE_CANDIDATE
    return raw, RESOLUTION_UNRESOLVED


def _resolve_ref_identity(
    ref: dict[str, Any],
    decl_file: dict[str, str],
    ref_name_index: dict[str, list[str]],
    id_index: dict[str, tuple[str, str]],
) -> tuple[str, str, str]:
    """Resolve a ``DeclRefExpr``'s ``referencedDecl`` to its full identity, file,
    and resolution tier (:data:`RESOLUTION_REF_EXACT`/
    :data:`RESOLUTION_REF_UNIQUE_CANDIDATE`/:data:`RESOLUTION_REF_UNRESOLVED`,
    ADR-041 P1 #4 — this edge kind used to be flat ``CONF_REDUCED`` regardless
    of how confidently its target was identified, the one edge kind the
    scope/unique_candidate/unresolved confidence pattern the rest of this
    module already uses hadn't reached).

    clang commonly emits an *incomplete* stub for ``referencedDecl`` — e.g.
    ``{"kind": "VarDecl", "name": "k"}`` with no ``mangledName``/``loc`` even
    though the full ``VarDecl`` elsewhere in the same TU carries both (Codex
    review). Keying the edge from the stub's bare-name identity means it never
    matches ``decl_file`` (indexed by the *full* declaration's identity), so
    the dependency's ``dst_file``/``defined_in_project`` provenance is lost —
    the exact scenario this module exists to catch
    (``inline int f() { return detail::k; }``).

    *Identity* resolution order: the stub's own mangled-or-bare identity when
    it already resolves (i.e. it *was* complete) — :data:`RESOLUTION_REF_EXACT`;
    else — before falling back to the ambiguous bare-name candidate list — the
    stub's own ``id`` looked up in *id_index*, which (like the file lookup
    below) is keyed by clang's per-node id rather than the shared bare name two
    same-named declarations in different scopes both collapse to (Codex
    review: this disambiguates ``a::k`` from ``b::k`` for *identity*, not only
    for ``dst_file`` as the id lookup previously did — a stub with no
    ``mangledName`` used to stay ambiguous even though the id already pinned
    down exactly which declaration it was) — this is *also*
    :data:`RESOLUTION_REF_EXACT`: a per-node id match is deterministic and
    unambiguous by construction, no guessing involved, exactly like an
    already-complete stub. Only when neither resolves does this fall back to
    the unique full declaration sharing the bare name, when unambiguous —
    :data:`RESOLUTION_REF_UNIQUE_CANDIDATE`, a genuine best-effort guess.
    Genuinely ambiguous bare names (no id match, more than one same-named
    candidate) are left unresolved (:data:`RESOLUTION_REF_UNRESOLVED`) rather
    than guessed, a known, documented limitation; a fully general fix needs a
    stable scope-qualified identity for every declaration, not just ones an
    ``id`` happens to disambiguate, tracked in ADR-041 P1.

    *File* resolution prefers the same *id_index* lookup — present even on
    an otherwise-incomplete stub and shared with the node's full declaration
    elsewhere in the same TU. Falls back to ``decl_file`` keyed by the
    resolved identity when no id match exists.
    """
    ident = _decl_identity(ref)
    node_id = str(ref.get("id") or "")
    id_hit = id_index.get(node_id)
    # The id-index match must win over "ident already happens to be a key in
    # decl_file" (Codex review): `decl_file` is keyed by bare identity across
    # the whole TU, so a same-named declaration in an unrelated scope can make
    # `ident in decl_file` true even though *this* stub names a different
    # declaration — the id-index lookup is per-node and unambiguous by
    # construction, so it must be tried first regardless.
    if id_hit and id_hit[0]:
        ident = id_hit[0]
        resolution = RESOLUTION_REF_EXACT
    elif ref.get("mangledName") and ident in decl_file:
        # The stub was already complete (a real mangledName, not just a bare
        # name that coincidentally matches another declaration) and resolves.
        resolution = RESOLUTION_REF_EXACT
    else:
        name = str(ref.get("name") or "")
        candidates = ref_name_index.get(name) if name else None
        if candidates and len(candidates) == 1:
            ident = candidates[0]
            resolution = RESOLUTION_REF_UNIQUE_CANDIDATE
        else:
            resolution = RESOLUTION_REF_UNRESOLVED
    file = (id_hit[1] if id_hit else "") or decl_file.get(ident, "")
    return ident, file, resolution


def _anonymous_tag_id_referenced(node: Any, tag_id: str) -> bool:
    """Whether *node* (or something nested under it) names *tag_id* as its
    own tag declaration -- ``ownedTagDecl.id`` (an ``ElaboratedType``) or
    ``decl.id`` (the sibling ``EnumType``), searched recursively since the
    two can nest either directly or one inside the other depending on
    clang's own version (verified against real Clang 18 for both shapes).
    """
    if not isinstance(node, dict):
        return False
    owned = node.get("ownedTagDecl")
    if isinstance(owned, dict) and owned.get("id") == tag_id:
        return True
    decl = node.get("decl")
    if isinstance(decl, dict) and decl.get("id") == tag_id:
        return True
    return any(
        _anonymous_tag_id_referenced(child, tag_id)
        for child in node.get("inner", []) or []
    )


_ANONYMOUS_ENUM_MARKER = "(unnamed enum"


def _declares_anonymous_enum_type(node: Any) -> bool:
    """Whether *node* (a ``FieldDecl``/``VarDecl``) is declared with the type
    of an anonymous enum -- clang spells this ``"enum (unnamed enum at
    <file>:<line>:<col>)"`` in both ``type.qualType`` and
    ``desugaredQualType`` (verified against real Clang 18), with **no** id
    linkage back to the ``EnumDecl`` at all (unlike the typedef/alias case
    :func:`_anonymous_tag_id_referenced` handles) -- so a substring check is
    the only join signal clang's JSON AST actually provides for this shape.
    """
    node_type = node.get("type")
    if not isinstance(node_type, dict):
        return False
    return _ANONYMOUS_ENUM_MARKER in str(node_type.get("qualType", ""))


def index_declared_type_files(ast: dict[str, Any]) -> dict[str, str]:
    """Public wrapper: qualified type/enum/typedef name -> declaring file.

    Reuses the same first-indexing pass :func:`parse_clang_ast_types` already
    runs over the AST (:func:`_index_declared_entities`), exposed standalone
    for a caller that only needs the declaring-file index (e.g. a header-only
    graph builder resolving a type's public/private origin) and not the
    type/reference edges themselves (ADR-041 header-only-graph addendum).
    Inside :func:`ast_derived_scope` it shares that one index walk with
    :func:`parse_clang_ast_types` and :func:`index_declared_entity_files`
    over the same tree; outside one it walks the AST itself.

    ``_index_declared_entities``'s ``decl_file`` output is a dict shared
    between two unrelated uses: type declarations (records/enums/typedefs,
    also indexed in ``name_index``) *and* var/enum-constant identities (used
    only to resolve ``DECL_REFERENCES_DECL`` edge targets, never indexed in
    ``name_index``). Returning it unfiltered would let a caller (Codex
    review: caught in ``header_graph.py``, which treats every key as a type)
    mistake a public constant or enum value for a record/enum/typedef
    declaration. Filtered here to exactly the qualified names
    ``name_index`` actually collected — the type-only subset.
    """
    idx = _ast_indexes(ast)
    type_qnames = {qname for qnames in idx.name_index.values() for qname in qnames}
    # decl_file's document order: a set's order follows per-process string hashing.
    return {q: f for q, f in idx.decl_file.items() if q in type_qnames}


def index_declared_entity_files(ast: dict[str, Any]) -> dict[str, str]:
    """Public wrapper: every declared identity's own declaring file.

    Unlike :func:`index_declared_type_files`, deliberately **unfiltered** —
    every entity kind :func:`_index_declared_entities` indexes into
    ``decl_file`` (record/enum/typedef, var/enum-constant, and field), not
    just the type-only subset. Safe for a caller that only ever backfills a
    *declaration node's own origin* (always seeded as a ``source_decl``,
    never a type node) — the "public constant mistaken for a record/enum/
    typedef" risk :func:`index_declared_type_files`'s filtering guards
    against does not apply to that use.

    For a header-only graph, this resolves the *source* side of a
    ``DECL_REFERENCES_DECL`` edge whose source is a field's default member
    initializer (``struct Widget { int x = detail::k; };`` — ``Widget::x``
    is never seeded from ``snapshot.functions``/``snapshot.variables``, the
    flat model has no per-field entity to iterate) — mirroring the existing
    per-edge ``dst_file``/``caller_file``/``callee_file`` backfill for each
    edge's *target* (Codex review).
    """
    # A copy: the indexes may be shared with other readers of this AST.
    return dict(_ast_indexes(ast).decl_file)


#: What this module derives from one AST, shared by every consumer that reads
#: the same tree while :func:`ast_derived_scope` is open: the whole-TU
#: indexes (:func:`_ast_indexes`) and the type edges built on them. Several
#: consumers read one TU's tree: the L5 ``type_graph`` pass and
#: ``override_graph``'s two parsers (each derives the class hierarchy from the
#: ``TYPE_INHERITS`` edges), and on the header-only path the type-file index,
#: the edges and the entity-file index. Each used to walk the whole AST again.
#: Keyed on the tree's identity and pinned to it (``ScopedCache``'s safe
#: ``id()`` keying), so a different -- or equal-but-distinct -- AST never
#: hits; sound because no consumer mutates the AST it is handed, and the
#: shared values are never handed out mutable (edges are frozen and copied
#: into a fresh list, the entity-file index is copied). Outside a scope, or
#: under ``ABICHECK_REFERENCE_MODE``, every call computes.
_AST_DERIVED: ScopedCache = ScopedCache("buildsource.type_graph.ast_derived")


def ast_derived_scope() -> AbstractContextManager[None]:
    """Share this module's per-AST derivations for one unit of work."""
    return _AST_DERIVED.scope()


def _ast_indexes(ast: dict[str, Any]) -> _AstIndexes:
    """The whole-TU indexes of *ast*: read-only to every caller."""

    def build() -> _AstIndexes:
        idx = _new_ast_indexes()
        _index_declared_entities(ast, [], "", idx)
        return idx

    return _AST_DERIVED.get_or_compute(
        request_key(derived="indexes", ast=id(ast)), build, pin=ast
    )
