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

"""Callback/function-pointer graph augmentation (G29 Phase 5 item 4, G29.6's
fourth open graph family: :data:`~abicheck.buildsource.graph_facts.
CALLBACK_EDGE_KINDS`). Closes the "plugin/event-loop/C-API callback" blind
spot the review calls out: a public registration function that stashes a
private handler's address into a slot which is later invoked indirectly, so
neither ``call_graph.py``'s exact call resolution nor a plain header-shape
diff sees the real runtime relationship at all.

Split the same way item 3 (``virtual_dispatch_graph.py``) is, by whether new
compiler evidence is needed:

**Part A — :func:`augment_graph_with_callback_invocations`
(``CALLBACK_MAY_INVOKE``).** A pure graph transformation, needing no new
clang invocation. ``call_graph.py`` already emits a ``DECL_CALLS_DECL`` edge
(``attrs["call_kind"] == "function_pointer"``) from a calling declaration to
the pointer-holding **slot's own decl node** — the variable/parameter/field
declared as a function-pointer type, not any particular function, since the
call graph alone cannot know which function's address ended up there at
runtime. Part B below (a genuinely new clang pass) separately records which
function's address flowed into which slot, as ``DECL_REGISTERS_CALLBACK``/
``DECL_TAKES_ADDRESS_OF`` edges (function -> slot). Part A materializes the
join: for every function-pointer-kind ``DECL_CALLS_DECL`` edge (caller ->
slot) and every registration/address-of edge naming that same slot as its
``dst`` (src = a function whose address reached it), it emits
``CALLBACK_MAY_INVOKE`` from the **original calling** declaration to the
registered function — enriching the call graph's already-known "called
through this slot" fact with the concrete possible targets a real indirect
call could reach. ``resolution`` is always ``"overapprox"`` (never
``"exact"`` — the same instruction every possible-target-set edge in this
graph family follows, mirroring ``call_graph.RESOLUTION_OVERAPPROX`` and
``virtual_dispatch_graph``'s identical rule for ``VIRTUAL_CALL_MAY_DISPATCH_TO``):
this is a static approximation of a runtime decision, never a proof of which
target a given indirect call actually takes. A slot with **no** function
registered anywhere this pass examined contributes **no**
``CALLBACK_MAY_INVOKE`` edge — not a spurious self-edge and not a "definitely
unused" claim either: the callback may be registered only by a consumer
binary this scan never saw, or by code outside this pass's scope, so the fact
is genuinely unknown, not empty (the identical reasoning
``virtual_dispatch_graph.py`` already documents for a leaf virtual method
with no override candidates).

**Part B — :func:`parse_clang_ast_callbacks` (``DECL_TAKES_ADDRESS_OF`` /
``DECL_REGISTERS_CALLBACK``).** The genuinely new capability: a pure function
over a ``clang -ast-dump=json`` tree finding every place a function's address
flows into a function-pointer-typed slot — an explicit ``&func`` (a
``UnaryOperator`` with ``opcode == "&"`` over a ``DeclRefExpr`` to a
``FunctionDecl``) or the implicit function-to-pointer decay of a bare
function name (an ``ImplicitCastExpr`` with ``castKind ==
"FunctionToPointerDecay"`` over the same) — both forms confirmed empirically
against real Clang 18 output (see "Empirical AST-dump findings" below, not
assumed). Two edge kinds, by how specific the destination is known to be:

- ``DECL_REGISTERS_CALLBACK`` (``CONF_HIGH``) — the address-taking expression
  is a **direct argument of a plain ``CallExpr``**, at a position whose
  callee parameter is itself function-pointer-typed: the classic
  ``signal(SIGINT, handler)``/plugin-registration shape. This is the more
  specific, more valuable fact and the one this module's identity design is
  built around (see "Identity design" below) — an exact structural match,
  not a guess.
- ``DECL_TAKES_ADDRESS_OF`` (``CONF_REDUCED``) — a broader catch-all: the
  address flows into a variable/field of function-pointer type via a plain
  assignment (``w->cb = &handler;``) or a variable/field's own initializer
  (``handler_t g = handler;``), not necessarily as a direct call argument.
  Weaker/more general: telling "this address-of is a real callback wire-up"
  from "this address was taken to print/compare it" is out of scope (a
  documented, accepted heuristic tradeoff, same spirit as
  ``type_graph.py``'s own approximate lookups) — the type-shape check (the
  target must itself be function-pointer-*typed*) is the only filter
  applied.

Both edge kinds: ``src`` = the identity of the **function whose address was
taken**, ``dst`` = the identity of the function-pointer-typed **slot** the
address flowed into. Deliberately scoped to a **plain, free-function**
``CallExpr`` for the registration case — not ``CXXMemberCallExpr``/
``CXXOperatorCallExpr`` — matching the plugin/event-loop/C-API callback shape
this item's design brief names; a method call registering a callback through
a member function is out of scope for this slice (mirrors
``override_graph.py``'s own "constructors/destructors deliberately out of
scope for this first slice" precedent).

**Identity design — the single most important correctness property here.**
Part A's join only works when Part B's edges land on *exactly* the same
``dst`` identity ``call_graph.py`` itself already used for the same slot in
its own ``DECL_CALLS_DECL`` edge. Investigated (not assumed) by reading
``call_graph._classify_call``/``_resolve_ref_callee_identity``: a call
through a variable/parameter/field (``call_kind == "function_pointer"``)
resolves its callee identity via ``id_index.get(node_id)`` — but
``call_graph.py``'s ``id_index`` is populated **only** for
``FunctionDecl``-kind nodes (built in ``_enter_function_scope``), never for a
``VarDecl``/``ParmVarDecl``/``FieldDecl``. So the lookup always misses, and
the identity falls back to ``_identity(ref)`` — the reference *stub*'s own
bare ``mangledName or name``, with **no scope qualification at all** (a
parameter named ``h`` in two unrelated functions both resolve to the
identical bare identity ``"h"``). This module's Part B deliberately computes
the same slot identity the same way — via ``call_graph._identity`` applied to
the full ``ParmVarDecl``/``VarDecl``/``FieldDecl`` node it has in hand (a
full node and a stub compute the identical string here, since a parameter/
field/local variable essentially never carries a real ``mangledName``) —
rather than a stronger, scope-qualified identity a from-scratch design might
prefer. This is not this module inventing a weaker identity scheme: it is
mirroring an existing, already-shipped limitation of the call graph it must
join onto, so the join is *correct* (same string, same collision risk) rather
than *silently unreachable* (a stronger identity here would simply never
match anything ``call_graph.py`` itself produces). Fixing the underlying
weak identity is out of scope for this slice — see "Deferred" below.

**A real consequence of this weak identity, not merely a theoretical one:
Part A's join can produce a genuinely false ``CALLBACK_MAY_INVOKE`` edge,
not just a missing one** (Codex review, fresh evidence). Two *unrelated*
functions each declaring their own same-named function-pointer parameter
(e.g. ``void register(handler_t h)`` and ``void invoke(handler_t h)``, both
naming their parameter ``h``) collapse onto the identical ``decl://h`` slot
node — a collision that already existed in ``call_graph.py`` alone, but with
no consumer able to draw a *specific* conclusion from it. Once this
module's Part B joins a registration onto that same shared node, Part A's
join in :func:`augment_graph_with_callback_invocations` reads it back
without distinguishing which of the colliding *scopes* the registration
and the call actually belong to: a function registered only ever with
``register``'s ``h`` is reported as a possible target of a call made
through ``invoke``'s completely unrelated ``h`` — a fabricated cross-
function association, not merely an unresolved one. This is a real,
outstanding correctness gap in the current identity scheme (not
hypothetical, not covered by ``resolution: "overapprox"``'s existing
"possible dispatch target" framing, since the two slots are not actually
the same runtime object at all), and it is the sharpest concrete argument
for the scope-qualified-identity follow-up named above — a fix here alone
cannot close it, since the ambiguity originates in ``call_graph.py``'s own
``DECL_CALLS_DECL`` identity, which this module's join must match exactly
to connect at all.

**A load-bearing negative empirical finding, not a design choice of this
module's own: a struct-field-typed callback slot invoked through
member-call syntax (``w->cb(x)``) still never joins in Part A.** Originally
found (and, at the time, verified against real compiled repros) that clang
emits a ``MemberExpr``'s own callee reference as
``"referencedMemberDecl": "<node-id-string>"`` — a bare **string**, not a
nested dict node — and that ``call_graph._find_referenced_decl`` only
recognized the dict shape, falling through to resolve the *receiver*'s own
``DeclRefExpr`` instead (e.g. ``w``, not ``cb``): a **wrong** edge, not
merely a missing one. A later, separate fix (Codex review, fresh evidence,
same PR) taught ``_find_referenced_decl`` to resolve a string
``referencedMemberDecl`` id through a new ``member_index`` — but that index
is only ever populated for ``_FUNCTION_DECL_KINDS`` nodes
(``FunctionDecl``/``CXXMethodDecl``/...), the exact set that fix's own
motivating case (a virtual **method** call, ``p->f()``) needed; a
``FieldDecl`` (a plain data member, which is what a callback slot's own
declaration always is) is never one of those kinds, so it is never
indexed. The practical effect on this module's own case changed, but did
not close: re-verified against the identical repro
(``struct Widget { handler_t cb; }; void f(Widget *w) { w->cb(1); }``) after
that fix landed — the call now resolves to **no edge at all**, not the
wrong ``decl://w`` edge the earlier finding recorded. Silently dropping the
call is the objectively safer outcome (ADR-028 D3: degrade to no fact,
never a wrong one), but Part A's join against this module's own
``DECL_REGISTERS_CALLBACK``/``DECL_TAKES_ADDRESS_OF`` edges (``dst`` =
the field's own identity, ``decl://cb`` — those two remain individually
correct) still only ever fires when some *other* code path calls through
the field in a form ``call_graph.py`` resolves to the field itself (rare)
rather than through ``->``/``.`` member-call syntax (the common one).
Extending ``member_index`` to also cover data-member declarations (a
distinct, separately-scoped change to the shared upstream module, not a
drive-by extension of either fix) remains out of scope for this slice — see
"Deferred" below; documented rather than silently accepted, per this
family's own evidence-honesty discipline.

**Empirical AST-dump findings** (Ubuntu Clang 18, verified with real compiled
repros, not assumed):

1. An explicit ``&func`` argument at a call site is a ``UnaryOperator`` node
   (``opcode: "&"``) whose single ``inner`` child is a ``DeclRefExpr`` naming
   the function directly (``referencedDecl.kind == "FunctionDecl"``).
2. A bare function name passed where a function-pointer parameter is expected
   (no explicit ``&``) is an ``ImplicitCastExpr`` node
   (``castKind: "FunctionToPointerDecay"``) wrapping the identical
   ``DeclRefExpr`` shape — the two forms are structurally parallel and
   handled by the same helper (:func:`_address_taken_function`).
3. A ``CallExpr``'s own ``inner`` list is ``[callee-subexpression, arg0,
   arg1, ...]`` — the callee's own reference (after unwrapping the implicit
   decay cast clang always inserts for a function-to-pointer callee) is
   ``inner[0]``, so argument index ``i`` (0-based, counting from
   ``inner[1]``) lines up positionally with the callee's own ``i``-th
   ``ParmVarDecl`` — confirmed against a real ``signal_register(&handler)``/
   ``signal_register(bare_handler)`` compile.
4. An assignment's own ``BinaryOperator`` node (``opcode: "="``) has exactly
   two ``inner`` children, LHS then RHS — confirmed against a real
   ``w->cb = &handler;`` compile, whose LHS is a ``MemberExpr`` and RHS the
   ``UnaryOperator`` described in (1).
5. A typedef'd function-pointer type (the overwhelmingly common real-world
   spelling, e.g. ``handler_t`` for ``void (*)(int)``) reports its *bare*
   ``type.qualType`` as the typedef's own name (``"handler_t"``, not the
   underlying spelling) but always also carries ``type.desugaredQualType``
   with the real, structurally-matchable ``"void (*)(int)"`` form — confirmed
   against a real compile with a typedef'd callback type. This module's
   :func:`_effective_qualtype`/:func:`_is_function_pointer` always prefer
   ``desugaredQualType`` when present, exactly for this reason; a plain,
   non-typedef'd spelling (``void (*)(int)`` written out directly) has no
   ``desugaredQualType`` at all and falls back to ``qualType`` unchanged.

**``FUNCTION_POINTER_HAS_SIGNATURE`` — investigated, found genuinely unmet,
implemented as a node-level fact rather than a graph edge.** Checked (not
assumed) whether the pre-existing ``DECL_HAS_TYPE``/``TYPE_HAS_FIELD_TYPE``
edges ``type_graph.py`` already emits for a function-pointer-typed slot carry
its signature anywhere: they do not. ``type_graph._emit_type_edges`` resolves
a raw ``qualType`` spelling down to the *nested* type names it can find
inside it (``_resolve_nested_type_names``) — for a callback-typed field, this
means an edge to each of the callback's own parameter/return *types*
individually, each folded under the *enclosing* declaration's own role
(``"field"``/``"param"``/``"var"``/``"return"``), not a `"callback_param"`-
shaped role of its own — so the exact ordered signature spelling (which
argument is which, and the return type) is lost, and no edge attribute
anywhere records the slot's own raw type string. This is a real, unmet gap,
not a rediscovery of existing coverage (unlike item 3's ``DECL_OVERRIDES_DECL``).
Once populated, though, it does not fit this schema's edge shape: a
function-pointer's signature is a property of exactly **one** declaration
(the slot itself), not a directed relationship between two distinct graph
entities — modeling it as an edge would need either a degenerate self-loop
(``src == dst``, unlike every other edge kind here) or minting a throwaway
node purely to hold one string (rejected — a new node kind must be bounded by
genuine identity, like ``vtable``, not invented as a string container). So
``FUNCTION_POINTER_HAS_SIGNATURE`` stays registered in
``graph_facts.CALLBACK_EDGE_KINDS`` (so a hand-built or future graph naming
it directly as an edge is never rejected — the same "registered vocabulary,
satisfied a different way" pattern item 3 used for ``DECL_OVERRIDES_DECL``,
just for the opposite reason: unmet, not redundant), but the *real* data is
populated by this module as a ``function_pointer_signature`` **node-level**
fact (via :func:`~abicheck.buildsource.graph_facts.register_fact`) on the
callback slot's own ``source_decl`` node, stamped alongside a
``DECL_REGISTERS_CALLBACK``/``DECL_TAKES_ADDRESS_OF`` join in
:func:`augment_graph_with_callback_registrations` — the raw
(desugared-preferred) ``qualType`` spelling of the slot's declared type. Read
this attribute directly off the slot's node rather than looking for a
``FUNCTION_POINTER_HAS_SIGNATURE`` edge in the graph.

**Deferred, not attempted this slice** (each needs its own scoped follow-up,
not a drive-by extension here):

- Extending ``call_graph.py``'s own ``id_index``/``_resolve_ref_callee_identity``
  to cover ``VarDecl``/``ParmVarDecl``/``FieldDecl`` (a genuinely scope-
  qualified slot identity) — would strengthen every function-pointer
  ``DECL_CALLS_DECL`` edge this module's Part A already depends on, not just
  this module's own edges; a change to shared, heavily-reviewed
  infrastructure four other passes also read, not a same-slice extension.
- Extending ``call_graph.py``'s ``member_index`` (the fix that closed the
  string-vs-dict ``referencedMemberDecl`` handling for method calls, see
  above) to also index ``FieldDecl`` nodes, closing the remaining
  field-callback join gap documented above — same reasoning: shared
  infrastructure, its own scoped fix.
- ``CXXMemberCallExpr``/``CXXOperatorCallExpr`` registration detection (a
  callback registered through a method call rather than a free function) —
  deliberately out of scope for this slice, mirroring ``override_graph.py``'s
  own precedent for narrowing a first slice's AST-node-kind coverage.
- A pointer-to-member-function slot (``void (Widget::*)(int)``) is matched by
  :data:`_FUNCTION_POINTER_TYPE_RE`'s shape check the same as a plain
  function pointer, but this module makes no attempt to additionally verify
  the *owning class* of a pointer-to-member-function assignment — a narrower,
  separately-verified extension, not attempted here.
- **Propagating a callback through an intermediate parameter-to-slot
  assignment — investigated, confirmed real, deliberately not implemented.**
  Codex review flagged a registration API that stashes its own *parameter*
  (not a function) into a stored slot: ``void reg(handler_t h) { stored =
  h; }``, with the eventual indirect call made through ``stored``, not
  ``h``. Reproduced empirically end to end against real Clang 18
  (``reg(my_handler)`` as the registration call, ``stored = h;`` inside
  ``reg``, ``stored(1);`` inside a separate ``invoke``): ``call_graph.py``
  correctly emits ``DECL_CALLS_DECL(invoke -> stored, function_pointer)``,
  and this module correctly emits ``DECL_REGISTERS_CALLBACK(my_handler ->
  h)`` for the outer ``reg(my_handler)`` call — but ``stored = h;`` inside
  ``reg`` is never captured as any edge at all, since ``_address_taken_
  function`` (what :func:`_emit_assignment_address_of`'s RHS resolution
  requires) only recognizes a real function address/decay, not a
  parameter/variable/field alias. Part A's join in
  :func:`augment_graph_with_callback_invocations` therefore has no way to
  connect ``stored`` back to ``h``, so the whole chain — a real,
  runtime-reachable ``invoke -> my_handler`` relationship — produces no
  ``CALLBACK_MAY_INVOKE`` edge at all, a false negative distinct from every
  other gap documented above (those under-report a slot that itself IS
  named; here the actually-invoked slot never gets linked to the
  originally-registered function at all). A sound fix needs two new
  pieces, not one: (1) a new intra-procedural "slot aliases slot" edge for
  an assignment whose RHS is itself a function-pointer-typed
  variable/parameter/field (broadening :func:`_resolve_assignment_slot`'s
  existing LHS-only slot recognition to the RHS too), and (2) a transitive
  closure in Part A's join — propagating a registration through zero or
  more alias hops before matching against ``call_graph.py``'s
  ``DECL_CALLS_DECL`` callee identity, with its own termination/cycle
  guard (an alias chain can be arbitrarily long, and — unlike every other
  edge kind this module emits — could self-reference through a pointer
  assigned back to itself). Both pieces are a real, scoped follow-up of
  their own, not a drive-by extension of the existing single-hop join;
  attempting the transitive-closure half without careful cycle protection
  risks turning a currently-safe "degrade to no fact" gap into a
  non-terminating or a spuriously overapproximating one, which is worse
  than the status quo. Pinned by a dedicated regression test documenting
  the current, known-incomplete behavior (``test_callback_graph.py``'s
  ``test_callback_propagated_through_stored_parameter_slot_is_not_joined``)
  rather than silently accepted.
"""

