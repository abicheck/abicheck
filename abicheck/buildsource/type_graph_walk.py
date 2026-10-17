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

"""Clang AST walkers that emit the type/reference edges (ADR-041 P0).

:func:`parse_clang_ast_types` is a pure function over one translation unit's
``clang -ast-dump=json`` tree. It walks every declaration and emits the
``TYPE_INHERITS``, ``TYPE_HAS_FIELD_TYPE``, ``DECL_HAS_TYPE`` and
``DECL_REFERENCES_DECL`` edges, deduplicated. Each spelled name is resolved
through :mod:`~abicheck.buildsource.type_graph_index`; the spelling helpers
come from :mod:`~abicheck.buildsource.type_graph_names`; the edge value type
and the cross-TU fold live in :mod:`~abicheck.buildsource.type_graph`.
"""

from __future__ import annotations

from typing import Any

from ..model.execution_cache import request_key
from ..model.graph_facts import CONF_HIGH, CONF_REDUCED
from ..model.name_decoration import macho_underscore as macho_decoration
from ..model.source_graph import function_decl_identity
from .type_graph import (
    EDGE_DECL_HAS_TYPE,
    EDGE_DECL_REFERENCES_DECL,
    EDGE_TYPE_HAS_FIELD_TYPE,
    EDGE_TYPE_INHERITS,
    TypeEdge,
    _dedupe_edges,
)
from .type_graph_index import (
    _AST_DERIVED,
    _FUNCTION_DECL_KINDS,
    _OTHER_TYPE_DECL_KINDS,
    _RECORD_DECL_KINDS,
    _REFERENCE_DECL_KINDS,
    _TEMPLATE_DECL_KINDS,
    _TEMPLATE_PARM_DECL_KINDS,
    RESOLUTION_REF_EXACT,
    RESOLUTION_SCOPE,
    _anonymous_tag_id_referenced,
    _ast_indexes,
    _AstIndexes,
    _declares_anonymous_enum_type,
    _resolve_ref_identity,
    _resolve_type_name,
)
from .type_graph_names import (
    _decl_return_type_name,
    _decl_type_name,
    _is_excluded_type,
    _resolve_nested_type_names,
)


def _emit_type_edges(
    edges: list[TypeEdge],
    src: str,
    raw: str,
    kind: str,
    role: str,
    scope: list[str],
    name_index: dict[str, list[str]],
    decl_file: dict[str, str],
) -> None:
    """Resolve *raw* — a **raw, unprocessed** ``qualType`` spelling — and
    every nested type name :func:`_resolve_nested_type_names` finds inside
    it (template arguments, callback parameter/return types, recursively),
    appending a :class:`TypeEdge` for each resolved, non-excluded name.

    A field typed ``std::unique_ptr<detail::Impl>`` — the common PImpl/
    container pattern — would otherwise only produce an edge to the whole,
    never-resolvable instantiation spelling, hiding the actual private-type
    dependency on ``detail::Impl`` (Codex review). Duplicate names (e.g. the
    same private type appearing as two template arguments) are only emitted
    once per call site.
    """
    seen: set[str] = set()
    for candidate in _resolve_nested_type_names(raw):
        name, resolution = _resolve_type_name(candidate, scope, name_index)
        if not name or _is_excluded_type(name) or name in seen:
            continue
        seen.add(name)
        edges.append(
            TypeEdge(
                src,
                name,
                kind,
                CONF_HIGH if resolution == RESOLUTION_SCOPE else CONF_REDUCED,
                role,
                decl_file.get(name, ""),
                resolution,
            )
        )


