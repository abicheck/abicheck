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

**Investigated and deliberately not attempted: joining a class
instantiation's own node onto ``type_graph.py``'s record_type node for the
identical qualified name.** A review round asked why
``source_graph.DEPENDENCY_EDGE_KINDS``/``DECL_NODE_KINDS`` aren't extended
with :data:`EDGE_TEMPLATE_USES_TYPE`/``template_instantiation`` — the
naive-looking fix is giving :func:`template_instantiation_node_id` the same
id space as ``type_graph.py``'s own ``_type_node_id(qname)`` for a class
instantiation, so a public decl's existing ``DECL_HAS_TYPE`` edge onto
``Wrapper<internal::Detail>`` would already reach the instantiation node
this module also populates. Empirically disproven rather than skipped on
suspicion: dumping a real ``Wrapper<internal::Detail> make()`` through both
this module and ``type_graph.parse_clang_ast_types`` shows clang's printer
spells the identical type **differently depending on where it's printed** —
``"Wrapper<internal::Detail>"`` for ``make()``'s own return type (printed
relative to the enclosing ``api::`` namespace, so the redundant qualifier is
dropped), but bare ``"Wrapper<Detail>"`` for a constructor parameter printed
from *inside* ``Wrapper``'s own scope (both ``api::`` and ``internal::``
elided) — while this module's own :func:`_instantiation_label` always
produces the fully-qualified ``"api::Wrapper<internal::Detail>"`` from the
declaration's own scope chain, a third, different spelling. There is no
single "the qname" to share a node id with; ``type_graph.py``'s own
``_resolve_type_name`` exists specifically to reverse this print-context
elision for its *own* edges via scope-relative lookup, and reusing that
same resolution for a template *label* (as opposed to a plain field/param
type spelling) is a genuinely new, unverified problem, not a one-line
join. Left as a known, real gap: a class instantiation's own node has no
inbound edge from a public declaration today, so :data:`EDGE_TEMPLATE_USES_TYPE`
is not (yet) reachable from ``crosscheck.py``'s ``public_to_internal_dependency``
or ``poi.py``'s reachability walk — the *argument* nodes it points at
(``_type_node_id(arg.target_qname)``, keyed off the argument's own
declaration-scope qualified name from ``id_to_qname``, not a print-context
spelling) do correctly join onto whatever node another pass already created
for that identity.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
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


def _normalize_mangled(mangled: str) -> str:
    """Strip a spurious macOS Mach-O ABI leading underscore from an Itanium
    mangled name clang reports (``__ZN...`` -> ``_ZN...``).

    Independent duplicate of ``call_graph._normalize_mangled``/
    ``type_graph._normalize_mangled`` (same bug, same fix, kept undependent
    — see this module's own ``_SCOPE_DECL_KINDS`` docstring for why: two
    independent AST passes, no import between them). On Darwin, clang's AST
    dump reports a ``mangledName`` with the platform's extra linker-symbol-
    table underscore still attached, but the ``binary_symbol`` nodes this
    module's :data:`EDGE_INSTANTIATION_EMITS_SYMBOL` join must match against
    carry the already-stripped, one-underscore form (``macho_metadata.py``
    strips it before a symbol becomes a graph node) — left unstripped, every
    instantiated member's emitted symbol silently fails to join on Mach-O.
    """
    if mangled.startswith("__Z"):
        return mangled[1:]
    return mangled


@dataclass(frozen=True)
class TemplateArgUse:
    """One template argument, as spelled at the instantiation site.

    ``target_qname`` is the argument's own qualified name when it names a
    record/enum declaration resolvable within this TU's AST (clang's own
    ``decl.id`` cross-reference — see the module docstring) — ``None`` for a
    non-type (literal) argument, a builtin/fundamental type, or a type
    declared outside this TU's AST (never fabricated from the spelling
    alone; this module's "degrade to no answer" discipline throughout).

    ``target_decl_kind`` is the target's own raw clang decl kind (e.g.
    ``"EnumDecl"``, ``"CXXRecordDecl"``) when ``target_qname`` resolved —
    ``None`` otherwise. Feeds :func:`_type_node_kind` so a resolved enum/
    typedef argument mints the correct graph node kind instead of every
    resolved argument defaulting to ``record_type``.
    """

    spelling: str
    target_qname: str | None = None
    target_decl_kind: str | None = None


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