from __future__ import annotations

import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from .. import deadline
from . import call_graph
from .clang_ast_run import run_clang_ast_dump
from .graph_facts import CONF_HIGH, CONF_REDUCED, GraphEdge, GraphNode, register_fact

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .build_evidence import BuildEvidence, CompileUnit as BuildEvidenceCompileUnit

EDGE_DECL_TAKES_ADDRESS_OF = "DECL_TAKES_ADDRESS_OF"
EDGE_DECL_REGISTERS_CALLBACK = "DECL_REGISTERS_CALLBACK"
EDGE_CALLBACK_MAY_INVOKE = "CALLBACK_MAY_INVOKE"

RESOLUTION_OVERAPPROX = "overapprox"

#: A plain function-pointer or pointer-to-member-function declarator inside a
#: (possibly desugared) ``qualType`` spelling: ``"(*)("`` (``void (*)(int)``)
#: or ``"(Owner::*)("`` (``void (Owner::*)(int)``), optionally with a
#: top-level cv-qualifier on the pointer itself (``"(*const)("``/
#: ``"(*volatile)("``/``"(*const volatile)("``, ``void reg(H const h)``'s
#: desugared spelling — Codex review, fresh evidence, verified against real
#: Clang 18 output for a typedef'd function-pointer parameter declared
#: ``const``). Best-effort textual matching, mirroring ``type_graph.py``'s
#: own accepted approximate-lookup tradeoff — not a real declarator parse.
_FUNCTION_POINTER_TYPE_RE = re.compile(
    r"\(\s*(?:[\w:]+::\s*)?\*\s*(?:const\s*|volatile\s*)*\)\s*\("
)

