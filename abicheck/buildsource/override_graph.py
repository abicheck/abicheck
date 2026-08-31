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

"""Optional Clang virtual-dispatch/class-hierarchy override-edge extraction
for the L5 graph (ADR-041 P2 item 1: "closes the loop to the actual override
set" — the call graph already labels a virtual call ``CALL_KIND_VIRTUAL``/
``RESOLUTION_OVERAPPROX``, but until this module nothing recorded WHICH
declarations are the actual candidate targets).

clang's plain ``-ast-dump=json`` carries no ``overriddenMethods`` list (no
direct base-method resolution at all) — only ``virtual: true`` on the
declaration that first introduces a virtual slot, and an optional
``OverrideAttr`` marker (present only when the ``override`` keyword was
written) with no target info either. So this module reconstructs the
override chain itself: build the class hierarchy from
``type_graph.py``'s already-resolved ``TYPE_INHERITS`` edges (reusing its
output rather than re-deriving base-name resolution), collect each class's
own methods, and match a derived method against the NEAREST ancestor that
declares a same-signature method — not necessarily a DIRECT base, since an
intermediate class in the chain may not redeclare the method at all (e.g.
``Base`` declares a virtual ``run()``, ``Mid : Base`` doesn't redeclare it,
``Derived : Mid`` overrides it — the real override target is ``Base::run``,
reached by walking past the non-redeclaring ``Mid``; verified against real
Clang 17 output, Codex review). Matching itself is by ``(name,
type.qualType)`` with the exception specification normalized out first
(:func:`_strip_exception_spec` — an override may legally STRENGTHEN it, e.g.
adding ``noexcept`` to an unmarked base virtual, and clang appends the
exception spec as the qualType's trailing token regardless of surrounding
cv/ref qualifiers; verified against real Clang 17 output, Codex review) —
the same shape "approximate, name/signature-only matching" every edge
family in this file's sibling modules already accepts (``type_graph.py``'s
own module docstring: "best-effort and approximate").

Architecture mirrors ``call_graph.py``/``type_graph.py`` deliberately:

- :func:`parse_clang_ast_overrides` is a **pure function** over a
  ``clang -Xclang -ast-dump=json`` tree — unit-tested without a compiler.
- :class:`ClangOverrideGraphExtractor` is the thin, side-effecting wrapper
  that shells out to ``clang`` for a translation unit and feeds the parser.
  Only exercised on the ``integration`` lane; a missing compiler degrades
  gracefully.
- :func:`augment_graph_with_overrides` folds the resulting edges into a
  :class:`~abicheck.model.source_graph.SourceGraphSummary`.

Deliberately scoped OUT of this first slice (documented gaps, not silent
omissions):

- Only ``CXXMethodDecl``/``CXXConversionDecl`` participate (see
  ``_OVERRIDE_CANDIDATE_KINDS``) — constructors/destructors
  (``CXXConstructorDecl``/``CXXDestructorDecl``) are excluded. A virtual
  destructor is a common, real override case, but the Itanium ABI mangles
  one C++ destructor into TWO symbols (complete- and base-object, ``D1``/
  ``D2``), which needs its own verified matching rule rather than reusing
  the ordinary-method one uncritically.
- A ``ClassTemplateSpecializationDecl`` is not walked for override edges —
  the same class of complexity ``dumper_clang.py``'s own template-
  specialization scope-key fix (this same PR) needed real, multi-round
  correctness work for; not reattempted here without equivalent
  verification.
- Multiple/virtual inheritance: a derived method matching more than one
  ancestor's virtual method (a real but rare shape — multiple-interface
  inheritance) emits an edge to EACH nearest declaring ancestor, not just
  one.
- Covariant return types are not modeled: an override with a covariant
  return type has a DIFFERENT ``type.qualType`` than its base (the return
  type differs), so this genuinely misses it (a known false negative, not a
  false positive) rather than risk a wrong match.
- KNOWN, deliberately-deferred gap (Codex review, fresh evidence): a base
  method spelling a parameter/return type through a TYPEDEF, overridden by
  a derived method spelling the underlying type directly (e.g. ``typedef
  int I; virtual void f(I);`` overridden by ``void f(int) override;``) is a
  real, clang-confirmed override (``OverrideAttr`` present) that this
  matcher misses — ``type.qualType`` spells ``"void (I)"`` vs. ``"void
  (int)"``, verified against real Clang 17 output. Each ``ParmVarDecl``
  child DOES carry its own ``desugaredQualType`` when its type is a
  typedef, but the enclosing METHOD's own ``type.qualType`` never does
  (verified: a typedef'd RETURN type has no desugared form anywhere in this
  compact JSON shape at all) — a real fix needs re-assembling the
  signature from each parameter's desugared type plus a return-type
  typedef resolution this format cannot provide, not a narrow extension of
  the current whole-qualType string comparison.
- KNOWN, deliberately-deferred gap (Codex review, fresh evidence): a LOCAL
  class defined inside a function body is scoped only by its enclosing
  namespace/class chain here (mirroring ``type_graph.py``'s own
  ``_SCOPE_DECL_KINDS``, deliberately — see :func:`_collect_class_methods`'s
  own docstring), never by its enclosing ``FunctionDecl``. Two DIFFERENT
  functions each locally declaring same-named classes (e.g. both defining
  ``struct B { ... }; struct D : B { ... };``) therefore key as the SAME
  ``B``/``D`` identities and collide in this module's own ``declared``
  dict — confirmed empirically (real Clang 18 output) that a genuine,
  ``OverrideAttr``-confirmed override in the first such function produces
  ZERO edges once a second, unrelated same-named local hierarchy follows it
  in the same translation unit. This is not a narrow fix scoped to this
  module alone: ``bases_of`` itself is built from ``type_graph.py``'s own
  ``TYPE_INHERITS`` edges, whose resolution has the identical scope-blind
  collision (documented in that module's own docstring: "two same-named
  types in different scopes can still collide" is an accepted, existing
  tradeoff) — adding function-scope tracking to only this module's method
  collection would leave ``bases_of`` itself still ambiguous between the
  two hierarchies, so the fix would not actually close the gap.
  ``call_graph.py`` carries the identical, independently-documented
  limitation in its own (deliberately duplicated, not shared)
  ``_SCOPE_DECL_KINDS``. A real fix needs function-scope tracking added
  consistently across all three sibling modules, not a drive-by extension
  of this one.
"""