def _specialization_scope_name(node: dict[str, Any], name: str) -> str:
    """A ``ClassTemplateSpecializationDecl`` node's own scope-disambiguating
    name: its bare *name* parameterized by its direct ``TemplateArgument``
    children's raw spellings (``"Wrapper<int>"``), or *name* unchanged for
    any other decl kind, or when no parseable ``TemplateArgument`` children
    exist (a pack-expansion-only specialization, or any other unmodeled
    shape — degrade to the ambiguous-but-harmless bare name, this module's
    usual "skip rather than guess" discipline)."""
    if str(node.get("kind", "")) != _CLASS_SPECIALIZATION_KIND:
        return name
    args = _flatten_template_args(node.get("inner", []) or [])
    if not args:
        return name
    return f"{name}<{', '.join(use.spelling for use in args)}>"


def _index_type_decls(
    node: Any,
    scope: list[str],
    id_to_qname: dict[str, str],
    id_to_decl_kind: dict[str, str],
    id_to_file: dict[str, str],
    cur_file: str = "",
) -> str:
    """Populate ``id_to_qname``/``id_to_decl_kind``/``id_to_file``: every
    declaration's clang node ``id`` -> (for a record/enum/typedef) its
    scope-qualified name and own AST decl kind (``"CXXRecordDecl"``/
    ``"EnumDecl"``/…, for :func:`_type_node_kind` — otherwise every resolved
    argument mints a ``record_type`` node regardless of whether it's
    actually an enum or typedef target), and (for *every* node with an id,
    regardless of kind) its declaring file, anywhere in *node*'s subtree.

    A small, independent AST walk rather than a reuse/extension of
    ``type_graph._index_declared_entities`` (Codex-review-shaped
    precedent: that function's own docstring already accepts "duplicates
    the one AST walk rather than threading an output parameter through the
    hardened, heavily-reviewed" pair, for ``index_declared_type_files``'s
    identical situation) — that function does not build an id-keyed index
    for type declarations at all (only for var/enum-constant references),
    so there is nothing to reuse here, only a risk of destabilizing an
    already-hardened function for an unrelated caller's need.

    Returns the last-seen file after visiting *node* and its whole subtree,
    same sticky-file threading contract as ``type_graph._index_declared_
    entities``: clang emits ``loc.file`` only on the very *first* node with
    a location in a TU — verified empirically against real clang output
    (Codex review, fresh evidence): a two-declaration single-file TU records
    ``loc.file`` on the *first* top-level declaration only, and every
    subsequent node -- including every ``ClassTemplateSpecializationDecl``/
    ``FunctionDecl`` this module cares about -- carries none at all. An
    earlier revision called the stateless :func:`_node_file` directly at
    each instantiation site, which is correct only for the file's very
    first declaration and answers "" for every other one, in practice
    leaving ``TemplateInstantiation.file`` unset for nearly every real
    instantiation. Every caller loop below must thread the returned
    ``cur_file`` from one sibling call into the next, not just pass down the
    parent's own value independently to each child, or every sibling after
    the first loses the file.
    """
    if not isinstance(node, dict):
        return cur_file
    f = _node_file(node)
    if f:
        cur_file = f
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")
    node_id = str(node.get("id") or "")
    if node_id:
        id_to_file.setdefault(node_id, cur_file)

    if kind in _RECORD_DECL_KINDS and name:
        if node_id:
            id_to_qname.setdefault(node_id, "::".join([*scope, name]))
            id_to_decl_kind.setdefault(node_id, kind)
        # A ClassTemplateSpecializationDecl's own *nested* declarations (e.g.
        # `Wrapper<int>::Nested`) must scope under the specialization's own
        # arguments, not its bare, unparameterized name -- otherwise two
        # distinct specializations' nested types (`Wrapper<int>::Nested` vs.
        # `Wrapper<double>::Nested`) both index as the identical bare
        # "Wrapper::Nested" and collide onto one type node (Codex review,
        # empirically confirmed against real clang AST output). Builds the
        # disambiguated scope name directly from this node's own
        # TemplateArgument children's raw spellings (mirrors
        # _instantiation_label) -- deliberately not a
        # _resolve_specialization_qname-style resolved qname: that helper
        # needs id_to_qname/full_by_id/id_to_template_qname, none of which
        # exist yet at this point in the "index first, resolve second"
        # pipeline (this call *is* the pass building id_to_qname).
        child_scope = [*scope, _specialization_scope_name(node, name)]
        for child in node.get("inner", []) or []:
            cur_file = _index_type_decls(
                child, child_scope, id_to_qname, id_to_decl_kind, id_to_file, cur_file
            )
        return cur_file

    if kind in _OTHER_TYPE_DECL_KINDS and name:
        if node_id:
            id_to_qname.setdefault(node_id, "::".join([*scope, name]))
            id_to_decl_kind.setdefault(node_id, kind)
        # No new scope: an enum's own enumerators aren't qualified by its
        # name in clang's spelling (mirrors type_graph's identical choice).
        for child in node.get("inner", []) or []:
            cur_file = _index_type_decls(
                child, scope, id_to_qname, id_to_decl_kind, id_to_file, cur_file
            )
        return cur_file

    if kind in _SCOPE_DECL_KINDS and name:
        child_scope = [*scope, name]
        for child in node.get("inner", []) or []:
            cur_file = _index_type_decls(
                child, child_scope, id_to_qname, id_to_decl_kind, id_to_file, cur_file
            )
        return cur_file

    for child in node.get("inner", []) or []:
        cur_file = _index_type_decls(
            child, scope, id_to_qname, id_to_decl_kind, id_to_file, cur_file
        )
    return cur_file


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