#: clang AST decl kinds that introduce a callable scope, mirroring
#: ``call_graph._FUNCTION_DECL_KINDS`` (duplicated rather than imported: the
#: two modules are siblings with no cross-dependency on this particular
#: constant today, matching the existing duplication convention
#: ``type_graph.py``/``macro_graph.py`` already use for their own copies).
_FUNCTION_DECL_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)
_RECORD_DECL_KINDS = frozenset(
    {"CXXRecordDecl", "RecordDecl", "ClassTemplateSpecializationDecl"}
)


def _effective_qualtype(type_obj: Any) -> str:
    """The most structurally-matchable spelling of a clang ``"type"`` object:
    ``desugaredQualType`` when present (a typedef'd function-pointer type
    resolves to its real ``void (*)(...)`` shape — empirical finding 5 in the
    module docstring), else the bare ``qualType``."""
    if not isinstance(type_obj, dict):
        return ""
    return str(type_obj.get("desugaredQualType") or type_obj.get("qualType") or "")


def _is_function_pointer(type_obj: Any) -> bool:
    """Whether a clang ``"type"`` object denotes a function-pointer (or
    pointer-to-member-function) type, by best-effort textual shape."""
    return bool(_FUNCTION_POINTER_TYPE_RE.search(_effective_qualtype(type_obj)))