from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from .. import deadline
from ..model.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
    _decl_node_id,
)
from ..model.source_graph import function_decl_identity
from .clang_ast_run import run_clang_ast_dump
from .type_graph import EDGE_TYPE_INHERITS, _normalize_mangled, parse_clang_ast_types

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .build_evidence import BuildEvidence, CompileUnit as BuildEvidenceCompileUnit

# ── edge kind (reserved by source_graph.EDGE_KINDS) ────────────────────────
EDGE_METHOD_POSSIBLE_OVERRIDE = "METHOD_POSSIBLE_OVERRIDE"

# ── resolution vocabulary (a fresh family, like DECL_REFERENCES_DECL's own
# RESOLUTION_REF_* — a signature/attribute match resolves differently than
# the existing RESOLUTION_SCOPE/RESOLUTION_UNIQUE_CANDIDATE type-name walk) ─
#: The overriding declaration carries clang's own ``OverrideAttr`` (the
#: ``override`` keyword) -- a compiler-CHECKED promise, not a guess.
RESOLUTION_OVERRIDE_CONFIRMED = "override_confirmed"
#: No ``OverrideAttr`` -- matched purely by (name, type.qualType) equality
#: against a base's already-virtual slot. Real C++ makes this implicitly
#: virtual regardless of the keyword, but this module cannot independently
#: confirm it the way ``OverrideAttr`` does.
RESOLUTION_OVERRIDE_SIGNATURE_MATCH = "override_signature_match"

_RECORD_DECL_KINDS = frozenset({"CXXRecordDecl", "RecordDecl"})
_NAMESPACE_SCOPE_KINDS = frozenset({"NamespaceDecl"})
#: clang AST node kinds this module collects as override candidates. A
#: virtual conversion operator (``operator int() const``) is its own kind,
#: ``CXXConversionDecl`` -- NOT ``CXXMethodDecl`` -- verified against real
#: Clang 18 output (Codex review, fresh evidence): an ordinary override of
#: `virtual operator int() const` by `operator int() const override` was
#: silently dropped by a ``CXXMethodDecl``-only filter, producing no
#: ``METHOD_POSSIBLE_OVERRIDE`` edge for a valid, ``OverrideAttr``-confirmed
#: override. ``call_graph.py``/``type_graph.py`` already include
#: ``CXXConversionDecl`` in their own callable-kind sets for the identical
#: reason. Constructors/destructors stay excluded per this module's own
#: docstring (a deliberate scoping decision, unlike this omission).
_OVERRIDE_CANDIDATE_KINDS = frozenset({"CXXMethodDecl", "CXXConversionDecl"})


