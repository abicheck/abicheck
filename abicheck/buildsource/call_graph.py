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

"""Optional Clang direct-call extraction for the L5 graph (ADR-031 D4, phase 6).

Call graphs for real C++ are *approximate* — virtual dispatch, function
pointers, templates, and LTO all defeat exact static resolution — so every call
edge is explicitly labelled with a ``call_kind`` and a ``resolution`` confidence
(ADR-031 D4, D9). A call-graph difference can *explain* implementation impact;
per ADR-031 D6 it never decides ABI breakage on its own.

This module is split so the hard part stays testable:

- :func:`parse_clang_ast_calls` is a **pure function** over a
  ``clang -Xclang -ast-dump=json`` tree (a plain dict). It is exercised by unit
  tests against captured AST fixtures — no compiler required.
- :class:`ClangCallGraphExtractor` is the thin, side-effecting wrapper that
  shells out to ``clang`` for a translation unit and feeds the parser. It is
  only run on the ``integration`` lane (it needs a real ``clang``); a missing
  compiler degrades gracefully, exactly like the L4 source extractors.
- :func:`augment_graph_with_calls` folds the resulting edges into a
  :class:`~abicheck.model.source_graph.SourceGraphSummary`.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from .. import deadline
from ..build_context import _extract_flags
from ..model.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    CONF_UNKNOWN,
    GraphEdge,
    GraphNode,
)
from ..model.source_graph import function_decl_identity
from .adapters.base import source_from_argv
from .clang_ast_run import run_clang_ast_dump
from .source_graph_build import project_source_files
from .source_graph_build_source_abi import _file_in_project

__all__ = ["_file_in_project", "project_source_files"]
# _file_in_project/project_source_files are defined in source_graph.py (moved
# there so source_graph.build_source_graph can call project_source_files(build)
# without a source_graph -> call_graph -> source_graph import cycle — this
# module already imports several names from source_graph at module level, so
# importing these two from there instead of defining them here is a free
# direction, not a new edge). Re-exported by name here for back-compat:
# type_graph.py's function-local import and inline_graph_fold.py's
# module-level import both still spell it `from .call_graph import ...`.

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .build_evidence import BuildEvidence, CompileUnit as BuildEvidenceCompileUnit

_log = logging.getLogger(__name__)

# ── call-edge labels (ADR-031 D4) ───────────────────────────────────────────
CALL_KIND_DIRECT = "direct"
CALL_KIND_VIRTUAL = "virtual"
CALL_KIND_FUNCTION_POINTER = "function_pointer"
CALL_KIND_TEMPLATE = "template_instantiation"
CALL_KIND_UNKNOWN = "unknown"

RESOLUTION_EXACT = "exact"
RESOLUTION_OVERAPPROX = "overapprox"
RESOLUTION_UNKNOWN = "unknown"

#: clang AST node kinds that introduce a callable scope (the "caller").
_FUNCTION_DECL_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)
#: clang AST node kinds that represent a call site.
_CALL_EXPR_KINDS = frozenset({"CallExpr", "CXXMemberCallExpr", "CXXOperatorCallExpr"})
#: referenced-decl kinds that mean "called through a pointer/variable".
_POINTER_DECL_KINDS = frozenset(
    {"VarDecl", "ParmVarDecl", "FieldDecl", "NonTypeTemplateParmDecl"}
)
#: clang AST decl kinds that open a named scope contributing to a qualified
#: name — mirrors ``type_graph._SCOPE_DECL_KINDS`` (duplicated rather than
#: imported: the two modules are siblings with no cross-dependency today).
_SCOPE_DECL_KINDS = frozenset(
    {"NamespaceDecl", "CXXRecordDecl", "RecordDecl", "ClassTemplateSpecializationDecl"}
)

#: ABI/API-affecting flags safe to replay into clang for AST parsing.  This is
#: intentionally narrower than the original compile command: flags such as
#: ``-Xclang -load`` and ``-fplugin=`` can execute arbitrary shared libraries
#: during compiler option processing, so live call-graph extraction rebuilds a
#: parse-only command from normalized build evidence instead of appending raw
#: compile database argv.
_SAFE_REPLAY_FLAG_PREFIXES: tuple[str, ...] = (
    "-fvisibility",
    "-fvisibility-inlines-hidden",
    "-fpack-struct",
    "/Zp",
    "-fshort-enums",
    "-fshort-wchar",
    "-fabi-version",
    "-fno-rtti",
    "-frtti",
    "-fno-exceptions",
    "-fexceptions",
    "-flto",
    "-fno-lto",
    "-fwhole-program-vtables",
    "-mabi=",
    "-m32",
    "-m64",
    "/arch:",
)

_LANGUAGE_TO_CLANG_X: dict[str, str] = {
    "C": "c",
    "CXX": "c++",
    "OBJC": "objective-c",
    "OBJCXX": "objective-c++",
    "CUDA": "cuda",
}


@dataclass(frozen=True)
class CallEdge:
    """One static call edge, with its approximation labels (ADR-031 D4)."""

    caller: str  # callee/caller identity: mangled name else qualified name
    callee: str
    call_kind: str = CALL_KIND_DIRECT
    resolution: str = RESOLUTION_EXACT
    #: Source file the *caller* is defined in (clang AST loc, sticky-tracked). Used
    #: to mark a decl ``defined_in_project`` from source-location provenance — a
    #: function whose body lives in a project compile-unit source, not a
    #: third-party/system header (ADR-035 D4 / Codex review).
    caller_file: str = ""
    #: Source file the *callee*'s declaration sits in (from its referencedDecl
    #: loc, when clang emits one). Lets a leaf project helper seen only as a
    #: callee still earn ``defined_in_project`` (Codex review).
    callee_file: str = ""

    def confidence(self) -> str:
        """Map the resolution onto a graph confidence label (ADR-031 D9)."""
        if self.resolution == RESOLUTION_EXACT:
            return CONF_HIGH
        if self.resolution == RESOLUTION_OVERAPPROX:
            return CONF_REDUCED
        return CONF_UNKNOWN


def _identity(node: dict[str, Any]) -> str:
    """Stable callee/caller identity: the mangled name when clang emits one
    (encodes the full signature, keeps overloads distinct), else the name."""
    return _normalize_mangled(str(node.get("mangledName") or node.get("name") or ""))


def _normalize_mangled(mangled: str) -> str:
    """Strip a spurious macOS Mach-O ABI leading underscore from an Itanium
    mangled name clang reports (``__ZN...`` -> ``_ZN...``).

    On Darwin, clang's own AST dump reports a C++ decl's ``mangledName`` with
    the platform's extra linker-symbol-table underscore still attached (the
    same decoration ``macho_metadata.py`` strips when parsing the *binary*
    export table) -- but the ``Function``/``Variable`` objects this identity
    must join against (``header_graph._decl_identity``, seeded from the flat
    ``AbiSnapshot``) carry the already-stripped, one-underscore form, since
    that normalization already happened upstream in ``macho_metadata.py``/
    ``dumper._dump_macho``. Left unstripped, a header-only graph's
    call/type-graph node for a public *function* (as opposed to a type --
    types don't get this decoration) never joins its ``SOURCE_DECLARES``-
    seeded, provenance-tagged counterpart, so it can never be recognized as
    a public graph entry (``is_public_dependency_node``) -- silently
    dropping every ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` finding rooted
    at a function on macOS. ``__Z`` is an unambiguous, platform-independent
    marker (a real Itanium mangled name always starts with ``_Z``; a literal
    C++ identifier starting with two underscores is reserved and never
    emitted here), so this is a no-op on Linux/Windows, where clang's
    ``mangledName`` is already the bare ``_Z...`` form.

    **Known gap, not fixed here** (self-review round, fresh evidence): the
    "no-op on Linux/Windows" claim above does not hold for an explicit GNU
    ``asm("__Zfake")`` label -- clang reports that literal spelling
    verbatim on *any* platform, confirmed empirically. Called
    unconditionally (unlike :mod:`template_graph`'s own
    ``_normalize_mangled``, whose join now tries the exact spelling first
    and only falls back to this strip -- see that module's
    ``_resolve_emitted_symbol``), this corrupts such a decl's identity here
    too, silently failing (or mis-joining) the same way. Porting the same
    guarded-fallback fix to this module's own join
    (:func:`augment_graph_with_calls`) needs its own scoped change, not a
    drive-by extension of this docstring.
    """
    return mangled[1:] if mangled.startswith("__Z") else mangled


def _function_identity(node: dict[str, Any], scope: list[str]) -> str:
    """Like :func:`_identity`, but falls back to
    :func:`~abicheck.model.source_graph.function_decl_identity` (ADR-041
    P1 #5) instead of the bare name when clang's ``mangledName`` doesn't
    distinguish the declaration (absent, or equal to ``name`` — the extern
    "C"/C-linkage case) — matching ``SourceEntity.identity()``'s own
    ``qualified_name#signature_hash`` fallback so this function's call-graph
    node lands on the same ``decl://`` id as its L4 ``SOURCE_DECLARES`` node.
    Used only where a node's *own* identity is recorded (the enclosing
    function scope); a ``referencedDecl`` call-site stub carries no scope
    to qualify with, so callee resolution still goes through the id-index
    (:func:`_resolve_ref_callee_identity`), which looks up the value this
    function already computed for the same declaration's full node.
    """
    name = str(node.get("name") or "")
    if not name:
        return _identity(node)
    qualified_name = "::".join([*scope, name]) if scope else name
    type_obj = node.get("type")
    type_qual = str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
    return function_decl_identity(
        _normalize_mangled(str(node.get("mangledName") or "")),
        name,
        qualified_name,
        type_qual,
    )


def _find_referenced_decl(
    node: dict[str, Any], member_index: Mapping[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Depth-first search for the first ``referencedDecl``/``referencedMemberDecl``
    under *node*.

    clang stores the callee target on a ``DeclRefExpr`` (``referencedDecl``) or,
    for member calls, on a ``MemberExpr`` (``referencedMemberDecl``). The call
    expression's callee subtree is the first inner child, so a DFS finds it
    without needing to model every wrapping cast/paren node.

    ``referencedMemberDecl`` is **not** a nested compact-declaration dict the
    way ``referencedDecl`` is — real clang emits it as a bare node-id string
    (Codex review, fresh evidence, verified against real Clang 17/18 output
    for ``p->f()``: ``MemberExpr`` carries ``"referencedMemberDecl":
    "0x...")``, never a dict). An earlier version of this function only
    checked ``isinstance(ref, dict)``, so a string ``referencedMemberDecl``
    was silently ignored and the DFS fell through into the ``MemberExpr``'s
    own children — finding the *receiver* expression's ``DeclRefExpr``
    instead (e.g. the parameter ``p``) and misclassifying every virtual/
    member method call as a ``CALL_KIND_FUNCTION_POINTER`` call through the
    receiver, making ``VIRTUAL_CALL_MAY_DISPATCH_TO``
    (``virtual_dispatch_graph.py``) effectively inert for this — the most
    common — call shape. *member_index* (``id -> full decl node``, built
    from every ``_FUNCTION_DECL_KINDS`` node seen so far during the same
    walk, mirroring *id_index*'s identical id-keyed pattern) resolves the
    string back to the real ``CXXMethodDecl``/... node, carrying its own
    ``virtual``/``type.qualType``/``id`` fields ``_classify_call``/
    ``_resolve_ref_callee_identity`` need. A member id not (yet) indexed —
    a forward reference, or a genuinely non-function member (a plain data
    field, e.g. a struct's own function-pointer-typed field invoked via
    ``w->cb(x)`` — ``FieldDecl`` is never in ``_FUNCTION_DECL_KINDS``, so
    never indexed here at all) resolves to ``None`` rather than falling
    through to the receiver: an unresolved call is a real, honest gap (no
    edge at all), never a *wrong* one (ADR-028 D3's "degrade to no fact,
    never a wrong fact" authority rule) — extending this resolution to
    data-member callback slots too is real, separately-scoped follow-up
    work (see ``callback_graph.py``'s own docstring, which already
    documents this exact gap from the consuming side).
    """
    ref = node.get("referencedDecl")
    if isinstance(ref, dict):
        # Upgrade a compact stub to the FULL declaration node when one is
        # already indexed (Codex review, fresh evidence): a plain
        # DeclRefExpr's own `referencedDecl` never carries `virtual`/
        # `inner` (OverrideAttr/FinalAttr) the way the full node does --
        # verified against real Clang 18 output for a virtual overloaded
        # operator invoked through a base reference (`B &b; b();`), whose
        # callee is a DeclRefExpr, not a MemberExpr, so it never went
        # through the string-`referencedMemberDecl` resolution path below
        # (which already upgrades to the full node). Falls back to the
        # stub itself when nothing is indexed for this id yet -- a forward
        # reference, or a non-function-decl-kind ref (VarDecl/ParmVarDecl/
        # FieldDecl, never added to member_index at all) whose own `kind`
        # the _POINTER_DECL_KINDS check downstream still needs intact.
        ref_id = str(ref.get("id") or "")
        if ref_id and ref_id in member_index:
            return member_index[ref_id]
        return ref
    member_ref = node.get("referencedMemberDecl")
    if isinstance(member_ref, dict):
        return member_ref
    if isinstance(member_ref, str):
        return member_index.get(member_ref)
    for child in node.get("inner", []) or []:
        if isinstance(child, dict):
            found = _find_referenced_decl(child, member_index)
            if found is not None:
                return found
    return None


def _resolve_ref_callee_identity(
    ref: dict[str, Any], id_index: Mapping[str, str]
) -> str:
    """Resolve a ``referencedDecl``/``referencedMemberDecl`` stub to its real identity.

    clang's compact ``referencedDecl`` never carries ``mangledName`` even when
    the full declaration elsewhere in the same TU does — verified against a
    real Clang 17/18 ``-ast-dump=json`` for an overloaded ``int f(int)``/
    ``double f(double)`` pair: both call sites' stubs are
    ``{"kind": "FunctionDecl", "name": "f", "type": {"qualType": ...}}`` with
    no ``mangledName``, differing only in ``id`` and ``type.qualType``
    (latest-main Clang plugin review, PR1b — the plugin itself already
    resolves callees from the live ``FunctionDecl*``, so this asymmetry was
    Flow B/the JSON-AST replay's alone). Keying solely off the stub's own
    identity therefore collapses every overload/constructor/destructor onto
    one bare name.

    *id_index* is built during the same AST walk from every full
    ``FunctionDecl``/``CXXMethodDecl``/... node seen (keyed by clang's own
    per-node ``id``, mirroring ``type_graph._resolve_ref_identity``'s
    established id-index pattern), so a stub's ``id`` — always present, even
    on an otherwise-incomplete stub — resolves to the real mangled identity
    recorded when that same declaration was visited in full elsewhere in the
    TU (its prototype or definition, whichever textually precedes this call
    per C/C++ declare-before-use). Falls back to the stub's own (almost
    always name-only) identity when its ``id`` was not indexed — a forward
    reference to a declaration this walk has not (yet) seen in full, or a
    hand-built/malformed AST fixture; a known, best-effort limitation,
    identical in spirit to ``type_graph``'s documented ADR-041 P1 gap.
    """
    node_id = str(ref.get("id") or "")
    indexed = id_index.get(node_id, "")
    return indexed or _identity(ref)


#: clang AST node kinds that mark a method as overriding/finalizing a base
#: virtual slot *without* repeating ``"virtual": true`` on the override's own
#: declaration (see ``_ref_is_virtual``'s docstring for the empirical finding).
_OVERRIDE_MARKER_KINDS = frozenset({"OverrideAttr", "FinalAttr"})


def _ref_is_virtual(ref: dict[str, Any]) -> bool:
    """Whether *ref* — a resolved callee declaration node — is virtual, own or
    inherited.

    ``bool(ref.get("virtual"))`` alone under-classifies a real, common case
    (Codex review, fresh evidence, verified against real Clang 17 output for
    ``struct B { virtual void f(); }; struct D : B { void f() override; void
    h(){ f(); } };``): clang repeats ``"virtual": true`` only on the slot's
    *original* declaring ancestor (``B::f``), never on the override's own
    ``CXXMethodDecl`` (``D::f``) — so when the resolved static target of a
    member call *is itself* an override, reading only ``ref["virtual"]``
    misclassified the call as ``CALL_KIND_DIRECT``/``RESOLUTION_EXACT``,
    excluding a real further-derived-override chain from
    ``VIRTUAL_CALL_MAY_DISPATCH_TO`` entirely — the opposite of the
    over-approximation this classification exists to make honest.

    Fixed locally (no cross-module dependency on ``override_graph.py``'s own
    hierarchy-walking ``parse_clang_ast_virtual_methods`` — threading its
    output through here as a precomputed set would make ``call_graph.py``
    import from ``override_graph.py``, which already function-locally
    imports several helpers *from* this module; the reverse edge forms a
    real cycle the AI-readiness ``import-cycle-growth`` gate correctly
    rejects, confirmed empirically against this exact change): clang always
    marks a written ``override``/``final`` keyword with an ``OverrideAttr``/
    ``FinalAttr`` child on the override's own declaration (verified against
    the same real AST above — ``D::f`` carries no ``"virtual": true`` but
    does carry an ``OverrideAttr`` child), and ``ref`` here is always the
    *full* declaration node (not a compact stub) whenever this matters: the
    only caller (``_classify_call``) only consults this for a
    ``CXXMemberCallExpr``, whose target always resolves through
    ``member_index`` (see ``_find_referenced_decl``), which stores full
    nodes. A derived method that redeclares a virtual signature with
    *neither* the ``override``/``final`` keyword *nor* a repeated
    ``"virtual": true`` (legal but unusual C++ style) is a documented,
    remaining false negative — the same conservative
    false-negative-over-false-positive default this module already uses
    throughout (ADR-028 D3).
    """
    if bool(ref.get("virtual")):
        return True
    return any(
        isinstance(child, dict) and child.get("kind") in _OVERRIDE_MARKER_KINDS
        for child in ref.get("inner", []) or []
    )


def _find_member_expr(node: dict[str, Any]) -> dict[str, Any] | None:
    """DFS for the first ``MemberExpr`` under *node* (mirrors
    ``_find_referenced_decl``'s shape, but returns the call-site's own
    ``MemberExpr`` node itself rather than resolving its callee — needed by
    ``_member_expr_is_qualified`` for the receiver's own source range, which
    a resolved *declaration* node never carries)."""
    if node.get("kind") == "MemberExpr":
        return node
    for child in node.get("inner", []) or []:
        if isinstance(child, dict):
            found = _find_member_expr(child)
            if found is not None:
                return found
    return None


def _range_end_offset(range_node: Any) -> int | None:
    """The character offset immediately past *range_node*'s last token, or
    ``None`` if the shape is missing/incomplete."""
    if not isinstance(range_node, dict):
        return None
    end = range_node.get("end")
    if not isinstance(end, dict):
        return None
    offset = end.get("offset")
    tok_len = end.get("tokLen")
    if not isinstance(offset, int) or not isinstance(tok_len, int):
        return None
    return offset + tok_len


def _range_begin_offset(range_node: Any) -> int | None:
    """The character offset of *range_node*'s first token, or ``None`` if the
    shape is missing/incomplete."""
    if not isinstance(range_node, dict):
        return None
    begin = range_node.get("begin")
    if not isinstance(begin, dict):
        return None
    offset = begin.get("offset")
    return offset if isinstance(offset, int) else None


def _is_implicit_this_receiver(node: Any) -> bool:
    """Whether *node* is a synthesized (not user-written) ``this`` receiver —
    a bare ``CXXThisExpr`` with ``"implicit": true`` (an unqualified call
    inside a method, ``f()``), possibly wrapped in an implicit
    derived-to-base cast (a qualified call to an inherited base method,
    ``Base::f()``) — as opposed to an explicitly written ``this->f()``, whose
    ``CXXThisExpr`` carries no ``implicit`` key at all (Codex review, fresh
    evidence, verified against real Clang 18 output for all three shapes).
    Descends through cast wrapper kinds only, mirroring
    ``callback_graph._address_taken_function``'s own cast-unwrapping shape.
    """
    if not isinstance(node, dict):
        return False
    if node.get("kind") == "CXXThisExpr":
        return bool(node.get("implicit"))
    if str(node.get("kind", "")).endswith("CastExpr"):
        inner = node.get("inner") or []
        if inner and isinstance(inner[0], dict):
            return _is_implicit_this_receiver(inner[0])
    return False


def _member_expr_is_qualified(member_expr: dict[str, Any]) -> bool:
    """Whether *member_expr* names its member through an explicit qualifier
    (``obj.Base::f()`` or, from inside a method, ``Base::f()``), which
    suppresses virtual dispatch at this call site regardless of whether the
    resolved method is itself virtual (Codex review, fresh evidence).

    Clang's ``-ast-dump=json`` genuinely does not expose a qualifier the way
    its text ``-ast-dump`` does — verified against real Clang 18 output for
    ``obj.B::f()`` vs. ``obj.f()`` (identical static receiver type, so both
    resolve ``referencedMemberDecl`` to the same ``B::f``): the text dump
    shows a sibling ``NestedNameSpecifier TypeSpec 'B'`` node for the
    qualified form, but the JSON ``MemberExpr``'s own key set — including
    ``inner`` — is byte-for-byte identical between the two; there is no
    ``qualifier`` field or extra child to read.

    Derived instead from source-range arithmetic the JSON *does* carry: for
    an unqualified access with a real, user-written receiver, the
    member-name token begins immediately after the receiver sub-expression's
    own end, plus one operator character (``.`` or ``->``, ``isArrow``) — a
    qualifier occupies the extra bytes in between (confirmed against the
    same real AST: the receiver-to-member gap is exactly ``len(".B::f")``
    for the qualified form and ``len(".f")`` for the unqualified one).

    A genuinely *implicit* ``this`` receiver (:func:`_is_implicit_this_receiver`)
    needs a different measurement: clang anchors the synthesized receiver's
    own position at the member name itself, not before a qualifier — the
    common "call the base implementation from an override"
    pattern, ``struct D : B { void f() override { B::f(); } };`` — so the
    receiver-to-member gap always reads as zero regardless of whether a
    qualifier is present. There, the ``MemberExpr``'s own begin-to-end span
    is used instead (confirmed against the same real AST: it covers the
    qualifier text plus the member name for ``B::f()``, and only the member
    name for a bare ``f()``).

    Only a STRICTLY LARGER-than-expected gap counts as qualified in either
    branch — a missing offset/tokLen field (an unusual receiver shape, or a
    hand-built test fixture) degrades to "not qualified" (the pre-existing,
    already-accepted over-approximation), never the reverse: wrongly
    suppressing a real virtual call's classification would silently drop a
    genuine dispatch target from ``VIRTUAL_CALL_MAY_DISPATCH_TO`` — a worse
    error than the over-approximation this heuristic exists to narrow.

    **A known, documented false positive, not fully closeable from this
    function's own inputs (Codex review, fresh evidence, second round):**
    legal whitespace or comments between the receiver and the member —
    ``obj . f()``, ``ptr /* note */ -> f()`` — also widen the receiver-to-
    member gap, so this heuristic reports "qualified" (and therefore
    suppresses the virtual classification) even though no ``Base::``
    qualifier is present. Confirmed against a real Clang 18 AST for
    ``obj . f()``: the JSON range arithmetic cannot distinguish two
    incidental whitespace characters from a real qualifier's bytes, because
    (as established above) the JSON AST carries no token stream and no
    qualifier field at all — only a genuine read of the source text between
    the two offsets could tell "  " from "B::", and this module is
    deliberately a pure function over the AST dict alone (unlike
    ``macro_graph.py``'s own Pass B, which has a source-file path to read
    from). Extending ``parse_clang_ast_calls`` to accept and read source
    text would resolve this exactly, but is a distinct, larger architectural
    change out of scope for this fix — this gap is deliberately left open
    rather than patched with an unsound threshold (no fixed cutoff separates
    "a couple of stray spaces" from "a real single-letter class name plus
    `::`" in the general case). In practice this only misfires on unusual,
    non-idiomatic whitespace styling no common formatter (clang-format
    included) produces; pinned by a dedicated regression test
    (``test_parse_call_with_whitespace_around_dot_is_a_known_false_positive``)
    documenting the current, accepted behavior rather than silently letting
    it drift.
    """
    inner = member_expr.get("inner") or []
    if not inner or not isinstance(inner[0], dict):
        return False
    member_end = _range_end_offset(member_expr.get("range"))
    name_len = len(str(member_expr.get("name") or ""))
    if member_end is None or not name_len:
        return False
    if _is_implicit_this_receiver(inner[0]):
        member_begin = _range_begin_offset(member_expr.get("range"))
        if member_begin is None:
            return False
        return (member_end - member_begin) > name_len
    receiver_end = _range_end_offset(inner[0].get("range"))
    if receiver_end is None:
        return False
    operator_len = 2 if member_expr.get("isArrow") else 1
    return (member_end - receiver_end) > (operator_len + name_len)


def _classify_call(
    call_node: dict[str, Any],
    ref: dict[str, Any] | None,
    id_index: Mapping[str, str],
) -> tuple[str, str, str]:
    """Return ``(callee_identity, call_kind, resolution)`` for one call site."""
    if ref is None:
        return "", CALL_KIND_UNKNOWN, RESOLUTION_UNKNOWN
    callee = _resolve_ref_callee_identity(ref, id_index)
    ref_kind = str(ref.get("kind", ""))
    if not callee:
        return "", CALL_KIND_UNKNOWN, RESOLUTION_UNKNOWN
    if ref_kind in _POINTER_DECL_KINDS:
        # Called through a variable/parameter/field → a function pointer; the
        # static target is unknown (could be any compatible function).
        return callee, CALL_KIND_FUNCTION_POINTER, RESOLUTION_UNKNOWN
    if call_node.get("kind") in (
        "CXXMemberCallExpr",
        "CXXOperatorCallExpr",
    ) and _ref_is_virtual(ref):
        # A virtual overloaded operator invoked through a base reference/
        # pointer (`B &b; b();`) is a CXXOperatorCallExpr, not a
        # CXXMemberCallExpr (Codex review, fresh evidence, verified against
        # real Clang 18 output): its own callee is a plain DeclRefExpr/
        # FunctionToPointerDecay, not wrapped in a MemberExpr at all, so
        # `_find_member_expr` correctly finds nothing to qualify-check for
        # this shape -- an explicitly-qualified operator call
        # (`b.Base::operator()()`) is a narrower, separately-verified case
        # not attempted here (same conservative false-negative default this
        # module already uses throughout).
        member_expr = _find_member_expr(call_node)
        if member_expr is not None and _member_expr_is_qualified(member_expr):
            # An explicitly-qualified call (obj.Base::f()) suppresses virtual
            # dispatch regardless of the resolved method's own virtuality.
            return callee, CALL_KIND_DIRECT, RESOLUTION_EXACT
        # A virtual member call: the static target is one possible override, so
        # the edge over-approximates the real dynamic dispatch.
        return callee, CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX
    return callee, CALL_KIND_DIRECT, RESOLUTION_EXACT


def _node_file(node: dict[str, Any]) -> str:
    """The source file a node names, if any (clang emits ``file`` only when it *changes* — sticky — so the caller tracks the last-seen value)."""
    loc = node.get("loc")
    if isinstance(loc, dict) and loc.get("file"):
        return str(loc["file"])
    rng = node.get("range")
    if isinstance(rng, dict):
        beg = rng.get("begin")
        if isinstance(beg, dict) and beg.get("file"):
            return str(beg["file"])
    return ""


def _has_function_body(node: dict[str, Any]) -> bool:
    """Whether a function-decl node carries a definition body (a ``CompoundStmt`` child)."""
    return any(
        isinstance(ch, dict) and ch.get("kind") == "CompoundStmt"
        for ch in node.get("inner", []) or []
    )


def _enter_function_scope(
    node: dict[str, Any],
    caller: str,
    caller_file: str,
    cur_file: str,
    scope: list[str],
    decl_files: dict[str, str],
    id_index: dict[str, str],
    member_index: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    """Return the ``(caller, caller_file)`` scope after a function-decl node, recording its definition file."""
    ident = _function_identity(node, scope) or caller
    if ident != caller:
        # Entering a new enclosing function: its body lives in cur_file.
        caller, caller_file = ident, cur_file
    # Record a file so a callee-only leaf helper resolves it, preferring a
    # body (the true definition) over a bare declaration but falling back to
    # the declaration's own file when no body is ever seen in this TU (Codex
    # review): a helper only *declared* here (e.g. a private header this TU
    # includes) with its body compiled in a separate TU previously left
    # callee_file empty, so a public function calling it through the Flow-2
    # source_edges-only path could never be marked defined_in_project even
    # though the declaration's own file is exactly the private-header
    # provenance that marking needs. A body seen after an earlier
    # declaration-only entry still upgrades it (the definition is the more
    # authoritative location); a later declaration-only sighting never
    # downgrades an already-recorded body.
    if ident and (ident not in decl_files or _has_function_body(node)):
        decl_files[ident] = cur_file
    # Index this full declaration by clang's own per-node id so a later call
    # site's compact referencedDecl stub (which never carries mangledName,
    # see _resolve_ref_callee_identity) can resolve back to the real
    # identity. Every FunctionDecl/CXXMethodDecl/... node is indexed, not
    # only ones with a body, so a pure prototype still resolves callers of
    # a not-yet-defined declaration.
    node_id = str(node.get("id") or "")
    real_ident = _function_identity(node, scope)
    if node_id and real_ident:
        id_index.setdefault(node_id, real_ident)
    # Index the full node too (not just its identity string), keyed the same
    # way, so a MemberExpr's bare-string `referencedMemberDecl` id can
    # resolve back to the real CXXMethodDecl node itself -- carrying its own
    # `virtual`/`type.qualType` fields `_classify_call` needs, which the
    # id_index's flat identity string alone can't provide (Codex review,
    # fresh evidence -- see `_find_referenced_decl`'s own docstring).
    if node_id:
        member_index.setdefault(node_id, node)
    return caller, caller_file


def _append_call_edge(
    node: dict[str, Any],
    caller: str,
    caller_file: str,
    edges: list[CallEdge],
    id_index: dict[str, str],
    member_index: dict[str, dict[str, Any]],
) -> None:
    """Resolve one call expression's callee and append the edge (unresolved/self calls dropped)."""
    ref = _find_referenced_decl(node, member_index)
    callee, call_kind, resolution = _classify_call(node, ref, id_index)
    if callee and callee != caller:
        edges.append(CallEdge(caller, callee, call_kind, resolution, caller_file))


def _walk_calls(
    node: Any,
    caller: str,
    caller_file: str,
    cur_file: str,
    scope: list[str],
    edges: list[CallEdge],
    decl_files: dict[str, str],
    id_index: dict[str, str],
    member_index: dict[str, dict[str, Any]],
) -> str:
    """Recursive AST walk tracking the nearest enclosing function as the *caller*
    and the qualified-name scope (ADR-041 P1 #5), mirroring
    ``type_graph._walk_types``'s identical scope-tracking pattern. Returns the
    sticky *cur_file* as last updated by this subtree, so the caller's loop
    over sibling children can thread it forward (Codex review): clang emits a
    node's ``file`` only when it *changes* from the previous node in the
    pre-order dump, so a sibling with no ``loc``/``range`` of its own (a
    second declaration from the same included header) must still see the
    file the *previous* sibling discovered, not the stale value from before
    that sibling ran.
    """
    if not isinstance(node, dict):
        return cur_file
    f = _node_file(node)
    if f:
        cur_file = f
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")
    if kind in _FUNCTION_DECL_KINDS:
        caller, caller_file = _enter_function_scope(
            node,
            caller,
            caller_file,
            cur_file,
            scope,
            decl_files,
            id_index,
            member_index,
        )
    if kind in _CALL_EXPR_KINDS and caller:
        _append_call_edge(node, caller, caller_file, edges, id_index, member_index)
    child_scope = [*scope, name] if kind in _SCOPE_DECL_KINDS and name else scope
    for child in node.get("inner", []) or []:
        cur_file = _walk_calls(
            child,
            caller,
            caller_file,
            cur_file,
            child_scope,
            edges,
            decl_files,
            id_index,
            member_index,
        )
    return cur_file


def _fill_callee_files(
    edges: list[CallEdge], decl_files: dict[str, str]
) -> list[CallEdge]:
    """Fill ``callee_file`` from the callee's own FunctionDecl file (body preferred, declaration-only as fallback)."""
    if not decl_files:
        return edges
    return [
        replace(e, callee_file=decl_files[e.callee]) if e.callee in decl_files else e
        for e in edges
    ]


def _dedupe_edges(edges: list[CallEdge]) -> list[CallEdge]:
    """De-duplicate edges by ``(caller, callee, call_kind)``, keeping first-seen order."""
    seen: set[tuple[str, str, str]] = set()
    out: list[CallEdge] = []
    for e in edges:
        key = (e.caller, e.callee, e.call_kind)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _index_member_decls(node: Any, index: dict[str, dict[str, Any]]) -> None:
    """Pre-index every function-decl node's ``id -> full node`` over the *whole*
    AST, before any call site is resolved (Codex review, fresh evidence).

    ``member_index`` was previously built incrementally, only as
    :func:`_walk_calls`'s single combined pre-order walk actually reached each
    declaration (:func:`_enter_function_scope`'s ``member_index.setdefault``).
    That silently mis-resolves a call to a member declared *later* in the same
    class body — verified against real Clang 17 output for
    ``struct A { virtual void f(){ g(); } virtual void g(); };``: clang visits
    ``f``'s body (and its call to ``g``) before ``g``'s own ``CXXMethodDecl``
    sibling, so at the moment ``f -> g`` is resolved, ``g`` is not yet in the
    index and the call is dropped entirely rather than misattributed —
    consistent with this module's degrade-to-no-fact default, but still a real
    coverage gap for an extremely common declare-after-use shape (mutually
    calling sibling methods, a class's public API calling a private helper
    declared below it, ...).

    Fixed by running this separate, scope-tracking-free pre-pass first: a
    plain ``id -> node`` index needs no caller/file context, so completeness
    doesn't depend on visit order. :func:`_enter_function_scope`'s own
    incremental population is left in place rather than removed — it costs
    nothing extra (``setdefault`` is idempotent against this pre-pass having
    already populated the same id) and keeps that function's index
    self-contained for any future caller that walks without this pre-pass.
    """
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))
    if kind in _FUNCTION_DECL_KINDS:
        node_id = str(node.get("id") or "")
        if node_id:
            index.setdefault(node_id, node)
    for child in node.get("inner", []) or []:
        _index_member_decls(child, index)


def parse_clang_ast_calls(ast: dict[str, Any]) -> list[CallEdge]:
    """Extract static call edges from a ``clang -ast-dump=json`` tree (pure).

    Walks the AST tracking the nearest enclosing function as the *caller*, and
    for every call expression resolves the callee to its referenced declaration.
    Edges are de-duplicated by ``(caller, callee, call_kind)``. Calls outside any
    function (e.g. a global initializer) and unresolved callees are dropped.
    """
    edges: list[CallEdge] = []
    # identity → file of its definition (preferred) or declaration (fallback,
    # Codex review: a helper only *declared* in this TU -- e.g. a private
    # header this TU includes, defined in a separately-compiled TU -- still
    # needs a resolvable file). Lets a leaf helper that only ever appears as
    # a callee still resolve its source file: in clang JSON the call's
    # ``referencedDecl`` usually carries no ``loc.file`` (the location sits
    # on the sibling FunctionDecl), so ``callee_file`` is filled from this
    # map after the walk, not from the reference node (Codex review).
    decl_files: dict[str, str] = {}
    # clang AST node id -> mangled-or-bare identity, built from every full
    # FunctionDecl/CXXMethodDecl/... node seen, so a call site's compact
    # referencedDecl stub can resolve its real (mangled, overload-distinct)
    # identity instead of the stub's own name-only fallback (PR1b).
    id_index: dict[str, str] = {}
    # clang AST node id -> the full FunctionDecl/CXXMethodDecl/... node
    # itself (Codex review, fresh evidence), so a MemberExpr's bare-string
    # `referencedMemberDecl` id can resolve back to a real node carrying its
    # own `virtual`/`type.qualType` fields -- see `_find_referenced_decl`'s
    # own docstring for the full empirical finding this closes.
    member_index: dict[str, dict[str, Any]] = {}
    # Pre-index every function-decl node up front (Codex review, fresh
    # evidence) so a call to a member declared *later* in the same class body
    # still resolves -- see `_index_member_decls`'s own docstring.
    _index_member_decls(ast, member_index)
    _walk_calls(ast, "", "", "", [], edges, decl_files, id_index, member_index)
    return _dedupe_edges(_fill_callee_files(edges, decl_files))


def extractor_pass_fully_covered(
    target: BuildEvidence, extractor: Any, narrowed: bool = False
) -> bool:
    """Whether a call/type-graph extraction run may claim confirmed pass coverage.

    Shared by ``inline_graph_fold.fold_call_graph``/``fold_type_graph``/
    ``fold_include_graph``/``fold_template_graph`` — called identically from
    the inline ``dump
    --sources`` path and the out-of-band ``collect --source-abi
    --source-graph summary`` path (both fold automatically, no separate
    opt-in flag) — so all stamp ``SourceGraphSummary.extractor_passes`` under
    the identical rule (ADR-041 P0 slice 2/3 coverage-honesty chain). Three
    conditions, all required:

    - Not *narrowed*: the run examined the whole compile DB, not a
      changed-path/headers-only-scoped subset (sixth Codex review) — a scoped
      run's "found nothing" only covers the TUs it actually parsed. The
      out-of-band collect path never narrows, so it always passes ``False``.
    - At least one compile unit to examine: an empty target trivially "finds
      nothing" without having looked at anything at all.
    - No per-TU diagnostics recorded on *extractor* (seventh Codex review):
      ``extract_from_build`` degrades a failing TU (clang crash/timeout/
      degenerate AST) to zero edges *silently* — the returned edge list alone
      cannot distinguish "every TU parsed cleanly, zero found" from "some TU
      never actually got parsed." Diagnostics are the only signal a partial
      failure happened; any of them disqualifies the whole pass from claiming
      confirmed coverage, even if most TUs did succeed.
    """
    if narrowed:
        return False
    return _pass_ran_cleanly(target, extractor)


def _pass_ran_cleanly(target: BuildEvidence, extractor: Any) -> bool:
    """Whether *extractor* examined at least one TU in *target* with no diagnostics.

    The scope-independent half of :func:`extractor_pass_fully_covered`'s
    checks, shared with :func:`narrowed_pass_confirmed` — narrowing changes
    only whether the examined scope may be trusted as *whole-project*
    coverage, not whether the run itself succeeded cleanly.
    """
    if not any(cu.source for cu in target.compile_units):
        return False
    return not extractor.diagnostics


def narrowed_pass_confirmed(target: BuildEvidence, extractor: Any) -> bool:
    """Whether a *narrowed* call/type-graph run may claim ``narrowed_passes`` coverage.

    Same rigor as :func:`extractor_pass_fully_covered` minus the "not
    narrowed" requirement (the caller already knows the run was narrowed) —
    at least one compile unit examined, and no per-TU diagnostics (seventh
    Codex review's rationale applies identically to a narrowed run: a
    silently-degraded TU inside the narrow scope must not read as "the scope
    was cleanly examined, zero found," fifteenth Codex review). Only once
    this holds does a narrowed run's zero-edge family become trustworthy
    enough for :func:`source_graph._common_dependency_edge_kinds` to widen a
    matched-scope comparison to the whole family.
    """
    return _pass_ran_cleanly(target, extractor)


def augment_graph_with_calls(
    graph: SourceGraphSummary,
    edges: list[CallEdge],
    project_files: frozenset[str] | None = None,
) -> int:
    """Fold call edges into *graph* as ``DECL_CALLS_DECL`` edges (ADR-031 D4).

    Caller/callee identities are mapped onto ``source_decl`` nodes keyed by
    ``decl://<identity>`` — the same id scheme the L4 enrichment uses, so a call
    edge whose endpoint matches an already-folded declaration links to it rather
    than creating a duplicate. Each edge carries its ``call_kind`` / ``resolution``
    labels and a derived confidence. Returns the number of edges added.

    When *project_files* (the project's compile-unit sources) is supplied, a decl
    whose body is defined in one of them is marked ``defined_in_project`` on its
    node — sound source-location provenance the cross-checks use to tell a
    project implementation helper (flag) from a third-party/system call target
    (don't), even when neither carries L4 visibility (ADR-035 D4 / Codex review).
    """
    from ..model.graph_facts import _decl_node_id

    # identity → the project source file its body is defined in. Both marks the
    # decl ``defined_in_project`` AND preserves the path so the cross-check's
    # changed-file HIGH-confidence elevation works for call-graph-only internals
    # (not just SOURCE_DECLARES-backed ones) — Codex review.
    project_def_file: dict[str, str] = {}
    if project_files:
        for e in edges:
            if e.caller_file and _file_in_project(e.caller_file, project_files):
                project_def_file.setdefault(e.caller, e.caller_file)
            # A leaf helper appears only as a callee; mark it too when its
            # declaration file is a project source (Codex review).
            if e.callee_file and _file_in_project(e.callee_file, project_files):
                project_def_file.setdefault(e.callee, e.callee_file)

    added = 0
    for e in edges:
        src = _decl_node_id(e.caller)
        dst = _decl_node_id(e.callee)
        for node_id, ident in ((src, e.caller), (dst, e.callee)):
            if not graph.has_node(node_id):
                attrs = (
                    {"defined_in_project": True, "def_file": project_def_file[ident]}
                    if ident in project_def_file
                    else {}
                )
                graph.add_node(
                    GraphNode(
                        id=node_id,
                        kind="source_decl",
                        label=ident,
                        provenance="call_graph",
                        confidence=e.confidence(),
                        attrs=attrs,
                    )
                )
        before = len(graph.edges)
        graph.add_edge(
            GraphEdge(
                src=src,
                dst=dst,
                kind="DECL_CALLS_DECL",
                provenance="call_graph",
                confidence=e.confidence(),
                attrs={"call_kind": e.call_kind, "resolution": e.resolution},
            )
        )
        added += len(graph.edges) - before
    return added


def _append_once(out: list[str], seen: set[tuple[str, ...]], *tokens: str) -> None:
    """Append *tokens* if the exact token tuple has not already been emitted."""
    if not all(tokens):
        return
    key = tuple(tokens)
    if key in seen:
        return
    seen.add(key)
    out.extend(tokens)


def _safe_replay_flags_from_context(
    *,
    language: str = "",
    standard: str = "",
    target_triple: str = "",
    sysroot: str | None = None,
    defines: Mapping[str, str | None] | None = None,
    undefines: list[str] | set[str] | None = None,
    include_paths: list[str] | None = None,
    system_include_paths: list[str] | None = None,
    abi_relevant_flags: list[str] | None = None,
) -> list[str]:
    """Build the allowlisted clang flags needed for parse-only AST replay.

    The inputs are normalized build-evidence fields, not the raw compile argv.
    Only preprocessor, include, language/target, and ABI-affecting parse flags
    are replayed.  Option families capable of loading code or causing compiler
    side effects are deliberately not represented here.
    """
    out: list[str] = []
    seen: set[tuple[str, ...]] = set()
    clang_language = _LANGUAGE_TO_CLANG_X.get(language)
    if clang_language:
        _append_once(out, seen, "-x", clang_language)
    if standard:
        _append_once(out, seen, f"-std={standard}")
    if target_triple:
        _append_once(out, seen, f"--target={target_triple}")
    if sysroot:
        _append_once(out, seen, f"--sysroot={sysroot}")
    for name, value in sorted((defines or {}).items()):
        define = f"-D{name}={value}" if value not in (None, "") else f"-D{name}"
        _append_once(out, seen, define)
    for name in sorted(undefines or []):
        _append_once(out, seen, f"-U{name}")
    for inc in include_paths or []:
        _append_once(out, seen, "-I", inc)
    for inc in system_include_paths or []:
        _append_once(out, seen, "-isystem", inc)
    for flag in abi_relevant_flags or []:
        if flag.startswith(_SAFE_REPLAY_FLAG_PREFIXES):
            _append_once(out, seen, flag)
    return out


def _safe_clang_args_from_argv(argv: list[str], cwd: str | None = None) -> list[str]:
    """Return a safe parse-only argv reconstructed from a compile argv."""
    ctx = _extract_flags(argv, Path(cwd or "."))
    source = source_from_argv(argv)
    flags = _safe_replay_flags_from_context(
        standard=ctx.language_standard or "",
        target_triple=ctx.target_triple or "",
        sysroot=str(ctx.sysroot) if ctx.sysroot else None,
        defines=ctx.defines,
        undefines=ctx.undefines,
        include_paths=[str(p) for p in ctx.include_paths],
        system_include_paths=[str(p) for p in ctx.system_includes],
        abi_relevant_flags=ctx.extra_flags,
    )
    return [*flags, "--", source] if source else flags


def _safe_clang_args_from_compile_unit(cu: BuildEvidenceCompileUnit) -> list[str]:
    """Return safe clang AST-replay args for one normalized compile unit.

    Every field on a normalized :class:`BuildEvidenceCompileUnit` (``source``,
    ``sysroot``, ``include_paths``, ...) is persisted with its home-directory
    prefix redacted to ``~`` (ADR-032 D7, ``adapters/compile_db.py``'s
    ``RedactionPolicy``). ``subprocess`` never expands ``~`` (no shell), so
    handing a redacted path straight to a real ``clang`` invocation makes it
    fail to find the file — every TU degrades uniformly, not just one, since
    the source positional itself is always redacted the same way. Confirmed on
    real Windows CI: this pass's own test fixture puts its temp source under
    the runner's home directory (``C:\\Users\\...\\AppData\\Local\\Temp\\...``),
    which redaction rewrites to ``~\\AppData\\...`` — degrading
    call/type/template graph collection uniformly while the sibling
    ``include_graph`` pass (which already un-redacts its own argv, see
    ``include_graph.ClangIncludeExtractor.extract_from_build``) succeeded.
    Un-redact every token here, the same pattern already used by
    ``include_graph.py``/``preprocessor_scan.py``/``archive_graph.py``, so
    every clang-backed L5 pass replays a real, resolvable path.

    This blanket-expands every token, ``-D``/``-U`` macro values included,
    not just path-shaped operands (Codex review) — a rare user-authored
    literal macro value that itself starts with ``~`` (e.g. ``-DROOT=~/x``
    meaning the literal two-character string, never a path the compiler
    should expand) would be corrupted the same way. This is not a new
    tradeoff introduced here: ``source_extractors/castxml.py``'s own
    ``extract()`` already blanket-expands its *entire* built command line
    the identical way, with this exact scenario already investigated and
    accepted there (see its docstring, from an earlier Codex review #335)
    as the necessary cost of correctly replaying the far more common case —
    a genuinely redacted home-path macro (e.g. ``-DCFG=~/build/cfg.h``
    consumed by ``#include CFG``) that must expand or replay parses a
    different TU / fails to find the header entirely. This call site keeps
    the same accepted tradeoff for consistency rather than inventing a
    narrower, path-only expansion this module alone would apply."""
    from .source_extractors._argv import unredact_home

    flags = _safe_replay_flags_from_context(
        language=cu.language,
        standard=cu.standard,
        target_triple=cu.target_triple,
        sysroot=cu.sysroot,
        defines=cu.defines,
        undefines=cu.undefines,
        include_paths=cu.include_paths,
        system_include_paths=cu.system_include_paths,
        abi_relevant_flags=cu.abi_relevant_flags,
    )
    return [*(unredact_home(a) for a in flags), "--", unredact_home(cu.source)]


def _replay_cwd(cu: BuildEvidenceCompileUnit) -> str | None:
    """Un-redact a compile unit's own ``directory`` for use as a real subprocess
    ``cwd`` -- shared by all three clang-backed L5 extractors
    (:mod:`call_graph`/:mod:`type_graph`/:mod:`template_graph`) for the same
    reason :func:`_safe_clang_args_from_compile_unit` un-redacts its argv."""
    from .source_extractors._argv import unredact_home

    return unredact_home(cu.directory) if cu.directory else None


def _call_graph_mem_cap() -> int | None:
    """Max call-graph workers that fit in available RAM, or ``None`` when unknown.

    The L5 call-graph pass shells out to the *same* heavy ``clang -ast-dump=json``
    per TU as the L4 replay, so it shares the L4 per-worker RAM budget and
    cgroup-aware available-memory probe (``source_replay._l4_mem_cap``). Imported
    lazily so a failure there (non-Linux / sandbox) just skips the clamp rather
    than breaking the call-graph pass. ``ABICHECK_L4_JOB_MEM_GIB`` tunes the
    shared budget.
    """
    try:
        from .source_replay import _l4_mem_cap

        return _l4_mem_cap()
    except Exception:  # defensive: a RAM-probe failure must never break L5 (tested)
        return None


def _call_graph_jobs(n_units: int) -> int:
    """Bounded worker count for the best-effort L5 clang call-graph pass.

    Capped by *available RAM* as well as CPU, mirroring the L4 replay
    (``source_replay._l4_jobs``): the pass runs the same multi-GiB
    ``clang -ast-dump=json`` per TU, so N concurrent template-heavy ASTs in one
    process can exhaust a low-memory host and get the pass OOM-killed — the exact
    failure the L4 memory clamp was added to prevent (the UXL oneTBB/oneDNN OOM).
    Without this, a constrained host (small cgroup / CI container) was protected
    on the L4 pass but not on the unseeded full-DB call-graph pass that
    ``--depth source``/``pr-deep`` runs. ``ABICHECK_CALL_GRAPH_JOBS`` overrides the
    CPU count; ``ABICHECK_L4_JOB_MEM_GIB`` tunes the shared per-worker RAM budget.
    The clamp is logged, never silent.
    """
    if n_units <= 1:
        return max(0, n_units)
    cpu = os.cpu_count() or 1
    cap = max(8, cpu * 2)
    raw = os.environ.get("ABICHECK_CALL_GRAPH_JOBS", "").strip()
    if raw:
        try:
            requested = int(raw)
        except ValueError:
            return 1
        jobs = max(1, min(n_units, requested, cap))
    else:
        jobs = max(1, min(n_units, cpu, 8))
    mem_cap = _call_graph_mem_cap()
    if mem_cap is not None and mem_cap < jobs:
        _log.info(
            "L5 call-graph workers reduced %d -> %d to fit available memory; "
            "set ABICHECK_CALL_GRAPH_JOBS / ABICHECK_L4_JOB_MEM_GIB to override, "
            "or seed/scope the scan (--since/--changed-path) to fewer TUs.",
            jobs,
            mem_cap,
        )
        return mem_cap
    return jobs


#: Generic over a worker's own return shape -- originally always
#: ``list[Any]`` (one AST-pass's edge/range list), now also the
#: ``(edges, local_diagnostics)`` tuple every ``Clang*GraphExtractor``'s
#: ``extract_from_build`` uses to keep its ``diagnostics`` list
#: deterministically input-ordered instead of subprocess-completion-ordered
#: (see each extractor's own ``extract_from_build`` docstring).
_WorkerResult = TypeVar("_WorkerResult")


def _deadline_bound_worker(
    deadline_ts: float | None,
    worker: Callable[[BuildEvidenceCompileUnit], _WorkerResult],
    unit: BuildEvidenceCompileUnit,
) -> _WorkerResult:
    """Re-establish a captured scan deadline inside a ThreadPoolExecutor worker.

    ``contextvars`` don't cross a ``ThreadPoolExecutor`` boundary, so a worker
    submitted from ``extract_from_build`` would otherwise see no active
    deadline and each clang subprocess call inside it would run to its full
    fixed 120s regardless of ``--budget`` (Codex review, PR #591; same
    pattern as ``source_replay._deadline_bound_worker``). Shared by every
    ``Clang*GraphExtractor.extract_from_build`` in this package (call/
    callback/override/macro/type/template graphs).
    """
    with deadline.with_deadline_ts(deadline_ts):
        return worker(unit)


# ── live clang extraction (integration only) ────────────────────────────────


@dataclass
class ClangCallGraphExtractor:
    """Shell out to ``clang`` to emit a TU's AST and parse its call edges.

    Side-effecting and compiler-dependent: only exercised on the ``integration``
    lane. A missing ``clang`` (or a parse failure) degrades gracefully —
    :meth:`extract` returns ``[]`` and records nothing — so the no-tool MVP and
    the verdict pipeline never depend on it (ADR-028 D3).
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)
    last_jobs: int = 0
    last_elapsed_s: float = 0.0

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def extract_from_args(
        self, argv: list[str], cwd: str | None = None
    ) -> list[CallEdge]:
        """Run clang AST extraction for one TU after allowlisting argv flags."""
        return self._extract_from_safe_args(
            _safe_clang_args_from_argv(argv, cwd), cwd=cwd
        )

    def _extract_from_safe_args(
        self,
        argv: list[str],
        cwd: str | None = None,
        *,
        diagnostics: list[str] | None = None,
    ) -> list[CallEdge]:
        """Run ``clang -Xclang -ast-dump=json -fsyntax-only`` with pre-sanitized args.

        The bounded run itself lives in :func:`clang_ast_run.run_clang_ast_dump`,
        shared verbatim with the type-graph pass; only the parser applied to the
        resulting AST differs between the two.

        *diagnostics*, when given, is appended to instead of ``self.diagnostics``
        — see :meth:`extract_from_build`'s own docstring for why a parallel
        caller passes a fresh per-unit list here rather than the shared one.
        """
        diag = self.diagnostics if diagnostics is None else diagnostics
        if not self.available():
            diag.append(f"{self.clang_bin} not found in PATH")
            return []
        ast = run_clang_ast_dump(self.clang_bin, argv, cwd=cwd, diagnostics=diag)
        if ast is None:
            return []
        try:
            return parse_clang_ast_calls(ast)
        except (ValueError, RecursionError) as exc:
            diag.append(f"could not parse clang AST JSON: {exc}")
            return []

    def _extract_from_compile_unit(
        self, cu: BuildEvidenceCompileUnit, *, diagnostics: list[str] | None = None
    ) -> list[CallEdge]:
        argv = _safe_clang_args_from_compile_unit(cu)
        return self._extract_from_safe_args(
            argv, cwd=_replay_cwd(cu), diagnostics=diagnostics
        )

    def extract_from_build(self, build: BuildEvidence) -> list[CallEdge]:
        """Extract call edges across every compile unit in *build* (best effort).

        Each unit's own diagnostics are collected into a *fresh, per-call*
        list (never the shared ``self.diagnostics`` directly) and only
        folded into ``self.diagnostics`` back on this (single) driving
        thread, in the loop below -- which iterates ``pool.map``'s result in
        *input* order, not worker-completion order. Appending straight to
        ``self.diagnostics`` from inside a worker (the original shape here)
        would still be individually thread-safe (``list.append`` is GIL-
        atomic) but nondeterministically *ordered* across identical, pinned
        inputs -- see :mod:`abicheck.parallel_probe`'s module docstring,
        which documents this exact bug class found here across all six
        ``Clang*GraphExtractor`` classes in this package (Codex review).
        """
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

        all_edges: list[CallEdge] = []
        seen: set[tuple[str, str, str]] = set()

        def add_edges(edges: Iterable[CallEdge]) -> None:
            for e in edges:
                key = (e.caller, e.callee, e.call_kind)
                if key not in seen:
                    seen.add(key)
                    all_edges.append(e)

        def _probe(cu: BuildEvidenceCompileUnit) -> tuple[list[CallEdge], list[str]]:
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