@dataclass(frozen=True)
class _ParamInfo:
    """One callee parameter's identity/type, as needed to test whether a call
    argument at the matching position registers a callback into it."""

    identity: str
    qual_type: str
    is_function_pointer: bool


@dataclass(frozen=True)
class _CallbackIndexes:
    """The whole-TU indexes :func:`_index_callback_targets` builds and
    :func:`_walk_emit` reads, mirroring ``type_graph._AstIndexes``'s bundling
    pattern (one argument instead of three, all mutated in place)."""

    #: clang node id (of a FunctionDecl-like node) -> its own real identity.
    id_index: dict[str, str]
    #: clang node id (of a FunctionDecl-like node) -> its ordered parameters.
    callee_params: dict[str, list[_ParamInfo]]
    #: clang node id (of a FieldDecl) -> the full FieldDecl node, so a
    #: MemberExpr's own ``referencedMemberDecl`` (a bare node-id *string* in
    #: real clang output, not a nested dict — see the module docstring's
    #: "load-bearing negative empirical finding") can still resolve back to
    #: its full declaration for this module's own (correct, independent)
    #: assignment-target detection.
    field_by_id: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class CallbackEdge:
    """One function-address-flows-into-a-slot fact extracted from a clang AST."""

    src: str  # the function whose address was taken
    dst: str  # the function-pointer-typed slot it flowed into
    kind: str  # EDGE_DECL_REGISTERS_CALLBACK | EDGE_DECL_TAKES_ADDRESS_OF
    confidence: str = CONF_REDUCED
    #: The slot's own (desugared-preferred) qualType spelling — feeds
    #: FUNCTION_POINTER_HAS_SIGNATURE (see module docstring).
    signature: str = ""


def _index_callback_targets(ast: dict[str, Any]) -> _CallbackIndexes:
    """First pass: index every function-like declaration's identity/params
    and every field declaration, across the whole TU, before any edge is
    built — mirrors ``type_graph``'s/``macro_graph``'s established
    two-pass shape (index everything, then walk for edges)."""
    id_index: dict[str, str] = {}
    callee_params: dict[str, list[_ParamInfo]] = {}
    field_by_id: dict[str, dict[str, Any]] = {}
    _index_walk(ast, [], id_index, callee_params, field_by_id)
    return _CallbackIndexes(id_index, callee_params, field_by_id)