def _collect_full_function_defs(
    node: Any, full_by_id: dict[str, dict[str, Any]]
) -> None:
    """Record ``id -> node`` for every function-shaped decl carrying a
    ``mangledName``, anywhere in the tree — the content half of a two-pass
    join an *explicit* function-template specialization needs, mirroring
    :func:`_collect_full_specializations`'s identical join for class
    templates. An explicit specialization (``template<> int foo<int>(int)``)
    produces the same detachment quirk this module's docstring already
    documents for class templates: an unmangled stub nested under the
    ``FunctionTemplateDecl`` (no ``TemplateArgument`` child, no content) and
    a full-content ``FunctionDecl`` sharing that id, detached as a top-level
    sibling — empirically confirmed against real clang AST output (Codex
    review). Without this join, ``_walk_function_templates``'s own
    ``if not mangled: continue`` guard silently skips the stub and the
    specialization's instantiation and emitted symbol are never recorded."""
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))
    if kind in _MEMBER_FUNCTION_KINDS | {"FunctionDecl"}:
        mangled = node.get("mangledName")
        node_id = str(node.get("id") or "")
        if isinstance(mangled, str) and mangled and node_id:
            full_by_id.setdefault(node_id, node)
    for child in node.get("inner", []) or []:
        _collect_full_function_defs(child, full_by_id)


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
    return TemplateArgUse(spelling=spelling, target_qname=_first_decl_id(arg_node))


def _is_opaque_template_argument(node: dict[str, Any]) -> bool:
    """Whether a ``TemplateArgument`` node carries none of the fields this
    module can ever spell anything from — no ``type``, no ``value``, not a
    pack wrapper (``isPack``). A **template-template argument** (a template
    passed as a template argument, e.g. ``Use<A>``/``Use<B>`` for
    ``template <template <typename> class C> struct Use;``) produces exactly
    this shape — empirically confirmed against real clang AST output (Codex
    review): the ``TemplateArgument`` node is entirely bare (``{"kind":
    "TemplateArgument"}``, no ``id``, no ``inner``, nothing distinguishing
    ``A`` from ``B``) with clang's own ``-ast-dump=json`` serializing zero
    identifying information for it at all."""
    return not node.get("isPack") and node.get("type") is None and "value" not in node