@dataclass(frozen=True)
class OverrideEdge:
    """One possible-override edge: *src* (the overriding method) -> *dst*
    (the immediate base method it overrides). Chains, not flattened to the
    ultimate root -- consistent with ``TYPE_INHERITS``'s own consecutive
    derived->base edge shape; a consumer wanting the full candidate set for
    a virtual call does the transitive closure itself.
    """

    src: str
    dst: str
    confidence: str = CONF_REDUCED
    resolution: str = RESOLUTION_OVERRIDE_SIGNATURE_MATCH


@dataclass(frozen=True)
class _MethodInfo:
    identity: str
    sig_key: tuple[str, str]
    has_override_attr: bool
    own_virtual: bool


def _strip_exception_spec(qual_type: str) -> str:
    """Drop a trailing ``noexcept``/``noexcept(...)``/``throw(...)`` exception
    specification from a method's ``type.qualType`` before it's used as a
    signature-matching key (Codex review, fresh evidence): an override may
    legally STRENGTHEN the exception spec (e.g. adding ``noexcept`` to an
    unmarked base virtual) while remaining a real, clang-confirmed override,
    and clang always appends the exception spec as the qualType's trailing
    token regardless of any preceding cv/ref qualifiers — verified against
    real Clang 17 output (``void ()`` vs. ``void () noexcept``, ``void ()
    const &`` vs. ``void () const & noexcept``, ``int (int) throw()`` vs.
    ``int (int) noexcept``, ...). Exact equality on the raw spelling would
    reject every one of these as a signature mismatch.

    A trailing ``)`` is only stripped when the matching (balance-counted, so
    a dependent ``noexcept(sizeof(int) == 4)`` strips as one unit) opening
    ``(`` is immediately preceded by ``noexcept``/``throw`` — an ordinary
    parameter list's own closing paren (e.g. plain ``void (int)``) is left
    untouched, since its prefix is neither keyword.
    """
    s = qual_type.rstrip()
    if s.endswith(")"):
        depth = 0
        for i in range(len(s) - 1, -1, -1):
            if s[i] == ")":
                depth += 1
            elif s[i] == "(":
                depth -= 1
                if depth == 0:
                    prefix = s[:i].rstrip()
                    for kw in ("noexcept", "throw"):
                        if prefix.endswith(kw):
                            return prefix[: -len(kw)].rstrip()
                    break
    if s.endswith("noexcept"):
        return s[: -len("noexcept")].rstrip()
    return s


def _method_info(node: dict[str, Any], scope: list[str]) -> _MethodInfo | None:
    name = str(node.get("name") or "")
    if not name:
        return None
    # _normalize_mangled strips a spurious macOS Mach-O leading underscore
    # (Codex review, fresh evidence, verified against real
    # `clang++ --target=x86_64-apple-darwin` output: mangledName reports
    # "__ZN1D1fEv", not the standard "_ZN1D1fEv") -- left unstripped, an
    # override edge's decl:// node never joins the call/type graph's own
    # node for the SAME method (both already normalize via this exact
    # helper), leaving it a disconnected duplicate on Darwin.
    mangled = _normalize_mangled(str(node.get("mangledName") or ""))
    type_obj = node.get("type")
    type_qual = str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
    qualified_name = "::".join([*scope, name]) if scope else name
    # function_decl_identity needs the REAL, un-normalized type_qual to match
    # SourceEntity.identity()'s own hash for the same declaration elsewhere
    # (source_graph.function_decl_identity's own docstring) -- only the
    # sig_key used for override MATCHING is exception-spec-normalized.
    identity = function_decl_identity(mangled, name, qualified_name, type_qual)
    has_override_attr = any(
        isinstance(c, dict) and c.get("kind") == "OverrideAttr"
        for c in node.get("inner", []) or []
    )
    return _MethodInfo(
        identity=identity,
        sig_key=(name, _strip_exception_spec(type_qual)),
        has_override_attr=has_override_attr,
        own_virtual=bool(node.get("virtual")),
    )