def _index_walk(
    node: Any,
    scope: list[str],
    id_index: dict[str, str],
    callee_params: dict[str, list[_ParamInfo]],
    field_by_id: dict[str, dict[str, Any]],
) -> None:
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")

    if kind in _FUNCTION_DECL_KINDS:
        node_id = str(node.get("id") or "")
        ident = call_graph._function_identity(node, scope)
        if node_id and ident:
            id_index.setdefault(node_id, ident)
            params: list[_ParamInfo] = []
            for child in node.get("inner", []) or []:
                if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
                    type_obj = child.get("type")
                    # An unnamed parameter (`void reg(void (*)(int));`, a
                    # common prototype-only registration-API shape) has no
                    # `mangledName`/`name` for call_graph._identity to read,
                    # so it resolves to "" -- every unnamed callback slot in
                    # the whole codebase would then collapse onto the SAME
                    # decl://  node, making the otherwise high-confidence
                    # registration edge ambiguous across unrelated APIs
                    # (Codex review, fresh evidence). An unnamed parameter
                    # can never be referenced by name from within its own
                    # function body either, so nothing else in this module
                    # (or call_graph.py's own function-pointer DECL_CALLS_
                    # DECL resolution) can ever need this identity to MATCH
                    # anything -- it only needs to be stably unique per
                    # callee, which the callee's own identity plus this
                    # parameter's 0-based position provides.
                    param_ident = call_graph._identity(child) or (
                        f"{ident}#param{len(params)}"
                    )
                    params.append(
                        _ParamInfo(
                            param_ident,
                            _effective_qualtype(type_obj),
                            _is_function_pointer(type_obj),
                        )
                    )
            callee_params[node_id] = params
        for child in node.get("inner", []) or []:
            _index_walk(child, scope, id_index, callee_params, field_by_id)
        return

    if kind in _RECORD_DECL_KINDS:
        # An unnamed struct/union still owns real FieldDecl children whose
        # own id a MemberExpr can reference (CodeRabbit review, fresh
        # evidence) -- gating field indexing on `name` truthy skipped an
        # anonymous record's fields entirely, so a callback slot inside one
        # (`union { handler_t cb; };`) could never resolve. Its own scope
        # only extends with a name when it has one; an unnamed record
        # contributes no extra qualification level of its own.
        child_scope = [*scope, name] if name else scope
        for child in node.get("inner", []) or []:
            if isinstance(child, dict) and child.get("kind") == "FieldDecl":
                field_id = str(child.get("id") or "")
                if field_id:
                    field_by_id[field_id] = child
        for child in node.get("inner", []) or []:
            _index_walk(child, child_scope, id_index, callee_params, field_by_id)
        return

    if kind == "NamespaceDecl" and name:
        for child in node.get("inner", []) or []:
            _index_walk(child, [*scope, name], id_index, callee_params, field_by_id)
        return

    for child in node.get("inner", []) or []:
        _index_walk(child, scope, id_index, callee_params, field_by_id)


def _resolve_func_ref(node: Any, id_index: dict[str, str]) -> str | None:
    """Shallow DFS for the first ``DeclRefExpr`` naming a function, honoring
    ``id_index`` (built by :func:`_index_walk`, the same identity a plain
    call-target reference resolves to elsewhere) over the reference stub's
    own (weaker) identity. Bounded to *node*'s own subtree, not the whole
    AST, and stops descending at the first match -- this is intentionally
    the same shallow shape as ``call_graph._find_referenced_decl``, applied
    to a narrower (single wrapper node's) subtree."""
    if not isinstance(node, dict):
        return None
    if node.get("kind") == "DeclRefExpr":
        ref = node.get("referencedDecl")
        if isinstance(ref, dict) and ref.get("kind") in _FUNCTION_DECL_KINDS:
            node_id = str(ref.get("id") or "")
            return id_index.get(node_id) or call_graph._identity(ref)
        return None
    for child in node.get("inner", []) or []:
        found = _resolve_func_ref(child, id_index)
        if found:
            return found
    return None


#: Explicit cast node kinds that can wrap a function-to-pointer decay without
#: changing which function's address is being taken -- a C-style cast
#: (``(handler_t)my_handler``) or any of C++'s named casts
#: (``static_cast``/``reinterpret_cast``/``const_cast``, and a functional-
#: style cast ``handler_t(my_handler)``). Confirmed empirically against real
#: Clang 18 output for both ``(handler_t)my_handler`` (a ``CStyleCastExpr``,
#: ``castKind == "NoOp"``, wrapping the identical ``ImplicitCastExpr``/
#: ``FunctionToPointerDecay`` this function already recognized) and
#: ``static_cast``/``reinterpret_cast`` (``CXXStaticCastExpr``/
#: ``CXXReinterpretCastExpr`` over the same shape) -- see
#: ``_address_taken_function``'s own docstring.
_EXPLICIT_CAST_KINDS = frozenset(
    {
        "CStyleCastExpr",
        "CXXStaticCastExpr",
        "CXXReinterpretCastExpr",
        "CXXConstCastExpr",
        "CXXFunctionalCastExpr",
        "CXXDynamicCastExpr",
    }
)