def _flatten_template_args(
    children: list[dict[str, Any]],
) -> list[TemplateArgUse] | None:
    """Collect every :class:`TemplateArgUse` from a decl's direct
    ``TemplateArgument`` children, flattening a parameter pack.

    A variadic template's pack argument is *itself* one ``TemplateArgument``
    node (``isPack: true``, no ``type``/``value`` of its own) whose real
    per-element arguments are nested one level deeper in its own ``inner`` —
    empirically confirmed against real clang AST output (Codex review):
    ``Pack<int>`` and ``Pack<double>`` both produce a pack-wrapper
    ``TemplateArgument`` with no ``type``/``value``, and the actual
    ``int``/``double`` argument nested inside it. :func:`_template_arg_use`
    alone treats that wrapper as an unspellable node (its own documented
    "a pack expansion's own wrapper" skip case) and drops it entirely, so a
    caller that only ever called it on each *direct* child lost the whole
    pack — both instantiations reduced to the identical, argument-less label
    ``"Pack"`` and collided onto one graph node. Recurses (rather than a
    single flattening pass) in case a pack itself nests another pack node,
    though not empirically observed.

    Returns ``None`` — instead of the args collected so far — when any
    argument is :func:`_is_opaque_template_argument` (a template-template
    argument is the confirmed real case, see that function's docstring):
    silently dropping just that one argument would still let the caller
    build a `TemplateInstantiation` for this decl, but with a label/args
    that omit it entirely — so two genuinely distinct instantiations
    differing *only* in that one opaque argument (``Use<A>`` vs. ``Use<B>``)
    would collide onto one shared graph-node identity, merging their real,
    distinct emitted-symbol/type-dependency edges (Codex review, empirically
    confirmed: both instantiations reduce to the identical label ``"Use"``).
    The caller must treat ``None`` as "skip this instantiation entirely",
    never as "empty argument list" — the same "degrade rather than fabricate
    an identity" discipline this whole module already applies elsewhere.
    """
    out: list[TemplateArgUse] = []
    for child in children:
        if str(child.get("kind", "")) != "TemplateArgument":
            continue
        if child.get("isPack") and child.get("type") is None and "value" not in child:
            nested = _flatten_template_args(child.get("inner", []) or [])
            if nested is None:
                return None
            out.extend(nested)
            continue
        if _is_opaque_template_argument(child):
            return None
        use = _template_arg_use(child)
        if use is not None:
            out.append(use)
    return out


def _first_decl_id(node: dict[str, Any]) -> str | None:
    """Depth-first, first-found ``decl.id`` anywhere under *node*'s own
    ``inner`` subtree.

    A pointer/reference/array/cv-qualified template argument nests its
    ``RecordType``/``EnumType`` (and that type's own ``decl`` cross-
    reference) one or more wrapper levels deep instead of as a direct
    ``TemplateArgument`` child — e.g. ``Box<internal::Detail *>`` produces
    ``TemplateArgument -> PointerType -> RecordType -> decl``, and
    ``Box<const internal::Detail &>`` nests two wrapper levels
    (``LValueReferenceType -> QualType -> RecordType -> decl``) — empirically
    confirmed against real clang AST output (Codex review; an earlier
    revision only checked *direct* children, missing every wrapped
    argument). Pre-order (checks *node* itself before recursing), so the
    outermost decl wins for a nested specialization argument (e.g.
    ``Box<Wrapper<internal::Detail>>`` resolves to ``Wrapper``'s own decl,
    not `internal::Detail`'s, matching the existing unwrapped behavior)."""
    decl = node.get("decl")
    if isinstance(decl, dict):
        # The decl reference is a *stub* (id/kind/name only) -- resolved
        # against id_to_qname by the caller, which has the whole-TU index
        # this function doesn't.
        decl_id = str(decl.get("id") or "") or None
        if decl_id:
            return decl_id
    for child in node.get("inner", []) or []:
        if isinstance(child, dict):
            found = _first_decl_id(child)
            if found:
                return found
    return None


