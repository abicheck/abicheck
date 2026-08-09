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

"""Template-instantiation graph extraction (G29 Phase 5 item 1, G29.6's
first-priority open graph family).

Closes the "public template → concrete instantiation → internal
specialization → emitted exported symbol" chain the review named: a public
class or function template's *own* declaration is often entirely
internal-type-free (``template <typename T> struct Wrapper { T value; };``),
but a specific **instantiation** (``Wrapper<internal::Detail>``) can both
depend on an internal type through its arguments and emit a real, linkable
symbol for its instantiated members — neither of which the existing
``type_graph``/``call_graph`` passes capture, since they only see the
template *pattern*'s own declaration.

Architecture mirrors ``type_graph.py``/``call_graph.py`` deliberately:

- :func:`parse_clang_ast_templates` is a **pure function** over a
  ``clang -Xclang -ast-dump=json`` tree — unit-tested without a compiler.
- :class:`ClangTemplateGraphExtractor` is the thin, side-effecting wrapper
  that shells out to ``clang`` for a translation unit. Only exercised on the
  ``integration`` lane; a missing compiler degrades gracefully.
- :func:`augment_graph_with_templates` folds the resulting facts into a
  :class:`~abicheck.buildsource.source_graph.SourceGraphSummary`.

This is a **third**, independent AST pass over the same TU (alongside the
call and type graph passes) — the same tradeoff ``type_graph.py``'s own
module docstring already accepts for itself: a real compiler-facts source
(one build-integrated frontend pass emitting every family at once) is future
work, not attempted here.

**Evidence used, verified empirically against real ``clang`` AST dumps**
(this module's own review notes — no vocabulary here was guessed):

- A class template's declaration (``ClassTemplateDecl``) nests its own
  pattern record and every concrete specialization as direct children — but
  an *explicit* instantiation (``template struct Wrapper<int>;``) produces a
  **second**, detached copy of the same specialization (identical clang
  ``id``, full ``completeDefinition`` content) as a *sibling* of the
  ``ClassTemplateDecl``, not nested under it. Resolving "which template does
  this instantiation belong to" therefore needs two passes joined by clang's
  own node ``id``: one recording ``id -> owning ClassTemplateDecl`` from the
  structural nesting (present even for the empty, detached-content stub),
  the other recording ``id -> full instantiation content`` from wherever a
  ``completeDefinition: true`` occurrence of that ``id`` actually appears.
- A function template's instantiations (``FunctionTemplateDecl``'s
  ``FunctionDecl`` children) do **not** exhibit this split — every
  occurrence observed, explicit or implicit, keeps its full content nested
  under the owning ``FunctionTemplateDecl``. The pattern itself is the one
  child with no ``mangledName``; every other child is a genuine
  instantiation.
- A type template argument's clang ``TemplateArgument`` node carries a
  ``type.qualType`` spelling *and*, when the argument names a record/enum
  (including through a ``using``/typedef alias — clang's own printer already
  resolves the alias to the real declaration), an ``inner`` child
  (``RecordType``/``EnumType``) whose own ``decl`` field names the target's
  clang ``id`` directly — exact identification, not the textual
  unqualified-name heuristic ``type_graph.py`` needs for a plain field/param
  type. A non-type (literal) argument carries a bare ``value`` instead, with
  no ``decl`` to resolve — :data:`TemplateArgUse.target_qname` is ``None``
  for these, and they contribute no ``TEMPLATE_USES_TYPE`` edge.
- A class specialization's own instantiated member functions
  (``CXXMethodDecl``/``CXXConstructorDecl``/``CXXDestructorDecl`` children)
  each carry their own ``mangledName`` directly — the class instantiation
  node's :data:`TemplateInstantiation.emitted_symbols` is these, not a
  separate per-member node.

**Deliberately not attempted this slice** (see ``docs/contribute/plans/
g29-impact-analysis-layer.md`` Phase 5 item 1 and ADR-041's registry for the
full, honest list of what's reserved but unpopulated):

- ``TEMPLATE_USES_DECL`` — a non-type template argument (e.g. a function
  pointer) that names a declaration rather than a literal value. Registered
  vocabulary, no producer yet; needs its own empirical AST verification.
- ``INSTANTIATION_MAPS_TO_EXPORT`` — redundant with reading
  ``BINARY_EXPORTS_SYMBOL`` off the same ``binary_symbol`` node
  :data:`EDGE_INSTANTIATION_EMITS_SYMBOL` already joins onto, the identical
  "one shared node id is the whole join mechanism" reasoning ADR-057 D1
  gives for not adding a second, entangled edge for the same fact.
- ``DECL_USES_DEFAULT_TEMPLATE_ARG`` — needs to distinguish an explicitly
  spelled template argument from one clang filled in from a default; not
  verified this slice.
- ``CONSTRAINT_DEPENDS_ON_DECL`` — C++20 concepts/``requires`` clauses are a
  separate AST subsystem (``ConceptSpecializationExpr``, ``RequiresExpr``)
  needing its own empirical pass, not a drive-by extension of this one.
- Variable templates (``template <typename T> constexpr T pi = ...;``) and a
  member function template nested inside a class template instantiation —
  neither decl shape is walked; both fall through as an ordinary,
  unrecognized child (no crash, no data, the same silent-skip default this
  whole module uses throughout).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from .. import deadline
from .clang_ast_run import run_clang_ast_dump
from .graph_facts import CONF_HIGH, CONF_REDUCED, GraphEdge, GraphNode

if TYPE_CHECKING:
    from .build_evidence import BuildEvidence, CompileUnit as BuildEvidenceCompileUnit
    from .source_graph import SourceGraphSummary

EDGE_DECL_INSTANTIATES_TEMPLATE = "DECL_INSTANTIATES_TEMPLATE"
EDGE_TEMPLATE_USES_TYPE = "TEMPLATE_USES_TYPE"
EDGE_INSTANTIATION_EMITS_SYMBOL = "INSTANTIATION_EMITS_SYMBOL"

NODE_TEMPLATE_DECL = "template_decl"
NODE_TEMPLATE_INSTANTIATION = "template_instantiation"

#: ``provenance`` tag on every node/edge this module creates.
TEMPLATE_GRAPH_PROVENANCE = "template_graph"

_CLASS_TEMPLATE_KIND = "ClassTemplateDecl"
_CLASS_SPECIALIZATION_KIND = "ClassTemplateSpecializationDecl"
_FUNCTION_TEMPLATE_KIND = "FunctionTemplateDecl"
_RECORD_KIND = "record"
_FUNCTION_KIND = "function"

#: Member-function decl kinds whose ``mangledName`` a class instantiation
#: "emits" (Codex-review-shaped precedent: mirrors ``type_graph._FUNCTION_DECL_KINDS``
#: minus ``CXXConversionDecl`` — a conversion operator is rare as an
#: instantiated-member emission source and not verified against a real AST
#: dump this slice; omitted rather than guessed).
_MEMBER_FUNCTION_KINDS = frozenset(
    {"CXXMethodDecl", "CXXConstructorDecl", "CXXDestructorDecl"}
)

#: Decl kinds that open a named scope contributing to a qualified name —
#: mirrors ``type_graph._SCOPE_DECL_KINDS`` (kept as an independent copy,
#: same reasoning ``call_graph._normalize_mangled``/``type_graph.
#: _normalize_mangled`` give for their own duplicated one-liners: this
#: module and ``type_graph.py`` are two independent AST passes with no
#: import dependency between them).
_SCOPE_DECL_KINDS = frozenset({"NamespaceDecl", "CXXRecordDecl", "RecordDecl"})

#: Decl kinds indexed for id -> qualified-name resolution (a template
#: argument's ``decl`` reference). Mirrors ``type_graph._RECORD_DECL_KINDS``/
#: ``_OTHER_TYPE_DECL_KINDS`` (independent copy, same reasoning as above).
_RECORD_DECL_KINDS = frozenset(
    {"CXXRecordDecl", "RecordDecl", _CLASS_SPECIALIZATION_KIND}
)
_OTHER_TYPE_DECL_KINDS = frozenset({"EnumDecl", "TypedefDecl", "TypeAliasDecl"})
_TYPE_NODE_KIND_BY_DECL: dict[str, str] = {
    "EnumDecl": "enum_type",
    "TypedefDecl": "typedef",
    "TypeAliasDecl": "typedef",
}


def _type_node_kind(decl_kind: str) -> str:
    """clang decl kind -> graph type-node kind. Mirrors
    ``source_graph._type_node_kind``'s ``record_type``-default convention,
    but keyed by the raw AST decl kind this module already has on hand
    rather than a ``SourceEntity.kind`` string."""
    return _TYPE_NODE_KIND_BY_DECL.get(decl_kind, "record_type")


@dataclass(frozen=True)
class TemplateArgUse:
    """One template argument, as spelled at the instantiation site.

    ``target_qname`` is the argument's own qualified name when it names a
    record/enum declaration resolvable within this TU's AST (clang's own
    ``decl.id`` cross-reference — see the module docstring) — ``None`` for a
    non-type (literal) argument, a builtin/fundamental type, or a type
    declared outside this TU's AST (never fabricated from the spelling
    alone; this module's "degrade to no answer" discipline throughout).
    """

    spelling: str
    target_qname: str | None = None


@dataclass(frozen=True)
class TemplateInstantiation:
    """One concrete instantiation of a class or function template.

    ``label`` is a human-readable spelling (``"Wrapper<internal::Detail>"``)
    used only for the node's display label — never parsed back apart, since
    clang's own arg list (:attr:`args`) is the structured form. ``file`` is
    the instantiation's own declaring file, when known (best-effort, from
    the first ``loc.file`` clang emits for it).
    """

    kind: str  # _RECORD_KIND or _FUNCTION_KIND
    template_qname: str
    label: str
    args: tuple[TemplateArgUse, ...] = ()
    emitted_symbols: tuple[str, ...] = ()
    file: str = ""


def _node_file(node: dict[str, Any]) -> str:
    """The source file a node names, if any (clang emits ``file`` only when
    it *changes* — sticky — so the caller tracks the last-seen value;
    mirrors ``call_graph._node_file``/``type_graph._node_file``)."""
    loc = node.get("loc")
    if isinstance(loc, dict):
        f = loc.get("file")
        if isinstance(f, str) and f:
            return f
    return ""


def _index_type_decls(node: Any, scope: list[str], id_to_qname: dict[str, str]) -> None:
    """Populate ``id_to_qname``: every record/enum/typedef declaration's
    clang node ``id`` -> its scope-qualified name, anywhere in *node*'s
    subtree.

    A small, independent AST walk rather than a reuse/extension of
    ``type_graph._index_declared_entities`` (Codex-review-shaped
    precedent: that function's own docstring already accepts "duplicates
    the one AST walk rather than threading an output parameter through the
    hardened, heavily-reviewed" pair, for ``index_declared_type_files``'s
    identical situation) — that function does not build an id-keyed index
    for type declarations at all (only for var/enum-constant references),
    so there is nothing to reuse here, only a risk of destabilizing an
    already-hardened function for an unrelated caller's need.
    """
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")
    node_id = str(node.get("id") or "")

    if kind in _RECORD_DECL_KINDS and name:
        if node_id:
            id_to_qname.setdefault(node_id, "::".join([*scope, name]))
        child_scope = [*scope, name]
        for child in node.get("inner", []) or []:
            _index_type_decls(child, child_scope, id_to_qname)
        return

    if kind in _OTHER_TYPE_DECL_KINDS and name:
        if node_id:
            id_to_qname.setdefault(node_id, "::".join([*scope, name]))
        # No new scope: an enum's own enumerators aren't qualified by its
        # name in clang's spelling (mirrors type_graph's identical choice).
        for child in node.get("inner", []) or []:
            _index_type_decls(child, scope, id_to_qname)
        return

    if kind in _SCOPE_DECL_KINDS and name:
        child_scope = [*scope, name]
        for child in node.get("inner", []) or []:
            _index_type_decls(child, child_scope, id_to_qname)
        return

    for child in node.get("inner", []) or []:
        _index_type_decls(child, scope, id_to_qname)


def _register_class_template(
    node: dict[str, Any], template_qname: str, id_to_template_qname: dict[str, str]
) -> None:
    """Record ``spec_id -> template_qname`` for every specialization *node*
    (a ``ClassTemplateDecl``) directly nests — the structural half of the
    two-pass join (see the module docstring)."""
    for child in node.get("inner", []) or []:
        if str(child.get("kind", "")) == _CLASS_SPECIALIZATION_KIND:
            spec_id = str(child.get("id") or "")
            if spec_id:
                id_to_template_qname[spec_id] = template_qname


def _collect_full_specializations(
    node: Any, full_by_id: dict[str, dict[str, Any]]
) -> None:
    """Record ``spec_id -> node`` for every ``ClassTemplateSpecializationDecl``
    anywhere in the tree that carries ``completeDefinition: true`` — the
    content half of the two-pass join. An empty stub (no
    ``completeDefinition``) is never recorded, so an id whose only
    occurrences are stubs correctly resolves to "no real instantiation"."""
    if not isinstance(node, dict):
        return
    if str(node.get("kind", "")) == _CLASS_SPECIALIZATION_KIND and node.get(
        "completeDefinition"
    ):
        spec_id = str(node.get("id") or "")
        if spec_id:
            full_by_id[spec_id] = node
    for child in node.get("inner", []) or []:
        _collect_full_specializations(child, full_by_id)


def _template_arg_use(arg_node: dict[str, Any]) -> TemplateArgUse | None:
    """Parse one ``TemplateArgument`` child into a :class:`TemplateArgUse`,
    or ``None`` if it isn't a spellable argument at all (a pack expansion's
    own wrapper, an expression argument with no ``type``/``value`` — clang
    emits several ``TemplateArgument`` shapes this module doesn't model;
    skipped rather than guessed)."""
    qual_type = arg_node.get("type")
    if isinstance(qual_type, dict):
        spelling = str(qual_type.get("qualType") or "")
    elif "value" in arg_node:
        # A non-type (literal) argument, e.g. an int NTTP -- has no
        # resolvable target decl (see the module docstring).
        return TemplateArgUse(spelling=str(arg_node.get("value")))
    else:
        return None
    if not spelling:
        return None
    target: str | None = None
    for child in arg_node.get("inner", []) or []:
        decl = child.get("decl")
        if isinstance(decl, dict):
            # The decl reference is a *stub* (id/kind/name only) -- resolved
            # against id_to_qname by the caller, which has the whole-TU
            # index this function doesn't.
            target = str(decl.get("id") or "") or None
            break
    return TemplateArgUse(spelling=spelling, target_qname=target)


def _resolve_arg_targets(
    args: list[TemplateArgUse], id_to_qname: dict[str, str]
) -> tuple[TemplateArgUse, ...]:
    """Replace each :attr:`TemplateArgUse.target_qname` clang-id placeholder
    (see :func:`_template_arg_use`) with the resolved qualified name, or
    ``None`` when the id names no declaration this TU's AST carries."""
    resolved = []
    for a in args:
        if a.target_qname is None:
            resolved.append(a)
            continue
        qname = id_to_qname.get(a.target_qname)
        resolved.append(TemplateArgUse(spelling=a.spelling, target_qname=qname))
    return tuple(resolved)


