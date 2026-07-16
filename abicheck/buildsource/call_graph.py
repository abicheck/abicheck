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
  :class:`~abicheck.buildsource.source_graph.SourceGraphSummary`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess  # noqa: S404 - call-graph extraction shells out to clang (never shell=True)
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..build_context import _extract_flags
from .adapters.base import source_from_argv
from .source_graph import (
    CONF_HIGH,
    CONF_REDUCED,
    CONF_UNKNOWN,
    GraphEdge,
    GraphNode,
    _file_in_project,
    function_decl_identity,
    project_source_files,
)

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
    from .build_evidence import BuildEvidence, CompileUnit as BuildEvidenceCompileUnit
    from .source_graph import SourceGraphSummary

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
    return str(node.get("mangledName") or node.get("name") or "")


def _function_identity(node: dict[str, Any], scope: list[str]) -> str:
    """Like :func:`_identity`, but falls back to
    :func:`~abicheck.buildsource.source_graph.function_decl_identity` (ADR-041
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
        str(node.get("mangledName") or ""), name, qualified_name, type_qual
    )


def _find_referenced_decl(node: dict[str, Any]) -> dict[str, Any] | None:
    """Depth-first search for the first ``referencedDecl`` under *node*.

    clang stores the callee target on a ``DeclRefExpr`` (``referencedDecl``) or,
    for member calls, on a ``MemberExpr`` (``referencedMemberDecl``). The call
    expression's callee subtree is the first inner child, so a DFS finds it
    without needing to model every wrapping cast/paren node.
    """
    ref = node.get("referencedDecl") or node.get("referencedMemberDecl")
    if isinstance(ref, dict):
        return ref
    for child in node.get("inner", []) or []:
        if isinstance(child, dict):
            found = _find_referenced_decl(child)
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


def _classify_call(
    call_node: dict[str, Any], ref: dict[str, Any] | None, id_index: Mapping[str, str]
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
    if call_node.get("kind") == "CXXMemberCallExpr" and bool(ref.get("virtual")):
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
    return caller, caller_file


def _append_call_edge(
    node: dict[str, Any],
    caller: str,
    caller_file: str,
    edges: list[CallEdge],
    id_index: dict[str, str],
) -> None:
    """Resolve one call expression's callee and append the edge (unresolved/self calls dropped)."""
    ref = _find_referenced_decl(node)
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
            node, caller, caller_file, cur_file, scope, decl_files, id_index
        )
    if kind in _CALL_EXPR_KINDS and caller:
        _append_call_edge(node, caller, caller_file, edges, id_index)
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
    _walk_calls(ast, "", "", "", [], edges, decl_files, id_index)
    return _dedupe_edges(_fill_callee_files(edges, decl_files))


def extractor_pass_fully_covered(
    target: BuildEvidence, extractor: Any, narrowed: bool = False
) -> bool:
    """Whether a call/type-graph extraction run may claim confirmed pass coverage.

    Shared by ``inline_graph_fold.fold_call_graph``/``fold_type_graph``/
    ``fold_include_graph`` — called identically from the inline ``dump
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
    from .source_graph import _decl_node_id

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
    """Return safe clang AST-replay args for one normalized compile unit."""
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
    return [*flags, "--", cu.source]


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
        self, argv: list[str], cwd: str | None = None
    ) -> list[CallEdge]:
        """Run ``clang -Xclang -ast-dump=json -fsyntax-only`` with pre-sanitized args."""
        if not self.available():
            self.diagnostics.append(f"{self.clang_bin} not found in PATH")
            return []
        cmd = [self.clang_bin, "-Xclang", "-ast-dump=json", "-fsyntax-only", *argv]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, never shell=True
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.diagnostics.append(f"clang invocation failed: {exc}")
            return []
        if not proc.stdout.strip():
            self.diagnostics.append(
                f"clang produced no AST (stderr: {proc.stderr[:200]})"
            )
            return []
        # A non-zero exit (real compile errors in the replayed, necessarily
        # approximate flag subset) does not stop clang's AST dump from still
        # printing a partial, error-recovered tree — `-ast-dump` walks
        # whatever it built. Still salvage any edges from that best-effort
        # AST (unchanged from before), but record a diagnostic regardless so
        # `extractor_pass_fully_covered` (ADR-041 P0 slice 3, ninth Codex
        # review) never treats this TU as cleanly, fully parsed — a bad exit
        # must disqualify confirmed pass coverage even though `diagnostics`
        # would otherwise stay empty.
        if proc.returncode != 0:
            self.diagnostics.append(
                f"clang exited {proc.returncode} (stderr: {proc.stderr[:200]})"
            )
        try:
            # Both json.loads and the recursive AST walk can hit Python's
            # recursion limit on a pathologically deep TU; guard so a degenerate
            # AST degrades to "no call edges" rather than aborting collection.
            return parse_clang_ast_calls(json.loads(proc.stdout))
        except (ValueError, RecursionError) as exc:
            self.diagnostics.append(f"could not parse clang AST JSON: {exc}")
            return []

    def _extract_from_compile_unit(
        self, cu: BuildEvidenceCompileUnit
    ) -> list[CallEdge]:
        argv = _safe_clang_args_from_compile_unit(cu)
        return self._extract_from_safe_args(argv, cwd=cu.directory or None)

    def extract_from_build(self, build: BuildEvidence) -> list[CallEdge]:
        """Extract call edges across every compile unit in *build* (best effort)."""
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

        try:
            if self.last_jobs > 1 and len(units) > 1:
                with ThreadPoolExecutor(max_workers=self.last_jobs) as pool:
                    for edges in pool.map(self._extract_from_compile_unit, units):
                        add_edges(edges)
            else:
                for cu in units:
                    add_edges(self._extract_from_compile_unit(cu))
        finally:
            self.last_elapsed_s = time.monotonic() - start

        return all_edges