def _resolve_specialization_qname(
    spec_id: str,
    id_to_qname: dict[str, str],
    id_to_decl_kind: dict[str, str],
    id_to_template_qname: dict[str, str],
    full_by_id: dict[str, dict[str, Any]],
    seen: frozenset[str] = frozenset(),
) -> str | None:
    """The qualified name for a resolved decl id, disambiguated by its own
    template arguments when the id names a ``ClassTemplateSpecializationDecl``
    itself.

    ``id_to_qname`` alone gives only the *bare*, unparameterized primary-
    template name for a specialization (the same clang quirk the top-level
    walk already works around — see the module docstring) — so two distinct
    specializations of the same template used as a *nested* template
    argument (``Outer<Wrapper<int>>`` vs. ``Outer<Wrapper<double>>``) would
    otherwise both resolve their argument's ``target_qname`` to the identical
    bare ``"Wrapper"`` and collide onto the same graph node (Codex review,
    empirically confirmed against real clang AST output: both instantiations'
    resolved argument came back as plain ``"api::Wrapper"``, losing the
    parameterization entirely). Recurses through :func:`_instantiation_label`
    using the specialization's *own* template arguments instead, the same
    disambiguation the top-level per-instantiation loop already applies to
    itself. ``seen`` guards a (not known to occur, but unverified) cyclic id
    reference so recursion still terminates; falls back to the bare name once
    a ``spec_id`` repeats.
    """
    if spec_id in seen:
        return id_to_qname.get(spec_id)
    template_qname = id_to_template_qname.get(spec_id)
    full = full_by_id.get(spec_id)
    if template_qname is None or full is None:
        # Not a (known, complete) specialization -- an ordinary record/enum/
        # typedef target, or a specialization stub with no recorded content
        # anywhere in this TU. The bare qname is already exact for these.
        return id_to_qname.get(spec_id)
    args = _flatten_template_args(full.get("inner", []) or [])
    if args is None:
        # An opaque argument (e.g. a template-template argument -- see
        # _is_opaque_template_argument) means this specialization's own
        # disambiguated label can't be trusted; fall back to the bare,
        # unparameterized qname rather than risk merging two genuinely
        # distinct nested specializations onto one identity.
        return id_to_qname.get(spec_id)
    resolved_args = _resolve_arg_targets(
        args,
        id_to_qname,
        id_to_decl_kind,
        id_to_template_qname,
        full_by_id,
        seen | {spec_id},
    )
    return _instantiation_label(template_qname, resolved_args)


def _resolve_arg_targets(
    args: list[TemplateArgUse],
    id_to_qname: dict[str, str],
    id_to_decl_kind: dict[str, str],
    id_to_template_qname: dict[str, str],
    full_by_id: dict[str, dict[str, Any]],
    seen: frozenset[str] = frozenset(),
) -> tuple[TemplateArgUse, ...]:
    """Replace each :attr:`TemplateArgUse.target_qname` clang-id placeholder
    (see :func:`_template_arg_use`) with the resolved qualified name (and
    :attr:`TemplateArgUse.target_decl_kind` with the target's own raw decl
    kind), or ``None``/``None`` when the id names no declaration this TU's
    AST carries."""
    resolved = []
    for a in args:
        if a.target_qname is None:
            resolved.append(a)
            continue
        qname = _resolve_specialization_qname(
            a.target_qname,
            id_to_qname,
            id_to_decl_kind,
            id_to_template_qname,
            full_by_id,
            seen,
        )
        decl_kind = id_to_decl_kind.get(a.target_qname)
        resolved.append(
            TemplateArgUse(
                spelling=a.spelling, target_qname=qname, target_decl_kind=decl_kind
            )
        )
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
                symbols.append(_normalize_mangled(mangled))
    return tuple(symbols)