def _address_taken_function(node: Any, id_index: dict[str, str]) -> str | None:
    """Whether *node* is a function-address-taking expression (empirical
    findings 1/2 in the module docstring): an explicit ``&func``
    (``UnaryOperator``, ``opcode == "&"``) or an implicit function-to-pointer
    decay of a bare function name (``ImplicitCastExpr``,
    ``castKind == "FunctionToPointerDecay"``). Returns the addressed
    function's identity, or ``None`` when *node* is neither shape (including
    an address-of/decay of something other than a function -- e.g. taking
    the address of an ordinary variable, which is not a callback wire-up).

    Also unwraps an explicit cast around either shape (Codex review, fresh
    evidence): a callback argument converted to its target function-pointer
    type -- ``register_cb((handler_t)handler)`` or
    ``register_cb(static_cast<handler_t>(handler))`` -- wraps the same
    ``FunctionToPointerDecay`` in a ``CStyleCastExpr``/``CXX*CastExpr``
    (:data:`_EXPLICIT_CAST_KINDS`), which an earlier version of this function
    did not unwrap (only ``ParenExpr``), silently omitting the registration
    for any API that requires or commonly receives a cast callback argument.

    Also unwraps a single-element ``InitListExpr`` (Codex review, fresh
    evidence): C++ list-initialization of a function-pointer-typed variable
    (``handler_t slot{my_handler};``) wraps the identical
    ``ImplicitCastExpr``/``FunctionToPointerDecay`` inside an
    ``InitListExpr`` — verified against real Clang 18 output. Only a
    single-element list is ever unwrapped: a scalar (function-pointer)
    type-checks to exactly one initializer in valid C++, so this can never
    accidentally swallow a real aggregate/multi-element list.
    """
    if not isinstance(node, dict):
        return None
    kind = node.get("kind")
    if kind == "UnaryOperator" and node.get("opcode") == "&":
        return _resolve_func_ref(node, id_index)
    if kind == "ImplicitCastExpr" and node.get("castKind") == "FunctionToPointerDecay":
        return _resolve_func_ref(node, id_index)
    if kind == "ParenExpr" or kind in _EXPLICIT_CAST_KINDS:
        inner = node.get("inner") or []
        if inner and isinstance(inner[0], dict):
            return _address_taken_function(inner[0], id_index)
    if kind == "InitListExpr":
        inner = node.get("inner") or []
        if len(inner) == 1 and isinstance(inner[0], dict):
            return _address_taken_function(inner[0], id_index)
    return None


def _callee_decl_id(callee_node: Any) -> str | None:
    """The clang node id of a ``CallExpr``'s own callee declaration (bounded
    DFS over the callee sub-expression only -- see :func:`_emit_call_registrations`
    for why this is always safe to call with just that one child)."""
    if not isinstance(callee_node, dict):
        return None
    if callee_node.get("kind") == "DeclRefExpr":
        ref = callee_node.get("referencedDecl")
        if isinstance(ref, dict) and ref.get("kind") in _FUNCTION_DECL_KINDS:
            node_id = str(ref.get("id") or "")
            return node_id or None
        return None
    for child in callee_node.get("inner", []) or []:
        found = _callee_decl_id(child)
        if found:
            return found
    return None


def _emit_call_registrations(
    call_node: dict[str, Any], idx: _CallbackIndexes, edges: list[CallbackEdge]
) -> None:
    """Emit ``DECL_REGISTERS_CALLBACK`` for every address-taking argument of a
    plain ``CallExpr`` landing on a function-pointer-typed parameter
    (empirical finding 3: argument position lines up 1:1, after the callee,
    with the callee's own ordered ``ParmVarDecl`` children)."""
    children = call_node.get("inner") or []
    if not children:
        return
    callee_id = _callee_decl_id(children[0])
    if not callee_id:
        return
    params = idx.callee_params.get(callee_id)
    if not params:
        return
    for i, arg in enumerate(children[1:]):
        if i >= len(params):
            break
        p = params[i]
        if not p.is_function_pointer:
            continue
        fn_ident = _address_taken_function(arg, idx.id_index)
        if not fn_ident:
            continue
        edges.append(
            CallbackEdge(
                fn_ident,
                p.identity,
                EDGE_DECL_REGISTERS_CALLBACK,
                CONF_HIGH,
                p.qual_type,
            )
        )


def _resolve_assignment_slot(
    node: Any, idx: _CallbackIndexes
) -> tuple[str, str] | None:
    """The ``(identity, qual_type)`` of an assignment/initializer target,
    when it is a function-pointer-typed variable/parameter/field -- ``None``
    otherwise (including a real variable/field target whose own type is not
    function-pointer-shaped, e.g. an ordinary ``int`` assignment)."""
    if not isinstance(node, dict):
        return None
    kind = node.get("kind")
    if kind == "DeclRefExpr":
        ref = node.get("referencedDecl")
        if not (
            isinstance(ref, dict) and ref.get("kind") in ("VarDecl", "ParmVarDecl")
        ):
            return None
        if not _is_function_pointer(node.get("type")):
            return None
        return call_graph._identity(ref), _effective_qualtype(node.get("type"))
    if kind == "MemberExpr":
        member = node.get("referencedMemberDecl")
        field_node: dict[str, Any] | None = None
        if isinstance(member, dict) and member.get("kind") == "FieldDecl":
            field_node = member
        elif isinstance(member, str):
            field_node = idx.field_by_id.get(member)
        if field_node is None:
            return None
        if not _is_function_pointer(node.get("type")):
            return None
        return call_graph._identity(field_node), _effective_qualtype(node.get("type"))
    return None


def _emit_assignment_address_of(
    node: dict[str, Any], idx: _CallbackIndexes, edges: list[CallbackEdge]
) -> None:
    """Emit ``DECL_TAKES_ADDRESS_OF`` for a plain assignment
    (``w->cb = &handler;``) whose LHS is a function-pointer-typed
    variable/field and whose RHS takes a function's address (empirical
    finding 4)."""
    children = node.get("inner") or []
    if len(children) != 2:
        return
    lhs, rhs = children
    slot = _resolve_assignment_slot(lhs, idx)
    if slot is None:
        return
    fn_ident = _address_taken_function(rhs, idx.id_index)
    if not fn_ident:
        return
    slot_ident, slot_qual = slot
    edges.append(
        CallbackEdge(
            fn_ident, slot_ident, EDGE_DECL_TAKES_ADDRESS_OF, CONF_REDUCED, slot_qual
        )
    )


def _emit_init_address_of(
    node: dict[str, Any], idx: _CallbackIndexes, edges: list[CallbackEdge]
) -> None:
    """Emit ``DECL_TAKES_ADDRESS_OF`` for a ``VarDecl``/``FieldDecl`` whose
    own initializer takes a function's address (``handler_t g = handler;``,
    or a field's default member initializer). The initializer, when present,
    is always this node's last child; a declaration with no initializer (the
    common case) has its last child be something else entirely (or no
    children at all), which :func:`_address_taken_function` correctly
    rejects -- no separate "has an initializer" check is needed."""
    name = node.get("name")
    if not name or not _is_function_pointer(node.get("type")):
        return
    children = node.get("inner") or []
    if not children:
        return
    fn_ident = _address_taken_function(children[-1], idx.id_index)
    if not fn_ident:
        return
    edges.append(
        CallbackEdge(
            fn_ident,
            call_graph._identity(node),
            EDGE_DECL_TAKES_ADDRESS_OF,
            CONF_REDUCED,
            _effective_qualtype(node.get("type")),
        )
    )