def _walk_children(
    node: Any,
    scope: list[str],
    enclosing_func: str,
    edges: list[TypeEdge],
    idx: _AstIndexes,
) -> None:
    """Recurse into every child of *node* with the given scope/function.

    Also closes a real gap in the ``enum_underlying`` role (Codex review,
    fresh evidence): the C-style ``typedef enum : U { ... } Name;`` /
    ``using Name = enum : U { ... };`` idiom gives the enum itself **no**
    name at all -- clang emits it as an anonymous ``EnumDecl`` (``"name":
    ""``), a *sibling* of the following ``TypedefDecl``/``TypeAliasDecl``,
    never nested inside it (verified against real Clang 18). ``_walk_types``'s
    own ``kind == "EnumDecl" and name`` guard on :func:`_emit_enum_underlying_edge`
    is correct for every *named* enum, but an anonymous one has no ``name``
    to satisfy it, so its ``fixedUnderlyingType`` dependency (``detail::Handle``
    in the example above) was silently dropped -- the enclosing alias edge the
    typedef itself produces names only the enum's own (anonymous) spelling,
    never the underlying type. Fixed by remembering the immediately preceding
    anonymous ``EnumDecl`` sibling and, when the next sibling is a named
    ``TypedefDecl``/``TypeAliasDecl`` that resolves back to that exact tag
    (via :func:`_anonymous_tag_id_referenced`, an ``id`` match, not a name
    heuristic), emitting the ``enum_underlying`` edge under the typedef's own
    name -- the only public identity this anonymous enum ever gets.

    A second, differently-shaped instance of the identical gap (Codex
    review, fresh evidence): ``struct Public { enum : U { A } value; };`` --
    an anonymous enum introduced through a field or (namespace/class-scope)
    variable declarator rather than a typedef/alias. clang gives this shape
    **no** id-based linkage whatsoever (confirmed empirically: the
    ``FieldDecl``/``VarDecl``'s own ``type`` carries only the location-
    encoded spelling, no ``ownedTagDecl``/``decl`` id at all), so sibling
    adjacency plus :func:`_declares_anonymous_enum_type`'s marker-string
    check is the only join signal available -- safe because C++ requires an
    anonymous tag's own declarator(s) to be the *same* declaration, always
    the immediately following sibling, never separated by an unrelated one.
    Attributed to the *owning* identity a field/variable's own type edge
    would already use (the record's own qualified name for a field,
    :func:`_var_decl_ident` for a variable) rather than inventing a name for
    the enum itself, since it has no public identity of its own to name.

    A thin wrapper over :func:`_walk_child_sequence` for the ordinary
    whole-tree walk, which always starts a fresh anonymous-tag group at each
    parent and has no use for the trailing state it returns.
    """
    _walk_child_sequence(node.get("inner", []) or [], scope, enclosing_func, edges, idx)