def _instantiation_label(template_qname: str, args: Iterable[TemplateArgUse]) -> str:
    spellings = ", ".join(a.spelling for a in args)
    return f"{template_qname}<{spellings}>" if spellings else template_qname


def _member_symbols(node: dict[str, Any]) -> tuple[str, ...]:
    symbols: list[str] = []
    for child in node.get("inner", []) or []:
        if str(child.get("kind", "")) in _MEMBER_FUNCTION_KINDS:
            mangled = child.get("mangledName")
            if isinstance(mangled, str) and mangled:
                symbols.append(mangled)
    return tuple(symbols)


def _walk_function_templates(
    node: Any,
    scope: list[str],
    id_to_qname: dict[str, str],
    out: list[TemplateInstantiation],
) -> None:
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))

    if kind == _FUNCTION_TEMPLATE_KIND:
        name = str(node.get("name") or "")
        qname = "::".join([*scope, name]) if name else ""
        if qname:
            for child in node.get("inner", []) or []:
                if str(child.get("kind", "")) not in (
                    "FunctionDecl",
                    "CXXMethodDecl",
                    "CXXConstructorDecl",
                    "CXXDestructorDecl",
                ):
                    continue
                mangled = child.get("mangledName")
                if not (isinstance(mangled, str) and mangled):
                    continue  # the pattern itself, or an unmangled instantiation
                args: list[TemplateArgUse] = []
                for grandchild in child.get("inner", []) or []:
                    if str(grandchild.get("kind", "")) == "TemplateArgument":
                        use = _template_arg_use(grandchild)
                        if use is not None:
                            args.append(use)
                resolved_args = _resolve_arg_targets(args, id_to_qname)
                out.append(
                    TemplateInstantiation(
                        kind=_FUNCTION_KIND,
                        template_qname=qname,
                        label=_instantiation_label(qname, resolved_args),
                        args=resolved_args,
                        emitted_symbols=(mangled,),
                        file=_node_file(child),
                    )
                )
        # Don't recurse into a FunctionTemplateDecl's own children again --
        # already fully handled above.
        return

    if kind in _SCOPE_DECL_KINDS:
        name = str(node.get("name") or "")
        child_scope = [*scope, name] if name else scope
        for child in node.get("inner", []) or []:
            _walk_function_templates(child, child_scope, id_to_qname, out)
        return

    for child in node.get("inner", []) or []:
        _walk_function_templates(child, scope, id_to_qname, out)


