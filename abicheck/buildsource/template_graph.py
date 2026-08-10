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
  needing its own empirical pass, not a drive-by extension of this one. A
  consequence of that deferral, confirmed empirically (Codex review, fresh
  evidence): two *constrained* overloads sharing both a qualified name and
  an identical function type (``requires integral<T> void f(T)`` vs.
  ``requires floating_point<T> void f(T)`` both print the pattern signature
  ``"void (T)"``) still collide onto one ``template_decl`` node under
  :func:`template_decl_node_id`'s signature discriminator, even though the
  underlying `FunctionTemplateDecl`s are genuinely distinct and their own
  instantiations correctly stay separate (each instantiation's own mangled
  name embeds the Itanium requires-clause encoding, e.g.
  ``_Z1fIiQsr3stdE8integralIT_EEvS0_`` vs. ``..14floating_pointIT_EE..``, so
  `template_instantiation_node_id` never merges them). A real fix needs a
  discriminator pulled from inside `ConceptSpecializationExpr`'s own nested
  `ImplicitConceptSpecializationDecl.loc` (the only place two distinct,
  identically-typed constraints differ in the dump) — squarely the
  "separate AST subsystem" this bullet already defers, not a one-line
  extension of the plain-signature discriminator.
- Variable templates (``template <typename T> constexpr T pi = ...;``) and a
  member function template nested inside a class template instantiation —
  neither decl shape is walked; both fall through as an ordinary,
  unrecognized child (no crash, no data, the same silent-skip default this
  whole module uses throughout).
- **A polymorphic class instantiation's vtable/typeinfo symbols are never
  discovered** (Codex review, fresh evidence, confirmed against a real
  compiled object: ``template <typename T> struct Poly { virtual ~Poly()
  {} virtual void foo(T) {} }; template struct Poly<int>;`` exports
  ``_ZTV4PolyIiE``/``_ZTI4PolyIiE``/``_ZTS4PolyIiE`` — vtable, typeinfo,
  and typeinfo-name — alongside its member functions, per ``nm``). These
  are never AST decl children with their own ``mangledName`` the way an
  ordinary member function is — they're synthesized by codegen from the
  class's own identity, so :func:`_member_symbols`'s child-scanning walk
  structurally cannot see them regardless of how many decl kinds it's
  taught to recognize. A real fix needs two new pieces this module
  doesn't have today, not a drive-by extension of the existing scan: (1) a
  way to tell whether a given ``ClassTemplateSpecializationDecl`` is
  polymorphic (a ``virtual`` method somewhere in its own member list —
  present in the AST, but not currently read by any pass here) combined
  with C++'s own vague-linkage rules for *when* a vtable/typeinfo is
  actually emitted for a given instantiation (always, for an explicit
  instantiation definition; only when a "key function" is defined in this
  TU, for an implicit one — a real, non-trivial linkage rule this module
  has no existing model of); and (2) a way to derive the exact mangled
  ``_ZTV``/``_ZTI``/``_ZTS`` class-name substring, which needs a
  *position*-returning structural split of the nested-name body into
  "everything but the trailing unqualified-name component" —
  :func:`~abicheck.diff_cxx_rules.itanium_scope_components` only returns
  the demangled component *strings*, not their spans in the original
  mangled text, so there is no existing utility to slice from today. The
  existing join-only-onto-an-existing-``binary_symbol``-node discipline
  (ADR-057 D1) means a wrong derived name would simply fail to join rather
  than mint a bad edge, but getting the derivation right at all is its own
  scoped piece of work, not attempted here.

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