def _walk_function_templates(
    node: Any,
    scope: list[str],
    id_to_qname: dict[str, str],
    id_to_decl_kind: dict[str, str],
    id_to_template_qname: dict[str, str],
    full_by_id: dict[str, dict[str, Any]],
    full_function_by_id: dict[str, dict[str, Any]],
    id_to_file: dict[str, str],
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
                if str(child.get("kind", "")) not in _MEMBER_FUNCTION_KINDS | {
                    "FunctionDecl"
                }:
                    continue
                mangled = child.get("mangledName")
                if not (isinstance(mangled, str) and mangled):
                    # An unmangled stub -- either the pattern itself, or an
                    # *explicit* specialization whose real, mangled content
                    # clang detached to a top-level sibling sharing this
                    # child's own id (the same quirk _collect_full_
                    # specializations already resolves for class templates).
                    child_id = str(child.get("id") or "")
                    full = full_function_by_id.get(child_id) if child_id else None
                    if full is None:
                        continue  # the pattern itself; no detached content anywhere
                    child = full
                    mangled = child.get("mangledName")
                mangled = _normalize_mangled(mangled)
                args = _flatten_template_args(child.get("inner", []) or [])
                if args is None:
                    # An opaque argument (e.g. a template-template argument
                    # -- see _is_opaque_template_argument) means this
                    # instantiation's own identity can't be trusted; skip
                    # rather than record a wrong, possibly-merged one
                    # (same reasoning as the class-kind loop above).
                    continue
                resolved_args = _resolve_arg_targets(
                    args, id_to_qname, id_to_decl_kind, id_to_template_qname, full_by_id
                )
                out.append(
                    TemplateInstantiation(
                        kind=_FUNCTION_KIND,
                        template_qname=qname,
                        label=_instantiation_label(qname, resolved_args),
                        args=resolved_args,
                        emitted_symbols=(mangled,),
                        file=id_to_file.get(str(child.get("id") or ""), ""),
                    )
                )
        # Don't recurse into a FunctionTemplateDecl's own children again --
        # already fully handled above.
        return

    if kind == _CLASS_SPECIALIZATION_KIND:
        # A class template specialization's own member function templates
        # need the specialization's name added to scope, or two unrelated
        # classes sharing a member-template name in the same enclosing
        # scope collapse onto one qname -- ClassTemplateSpecializationDecl
        # isn't in _SCOPE_DECL_KINDS, so the generic fallback below leaves
        # scope unchanged (Codex review, empirically confirmed against real
        # clang output: api::Holder's and api::Wrapper's own member
        # `apply<int>` both resolved to the identical qname "api::apply",
        # so both instantiations' DECL_INSTANTIATES_TEMPLATE edge pointed at
        # one shared template_decl node as if they instantiated the same
        # template). The bare, unparameterized name is enough here -- this
        # isn't trying to distinguish Holder<int>::apply from
        # Holder<double>::apply (both are genuinely the same syntactic
        # template declaration, correctly sharing one template_decl node,
        # matching how Holder's own class-template pattern is already
        # shared across all its instantiations), only Holder's own member
        # templates from an unrelated Wrapper's.
        name = str(node.get("name") or "")
        child_scope = [*scope, name] if name else scope
        for child in node.get("inner", []) or []:
            _walk_function_templates(
                child,
                child_scope,
                id_to_qname,
                id_to_decl_kind,
                id_to_template_qname,
                full_by_id,
                full_function_by_id,
                id_to_file,
                out,
            )
        return

    if kind in _SCOPE_DECL_KINDS:
        name = str(node.get("name") or "")
        child_scope = [*scope, name] if name else scope
        for child in node.get("inner", []) or []:
            _walk_function_templates(
                child,
                child_scope,
                id_to_qname,
                id_to_decl_kind,
                id_to_template_qname,
                full_by_id,
                full_function_by_id,
                id_to_file,
                out,
            )
        return

    for child in node.get("inner", []) or []:
        _walk_function_templates(
            child,
            scope,
            id_to_qname,
            id_to_decl_kind,
            id_to_template_qname,
            full_by_id,
            full_function_by_id,
            id_to_file,
            out,
        )


def _walk_class_templates(
    node: Any,
    scope: list[str],
    id_to_qname: dict[str, str],
    id_to_template_qname: dict[str, str],
) -> None:
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))

    if kind == _CLASS_TEMPLATE_KIND:
        name = str(node.get("name") or "")
        qname = "::".join([*scope, name]) if name else ""
        if qname:
            _register_class_template(node, qname, id_to_template_qname)
        # Stop here: a ClassTemplateDecl's own pattern CXXRecordDecl child
        # would open a scope for its own members if recursed into
        # generically. A template nested inside a template pattern is
        # therefore not registered either -- the same "member function
        # template nested in a class template instantiation" gap this
        # module's docstring already names as unhandled (Codex review: an
        # earlier revision of this comment claimed recursion into nested
        # templates that the code never actually performs).
        return

    if kind in _SCOPE_DECL_KINDS:
        name = str(node.get("name") or "")
        child_scope = [*scope, name] if name else scope
        for child in node.get("inner", []) or []:
            _walk_class_templates(child, child_scope, id_to_qname, id_to_template_qname)
        return

    for child in node.get("inner", []) or []:
        _walk_class_templates(child, scope, id_to_qname, id_to_template_qname)


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
    id_to_decl_kind: dict[str, str] = {}
    id_to_file: dict[str, str] = {}
    _index_type_decls(ast, [], id_to_qname, id_to_decl_kind, id_to_file)

    full_by_id: dict[str, dict[str, Any]] = {}
    _collect_full_specializations(ast, full_by_id)
    id_to_template_qname: dict[str, str] = {}
    _walk_class_templates(ast, [], id_to_qname, id_to_template_qname)
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
        args = _flatten_template_args(full.get("inner", []) or [])
        if args is None:
            # An opaque argument (e.g. a template-template argument -- see
            # _is_opaque_template_argument) means this instantiation's own
            # identity can't be trusted -- two genuinely distinct
            # instantiations differing only in that argument (Use<A> vs.
            # Use<B>) would otherwise collide onto one shared label/node,
            # merging their real, distinct emitted-symbol/type-dependency
            # edges (Codex review, empirically confirmed against real clang
            # AST output: clang's -ast-dump=json serializes zero
            # identifying information for a template-template argument).
            # Skip rather than record a wrong identity.
            continue
        resolved_args = _resolve_arg_targets(
            args, id_to_qname, id_to_decl_kind, id_to_template_qname, full_by_id
        )
        out.append(
            TemplateInstantiation(
                kind=_RECORD_KIND,
                template_qname=template_qname,
                label=_instantiation_label(template_qname, resolved_args),
                args=resolved_args,
                emitted_symbols=_member_symbols(full),
                file=id_to_file.get(spec_id, ""),
            )
        )

    full_function_by_id: dict[str, dict[str, Any]] = {}
    _collect_full_function_defs(ast, full_function_by_id)
    _walk_function_templates(
        ast,
        [],
        id_to_qname,
        id_to_decl_kind,
        id_to_template_qname,
        full_by_id,
        full_function_by_id,
        id_to_file,
        out,
    )
    return out