def _walk_emit(node: Any, idx: _CallbackIndexes, edges: list[CallbackEdge]) -> None:
    if not isinstance(node, dict):
        return
    kind = node.get("kind")
    # Deliberately a plain CallExpr only, not CXXMemberCallExpr/
    # CXXOperatorCallExpr -- see the module docstring's "Deferred" section.
    if kind == "CallExpr":
        _emit_call_registrations(node, idx, edges)
    elif kind == "BinaryOperator" and node.get("opcode") == "=":
        _emit_assignment_address_of(node, idx, edges)
    elif kind in ("VarDecl", "FieldDecl"):
        _emit_init_address_of(node, idx, edges)
    for child in node.get("inner", []) or []:
        _walk_emit(child, idx, edges)


def _dedupe_callback_edges(edges: list[CallbackEdge]) -> list[CallbackEdge]:
    """De-duplicate by ``(src, dst, kind)``, keeping first-seen order --
    mirrors ``call_graph._dedupe_edges``/``type_graph._dedupe_edges``."""
    seen: set[tuple[str, str, str]] = set()
    out: list[CallbackEdge] = []
    for e in edges:
        key = (e.src, e.dst, e.kind)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def parse_clang_ast_callbacks(ast: dict[str, Any]) -> list[CallbackEdge]:
    """Extract callback registration/address-of edges from a
    ``clang -ast-dump=json`` tree (pure). See the module docstring for the
    two edge kinds, the identity design, and the empirical AST-shape
    findings this is grounded in.
    """
    idx = _index_callback_targets(ast)
    edges: list[CallbackEdge] = []
    _walk_emit(ast, idx, edges)
    return _dedupe_callback_edges(edges)


# ── Part A: CALLBACK_MAY_INVOKE ─────────────────────────────────────────────


@dataclass
class CallbackDispatchResult:
    """What one :func:`augment_graph_with_callback_invocations` pass did."""

    function_pointer_call_edges_seen: int = 0
    dispatch_edges_added: int = 0


def augment_graph_with_callback_invocations(
    graph: SourceGraphSummary,
) -> CallbackDispatchResult:
    """Fold ``CALLBACK_MAY_INVOKE`` edges into *graph* (Part A -- see the
    module docstring for the full join and its "no fact for an unregistered
    slot" rule). Pure graph transformation: reads ``DECL_CALLS_DECL``/
    ``DECL_REGISTERS_CALLBACK``/``DECL_TAKES_ADDRESS_OF`` edges *graph
    already carries*, mints no new node."""
    result = CallbackDispatchResult()

    # slot decl id -> [(registered function decl id, registration edge kind)]
    registrants_of: dict[str, list[tuple[str, str]]] = {}
    for e in graph.edges:
        if e.kind in (EDGE_DECL_REGISTERS_CALLBACK, EDGE_DECL_TAKES_ADDRESS_OF):
            registrants_of.setdefault(e.dst, []).append((e.src, e.kind))

    # Snapshot before folding: this loop calls graph.add_edge() below, and
    # every edge it adds is itself kind CALLBACK_MAY_INVOKE -- always
    # rejected by the "!= DECL_CALLS_DECL" guard above, so iterating the
    # live list is safe today, but a future edge kind that passed the guard
    # would feed the loop its own output (Codex review, fresh evidence).
    for e in list(graph.edges):
        if e.kind != "DECL_CALLS_DECL":
            continue
        if e.resolved.get("call_kind") != "function_pointer":
            continue
        result.function_pointer_call_edges_seen += 1
        candidates = registrants_of.get(e.dst)
        if not candidates:
            continue  # unregistered slot: no fact to represent (see docstring)
        for fn_id, registration_kind in candidates:
            before = len(graph.edges)
            graph.add_edge(
                GraphEdge(
                    src=e.src,
                    dst=fn_id,
                    kind=EDGE_CALLBACK_MAY_INVOKE,
                    provenance="callback_graph",
                    confidence=CONF_REDUCED,
                    attrs={
                        "resolution": RESOLUTION_OVERAPPROX,
                        "slot": e.dst,
                        # The same caller can reach the same registered
                        # function through two DIFFERENT slots (`a` and `b`
                        # both hold `h`) -- without a role, both edges share
                        # the identical (src, dst, kind) relation_key
                        # (ADR-046 D1) and SourceGraphSummary.add_edge's
                        # dedup-on-relation-key collapses the second into
                        # the first, silently dropping one real dispatch
                        # path (Codex review, fresh evidence; reproduced
                        # empirically: only 1 of 2 expected edges survived).
                        # The slot id is itself the natural role here.
                        "role": e.dst,
                        "registration_kind": registration_kind,
                    },
                )
            )
            result.dispatch_edges_added += len(graph.edges) - before

    return result


# ── Part B: DECL_REGISTERS_CALLBACK / DECL_TAKES_ADDRESS_OF ────────────────


@dataclass
class CallbackRegistrationResult:
    """What one :func:`augment_graph_with_callback_registrations` pass did.

    No ``edges_skipped_unjoined`` field: since that function now mints a
    missing endpoint's ``source_decl`` node rather than skipping the edge
    (Codex review, fresh evidence — see its own docstring), every edge it
    is given always joins; a permanently-zero counter would be dead
    bookkeeping, not real coverage information.
    """

    edges_seen: int = 0
    edges_joined: int = 0