from .. import deadline, diff_cxx_rules
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
    the first ``loc.file`` clang emits for it). ``template_signature`` is a
    **function**-kind instantiation's own pattern signature (e.g. ``"T (T,
    T)"``, clang's own printed, unspecialized function type) — see
    :func:`template_decl_node_id` for why this exists: a class template's
    qname alone already identifies its declaration (a class template can't
    be overloaded), but two distinct function-template *overloads* sharing
    one name (``f<T>(T)`` vs. ``f<T>(T,T)``) need a discriminator beyond the
    bare qname or their abstract ``template_decl`` nodes collapse onto one
    identity. Empty for a class-kind instantiation.
    """

    kind: str  # _RECORD_KIND or _FUNCTION_KIND
    template_qname: str
    label: str
    args: tuple[TemplateArgUse, ...] = ()
    emitted_symbols: tuple[str, ...] = ()
    file: str = ""
    template_signature: str = ""


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
        if f:
            # This node carries its own explicit loc.file -- authoritative
            # for this exact id, so it always wins over whatever an earlier
            # occurrence of the same id recorded (Codex review, fresh
            # evidence): the explicit-instantiation/specialization
            # detachment quirk means the same id can appear twice -- an
            # empty, loc-less stub nested under the primary ClassTemplateDecl
            # (which inherits the *header*'s sticky file, since the header's
            # own top-level declarations are visited first) and a detached
            # full-content sibling elsewhere that DOES carry its own loc
            # (the actual .cpp where the specialization/instantiation is
            # written). A plain setdefault let whichever occurrence was
            # visited first win — for this exact shape, the loc-less stub
            # always wins, permanently misattributing the specialization to
            # the header. project_source_files() then excludes it (public
            # headers aren't "project" sources), silently dropping the
            # instantiation's own defined_in_project provenance. An inherited
            # (non-own) file still only sets if absent, so a later
            # loc-less occurrence of an id already resolved by an explicit
            # one can't un-set it.
            id_to_file[node_id] = cur_file
        else:
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


def _node_loc_offset(node: dict[str, Any]) -> int | None:
    """*node*'s own ``loc.offset`` -- a byte position within the TU, present
    on every located node regardless of whether ``loc.file`` itself was
    omitted (clang's sticky-file convention). ``None`` when absent."""
    loc = node.get("loc")
    if not isinstance(loc, dict):
        return None
    offset = loc.get("offset")
    return offset if isinstance(offset, int) else None


def _primary_pattern_member_locs(
    class_template_node: dict[str, Any],
) -> dict[tuple[str, str], int]:
    """``(member name, pattern signature) -> loc.offset`` for each direct
    ``FunctionTemplateDecl`` child of a ``ClassTemplateDecl``'s own primary
    pattern (its un-specialized ``CXXRecordDecl`` child) -- the signal
    :func:`_walk_function_templates` needs to tell a genuinely *shared*
    member (an implicit instantiation, or an explicit-instantiation
    *definition* that forces the primary pattern's own body without writing
    a new one) from an independently-authored member on a distinct
    *explicit specialization* (Codex review, second round, fresh evidence,
    verified empirically against real clang AST output): every copy of a
    shared member -- whether nested directly under the ``ClassTemplateDecl``
    or detached to a top-level sibling by the identical explicit-
    instantiation-definition quirk an explicit specialization also
    triggers -- carries the *exact same* ``loc``/``range`` as the primary
    pattern's own declaration (the same source text, reused), while an
    explicit specialization's own member has its *own*, different ``loc``
    (independently written, at its own specialization's source position).
    Comparing structural position alone (a detached top-level sibling)
    cannot distinguish these two cases -- both detach identically -- so this
    compares source identity instead. Returns ``{}`` when the primary
    pattern's own record isn't found (an unmodeled shape; degrades to no
    shared members recognized, this module's usual conservative default).

    Keyed by ``(name, signature)``, not name alone (Codex review, third
    round, fresh evidence, confirmed against real clang AST output): when
    the primary pattern itself *overloads* a member-template name (``template
    <typename U> U f(U);`` alongside ``template <typename U> U f(U, U);``,
    both named ``f``), a name-only dict keeps only the last-registered
    overload's offset, so every earlier overload's own instantiated member
    then fails the shared-member check and gets wrongly disambiguated --
    confirmed empirically: two overloads of ``f`` on ``Holder``'s primary
    pattern, each instantiated through both ``Holder<int>`` and
    ``Holder<double>``, split the *first* overload into separate
    ``Holder<int>::f``/``Holder<double>::f`` template-declaration nodes for
    what both AST dumps show is the identical syntactic overload, while the
    second overload (whose offset survived the overwrite) stayed correctly
    merged. :func:`_function_template_pattern_signature` (clang's printed,
    unspecialized function type, e.g. ``"T (T)"`` vs. ``"T (T, T)"``) is the
    same discriminator :func:`template_decl_node_id` already uses to keep
    two such overloads' own declaration nodes distinct."""
    pattern: dict[str, Any] | None = None
    for child in class_template_node.get("inner", []) or []:
        if str(child.get("kind", "")) in ("CXXRecordDecl", "RecordDecl"):
            pattern = child
            break
    if pattern is None:
        return {}
    locs: dict[tuple[str, str], int] = {}
    for child in pattern.get("inner", []) or []:
        if str(child.get("kind", "")) == _FUNCTION_TEMPLATE_KIND:
            name = str(child.get("name") or "")
            offset = _node_loc_offset(child)
            if name and offset is not None:
                signature = _function_template_pattern_signature(child)
                locs[(name, signature)] = offset
    return locs


def _class_template_has_auto_nttp(class_template_node: dict[str, Any]) -> bool:
    """Whether *class_template_node* (a ``ClassTemplateDecl``) declares a
    non-type template parameter (``NonTypeTemplateParmDecl``) via ``auto``
    (e.g. ``template <auto V> struct A;``) -- the signal
    :func:`parse_clang_ast_templates`'s class-instantiation loop needs to
    know a specialization's own NTTP argument spelling can't be trusted as
    a unique identity (Codex review, fresh evidence, confirmed against real
    clang 18 AST output): for an ``auto`` NTTP whose deduced type is a
    scoped-enum, clang's ``-ast-dump=json`` serializes the ``TemplateArgument``
    as bare ``{"value": 1}`` -- no ``type`` field, nothing distinguishing an
    argument's *deduced* type at all -- so ``A<E1::X>`` and ``A<E2::X>``
    (two different enum types sharing the same underlying value) both
    reduce to the identical label ``"A<1>"`` and collide onto the same
    ``template_instantiation`` graph node, merging their genuinely distinct
    emitted-symbol edges. A concrete, non-``auto`` NTTP (``template <int
    V>``) carries no such ambiguity -- its type is fixed and known from the
    parameter declaration itself, so the identical bare ``{"value": N}``
    shape means the identical, single-typed argument every time; only the
    ``auto`` case is deliberately checked, not every bare-``value`` shape
    (that would needlessly discard the common, unambiguous case too, the
    opposite of this module's usual false-negative-over-false-positive
    default).

    **Class instantiations only** -- a ``FunctionTemplateDecl`` with its own
    ``auto`` NTTP parameter (``template <auto V> void f();``) has the
    identical ambiguity, and this helper is not applied there (CodeRabbit
    nitpick, confirmed real but low-impact): unlike a class instantiation, a
    function instantiation's own graph-node identity
    (:func:`template_instantiation_node_id`) already keys on its unique
    mangled name when available, not the label, so two ambiguous NTTP
    arguments still mint distinct, correctly-joined instantiation nodes --
    only the cosmetic ``label``/``args`` spelling collides, and (when both
    instantiate the same underlying template, which an ambiguous same-
    valued ``auto`` NTTP always does) the shared ``template_decl`` node is
    the semantically correct outcome anyway, not a merge bug. A
    ``FunctionTemplateDecl`` equivalent of this helper would need its own
    verification pass before being wired in, deferred rather than added
    as a drive-by extension of the class-only fix above."""
    for child in class_template_node.get("inner", []) or []:
        if str(child.get("kind", "")) != "NonTypeTemplateParmDecl":
            continue
        param_type = child.get("type")
        if isinstance(param_type, dict) and param_type.get("qualType") == "auto":
            return True
    return False


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
    *,
    ambiguous_value_args: bool = False,
) -> list[TemplateArgUse] | None:
    """Collect every :class:`TemplateArgUse` from a decl's direct
    ``TemplateArgument`` children, flattening a parameter pack.

    *ambiguous_value_args* — set by the caller when the enclosing class
    template declares an ``auto`` NTTP parameter (see
    :func:`_class_template_has_auto_nttp`'s own docstring) — treats a
    bare, type-less ``{"value": N}`` argument the same way an opaque
    template-template argument is already treated: identity untrustworthy,
    skip the whole instantiation rather than record a wrong one. Default
    ``False`` everywhere else (an ordinary, non-``auto`` NTTP's bare value
    shape is unambiguous on its own, see that function's docstring for why
    only the ``auto`` case is checked).

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
            nested = _flatten_template_args(
                child.get("inner", []) or [],
                ambiguous_value_args=ambiguous_value_args,
            )
            if nested is None:
                return None
            out.extend(nested)
            continue
        if _is_opaque_template_argument(child):
            return None
        if ambiguous_value_args and child.get("type") is None and "value" in child:
            # An `auto` NTTP's bare {"value": N} shape carries no type --
            # see this function's own docstring and
            # _class_template_has_auto_nttp's. Treated the same as an
            # opaque argument: skip the whole instantiation.
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
        # disambiguated label can't be trusted. Return unresolved (None)
        # rather than the bare, unparameterized qname (Codex review, fresh
        # evidence, verified empirically against real clang AST output): an
        # earlier revision's bare-qname fallback let two genuinely distinct
        # nested specializations -- Outer<Use<A>> and Outer<Use<B>>, where
        # Use<A>/Use<B> both take an opaque template-template argument --
        # both resolve their outer argument's target_qname to the identical
        # ambiguous "Use", so both TEMPLATE_USES_TYPE edges landed on the
        # same type://Use node, falsely merging dependencies on two
        # concrete specializations. The outer instantiation *nodes*
        # themselves stayed correctly distinct (each keeps its own full
        # spelling, "Use<A>"/"Use<B>"), so only this edge target was wrong.
        # None here means _resolve_arg_targets' caller sees an unresolved
        # target_qname and (per its own docstring) omits the
        # TEMPLATE_USES_TYPE edge entirely -- a missed edge, not a merged
        # one, matching this module's usual "never guess" discipline.
        return None
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


#: Additional Itanium ctor/dtor manglings implied by the one clang's AST
#: reports -- see :func:`_ctor_dtor_symbol_variants`. clang's ``mangledName``
#: on a ``CXXConstructorDecl``/``CXXDestructorDecl`` is always the complete-
#: object variant (``C1``/``D1``), never the sibling(s), verified empirically
#: (real clang output for both a trivial and a parameterized/virtual case
#: only ever reports ``C1``/``D1``).  ``C3`` (the allocating constructor) is
#: omitted -- not observed emitted by clang/GCC in practice, unlike ``C1``/
#: ``C2`` which are always both present for any non-trivial constructor.
_CTOR_SIBLING_CODES = ("C2",)
_DTOR_SIBLING_CODES = ("D0", "D2")


def _ctor_dtor_symbol_variants(mangled: str, *, is_ctor: bool) -> tuple[str, ...]:
    """The sibling Itanium ctor/dtor manglings ``mangled`` (clang's reported
    ``C1``/``D1`` complete-object variant) implies -- Codex review, verified
    empirically against real clang + compiled-object output: a real Mach-O/
    ELF binary commonly exports ``C1``/``C2`` (and, for a virtual
    destructor, ``D0``/``D1``/``D2``) as **separate** symbol-table entries,
    not aliases collapsed to one, while clang's AST ``mangledName`` only
    ever reports the ``C1``/``D1`` spelling -- so an instantiated ctor/dtor
    that legitimately emits (say) a ``C2`` export previously had no way to
    join that export's ``binary_symbol`` node at all.

    Locates the real ctor/dtor code via :func:`~abicheck.diff_cxx_rules.
    itanium_ctor_dtor_marker_span`'s structural (length-prefix-aware) walk
    and substitutes each sibling code in its place -- **not** a naive
    substring search (Codex review, second round, fresh evidence, a real
    correctness bug in an earlier revision of this function): a class named
    ``C1Evil<int>`` mangles to ``_ZN6C1EvilIiEC1Ev``, and a bare
    ``"C1E"`` text search finds the *class name*'s own embedded ``"C1E"``
    first, not the real ctor code that follows it -- deriving
    ``_ZN6C2EvilIiEC1Ev``, which is the genuinely different class
    ``C2Evil<int>``'s own *real* constructor mangling if that class also
    exists and is exported, a false positive rather than merely a missed
    one (the join-only-onto-an-existing-node safety net this module relies
    on elsewhere does not save this case, since the corrupted string can
    coincidentally *be* a real, different symbol). ``mangled`` does not need
    to be pre-normalized for the Mach-O double-underscore prefix -- see
    ``itanium_ctor_dtor_marker_span``'s own docstring, which confirms
    empirically that it handles an unnormalized ``__Z...`` input correctly.
    This function's own caller still normalizes first anyway (see
    ``_member_symbols``), purely so the *substituted* sibling manglings this
    function returns come out in the same normalized form as every other
    symbol this module joins against, not because normalizing first is
    required for correctness here.

    Returns ``()`` when the marker parser doesn't recognize *mangled* as a
    ctor/dtor mangling at all (an unmangled or non-Itanium name, or a form
    outside what that structural parser models)."""
    span = diff_cxx_rules.itanium_ctor_dtor_marker_span(mangled)
    if span is None:
        return ()
    start, end = span
    codes = _CTOR_SIBLING_CODES if is_ctor else _DTOR_SIBLING_CODES
    return tuple(mangled[:start] + code + mangled[end:] for code in codes)


def _member_symbols(node: dict[str, Any]) -> tuple[str, ...]:
    """A class-template instantiation's own emitted member symbols --
    ``node`` is the full-content ``ClassTemplateSpecializationDecl``, and
    every direct child with a ``mangledName`` is a member that has real
    linkage. Includes ``VarDecl`` alongside the member-function kinds
    (Codex review, fresh evidence, verified empirically against real clang
    AST output): an instantiated *static data member* --
    ``template <typename T> struct Box { static T value; };`` explicitly
    instantiated as ``Box<int>`` -- carries its mangled name
    (``_ZN3BoxIiE5valueE``) directly on a ``VarDecl`` child, not one of the
    member-function kinds, so it was previously silently dropped from
    ``emitted_symbols`` entirely. An *ordinary* (non-static) field is also a
    direct child here but is never mistaken for one: a non-static field has
    no linkage and clang never gives it a ``mangledName``, so the existing
    ``isinstance(mangled, str) and mangled`` guard already excludes it
    without kind-specific filtering.

    A constructor/destructor child additionally contributes its sibling
    Itanium manglings (``C2``, and for a destructor ``D0``/``D2``) -- see
    :func:`_ctor_dtor_symbol_variants`."""
    symbols: list[str] = []
    for child in node.get("inner", []) or []:
        kind = str(child.get("kind", ""))
        if kind in _MEMBER_FUNCTION_KINDS | {"VarDecl"}:
            mangled = child.get("mangledName")
            if isinstance(mangled, str) and mangled:
                # Normalize (strip a Mach-O double-underscore) once, up
                # front: not required for _ctor_dtor_symbol_variants's own
                # offset arithmetic (it handles an unnormalized "__Z..."
                # input correctly -- see its docstring), but doing it here
                # keeps every symbol this function returns, including the
                # substituted siblings, in the same normalized form the
                # rest of this module joins binary_symbol nodes against.
                normalized = _normalize_mangled(mangled)
                symbols.append(normalized)
                if kind in ("CXXConstructorDecl", "CXXDestructorDecl"):
                    symbols.extend(
                        _ctor_dtor_symbol_variants(
                            normalized, is_ctor=kind == "CXXConstructorDecl"
                        )
                    )
    return tuple(symbols)


#: A FunctionTemplateDecl's own template-parameter declaration kinds --
#: what :func:`_function_template_pattern_signature` additionally counts
#: to discriminate two templates that share both a qualified name and an
#: identical *function* signature but differ in their own template
#: parameter list (see that function's docstring for the confirmed
#: real-world case this closes).
_TEMPLATE_PARAM_DECL_KINDS = frozenset(
    {"TemplateTypeParmDecl", "NonTypeTemplateParmDecl", "TemplateTemplateParmDecl"}
)


def _function_template_pattern_signature(node: dict[str, Any]) -> str:
    """A ``FunctionTemplateDecl``'s own pattern signature -- the
    discriminator :func:`template_decl_node_id` needs between two function
    templates sharing one qualified name, combining two independently
    confirmed collision sources into one string:

    1. Clang's printed, unspecialized function type (e.g. ``"T (T, T)"``),
       distinguishing two *overloads* (``f<T>(T)`` vs. ``f<T>(T,T)``,
       verified empirically against real clang AST output: each overload's
       own pattern ``FunctionDecl`` child, no ``mangledName``, carries a
       distinct ``type.qualType`` reliably differing between overloads
       without depending on any particular instantiation's argument
       substitution).
    2. The ordered kinds of *this* ``FunctionTemplateDecl``'s own leading
       template-parameter children (Codex review, fresh evidence beyond
       the overload fix above): ``template <class T> void f()`` and
       ``template <class T, class U> void f()`` both print the *identical*
       function-type spelling ``"void ()"`` -- the function parameter list
       is genuinely empty for both, and clang's printer never reflects the
       *template* parameter list in that spelling at all -- so (1) alone
       still collapsed both onto one shared ``template_decl`` node despite
       their concrete instantiations correctly staying separate (distinct
       mangled names), falsely making every ``DECL_INSTANTIATES_TEMPLATE``
       edge from either one point at a declaration the other didn't
       actually come from. Confirmed via a real compiled/dumped
       `template<class T> void f()` / `template<class T,class U> void f()`
       pair reducing to the identical `"void ()"` printed type. A
       ``NonTypeTemplateParmDecl``'s own *kind* alone still wasn't enough
       (Codex review, second round, fresh evidence): ``template <E1 N>
       void f()`` and ``template <E2 N> void f()`` (two distinct enum
       types) both counted as one bare ``"NonTypeTemplateParmDecl"``,
       colliding the identical way -- confirmed via a real compiled/dumped
       pair (`f<E1::A>`/`f<E2::A>`) reducing to the identical
       ``"NonTypeTemplateParmDecl|void ()"`` signature. Each NTTP
       parameter's own descriptor now also folds in its declared
       ``type.qualType`` (always present, reliably differs), as
       ``"NonTypeTemplateParmDecl:E1"`` vs. ``"...:E2"``.
       ``TemplateTypeParmDecl``/``TemplateTemplateParmDecl`` stay
       kind-only -- no confirmed real-world collision source for either
       found yet, so no unverified extension of this discriminator was
       added for them.

    Returns ``"<param-kinds>|<pattern-type>"`` (the param-kind half always
    present even when no pattern spelling is found, so two templates
    differing only in parameter *count* still discriminate); ``""`` only
    when *node* has neither -- degrade to the bare-qname identity, this
    module's usual discipline.

    Skipping a ``mangledName``-carrying child for the pattern-type half is
    defense-in-depth, not a fix for an observed collision (CodeRabbit
    review): empirically, across every AST shape checked against real
    clang output -- a plain overload pair, multiple instantiations of one
    overload, an explicit full specialization's detached unmangled stub, a
    forward-declared-then-defined template, and an out-of-line class-member
    template -- the unmangled pattern child is always clang's *first*
    function-shaped child, so the prior first-match behavior already
    returned it in every case tried. There is no structural AST reason
    this could differ (a primary template must be declared before
    anything instantiates or specializes it, so its pattern child is
    always emitted first), but filtering explicitly removes the dependency
    on that ordering rather than relying on it implicitly."""
    param_kinds: list[str] = []
    pattern_type = ""
    for child in node.get("inner", []) or []:
        child_kind = str(child.get("kind", ""))
        if child_kind in _TEMPLATE_PARAM_DECL_KINDS:
            descriptor = child_kind
            if child_kind == "NonTypeTemplateParmDecl":
                # Two NTTP parameters differing only in their own declared
                # type (`template <E1 N> void f()` vs. `template <E2 N>
                # void f()`) both still counted as an identical bare
                # "NonTypeTemplateParmDecl" kind, so distinct templates
                # collided the same way the earlier kind-count-only fix
                # was meant to prevent (Codex review, fresh evidence,
                # confirmed against real clang output: both instantiations
                # -- f<E1::A>, f<E2::A> -- reduced to the identical
                # "NonTypeTemplateParmDecl|void ()" signature). A
                # NonTypeTemplateParmDecl's own type.qualType (e.g. "E1"/
                # "E2") is always present and reliably differs, so it's
                # folded into this parameter's own descriptor.
                nttp_type = child.get("type")
                if isinstance(nttp_type, dict):
                    nttp_spelling = nttp_type.get("qualType")
                    if isinstance(nttp_spelling, str) and nttp_spelling:
                        descriptor = f"{child_kind}:{nttp_spelling}"
            param_kinds.append(descriptor)
            continue
        if pattern_type or child_kind not in _MEMBER_FUNCTION_KINDS | {"FunctionDecl"}:
            continue
        if child.get("mangledName"):
            continue
        qual_type = child.get("type")
        if isinstance(qual_type, dict):
            spelling = qual_type.get("qualType")
            if isinstance(spelling, str) and spelling:
                pattern_type = spelling
    if not param_kinds and not pattern_type:
        return ""
    return f"{','.join(param_kinds)}|{pattern_type}"


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
    qname_to_member_locs: dict[str, dict[tuple[str, str], int]],
) -> None:
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))

    if kind == _FUNCTION_TEMPLATE_KIND:
        name = str(node.get("name") or "")
        # An out-of-line member-template *definition* (`template <class T>
        # void C::f(T) {}`, the class's own declaration `template <class T>
        # void f(T);` staying in-class) detaches as a *separate*, top-level
        # FunctionTemplateDecl -- not nested inside CXXRecordDecl:C at all
        # -- confirmed empirically against real clang AST output (Codex
        # review, fresh evidence): this node's own structural `scope` is
        # therefore `[]`, computing the bare "f" instead of "C::f", and its
        # own children happen to resolve (via full_function_by_id's id
        # join) to the identical instantiated symbols the correctly-scoped
        # in-class declaration already captures -- so without this fix the
        # graph would carry the same instantiation twice, once correctly
        # under "C::f" and once wrongly under a bare "f" that could
        # coincidentally merge with an unrelated global template also named
        # "f". Clang emits `parentDeclContextId` on exactly this node shape
        # (confirmed absent on the ordinary, correctly-nested in-class
        # declaration) -- the id of the *real* semantic owner, resolvable
        # through id_to_qname the same way a template argument's `decl`
        # cross-reference already is. Preferred unconditionally when
        # present, since clang only emits it to correct a structural
        # mismatch in the first place.
        owner_id = str(node.get("parentDeclContextId") or "")
        owner_qname = id_to_qname.get(owner_id) if owner_id else None
        qname = (
            f"{owner_qname}::{name}"
            if owner_qname and name
            else ("::".join([*scope, name]) if name else "")
        )
        if qname:
            signature = _function_template_pattern_signature(node)
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
                        template_signature=signature,
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
        # template).
        #
        # The bare, unparameterized name is correct for a genuinely *shared*
        # member -- Holder<int>::apply and Holder<double>::apply are the
        # same syntactic template declaration (apply is written once, in
        # the primary Holder pattern) and correctly share one template_decl
        # node, matching how Holder's own class-template pattern is already
        # shared across all its instantiations. An earlier revision decided
        # this from *where* the node was reached (nested under its own
        # ClassTemplateDecl vs. a detached top-level sibling), on the theory
        # that only an *explicit specialization*'s own independently-
        # authored content detaches. Codex review, second round, fresh
        # evidence, empirically confirmed against real clang AST output:
        # that's wrong -- an *explicit instantiation definition* (`template
        # struct Holder<int>;`, forcing the shared primary body without
        # writing a new one) detaches identically, and its member's
        # template_qname was then wrongly disambiguated from every other
        # Holder<T>::apply, splitting one shared declaration into two.
        # Structural position alone cannot tell these two detachment causes
        # apart.
        #
        # The reliable signal is source identity, not position: every copy
        # of a genuinely shared member -- however it was reached -- carries
        # the *exact same* `loc`/`range` as the primary pattern's own
        # declaration (the same source text, reused), while an explicit
        # specialization's own member has its own, different `loc`
        # (independently written, at its own specialization's position) --
        # see _primary_pattern_member_locs's own docstring for the full
        # empirical evidence, including the H<int>::f / H<double>::f
        # counter-example (H has no generic member `f` on its primary
        # template at all, so both previously collapsed onto the identical
        # bare qname "H::f", sharing one template_decl id). Decided per
        # member (not per specialization) since only a FunctionTemplateDecl
        # child's own identity is in question here.
        #
        # class_qname prefers the id-keyed registration (id_to_template_qname,
        # set from the *stub* occurrence nested under the real ClassTemplateDecl)
        # over this walk's own structural scope (Codex review, fresh evidence,
        # confirmed against real clang AST output): a namespace-scoped explicit
        # instantiation written with a qualified name *outside* its namespace
        # (`template struct api::Holder<int>;`) detaches its full-content
        # sibling as a direct TranslationUnitDecl child -- not nested inside
        # NamespaceDecl:api at all -- so this walk's own scope is `[]` there,
        # computing the bare "Holder" instead of "api::Holder". The stub,
        # always reached correctly nested inside its own ClassTemplateDecl
        # (itself correctly nested inside NamespaceDecl:api), already
        # registered the right id -> "api::Holder" mapping; reusing it here
        # means the member-locs lookup below stays correct regardless of
        # where clang chose to detach this occurrence's full content.
        # Structural scope is still the fallback for a specialization id
        # this module never resolved via the stub path (an unmodeled shape).
        node_id = str(node.get("id") or "")
        bare_name = str(node.get("name") or "")
        resolved_qname = id_to_template_qname.get(node_id)
        if resolved_qname:
            class_qname = resolved_qname
            # The scope *above* this class (what children get appended
            # onto) -- drop the class's own trailing bare_name back off
            # resolved_qname, mirroring how class_qname was itself built
            # ("::".join([*parent_scope, bare_name])) without needing to
            # trust this walk's own (possibly wrong, see below) `scope`.
            class_parent_scope = (
                resolved_qname.rsplit("::", 1)[0].split("::")
                if "::" in resolved_qname
                else []
            )
        else:
            class_qname = "::".join([*scope, bare_name]) if bare_name else ""
            class_parent_scope = scope
        member_locs = qname_to_member_locs.get(class_qname, {})
        disambiguated_name: str | None = None
        for child in node.get("inner", []) or []:
            if str(child.get("kind", "")) == _FUNCTION_TEMPLATE_KIND:
                member_name = str(child.get("name") or "")
                # Keyed by (name, signature), not name alone -- a
                # same-named overload on the primary pattern (`f<U>(U)`
                # vs. `f<U>(U, U)`) needs its own discriminator or the
                # dict-overwrite-by-name loses every overload but the last
                # (Codex review, third round, fresh evidence; see
                # _primary_pattern_member_locs's own docstring).
                member_key = (member_name, _function_template_pattern_signature(child))
                shared = (
                    member_name
                    and member_key in member_locs
                    and _node_loc_offset(child) == member_locs[member_key]
                )
                if shared:
                    name = bare_name
                else:
                    if disambiguated_name is None:
                        disambiguated_name = _specialization_scope_name(node, bare_name)
                    name = disambiguated_name
            else:
                name = bare_name
            # The scope prefix for this child comes from class_parent_scope
            # (the id-corrected value above), not the raw structural `scope`
            # -- same reasoning as class_qname itself: a detached-to-top-level
            # occurrence's structural scope can be wrong even though its id
            # was already correctly resolved.
            child_scope = [*class_parent_scope, name] if name else class_parent_scope
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
                qname_to_member_locs,
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
                qname_to_member_locs,
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
            qname_to_member_locs,
        )


def _walk_class_templates(
    node: Any,
    scope: list[str],
    id_to_qname: dict[str, str],
    id_to_template_qname: dict[str, str],
    qname_to_member_locs: dict[str, dict[tuple[str, str], int]],
    qname_has_auto_nttp: set[str],
) -> None:
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))

    if kind == _CLASS_TEMPLATE_KIND:
        name = str(node.get("name") or "")
        qname = "::".join([*scope, name]) if name else ""
        if qname:
            _register_class_template(node, qname, id_to_template_qname)
            # See _primary_pattern_member_locs's own docstring: this is the
            # signal _walk_function_templates needs to tell a genuinely
            # shared member (implicit instantiation, or explicit-
            # instantiation *definition*) from an independently-authored one
            # on a distinct explicit *specialization* -- both detach
            # identically (a stub nested here, full content elsewhere), so
            # structural position alone can't distinguish them.
            qname_to_member_locs[qname] = _primary_pattern_member_locs(node)
            # See _class_template_has_auto_nttp's own docstring: an `auto`
            # NTTP parameter means a specialization's own bare-value
            # argument spelling can't be trusted as a unique identity.
            if _class_template_has_auto_nttp(node):
                qname_has_auto_nttp.add(qname)
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
            _walk_class_templates(
                child,
                child_scope,
                id_to_qname,
                id_to_template_qname,
                qname_to_member_locs,
                qname_has_auto_nttp,
            )
        return

    for child in node.get("inner", []) or []:
        _walk_class_templates(
            child,
            scope,
            id_to_qname,
            id_to_template_qname,
            qname_to_member_locs,
            qname_has_auto_nttp,
        )


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
    qname_to_member_locs: dict[str, dict[tuple[str, str], int]] = {}
    qname_has_auto_nttp: set[str] = set()
    _walk_class_templates(
        ast,
        [],
        id_to_qname,
        id_to_template_qname,
        qname_to_member_locs,
        qname_has_auto_nttp,
    )
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
        args = _flatten_template_args(
            full.get("inner", []) or [],
            ambiguous_value_args=template_qname in qname_has_auto_nttp,
        )
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
        qname_to_member_locs,
    )
    return out


# ── graph augmentation ───────────────────────────────────────────────────────


def template_decl_node_id(qname: str, signature: str | None = None) -> str:
    """Node id for one template declaration (the abstract pattern).

    A **class** template is keyed by its qname alone — a class template
    can't be overloaded, so two declarations sharing a qname really are the
    same template (matches how ``Holder<int>``'s and ``Holder<double>``'s
    own class-template pattern is correctly shared across every
    instantiation). A **function** template is additionally disambiguated
    by its own pattern *signature* (e.g. ``"T (T, T)"``) when known: two
    distinct overloads sharing one name (``f<T>(T)`` vs. ``f<T>(T,T)``)
    would otherwise collapse their abstract declarations onto one shared
    ``template_decl`` node even after their own instantiation nodes were
    separated by mangled name — a real gap the earlier instantiation-id fix
    didn't close (Codex review, empirically confirmed against real clang
    AST output: both overloads' ``DECL_INSTANTIATES_TEMPLATE`` edges still
    terminated at the identical ``template_decl://f`` node)."""
    if signature:
        return f"template_decl://{qname}#{signature}"
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
        template_id = template_decl_node_id(
            inst.template_qname, inst.template_signature
        )
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