def _walk_child_sequence(
    children: Any,
    scope: list[str],
    enclosing_func: str,
    edges: list[TypeEdge],
    idx: _AstIndexes,
    pending_anon_enum: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Walk *children* in order, threading the anonymous-tag group state.

    Split out of :func:`_walk_children` so a caller that supplies the child
    list itself -- rather than reading it off one parent node -- can carry
    that state across calls. The header-graph streaming projection
    (:mod:`abicheck.buildsource.header_graph_ast_stream`) is the one such
    caller: it hands the translation unit's top-level declarations over one
    at a time so the whole tree is never resident, and an anonymous
    ``EnumDecl`` and the ``TypedefDecl``/``FieldDecl``/``VarDecl`` declarator
    that names it are *siblings*, so at top level they can fall either side
    of an element boundary. Returning the trailing state (and accepting it
    back on the next call) is what keeps that split invisible: the sequence
    of calls sees exactly the sibling run a single call over the whole list
    would have seen.

    Returns the group state left over after the last child, for that caller
    to feed into the next call.
    """
    for child in children:
        if isinstance(child, dict):
            tag_id = str((pending_anon_enum or {}).get("id") or "")
            name = str(child.get("name") or "")
            kind = child.get("kind")
            matched = False
            if (
                tag_id
                and name
                and kind in ("TypedefDecl", "TypeAliasDecl")
                and _anonymous_tag_id_referenced(child, tag_id)
            ):
                assert pending_anon_enum is not None
                _emit_enum_underlying_edge(pending_anon_enum, scope, name, edges, idx)
                matched = True
            elif (
                pending_anon_enum is not None
                and kind == "FieldDecl"
                and name
                and _declares_anonymous_enum_type(child)
            ):
                _emit_enum_underlying_edge_from(
                    "::".join(scope),
                    pending_anon_enum,
                    EDGE_TYPE_HAS_FIELD_TYPE,
                    scope,
                    edges,
                    idx,
                )
                matched = True
            elif (
                pending_anon_enum is not None
                and kind == "VarDecl"
                and name
                and not enclosing_func
                and _declares_anonymous_enum_type(child)
            ):
                _emit_enum_underlying_edge_from(
                    _var_decl_ident(child, scope, name),
                    pending_anon_enum,
                    EDGE_DECL_HAS_TYPE,
                    scope,
                    edges,
                    idx,
                )
                matched = True

            if kind == "EnumDecl" and not child.get("name"):
                pending_anon_enum = child
            elif not matched:
                # A sibling that neither starts a new anonymous tag nor
                # continues the current one ends the group -- but a
                # *matching* declarator does NOT (Codex review, fresh
                # evidence): one anonymous-enum declaration can introduce
                # more than one declarator (`enum : U { A } first, second;`
                # / `typedef enum : U { A } NameA, NameB;`, both verified
                # against real Clang 18 to emit the tag once followed by
                # ALL of its declarators as siblings, each independently
                # carrying the same id/marker linkage) -- clearing on the
                # first match silently dropped every declarator after it.
                pending_anon_enum = None
        _walk_types(child, scope, enclosing_func, edges, idx)
    return pending_anon_enum


def _emit_record_member_edges(
    node: Any,
    qname: str,
    child_scope: list[str],
    edges: list[TypeEdge],
    idx: _AstIndexes,
) -> None:
    """Emit a record's own base and field type edges.

    Base/field types resolve against the record's *own* scope
    (``child_scope``), not just the enclosing one — a field or base naming a
    type nested in this same record (``Outer::Inner`` referenced as bare
    ``"Inner"`` from inside ``Outer``) must be looked up starting from the
    record's own body outward, matching real C++ member lookup order (Codex
    review). :func:`_resolve_type_name` still tries every shorter prefix down
    to the enclosing scopes, so this is a strict superset of the previous
    (enclosing-scope-only) lookup.
    """
    for base in node.get("bases", []) or []:
        if not isinstance(base, dict):
            continue
        base_type = base.get("type")
        raw_base = (
            str(base_type.get("qualType", "")) if isinstance(base_type, dict) else ""
        )
        _emit_type_edges(
            edges,
            qname,
            raw_base,
            EDGE_TYPE_INHERITS,
            "base",
            child_scope,
            idx.name_index,
            idx.decl_file,
        )
    for child in node.get("inner", []) or []:
        if isinstance(child, dict) and child.get("kind") == "FieldDecl":
            _emit_type_edges(
                edges,
                qname,
                _decl_type_name(child),
                EDGE_TYPE_HAS_FIELD_TYPE,
                "field",
                child_scope,
                idx.name_index,
                idx.decl_file,
            )


def _emit_alias_edge(
    node: Any, scope: list[str], name: str, edges: list[TypeEdge], idx: _AstIndexes
) -> None:
    """Emit the edge from a typedef/type-alias to its *underlying* type.

    A public alias's underlying type was never emitted as a dependency at all
    — only the alias's own name was indexed as a resolvable target (Codex
    review: ``using Handle = detail::Impl *;`` produced no edge from
    ``Handle`` to the private ``detail::Impl`` it wraps, so
    ``public_to_internal_dependency`` had nothing to report for APIs that only
    ever spell the public alias name).

    Also covers an **alias template**'s target (G29 Phase 5 item 5): clang
    nests the real ``TypeAliasDecl`` inside a ``TypeAliasTemplateDecl``, and
    :func:`_walk_types` recurses into the template node's children with the
    unchanged scope — so ``template <class T> using Ptr = detail::Impl *;``
    reaches this function as an ordinary ``TypeAliasDecl`` named ``Ptr`` and
    emits ``api::Ptr -> detail::Impl`` under the same ``"alias"`` role.
    Verified empirically against real Clang 18 output rather than assumed;
    pinned by ``tests/test_type_graph_roles.py``.
    """
    _emit_type_edges(
        edges,
        "::".join([*scope, name]),
        _decl_type_name(node),
        EDGE_TYPE_HAS_FIELD_TYPE,
        "alias",
        scope,
        idx.name_index,
        idx.decl_file,
    )


def _emit_enum_underlying_edge_from(
    src: str,
    node: Any,
    edge_kind: str,
    scope: list[str],
    edges: list[TypeEdge],
    idx: _AstIndexes,
) -> None:
    """Core of the ``enum_underlying`` role: emit *node* (an ``EnumDecl``)'s
    ``fixedUnderlyingType`` dependency from an already-computed *src*
    identity, under *edge_kind* (``TYPE_HAS_FIELD_TYPE`` for a type-shaped
    src -- a named enum, or a typedef/alias/record standing in for an
    anonymous one; ``DECL_HAS_TYPE`` for a decl-shaped src -- a variable).
    Split out so every caller that can name *some* public identity for an
    otherwise-anonymous enum (see :func:`_emit_enum_underlying_edge` and
    :func:`_walk_children`'s field/variable declarator handling) shares one
    implementation rather than re-deriving the ``fixedUnderlyingType`` read.

    An ``EnumDecl`` carries **no** ``type`` key at all — the underlying type
    lives in its own ``fixedUnderlyingType`` object (verified against real
    Clang 18 output). So the shared :func:`_emit_alias_edge` path this kind
    used to take read an empty spelling and emitted nothing: ``enum class
    Color : detail::Handle`` produced no dependency on the private
    ``detail::Handle`` it is laid out as.

    ``qualType`` (the spelling *as written*, ``"detail::Handle"``) is read
    rather than the sibling ``desugaredQualType`` (``"int"``), matching what
    :func:`_resolve_type_name` expects everywhere else in this module — the
    desugared form has already lost the very identity this edge exists to
    name.

    An enum with no *written* underlying type still gets a
    ``fixedUnderlyingType`` from clang when it is scoped (``enum class Plain
    { A };`` → ``"int"``) and none at all when it is unscoped — the former
    is filtered by :func:`_is_excluded_type` like any other fundamental type,
    so neither shape produces a spurious edge.
    """
    fixed = node.get("fixedUnderlyingType")
    raw = str(fixed.get("qualType", "")) if isinstance(fixed, dict) else ""
    if not raw:
        return
    _emit_type_edges(
        edges,
        src,
        raw,
        edge_kind,
        "enum_underlying",
        scope,
        idx.name_index,
        idx.decl_file,
    )


def _emit_enum_underlying_edge(
    node: Any, scope: list[str], name: str, edges: list[TypeEdge], idx: _AstIndexes
) -> None:
    """Emit the edge from a *named* enum to its fixed underlying type (G29
    Phase 5 item 5's ``enum_underlying`` role) -- see
    :func:`_emit_enum_underlying_edge_from` for the shared implementation.
    """
    _emit_enum_underlying_edge_from(
        "::".join([*scope, name]), node, EDGE_TYPE_HAS_FIELD_TYPE, scope, edges, idx
    )


def _var_decl_ident(node: Any, scope: list[str], name: str) -> str:
    """The identity a namespace/class-scope variable contributes as an edge src.

    Factored out of :func:`_emit_var_decl_edge` so a variable *template*'s own
    template-parameter edges (G29 Phase 5 item 5) land on the exact same
    ``decl://`` node the templated ``VarDecl``'s own type edge does — see that
    function's docstring for why the qualified-name fallback is load-bearing.
    """
    return function_decl_identity(
        macho_decoration.decode_itanium(str(node.get("mangledName") or "")),
        name,
        "::".join([*scope, name]) if scope else name,
        "",
    )


def _emit_var_decl_edge(
    node: Any, scope: list[str], name: str, edges: list[TypeEdge], idx: _AstIndexes
) -> None:
    """Emit a namespace/class-scope variable's own DECL_HAS_TYPE edge.

    A public/exported data declaration's own type was never emitted either —
    this module only ever read a VarDecl's type when it was the *target* of a
    DeclRefExpr, never at its own declaration site (Codex review: ``extern
    detail::Impl *g;`` or a public static data member produced no
    DECL_HAS_TYPE edge for the private pointee).

    Identity must be scope-qualified when unmangled (Codex review): a public
    ``extern "C"`` variable inside a namespace (``namespace api { extern "C"
    detail::Impl *g; }``) reports ``mangledName == name`` (no real Itanium
    mangling), so ``SourceEntity.identity()`` falls back to the qualified name
    ``"api::g"`` -- but the bare ``_decl_identity(node)`` gives just ``"g"``,
    landing this edge's src on a different ``decl://`` node than the public
    SOURCE_DECLARES node and breaking reachability from the public variable to
    its private pointee. :func:`function_decl_identity` with an empty
    ``type_qual`` falls through to the same bare-qualified-name case (a
    variable's ``SourceEntity`` never sets ``signature_hash``, unlike a
    function's), so it doubles as the right fallback here.
    """
    ident = _var_decl_ident(node, scope, name)
    if not ident:
        return
    _emit_type_edges(
        edges,
        ident,
        _decl_type_name(node),
        EDGE_DECL_HAS_TYPE,
        "var",
        scope,
        idx.name_index,
        idx.decl_file,
    )


def _emit_function_signature_edges(
    node: Any, ident: str, scope: list[str], edges: list[TypeEdge], idx: _AstIndexes
) -> None:
    """Emit a function's own return-type and parameter-type edges."""
    raw_return = _decl_return_type_name(node)
    if raw_return:
        _emit_type_edges(
            edges,
            ident,
            raw_return,
            EDGE_DECL_HAS_TYPE,
            "return",
            scope,
            idx.name_index,
            idx.decl_file,
        )
    for child in node.get("inner", []) or []:
        if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
            _emit_type_edges(
                edges,
                ident,
                _decl_type_name(child),
                EDGE_DECL_HAS_TYPE,
                "param",
                scope,
                idx.name_index,
                idx.decl_file,
            )


def _function_decl_ident(node: Any, scope: list[str], name: str) -> str:
    """The identity a function/method declaration contributes as an edge src."""
    if not name:
        return ""
    type_obj = node.get("type")
    return function_decl_identity(
        macho_decoration.decode_itanium(str(node.get("mangledName") or "")),
        name,
        "::".join([*scope, name]) if scope else name,
        str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else "",
    )


def _templated_entity_src(node: Any, scope: list[str]) -> tuple[str, str]:
    """The ``(identity, edge_kind)`` a template declaration's *own* node uses.

    A ``ClassTemplateDecl``/``FunctionTemplateDecl``/``VarTemplateDecl``/
    ``TypeAliasTemplateDecl`` carries its template parameters as direct
    children of the *wrapper*, while the templated entity itself is a sibling
    child (verified against real Clang 18 output). The wrapper is not a node
    in this graph — so a parameter's type dependency must be attributed to the
    templated entity, and it must land on the **same** node that entity's own
    edges already use, or a public template's constraint would sit on an
    orphan node nothing else reaches.

    That means re-deriving the identity exactly the way this module's walk
    does for the templated child's own kind — a record/alias becomes a
    ``record_type`` node (so ``TYPE_HAS_FIELD_TYPE``), a function/variable a
    ``source_decl`` one (so ``DECL_HAS_TYPE``); see
    :func:`augment_graph_with_types` for that edge-kind→node-kind mapping.
    Returns ``("", "")`` when no templated child is recognized (e.g. a
    template whose entity kind this module doesn't model), so the caller
    emits nothing rather than guessing at a node id.
    """
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = str(child.get("kind", ""))
        name = str(child.get("name") or "")
        if not name:
            continue
        if kind in _RECORD_DECL_KINDS or kind in _OTHER_TYPE_DECL_KINDS:
            return "::".join([*scope, name]), EDGE_TYPE_HAS_FIELD_TYPE
        if kind in _FUNCTION_DECL_KINDS:
            return _function_decl_ident(child, scope, name), EDGE_DECL_HAS_TYPE
        if kind == "VarDecl":
            return _var_decl_ident(child, scope, name), EDGE_DECL_HAS_TYPE
    return "", ""


def _emit_template_parameter_edges(
    node: Any, scope: list[str], edges: list[TypeEdge], idx: _AstIndexes
) -> None:
    """Emit a template's own ``template_param``/``default_template_arg`` edges
    (G29 Phase 5 item 5).

    Two of the nine type roles that item lists had **no** producer at all
    before this — a public template constrained by, or defaulted to, a private
    type carried a real dependency the graph never recorded:

    - ``template <detail::Handle H> struct Slot`` — the non-type parameter's
      own type (``NonTypeTemplateParmDecl.type.qualType``), role
      ``template_param``. A *type* parameter (``TemplateTypeParmDecl``) names
      no type of its own, so only the non-type kind contributes here.
    - ``template <class T = detail::Impl> struct Box`` — the default argument
      (``defaultArg.type.qualType``), role ``default_template_arg``.

    **Scope note (Codex review, fresh evidence):** ``template_param`` answers
    "what type does this *parameter* require", not "what does a specific
    *instantiation's argument* reference" — a declaration-valued non-type
    argument at a use site (``Holder<&detail::f>``) is a different question
    this function does not answer, resolved instead (when it is resolved at
    all) via a ``ClassTemplateSpecializationDecl``'s own ``TemplateArgument.
    decl`` cross-reference — exactly ``template_graph.py``'s reserved,
    unpopulated ``TEMPLATE_USES_DECL``, not this role. ``_walk_types``
    deliberately never emits edges for a ``ClassTemplateSpecializationDecl``
    node at all (see its own comment), so no code path here could reach that
    case even incidentally.

    Two default-argument shapes are deliberately **not** emitted, because
    clang's JSON simply does not carry the dependency (both verified against
    real Clang 18 output, not assumed):

    - a *non-type* parameter's default **value** (``template <detail::Handle
      H = detail::K>``) dumps as ``{"kind": "TemplateArgument", "isExpr":
      true}`` with no ``type`` — the referenced constant is a nested
      ``DeclRefExpr``, a ``DECL_REFERENCES_DECL`` question rather than a type
      role, and out of scope for this item;
    - a *template template* parameter's default (``template <template <class>
      class C = detail::Def>``) dumps as a bare ``{"kind":
      "TemplateArgument"}`` — neither a type nor a name — so there is nothing
      to resolve and this degrades to no fact rather than a guessed one
      (ADR-028 D3).
    """
    src, edge_kind = _templated_entity_src(node, scope)
    if not src:
        return
    _emit_template_parm_list_edges(node, src, edge_kind, scope, edges, idx)


def _emit_template_parm_list_edges(
    node: Any,
    src: str,
    edge_kind: str,
    scope: list[str],
    edges: list[TypeEdge],
    idx: _AstIndexes,
) -> None:
    """Emit ``template_param``/``default_template_arg`` edges for every
    parameter in *node*'s own ``inner`` children, recursing into a
    ``TemplateTemplateParmDecl`` child's *own* parameter list too.

    ``template <template <detail::Handle H> class C> struct Outer`` nests
    the non-type parameter ``H`` — typed by the private ``detail::Handle`` —
    directly inside the outer ``TemplateTemplateParmDecl`` node, verified
    against real Clang 18 output (Codex review, fresh evidence): the
    original single-level loop checked each direct child's own kind against
    ``NonTypeTemplateParmDecl`` but never descended into a
    ``TemplateTemplateParmDecl`` child's ``inner``, so this dependency was
    invisible. C++ permits an arbitrarily deep chain of template-template
    parameters (``template <template <template <class> class> class> ...``),
    so this recurses rather than unrolling one extra level — every nested
    parameter still attributes its edge to the *same* ``src``/``edge_kind``
    the outermost templated entity uses (:func:`_templated_entity_src`),
    since a nested parameter's own type dependency is exactly as much a
    dependency of the outer entity as a top-level one.
    """
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = str(child.get("kind", ""))
        if kind not in _TEMPLATE_PARM_DECL_KINDS:
            continue
        if kind == "NonTypeTemplateParmDecl":
            _emit_type_edges(
                edges,
                src,
                _decl_type_name(child),
                edge_kind,
                "template_param",
                scope,
                idx.name_index,
                idx.decl_file,
            )
        elif kind == "TemplateTemplateParmDecl":
            _emit_template_parm_list_edges(child, src, edge_kind, scope, edges, idx)
        default = child.get("defaultArg")
        default_type = default.get("type") if isinstance(default, dict) else None
        if isinstance(default_type, dict):
            _emit_type_edges(
                edges,
                src,
                str(default_type.get("qualType", "")),
                edge_kind,
                "default_template_arg",
                scope,
                idx.name_index,
                idx.decl_file,
            )


def _emit_decl_ref_edge(
    node: Any, enclosing_func: str, edges: list[TypeEdge], idx: _AstIndexes
) -> None:
    """Emit the DECL_REFERENCES_DECL edge for one ``DeclRefExpr``."""
    ref = node.get("referencedDecl")
    if not isinstance(ref, dict) or ref.get("kind") not in _REFERENCE_DECL_KINDS:
        return
    ref_ident, ref_file, ref_resolution = _resolve_ref_identity(
        ref, idx.decl_file, idx.ref_name_index, idx.id_index
    )
    if not ref_ident or ref_ident == enclosing_func:
        return
    edges.append(
        TypeEdge(
            enclosing_func,
            ref_ident,
            EDGE_DECL_REFERENCES_DECL,
            CONF_HIGH if ref_resolution == RESOLUTION_REF_EXACT else CONF_REDUCED,
            "ref",
            ref_file,
            ref_resolution,
        )
    )


def _walk_types(
    node: Any,
    scope: list[str],
    enclosing_func: str,
    edges: list[TypeEdge],
    idx: _AstIndexes,
) -> None:
    """Recursive AST walk tracking the qualified-name scope and enclosing function."""
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")

    if kind in _RECORD_DECL_KINDS and name:
        child_scope = [*scope, name]
        # A ClassTemplateSpecializationDecl's own "name" is the *primary*
        # template's bare name (clang does not fold template arguments into
        # it), so the qualified name here collides with the generic template's
        # node id. Emitting base/field edges from a specific *internal*
        # instantiation (e.g. Holder<detail::Impl>) would misattribute that one
        # instantiation's dependency to the public generic template itself
        # (Codex review) — skip edge emission for specializations, but still
        # recurse into their children (nested decls still resolve).
        if kind != "ClassTemplateSpecializationDecl":
            _emit_record_member_edges(
                node, "::".join([*scope, name]), child_scope, edges, idx
            )
        _walk_children(node, child_scope, enclosing_func, edges, idx)
        return

    if kind == "NamespaceDecl" and name:
        _walk_children(node, [*scope, name], enclosing_func, edges, idx)
        return

    if kind in _TEMPLATE_DECL_KINDS:
        # Not a `return` — the templated entity itself is a child of this
        # wrapper node, and it still needs the ordinary walk (a class
        # template's record body, a function template's signature/body).
        _emit_template_parameter_edges(node, scope, edges, idx)

    if kind == "EnumDecl" and name:
        _emit_enum_underlying_edge(node, scope, name, edges, idx)
    elif kind in _OTHER_TYPE_DECL_KINDS and name:
        _emit_alias_edge(node, scope, name, edges, idx)

    if kind == "VarDecl" and name and not enclosing_func:
        # Block-scope locals are excluded the same way
        # `_index_declared_entities`'s `in_body` tracking excludes them from
        # provenance — `enclosing_func` is only truthy inside a function/
        # method body, never for a namespace- or class-scope declaration.
        _emit_var_decl_edge(node, scope, name, edges, idx)

    if kind in _FUNCTION_DECL_KINDS:
        ident = _function_decl_ident(node, scope, name)
        if ident:
            _emit_function_signature_edges(node, ident, scope, edges, idx)
        # Recurse into every child, including a ParmVarDecl — its type was
        # already recorded as a "param" edge above, but a default-argument
        # expression (e.g. `int f(int x = detail::k)`) lives *under* the
        # ParmVarDecl node itself, so skipping it entirely (rather than just
        # not re-emitting its type edge) silently dropped any
        # DECL_REFERENCES_DECL edge for a private constant read only in a
        # default argument (Codex review). ParmVarDecl isn't a case this
        # walk special-cases, so recursing into it just walks its children
        # with the enclosing function scope — the same as every other node.
        _walk_children(node, scope, ident or enclosing_func, edges, idx)
        return

    if kind == "FieldDecl" and name:
        # A default member initializer (``int x = detail::k;``) lives under
        # the FieldDecl node itself, not inside a function body — without a
        # truthy enclosing_func of its own, this recursed with whatever the
        # *record's* enclosing_func was (empty for a top-level record), so
        # the DeclRefExpr guard below never fired and a reference in a
        # default member initializer produced no edge at all (CodeRabbit
        # review). Give it a scope-qualified identity, mirroring how a
        # function's own identity becomes the enclosing_func for its body.
        field_ident = "::".join([*scope, name])
        _walk_children(node, scope, field_ident or enclosing_func, edges, idx)
        return

    if kind == "DeclRefExpr" and enclosing_func:
        _emit_decl_ref_edge(node, enclosing_func, edges, idx)

    _walk_children(node, scope, enclosing_func, edges, idx)


def parse_clang_ast_types(ast: dict[str, Any]) -> list[TypeEdge]:
    """Extract type/reference edges from a ``clang -ast-dump=json`` tree (pure).

    A first pass (:func:`_index_declared_entities`) indexes every type
    declaration's (record/enum/typedef/type-alias) qualified name, every
    var/enum-constant's declaring file, a bare-name index of var/enum-constant
    declarations, and a clang-node-id index of var/enum-constant declarations
    — all across the *whole* TU before any edge is built — so an unqualified
    type spelling (clang's ``qualType`` is written, not fully qualified) can
    be resolved against the nearest enclosing scope, an incomplete
    ``DeclRefExpr`` reference stub can be resolved to its full declaration
    (by node id first, disambiguating two declarations that share a bare
    name), and an AST-only dependency node can carry its declaring file
    regardless of declaration order. The second pass (:func:`_walk_types`)
    then walks the AST tracking the enclosing namespace/record scope (for
    qualified type names) and the nearest enclosing function (for parameter
    types and non-call body references). Edges are de-duplicated by
    ``(src, dst, kind)``.
    """
    return list(
        _AST_DERIVED.get_or_compute(
            request_key(derived="type_edges", ast=id(ast)),
            lambda: _parse_clang_ast_types(ast),
            pin=ast,
        )
    )


def _parse_clang_ast_types(ast: dict[str, Any]) -> tuple[TypeEdge, ...]:
    idx = _ast_indexes(ast)
    edges: list[TypeEdge] = []
    _walk_types(ast, [], "", edges, idx)
    return tuple(_dedupe_edges(edges))