def _collect_class_methods(ast: dict[str, Any]) -> dict[str, list[_MethodInfo]]:
    """Every ``CXXRecordDecl``'s own direct ``CXXMethodDecl``/
    ``CXXConversionDecl`` children, keyed by scope-qualified class name (the
    same ``"::".join(scope)`` convention ``type_graph.py``'s own resolution
    produces, so the two data sources' class identities line up without a
    second resolution pass)."""
    result: dict[str, list[_MethodInfo]] = {}

    def walk(node: Any, scope: list[str]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = str(node.get("name") or "")
        if kind in _RECORD_DECL_KINDS and name:
            qname = "::".join([*scope, name])
            methods = [
                info
                for child in node.get("inner", []) or []
                if isinstance(child, dict)
                and child.get("kind") in _OVERRIDE_CANDIDATE_KINDS
                for info in [_method_info(child, [*scope, name])]
                if info is not None
            ]
            result.setdefault(qname, []).extend(methods)
            child_scope = [*scope, name]
            for child in node.get("inner", []) or []:
                walk(child, child_scope)
            return
        child_scope = (
            [*scope, name] if kind in _NAMESPACE_SCOPE_KINDS and name else scope
        )
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(ast, [])
    return result


_SlotKey = tuple[str, tuple[str, str]]


def _nearest_declaring_ancestors(
    qname: str,
    sig_key: tuple[str, str],
    bases_of: dict[str, list[str]],
    declared: dict[_SlotKey, _MethodInfo],
    memo: dict[_SlotKey, list[str]],
    visiting: frozenset[str] = frozenset(),
) -> list[str]:
    """The nearest ancestor class(es) of *qname* that actually DECLARE a
    method matching *sig_key* — walking PAST an intermediate class that
    doesn't redeclare the method at all (Codex review, fresh evidence,
    verified against real Clang 17 output: ``Base`` declares a virtual
    ``run()``, ``Mid : Base`` doesn't redeclare it, ``Derived : Mid``
    overrides it — the real override target is ``Base::run``, reached only
    by walking past the non-redeclaring ``Mid``). Memoized per
    ``(qname, sig_key)``; *visiting* guards against a cyclic
    (malformed/hand-built test) hierarchy real compiler output never
    produces.
    """
    key = (qname, sig_key)
    if key in memo:
        return memo[key]
    if qname in visiting:
        return []
    visiting = visiting | {qname}
    result: list[str] = []
    for base_qname in bases_of.get(qname, []):
        if (base_qname, sig_key) in declared:
            result.append(base_qname)
        else:
            result.extend(
                _nearest_declaring_ancestors(
                    base_qname, sig_key, bases_of, declared, memo, visiting
                )
            )
    memo[key] = result
    return result


def _is_virtual(
    qname: str,
    sig_key: tuple[str, str],
    bases_of: dict[str, list[str]],
    declared: dict[_SlotKey, _MethodInfo],
    ancestor_memo: dict[_SlotKey, list[str]],
    virtual_memo: dict[_SlotKey, bool],
    visiting: frozenset[str] = frozenset(),
) -> bool:
    """Whether the ``sig_key`` slot is virtual on *qname* — directly (clang
    said so), or transitively through the NEAREST declaring ancestor(s)
    (real C++'s own "once virtual, always virtual down the hierarchy" rule
    — clang does NOT repeat ``"virtual": true`` on an override, so this must
    be derived). Recurses through :func:`_nearest_declaring_ancestors`
    rather than only direct bases, for the same non-redeclaring-intermediate
    reason that function documents. Memoized; *visiting* guards a cyclic
    fixture the same way.
    """
    key = (qname, sig_key)
    if key in virtual_memo:
        return virtual_memo[key]
    m = declared.get(key)
    if m is not None and m.own_virtual:
        virtual_memo[key] = True
        return True
    if qname in visiting:
        virtual_memo[key] = False
        return False
    visiting = visiting | {qname}
    result = any(
        _is_virtual(
            ancestor, sig_key, bases_of, declared, ancestor_memo, virtual_memo, visiting
        )
        for ancestor in _nearest_declaring_ancestors(
            qname, sig_key, bases_of, declared, ancestor_memo
        )
    )
    virtual_memo[key] = result
    return result


def parse_clang_ast_overrides(ast: dict[str, Any]) -> list[OverrideEdge]:
    """Pure function: a ``clang -ast-dump=json`` tree -> possible-override
    edges. See this module's own docstring for the full algorithm/scope.
    """
    bases_of: dict[str, list[str]] = {}
    for e in parse_clang_ast_types(ast):
        if e.kind == EDGE_TYPE_INHERITS:
            bases_of.setdefault(e.src, []).append(e.dst)

    methods_by_class = _collect_class_methods(ast)
    declared: dict[_SlotKey, _MethodInfo] = {
        (qname, m.sig_key): m
        for qname, methods in methods_by_class.items()
        for m in methods
    }
    ancestor_memo: dict[_SlotKey, list[str]] = {}
    virtual_memo: dict[_SlotKey, bool] = {}

    edges: list[OverrideEdge] = []
    seen: set[tuple[str, str]] = set()
    for qname, methods in methods_by_class.items():
        if qname not in bases_of:
            continue  # no bases at all -> no ancestor can be reached
        for m in methods:
            if not _is_virtual(
                qname, m.sig_key, bases_of, declared, ancestor_memo, virtual_memo
            ):
                continue
            for base_qname in _nearest_declaring_ancestors(
                qname, m.sig_key, bases_of, declared, ancestor_memo
            ):
                bm = declared[(base_qname, m.sig_key)]
                if m.identity == bm.identity:
                    continue
                # qname's OWN slot being virtual (checked above) only means
                # SOME ancestor makes it so -- under multiple inheritance a
                # sibling base can independently declare a same-signature
                # NON-virtual method that m merely HIDES, not overrides
                # (Codex review, fresh evidence, verified against real Clang
                # 17 output: B1::f virtual, B2::f not, D : B1, B2 overriding
                # f -- clang accepts D::f's `override` keyword and attaches
                # OverrideAttr since it overrides B1::f, but D::f does NOT
                # override the unrelated, non-virtual B2::f). Each candidate
                # ancestor's OWN slot must independently be virtual too.
                if not _is_virtual(
                    base_qname,
                    bm.sig_key,
                    bases_of,
                    declared,
                    ancestor_memo,
                    virtual_memo,
                ):
                    continue
                key = (m.identity, bm.identity)
                if key in seen:
                    continue
                seen.add(key)
                if m.has_override_attr:
                    confidence, resolution = (
                        CONF_HIGH,
                        RESOLUTION_OVERRIDE_CONFIRMED,
                    )
                else:
                    confidence, resolution = (
                        CONF_REDUCED,
                        RESOLUTION_OVERRIDE_SIGNATURE_MATCH,
                    )
                edges.append(
                    OverrideEdge(
                        src=m.identity,
                        dst=bm.identity,
                        confidence=confidence,
                        resolution=resolution,
                    )
                )
    return edges


def parse_clang_ast_virtual_methods(ast: dict[str, Any]) -> frozenset[str]:
    """Pure function: a ``clang -ast-dump=json`` tree -> the identity of
    EVERY method whose slot is virtual (own or inherited), regardless of
    whether it has any override candidate at all.

    :func:`parse_clang_ast_overrides` only ever visits a class that
    ``bases_of`` names (``if qname not in bases_of: continue`` — correct for
    finding override *pairs*, since a class with no base can't be the
    *overriding* side of one) and only ever emits an edge when a matching
    ancestor slot exists — so a method that IS virtual but has no override
    anywhere in the scanned codebase (a base class's own leaf virtual method
    with no override, or ANY method on a class with no base at all, e.g. the
    common ``struct Base { virtual void run(); };`` with nothing deriving
    from it in scope) never appears in that function's output at all
    (Codex review, fresh evidence: this silently made
    ``virtual_dispatch_graph.augment_graph_with_vtable_presence`` blind to
    the single most common shape of polymorphic class — one with no override
    the scanned codebase happens to also declare).

    This function answers the narrower, genuinely different question
    "is this slot virtual", independent of whether an override edge exists
    for it — reusing the exact same ``bases_of``/``declared``/``_is_virtual``
    machinery :func:`parse_clang_ast_overrides` already builds, but iterating
    over every class :func:`_collect_class_methods` found (no ``bases_of``
    membership gate) and every one of its methods.
    """
    bases_of: dict[str, list[str]] = {}
    for e in parse_clang_ast_types(ast):
        if e.kind == EDGE_TYPE_INHERITS:
            bases_of.setdefault(e.src, []).append(e.dst)

    methods_by_class = _collect_class_methods(ast)
    declared: dict[_SlotKey, _MethodInfo] = {
        (qname, m.sig_key): m
        for qname, methods in methods_by_class.items()
        for m in methods
    }
    ancestor_memo: dict[_SlotKey, list[str]] = {}
    virtual_memo: dict[_SlotKey, bool] = {}

    virtual_identities: set[str] = set()
    for qname, methods in methods_by_class.items():
        for m in methods:
            if _is_virtual(
                qname, m.sig_key, bases_of, declared, ancestor_memo, virtual_memo
            ):
                virtual_identities.add(m.identity)
    return frozenset(virtual_identities)


def parse_clang_ast_virtual_destructor_owners(ast: dict[str, Any]) -> frozenset[str]:
    """Pure function: a ``clang -ast-dump=json`` tree -> the scope-qualified
    identity of every class that directly declares its own virtual
    destructor.

    A separate, narrower pass from :func:`parse_clang_ast_virtual_methods`
    for a real reason (Codex review, fresh evidence): a destructor
    (``CXXDestructorDecl``) is deliberately excluded from
    ``_OVERRIDE_CANDIDATE_KINDS`` — override-EDGE matching for a destructor
    needs its own verified rule for the Itanium ABI's dual ``D1``/``D2``
    mangling (this module's own docstring, "Deliberately scoped OUT"), which
    this function does not attempt. But a class's own vtable *presence* only
    needs the much narrower fact "this destructor's slot is virtual" — clang
    stamps ``"virtual": true`` directly on a ``CXXDestructorDecl`` node the
    identical way it does for any other virtual method, own or inherited, no
    override-pair matching required (verified against real Clang 18 output:
    ``struct Base { virtual ~Base(); };`` alone, no override anywhere in
    scope, still carries ``"virtual": true`` on its own ``CXXDestructorDecl``
    child). Without this, a class whose *only* virtual member is its
    destructor — a common, real shape, e.g. a plugin/interface base class —
    was invisible to ``virtual_dispatch_graph.augment_graph_with_vtable_
    presence``'s existing two seeds (the override-edge seed can't see a
    destructor at all; the leaf-virtual-method seed reads
    ``parse_clang_ast_virtual_methods``'s output, which never contains one
    either). The returned identities feed a third, independent vtable-
    presence seed stamped directly onto the owning class's own
    ``record_type`` node — not routed through a ``decl://`` node the way the
    other two seeds are, since a bare, uncalled destructor declaration often
    has no ``decl://`` node in the graph at all (unlike a class's other
    members, which ``type_graph.py`` mints a node for via
    ``DECL_HAS_TYPE``), and this module's "join-only-onto-an-existing-node"
    discipline correctly declines to mint one just for this purpose — the
    owning class's own ``record_type`` node, by contrast, always exists once
    ``type_graph.py`` has walked the class at all.
    """
    owners: set[str] = set()

    def walk(node: Any, scope: list[str]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = str(node.get("name") or "")
        if kind in _RECORD_DECL_KINDS and name:
            qname = "::".join([*scope, name])
            if any(
                isinstance(child, dict)
                and child.get("kind") == "CXXDestructorDecl"
                and child.get("virtual")
                for child in node.get("inner", []) or []
            ):
                owners.add(qname)
            child_scope = [*scope, name]
            for child in node.get("inner", []) or []:
                walk(child, child_scope)
            return
        child_scope = (
            [*scope, name] if kind in _NAMESPACE_SCOPE_KINDS and name else scope
        )
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(ast, [])
    return frozenset(owners)


def augment_graph_with_overrides(
    graph: SourceGraphSummary,
    edges: list[OverrideEdge],
    virtual_methods: frozenset[str] = frozenset(),
    virtual_destructor_owners: frozenset[str] = frozenset(),
) -> int:
    """Fold possible-override edges into *graph*. Both endpoints are
    ``decl://`` (``source_decl``) nodes — the same id scheme
    ``augment_graph_with_calls``/``augment_graph_with_types`` use for a
    function/method identity, so an override edge's endpoint merges onto
    the SAME node a ``DECL_CALLS_DECL``/L4 ``SOURCE_DECLARES`` node for
    that same method already created, rather than a disconnected duplicate.
    Uses ``graph.has_node``/``add_node``/``add_edge`` (not a raw
    ``nodes.append``/``edges.append``) so a duplicate registration merges
    through the ADR-046 D2 evidence-preserving fold like every other
    extractor in this package, rather than silently bypassing it. The
    ``resolution`` label rides in ``attrs`` (``GraphEdge`` has no such
    field of its own) — the same place ``DECL_CALLS_DECL``'s ``call_kind``/
    ``resolution`` pair lives (``call_graph.augment_graph_with_calls``).

    *virtual_methods* (:func:`parse_clang_ast_virtual_methods`'s output,
    aggregated across every TU — see :attr:`ClangOverrideGraphExtractor.
    last_virtual_methods`) additionally stamps an ``is_virtual: True`` fact
    (``CONF_HIGH`` — a real, clang-confirmed structural fact, not a guess)
    onto every identity's ``decl://`` node the graph **already carries**
    (Codex review, fresh evidence): a method that is virtual but has no
    override anywhere in the scanned codebase — the single most common
    polymorphic-class shape, e.g. a base class with no override in scope, or
    ANY method on a class with no base at all — never appears as either
    endpoint of an ``OverrideEdge`` at all, so
    ``virtual_dispatch_graph.augment_graph_with_vtable_presence``'s own
    override-edge-based seeding was blind to it. Never mints a new node for
    a virtual-method identity the graph doesn't already know about (the
    same join-only-onto-an-existing-node discipline this whole graph
    family applies) — ``graph.add_node`` on an already-present id merges
    facts rather than creating a duplicate, so gating on ``has_node`` first
    is what keeps this purely additive.

    Returns the number of edges added.
    """
    added = 0
    for e in edges:
        src_id, dst_id = _decl_node_id(e.src), _decl_node_id(e.dst)
        for node_id in (src_id, dst_id):
            if not graph.has_node(node_id):
                graph.add_node(
                    GraphNode(
                        id=node_id,
                        kind="source_decl",
                        provenance="override_graph",
                        confidence=e.confidence,
                    )
                )
        before = len(graph.edges)
        graph.add_edge(
            GraphEdge(
                src=src_id,
                dst=dst_id,
                kind=EDGE_METHOD_POSSIBLE_OVERRIDE,
                provenance="override_graph",
                confidence=e.confidence,
                attrs={"resolution": e.resolution},
            )
        )
        added += len(graph.edges) - before
    for identity in virtual_methods:
        node_id = _decl_node_id(identity)
        if graph.has_node(node_id):
            graph.add_node(
                GraphNode(
                    id=node_id,
                    kind="source_decl",
                    provenance="override_graph",
                    confidence=CONF_HIGH,
                    attrs={"is_virtual": True},
                )
            )
    # A class's own virtual destructor is a third, independent vtable-
    # presence seed (see parse_clang_ast_virtual_destructor_owners's own
    # docstring for why it can't reuse the decl://-node-based seed above) --
    # stamped directly on the owning class's own record_type node, which
    # (unlike a bare, uncalled destructor's own decl:// node) always exists
    # once type_graph.py has walked the class at all.
    from ..model.graph_facts import _type_node_id

    for owner in virtual_destructor_owners:
        type_id = _type_node_id(owner)
        if graph.has_node(type_id):
            graph.add_node(
                GraphNode(
                    id=type_id,
                    kind="record_type",
                    provenance="override_graph",
                    confidence=CONF_HIGH,
                    attrs={"has_virtual_destructor": True},
                )
            )
    return added


@dataclass
class ClangOverrideGraphExtractor:
    """Shell out to ``clang`` to emit a TU's AST and parse its override edges.

    Side-effecting and compiler-dependent: only exercised on the
    ``integration`` lane. A missing ``clang`` (or a parse failure) degrades
    gracefully — extraction returns ``[]`` and records nothing (ADR-028 D3).
    Reuses ``call_graph``'s vetted parse-only argv builder (same ABI-relevant
    flag allowlist) so every AST-replay pass stays in lockstep on what is
    safe to replay.
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)
    last_jobs: int = 0
    last_elapsed_s: float = 0.0
    #: Every virtual-method identity found across the most recent
    #: :meth:`extract_from_build` call (:func:`parse_clang_ast_virtual_methods`,
    #: unioned across every TU) — a side-effecting sibling to ``diagnostics``/
    #: ``last_jobs``, not a second return value, following the same pattern
    #: those already use. Consumed by ``inline_graph_fold.fold_override_graph``
    #: to close the "leaf virtual method has no override edge at all" gap in
    #: :func:`augment_graph_with_overrides`'s ``virtual_methods`` parameter
    #: (Codex review, fresh evidence — see that function's own docstring).
    #: Mutated from :meth:`_extract_from_safe_args`, which may run inside a
    #: thread-pool worker — ``set.update`` from multiple threads is safe
    #: under CPython's GIL, and unlike ``diagnostics`` (a list, order-
    #: sensitive — see :meth:`extract_from_build`'s own docstring) a *set*
    #: has no order to get wrong in the first place, so no further fix is
    #: needed here.
    last_virtual_methods: set[str] = field(default_factory=set)
    #: Every class identity found to directly declare its own virtual
    #: destructor across the most recent :meth:`extract_from_build` call
    #: (:func:`parse_clang_ast_virtual_destructor_owners`, unioned across
    #: every TU) — same side-effecting-sibling pattern as
    #: ``last_virtual_methods``, closing the sibling gap that field's own
    #: docstring cross-references (a class whose only virtual member is its
    #: destructor was invisible to every existing vtable-presence seed).
    last_virtual_destructor_owners: set[str] = field(default_factory=set)

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def _extract_from_safe_args(
        self,
        argv: list[str],
        cwd: str | None = None,
        *,
        diagnostics: list[str] | None = None,
    ) -> list[OverrideEdge]:
        diag = self.diagnostics if diagnostics is None else diagnostics
        if not self.available():
            diag.append(f"{self.clang_bin} not found in PATH")
            return []
        ast = run_clang_ast_dump(self.clang_bin, argv, cwd=cwd, diagnostics=diag)
        if ast is None:
            return []
        try:
            edges = parse_clang_ast_overrides(ast)
            self.last_virtual_methods.update(parse_clang_ast_virtual_methods(ast))
            self.last_virtual_destructor_owners.update(
                parse_clang_ast_virtual_destructor_owners(ast)
            )
            return edges
        except (ValueError, RecursionError) as exc:
            diag.append(f"could not parse clang AST JSON: {exc}")
            return []

    def _extract_from_compile_unit(
        self, cu: BuildEvidenceCompileUnit, *, diagnostics: list[str] | None = None
    ) -> list[OverrideEdge]:
        from .call_graph import _replay_cwd, _safe_clang_args_from_compile_unit

        argv = _safe_clang_args_from_compile_unit(cu)
        return self._extract_from_safe_args(
            argv, cwd=_replay_cwd(cu), diagnostics=diagnostics
        )

    def extract_from_build(self, build: BuildEvidence) -> list[OverrideEdge]:
        """Extract override edges across every compile unit in *build*
        (best effort). Mirrors ``ClangTypeGraphExtractor.extract_from_build``
        exactly, including its cross-TU dedup-by-(src,dst) merge, and
        ``ClangCallGraphExtractor.extract_from_build``'s deterministic-
        diagnostics-ordering fix — each unit's diagnostics are collected into
        a fresh per-call list and only folded into ``self.diagnostics`` on
        the single driving thread, in ``pool.map``'s own input-ordered
        iteration."""
        from .call_graph import _call_graph_jobs, _deadline_bound_worker

        self.last_virtual_methods = set()
        self.last_virtual_destructor_owners = set()
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

        all_edges: list[OverrideEdge] = []
        seen: set[tuple[str, str]] = set()

        def add_edges(edges: list[OverrideEdge]) -> None:
            for e in edges:
                key = (e.src, e.dst)
                if key in seen:
                    continue
                seen.add(key)
                all_edges.append(e)

        def _probe(
            cu: BuildEvidenceCompileUnit,
        ) -> tuple[list[OverrideEdge], list[str]]:
            local_diagnostics: list[str] = []
            edges = self._extract_from_compile_unit(cu, diagnostics=local_diagnostics)
            return edges, local_diagnostics

        try:
            if self.last_jobs > 1 and len(units) > 1:
                pool_worker = partial(
                    _deadline_bound_worker,
                    deadline.current_deadline_ts(),
                    _probe,
                )
                with ThreadPoolExecutor(max_workers=self.last_jobs) as pool:
                    for edges, local_diagnostics in pool.map(pool_worker, units):
                        add_edges(edges)
                        self.diagnostics.extend(local_diagnostics)
            else:
                for cu in units:
                    edges, local_diagnostics = _probe(cu)
                    add_edges(edges)
                    self.diagnostics.extend(local_diagnostics)
        finally:
            self.last_elapsed_s = time.monotonic() - start

        return all_edges