def _walk_class_templates(
    node: Any,
    scope: list[str],
    id_to_qname: dict[str, str],
    id_to_template_qname: dict[str, str],
    out: list[TemplateInstantiation],
) -> None:
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))

    if kind == _CLASS_TEMPLATE_KIND:
        name = str(node.get("name") or "")
        qname = "::".join([*scope, name]) if name else ""
        if qname:
            _register_class_template(node, qname, id_to_template_qname)
        # A ClassTemplateDecl's own pattern CXXRecordDecl child would open a
        # scope for its own members if recursed into generically -- skip it
        # explicitly and only recurse into further nested templates (a
        # template nested inside a template pattern), matching the "member
        # function template nested in a class template instantiation" gap
        # this module's docstring already names as unhandled either way.
        return

    if kind in _SCOPE_DECL_KINDS:
        name = str(node.get("name") or "")
        child_scope = [*scope, name] if name else scope
        for child in node.get("inner", []) or []:
            _walk_class_templates(
                child, child_scope, id_to_qname, id_to_template_qname, out
            )
        return

    for child in node.get("inner", []) or []:
        _walk_class_templates(child, scope, id_to_qname, id_to_template_qname, out)


def parse_clang_ast_templates(ast: dict[str, Any]) -> list[TemplateInstantiation]:
    """Extract template instantiations from a ``clang -ast-dump=json`` tree
    (pure).

    Three passes over the AST — matching :func:`type_graph.parse_clang_ast_types`'s
    own "index first, resolve second" shape:

    1. :func:`_index_type_decls` — every record/enum/typedef's own clang id
       -> qualified name, so a template argument's ``decl`` reference (a bare
       id/kind/name stub) can be resolved to its real, scope-qualified
       identity.
    2. :func:`_collect_full_specializations` — every
       ``ClassTemplateSpecializationDecl`` id that carries real content
       (``completeDefinition: true``), wherever in the tree it physically
       appears (see the module docstring for why this can't be assumed to be
       nested under its own ``ClassTemplateDecl``).
    3. :func:`_walk_class_templates`/:func:`_walk_function_templates` — walk
       the tree again to find every ``ClassTemplateDecl``/
       ``FunctionTemplateDecl``, join each instantiation onto its resolved
       template/argument identities, and emit a :class:`TemplateInstantiation`
       per genuine instantiation.
    """
    id_to_qname: dict[str, str] = {}
    _index_type_decls(ast, [], id_to_qname)

    full_by_id: dict[str, dict[str, Any]] = {}
    _collect_full_specializations(ast, full_by_id)
    id_to_template_qname: dict[str, str] = {}
    _walk_class_templates(ast, [], id_to_qname, id_to_template_qname, out=[])
    # _walk_class_templates above only *registers* class-template membership
    # (id_to_template_qname); the actual instantiation objects are built
    # here, once, over the join of both indices -- doing it inline in the
    # walk would double-emit an id seen more than once (e.g. the detached
    # explicit-instantiation copy).
    out: list[TemplateInstantiation] = []
    for spec_id, template_qname in id_to_template_qname.items():
        full = full_by_id.get(spec_id)
        if full is None:
            continue  # a stub with no corresponding full definition anywhere
        args: list[TemplateArgUse] = []
        for child in full.get("inner", []) or []:
            if str(child.get("kind", "")) == "TemplateArgument":
                use = _template_arg_use(child)
                if use is not None:
                    args.append(use)
        resolved_args = _resolve_arg_targets(args, id_to_qname)
        out.append(
            TemplateInstantiation(
                kind=_RECORD_KIND,
                template_qname=template_qname,
                label=_instantiation_label(template_qname, resolved_args),
                args=resolved_args,
                emitted_symbols=_member_symbols(full),
                file=_node_file(full),
            )
        )

    _walk_function_templates(ast, [], id_to_qname, out)
    return out