# ── graph augmentation ───────────────────────────────────────────────────────


def template_decl_node_id(qname: str) -> str:
    return f"template_decl://{qname}"


def template_instantiation_node_id(label: str, mangled: str | None = None) -> str:
    """Node id for one concrete instantiation.

    A class instantiation is keyed by its own human label
    (``"Wrapper<internal::Detail>"``) — it has no single symbol of its own
    (it emits one per instantiated member), so the label is the only
    identity available. A **function** instantiation is keyed by its own
    unique mangled name instead, when known (*mangled* set): two distinct
    overloads of the same function template (``f<T>(T)`` vs. ``f<T>(T,T)``)
    both instantiated with ``T=int`` produce the identical *label* (built
    only from template arguments — arity/signature isn't one), so keying by
    label alone collapsed both overloads onto a single node (Codex review,
    empirically confirmed against real clang AST output: only one
    ``DECL_INSTANTIATES_TEMPLATE`` edge survived for two genuinely distinct
    instantiations). The mangled name always differs between overloads, so
    it's the correct, collision-free identity for this kind."""
    if mangled:
        return f"template_instantiation://{mangled}"
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

        function_mangled = (
            inst.emitted_symbols[0]
            if inst.kind == _FUNCTION_KIND and inst.emitted_symbols
            else None
        )
        inst_id = template_instantiation_node_id(inst.label, function_mangled)
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
            # arg.target_decl_kind is the target's raw clang decl kind
            # (populated by _resolve_arg_targets) -- record_type is still the
            # right default for an unrecognized/absent kind (e.g. a nested
            # specialization resolved via _resolve_specialization_qname,
            # which is always itself a record), matching
            # augment_graph_with_types's own fallback for the identical
            # situation.
            type_id = _type_node_id(arg.target_qname)
            ensure_node(
                type_id, _type_node_kind(arg.target_decl_kind or ""), arg.target_qname
            )
            add_edge(inst_id, type_id, EDGE_TEMPLATE_USES_TYPE, CONF_HIGH)

        for symbol in inst.emitted_symbols:
            sid = _symbol_node_id(symbol)
            if sid not in known_symbols:
                continue
            add_edge(inst_id, sid, EDGE_INSTANTIATION_EMITS_SYMBOL, CONF_REDUCED)

    return added