def augment_graph_with_callback_registrations(
    graph: SourceGraphSummary, edges: list[CallbackEdge]
) -> CallbackRegistrationResult:
    """Fold Part B's ``DECL_REGISTERS_CALLBACK``/``DECL_TAKES_ADDRESS_OF``
    edges into *graph*. Also stamps the slot's own
    ``function_pointer_signature`` node-level fact (see the module
    docstring's ``FUNCTION_POINTER_HAS_SIGNATURE`` section) whenever a join
    succeeds and the edge carries a non-empty signature.

    **Mints a ``source_decl`` node for either endpoint missing one, rather
    than skipping the edge** (Codex review, fresh evidence) — a real, and
    common, gap in an earlier version's strict join-only-onto-an-existing-
    node behavior: a private handler used *only* as a callback (never
    itself called, so ``call_graph.py`` never creates a node for it either)
    or a registration API's own callback parameter (never a standalone
    type-graph node — ``type_graph.py`` doesn't mint one for a bare
    parameter declaration on its own) routinely has **no** pre-existing
    node for either side of a ``DECL_REGISTERS_CALLBACK``/
    ``DECL_TAKES_ADDRESS_OF`` edge, silently dropping the exact
    plugin/event-loop/C-API registration pattern this family exists to
    capture — the whole feature was live only for the narrower case where
    some *other* pass happened to have already created both nodes for an
    unrelated reason. This module's own AST pass (:func:`parse_clang_ast_callbacks`)
    already has complete, real information about both declarations (their
    exact identity, from the same full AST node — not a guess or an
    inference), so minting here is the same "mint for this producer's own,
    fully-resolved edge endpoints" precedent ``override_graph.
    augment_graph_with_overrides`` already establishes in this exact
    module family, not a new, weaker exception to it. ``CONF_REDUCED`` for a
    freshly-minted node (this module alone vouches for its existence,
    unlike an already-multiply-confirmed node) — merges into an existing
    node's own (possibly higher) confidence via the standard evidence-
    preserving fold when one is already present.
    """
    from ..model.graph_facts import _decl_node_id

    result = CallbackRegistrationResult()

    for e in edges:
        result.edges_seen += 1
        src_id, dst_id = _decl_node_id(e.src), _decl_node_id(e.dst)
        for node_id in (src_id, dst_id):
            if not graph.has_node(node_id):
                graph.add_node(
                    GraphNode(
                        id=node_id,
                        kind="source_decl",
                        provenance="callback_graph",
                        confidence=CONF_REDUCED,
                    )
                )
        dst_node = next(n for n in graph.nodes if n.id == dst_id)
        before = len(graph.edges)
        graph.add_edge(
            GraphEdge(
                src=src_id,
                dst=dst_id,
                kind=e.kind,
                provenance="callback_graph",
                confidence=e.confidence,
                attrs={"signature": e.signature} if e.signature else {},
            )
        )
        result.edges_joined += len(graph.edges) - before
        if e.signature:
            register_fact(
                dst_node,
                "callback_graph",
                CONF_HIGH,
                {"function_pointer_signature": e.signature},
            )

    return result


# ── live clang extraction (integration only) ────────────────────────────────


@dataclass
class ClangCallbackGraphExtractor:
    """Shell out to ``clang`` to emit a TU's AST and parse its callback
    registration/address-of edges.

    Side-effecting and compiler-dependent: only exercised on the
    ``integration`` lane. A missing ``clang`` (or a parse failure) degrades
    gracefully -- extraction returns ``[]`` and records nothing (ADR-028 D3).
    Mirrors ``type_graph.ClangTypeGraphExtractor``'s shape exactly, reusing
    ``call_graph``'s vetted argv-sanitizing/deadline/parallelism helpers.
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)
    last_jobs: int = 0
    last_elapsed_s: float = 0.0

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def _extract_from_safe_args(
        self,
        argv: list[str],
        cwd: str | None = None,
        *,
        diagnostics: list[str] | None = None,
    ) -> list[CallbackEdge]:
        diag = self.diagnostics if diagnostics is None else diagnostics
        if not self.available():
            diag.append(f"{self.clang_bin} not found in PATH")
            return []
        ast = run_clang_ast_dump(self.clang_bin, argv, cwd=cwd, diagnostics=diag)
        if ast is None:
            return []
        try:
            return parse_clang_ast_callbacks(ast)
        except (ValueError, RecursionError) as exc:
            diag.append(f"could not parse clang AST JSON: {exc}")
            return []

    def _extract_from_compile_unit(
        self, cu: BuildEvidenceCompileUnit, *, diagnostics: list[str] | None = None
    ) -> list[CallbackEdge]:
        argv = call_graph._safe_clang_args_from_compile_unit(cu)
        return self._extract_from_safe_args(
            argv, cwd=call_graph._replay_cwd(cu), diagnostics=diagnostics
        )

    def extract_from_build(self, build: BuildEvidence) -> list[CallbackEdge]:
        """Extract callback edges across every compile unit in *build* (best
        effort). See ``call_graph.ClangCallGraphExtractor.extract_from_build``'s
        docstring for why each unit's diagnostics are collected into a fresh
        per-call list and only folded into ``self.diagnostics`` on the single
        driving thread, in ``pool.map``'s own input-ordered iteration.
        """
        start = time.monotonic()
        units = [cu for cu in build.compile_units if cu.source]
        self.last_jobs = call_graph._call_graph_jobs(len(units))
        if not units:
            self.last_elapsed_s = 0.0
            return []
        if not self.available():
            self.diagnostics.append(f"{self.clang_bin} not found in PATH")
            self.last_elapsed_s = time.monotonic() - start
            return []

        all_edges: list[CallbackEdge] = []
        seen: set[tuple[str, str, str]] = set()

        def add_edges(edges: list[CallbackEdge]) -> None:
            for e in edges:
                key = (e.src, e.dst, e.kind)
                if key not in seen:
                    seen.add(key)
                    all_edges.append(e)

        def _probe(
            cu: BuildEvidenceCompileUnit,
        ) -> tuple[list[CallbackEdge], list[str]]:
            local_diagnostics: list[str] = []
            edges = self._extract_from_compile_unit(cu, diagnostics=local_diagnostics)
            return edges, local_diagnostics

        try:
            if self.last_jobs > 1 and len(units) > 1:
                pool_worker = partial(
                    call_graph._deadline_bound_worker,
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