# ── graph augmentation ───────────────────────────────────────────────────────


def template_decl_node_id(qname: str) -> str:
    return f"template_decl://{qname}"


def template_instantiation_node_id(label: str) -> str:
    """Node id for one concrete instantiation, keyed by its own human label
    (``"Wrapper<internal::Detail>"``) — unlike a function instantiation
    (which could be keyed by its unique mangled name), a class
    instantiation has no single symbol of its own, so the label is the only
    identity both kinds share."""
    return f"template_instantiation://{label}"


def _symbol_node_ids(graph: SourceGraphSummary) -> frozenset[str]:
    return frozenset(n.id for n in graph.nodes if n.kind == "binary_symbol")


def augment_graph_with_templates(
    graph: SourceGraphSummary,
    instantiations: list[TemplateInstantiation],
    project_files: frozenset[str] | None = None,
) -> int:
    """Fold *instantiations* into *graph* (G29 Phase 5 item 1).

    Mints a ``template_decl`` node per distinct :attr:`TemplateInstantiation.
    template_qname` and a ``template_instantiation`` node per instantiation,
    joined by :data:`EDGE_DECL_INSTANTIATES_TEMPLATE`. A resolved argument
    (:attr:`TemplateArgUse.target_qname` set) gets a
    :data:`EDGE_TEMPLATE_USES_TYPE` edge onto the same ``record_type``/
    ``enum_type``/``typedef`` node id :mod:`type_graph`'s own AST-only edges
    would use for the identical qualified name (``type://<qname>``) — the
    shared-node-id join, same principle as every other producer in this
    package.

    **An :data:`EDGE_INSTANTIATION_EMITS_SYMBOL` edge is only emitted for a
    mangled name the graph already carries a ``binary_symbol://`` node
    for** — the identical ADR-057 D1 "one shared node id is the whole join
    mechanism" rule :mod:`archive_graph` already applies: an instantiated
    member the linker discarded (never ODR-used, or inlined away) has no
    export-table entry and no finding can ever be about it, so minting a
    symbol node for it would inflate the graph for no analytical gain.

    Returns the number of edges added.
    """
    from .call_graph import _file_in_project
    from .source_graph import _symbol_node_id, _type_node_id

    node_by_id: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
    known_symbols = _symbol_node_ids(graph)
    added = 0

    def ensure_node(
        node_id: str, kind: str, label: str, attrs: dict[str, Any] | None = None
    ) -> None:
        if node_id in node_by_id:
            return
        node = GraphNode(
            id=node_id,
            kind=kind,
            label=label,
            provenance=TEMPLATE_GRAPH_PROVENANCE,
            confidence=CONF_HIGH,
            attrs=dict(attrs or {}),
        )
        graph.add_node(node)
        node_by_id[node_id] = node

    def add_edge(src: str, dst: str, kind: str, confidence: str) -> None:
        nonlocal added
        before = len(graph.edges)
        graph.add_edge(
            GraphEdge(
                src=src,
                dst=dst,
                kind=kind,
                provenance=TEMPLATE_GRAPH_PROVENANCE,
                confidence=confidence,
            )
        )
        added += len(graph.edges) - before

    for inst in instantiations:
        template_id = template_decl_node_id(inst.template_qname)
        ensure_node(template_id, NODE_TEMPLATE_DECL, inst.template_qname)

        inst_id = template_instantiation_node_id(inst.label)
        dst_in_project = bool(
            project_files and inst.file and _file_in_project(inst.file, project_files)
        )
        ensure_node(
            inst_id,
            NODE_TEMPLATE_INSTANTIATION,
            inst.label,
            attrs=(
                {"defined_in_project": True, "def_file": inst.file}
                if dst_in_project
                else {}
            ),
        )
        add_edge(inst_id, template_id, EDGE_DECL_INSTANTIATES_TEMPLATE, CONF_HIGH)

        for arg in inst.args:
            if not arg.target_qname:
                continue
            # The arg's own decl kind isn't threaded through TemplateArgUse
            # (only its qualified name is) -- default to record_type, the
            # same "can't distinguish without more context" fallback
            # type_graph.py's own AST-only edges already use for this exact
            # situation (see augment_graph_with_types's docstring).
            type_id = _type_node_id(arg.target_qname)
            ensure_node(type_id, "record_type", arg.target_qname)
            add_edge(inst_id, type_id, EDGE_TEMPLATE_USES_TYPE, CONF_HIGH)

        for symbol in inst.emitted_symbols:
            sid = _symbol_node_id(symbol)
            if sid not in known_symbols:
                continue
            add_edge(inst_id, sid, EDGE_INSTANTIATION_EMITS_SYMBOL, CONF_REDUCED)

    return added