def _merge_template_instantiations(
    existing: TemplateInstantiation, new: TemplateInstantiation
) -> TemplateInstantiation:
    """Merge two instantiations sharing a ``(kind, template_qname, label)``
    key from different TUs (mirrors ``type_graph._merge_type_edges``'s
    identical cross-TU-richness reasoning, Codex review).

    A TU that doesn't include the header declaring an argument's type sees
    that argument's ``target_qname`` unresolved (``None``); another TU that
    does include it resolves it fully — keeping whichever TU happened to run
    first would silently drop the richer resolution. Likewise
    ``emitted_symbols``: one TU may only reach a subset of the instantiated
    members actually used project-wide (a member only called from a
    different TU), and ``file``, the same first-non-empty-wins fallback
    ``type_graph.py`` uses for ``dst_file``.
    """
    if len(existing.args) == len(new.args):
        merged_args = tuple(
            new_arg
            if old_arg.target_qname is None and new_arg.target_qname
            else old_arg
            for old_arg, new_arg in zip(existing.args, new.args)
        )
    else:
        # Argument count mismatch shouldn't occur for a genuinely identical
        # (kind, template_qname, label) key, but degrade to the first-seen
        # value rather than guess at a pairing (ADR-028 D3).
        merged_args = existing.args
    merged_symbols = existing.emitted_symbols + tuple(
        s for s in new.emitted_symbols if s not in existing.emitted_symbols
    )
    return replace(
        existing,
        args=merged_args,
        emitted_symbols=merged_symbols,
        file=existing.file or new.file,
    )


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
        from .call_graph import _replay_cwd, _safe_clang_args_from_compile_unit

        argv = _safe_clang_args_from_compile_unit(cu)
        return self._extract_from_safe_args(argv, cwd=_replay_cwd(cu))

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
        # header) must not double the graph's edge count. A later TU seeing
        # the same instantiation is merged in (_merge_template_instantiations),
        # not dropped -- one TU may resolve an argument's target_qname or
        # reach more of the instantiated members than another (Codex review,
        # mirrors type_graph.py's own cross-TU merge for the identical
        # richness gap).
        # For a function-kind instantiation, disambiguate by its own mangled
        # name (falling back to label only when unavailable) rather than
        # (kind, template_qname, label) alone -- two distinct overloads of
        # the same function template (`f<T>(T)` vs `f<T>(T,T)`) instantiated
        # with identical template arguments produce the identical label
        # (arity isn't a template argument), so the plain 3-tuple key would
        # merge them into one instantiation here too, the same collision
        # template_instantiation_node_id's own fix addresses (Codex review).
        # A class-kind instantiation has no such ambiguity (a class template
        # can't be overloaded), so it keeps the plain key.
        def dedup_key(inst: TemplateInstantiation) -> tuple[str, str, str]:
            if inst.kind == _FUNCTION_KIND and inst.emitted_symbols:
                return (inst.kind, inst.template_qname, inst.emitted_symbols[0])
            return (inst.kind, inst.template_qname, inst.label)

        seen: dict[tuple[str, str, str], int] = {}

        def add(instantiations: Iterable[TemplateInstantiation]) -> None:
            for inst in instantiations:
                key = dedup_key(inst)
                idx = seen.get(key)
                if idx is None:
                    seen[key] = len(all_instantiations)
                    all_instantiations.append(inst)
                else:
                    all_instantiations[idx] = _merge_template_instantiations(
                        all_instantiations[idx], inst
                    )

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