@dataclass
class ClangTemplateGraphExtractor:
    """Shell out to ``clang`` to emit a TU's AST and parse its template
    instantiations.

    Side-effecting and compiler-dependent: only exercised on the
    ``integration`` lane. A missing ``clang`` (or a parse failure) degrades
    gracefully — extraction returns ``[]`` and records nothing (ADR-028 D3).
    Reuses ``call_graph``'s vetted parse-only argv builder (same ABI-relevant
    flag allowlist) so all three AST passes stay in lockstep on what is safe
    to replay.
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)
    last_jobs: int = 0
    last_elapsed_s: float = 0.0

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def _extract_from_safe_args(
        self, argv: list[str], cwd: str | None = None
    ) -> list[TemplateInstantiation]:
        if not self.available():
            self.diagnostics.append(f"{self.clang_bin} not found in PATH")
            return []
        ast = run_clang_ast_dump(
            self.clang_bin, argv, cwd=cwd, diagnostics=self.diagnostics
        )
        if ast is None:
            return []
        try:
            return parse_clang_ast_templates(ast)
        except (ValueError, RecursionError) as exc:
            self.diagnostics.append(f"could not parse clang AST JSON: {exc}")
            return []

    def _extract_from_compile_unit(
        self, cu: BuildEvidenceCompileUnit
    ) -> list[TemplateInstantiation]:
        from .call_graph import _safe_clang_args_from_compile_unit

        argv = _safe_clang_args_from_compile_unit(cu)
        return self._extract_from_safe_args(argv, cwd=cu.directory or None)

    def extract_from_build(self, build: BuildEvidence) -> list[TemplateInstantiation]:
        """Extract template instantiations across every compile unit in
        *build* (best effort)."""
        from .call_graph import _call_graph_jobs, _deadline_bound_worker

        start = time.monotonic()
        units = [cu for cu in build.compile_units if cu.source]
        self.last_jobs = _call_graph_jobs(len(units))
        if not units:
            self.last_elapsed_s = 0.0
            return []
        if not self.available():
            self.diagnostics.append(f"{self.clang_bin} not found in PATH")
            self.last_elapsed_s = time.monotonic() - start
            return []

        all_instantiations: list[TemplateInstantiation] = []
        # Dedup by (kind, template_qname, label) -- two TUs instantiating the
        # identical template with the identical arguments (a shared public
        # header) must not double the graph's edge count.
        seen: set[tuple[str, str, str]] = set()

        def add(instantiations: Iterable[TemplateInstantiation]) -> None:
            for inst in instantiations:
                key = (inst.kind, inst.template_qname, inst.label)
                if key in seen:
                    continue
                seen.add(key)
                all_instantiations.append(inst)

        try:
            if self.last_jobs > 1 and len(units) > 1:
                pool_worker = partial(
                    _deadline_bound_worker,
                    deadline.current_deadline_ts(),
                    self._extract_from_compile_unit,
                )
                with ThreadPoolExecutor(max_workers=self.last_jobs) as pool:
                    for instantiations in pool.map(pool_worker, units):
                        add(instantiations)
            else:
                for cu in units:
                    add(self._extract_from_compile_unit(cu))
        finally:
            self.last_elapsed_s = time.monotonic() - start

        return all_instantiations
