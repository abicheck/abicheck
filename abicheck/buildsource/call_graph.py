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
import shutil
import subprocess  # noqa: S404 - call-graph extraction shells out to clang (never shell=True)
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..build_context import _extract_flags
from .adapters.base import source_from_argv
from .source_graph import CONF_HIGH, CONF_REDUCED, CONF_UNKNOWN, GraphEdge, GraphNode

if TYPE_CHECKING:
    from .build_evidence import BuildEvidence
    from .build_evidence import CompileUnit as BuildEvidenceCompileUnit
    from .source_graph import SourceGraphSummary

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
_FUNCTION_DECL_KINDS = frozenset({
    "FunctionDecl", "CXXMethodDecl", "CXXConstructorDecl",
    "CXXDestructorDecl", "CXXConversionDecl",
})
#: clang AST node kinds that represent a call site.
_CALL_EXPR_KINDS = frozenset({"CallExpr", "CXXMemberCallExpr", "CXXOperatorCallExpr"})
#: referenced-decl kinds that mean "called through a pointer/variable".
_POINTER_DECL_KINDS = frozenset({"VarDecl", "ParmVarDecl", "FieldDecl", "NonTypeTemplateParmDecl"})

#: ABI/API-affecting flags safe to replay into clang for AST parsing.  This is
#: intentionally narrower than the original compile command: flags such as
#: ``-Xclang -load`` and ``-fplugin=`` can execute arbitrary shared libraries
#: during compiler option processing, so live call-graph extraction rebuilds a
#: parse-only command from normalized build evidence instead of appending raw
#: compile database argv.
_SAFE_REPLAY_FLAG_PREFIXES: tuple[str, ...] = (
    "-fvisibility", "-fvisibility-inlines-hidden",
    "-fpack-struct", "/Zp", "-fshort-enums", "-fshort-wchar",
    "-fabi-version", "-fno-rtti", "-frtti", "-fno-exceptions", "-fexceptions",
    "-flto", "-fno-lto", "-fwhole-program-vtables",
    "-mabi=", "-m32", "-m64", "/arch:",
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

    caller: str                     # callee/caller identity: mangled name else qualified name
    callee: str
    call_kind: str = CALL_KIND_DIRECT
    resolution: str = RESOLUTION_EXACT

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


def _classify_call(call_node: dict[str, Any], ref: dict[str, Any] | None) -> tuple[str, str, str]:
    """Return ``(callee_identity, call_kind, resolution)`` for one call site."""
    if ref is None:
        return "", CALL_KIND_UNKNOWN, RESOLUTION_UNKNOWN
    callee = _identity(ref)
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


def parse_clang_ast_calls(ast: dict[str, Any]) -> list[CallEdge]:
    """Extract static call edges from a ``clang -ast-dump=json`` tree (pure).

    Walks the AST tracking the nearest enclosing function as the *caller*, and
    for every call expression resolves the callee to its referenced declaration.
    Edges are de-duplicated by ``(caller, callee, call_kind)``. Calls outside any
    function (e.g. a global initializer) and unresolved callees are dropped.
    """
    edges: list[CallEdge] = []

    def visit(node: Any, caller: str) -> None:
        if not isinstance(node, dict):
            return
        kind = str(node.get("kind", ""))
        if kind in _FUNCTION_DECL_KINDS:
            caller = _identity(node) or caller
        if kind in _CALL_EXPR_KINDS and caller:
            callee, call_kind, resolution = _classify_call(node, _find_referenced_decl(node))
            if callee and callee != caller:
                edges.append(CallEdge(caller, callee, call_kind, resolution))
        for child in node.get("inner", []) or []:
            visit(child, caller)

    visit(ast, "")

    seen: set[tuple[str, str, str]] = set()
    out: list[CallEdge] = []
    for e in edges:
        key = (e.caller, e.callee, e.call_kind)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def augment_graph_with_calls(graph: SourceGraphSummary, edges: list[CallEdge]) -> int:
    """Fold call edges into *graph* as ``DECL_CALLS_DECL`` edges (ADR-031 D4).

    Caller/callee identities are mapped onto ``source_decl`` nodes keyed by
    ``decl://<identity>`` — the same id scheme the L4 enrichment uses, so a call
    edge whose endpoint matches an already-folded declaration links to it rather
    than creating a duplicate. Each edge carries its ``call_kind`` / ``resolution``
    labels and a derived confidence. Returns the number of edges added.
    """
    from .source_graph import _decl_node_id

    added = 0
    for e in edges:
        src = _decl_node_id(e.caller)
        dst = _decl_node_id(e.callee)
        for node_id, ident in ((src, e.caller), (dst, e.callee)):
            if not graph.has_node(node_id):
                graph.add_node(GraphNode(
                    id=node_id, kind="source_decl", label=ident,
                    provenance="call_graph", confidence=e.confidence(),
                ))
        before = len(graph.edges)
        graph.add_edge(GraphEdge(
            src=src, dst=dst, kind="DECL_CALLS_DECL",
            provenance="call_graph", confidence=e.confidence(),
            attrs={"call_kind": e.call_kind, "resolution": e.resolution},
        ))
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

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def extract_from_args(self, argv: list[str], cwd: str | None = None) -> list[CallEdge]:
        """Run clang AST extraction for one TU after allowlisting argv flags."""
        return self._extract_from_safe_args(_safe_clang_args_from_argv(argv, cwd), cwd=cwd)

    def _extract_from_safe_args(self, argv: list[str], cwd: str | None = None) -> list[CallEdge]:
        """Run ``clang -Xclang -ast-dump=json -fsyntax-only`` with pre-sanitized args."""
        if not self.available():
            self.diagnostics.append(f"{self.clang_bin} not found in PATH")
            return []
        cmd = [self.clang_bin, "-Xclang", "-ast-dump=json", "-fsyntax-only", *argv]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, never shell=True
                cmd, cwd=cwd, capture_output=True, text=True, timeout=120, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.diagnostics.append(f"clang invocation failed: {exc}")
            return []
        if not proc.stdout.strip():
            self.diagnostics.append(f"clang produced no AST (stderr: {proc.stderr[:200]})")
            return []
        try:
            # Both json.loads and the recursive AST walk can hit Python's
            # recursion limit on a pathologically deep TU; guard so a degenerate
            # AST degrades to "no call edges" rather than aborting collection.
            return parse_clang_ast_calls(json.loads(proc.stdout))
        except (ValueError, RecursionError) as exc:
            self.diagnostics.append(f"could not parse clang AST JSON: {exc}")
            return []

    def extract_from_build(self, build: BuildEvidence) -> list[CallEdge]:
        """Extract call edges across every compile unit in *build* (best effort)."""
        all_edges: list[CallEdge] = []
        seen: set[tuple[str, str, str]] = set()
        for cu in build.compile_units:
            if not cu.source:
                continue
            argv = _safe_clang_args_from_compile_unit(cu)
            for e in self._extract_from_safe_args(argv, cwd=cu.directory or None):
                key = (e.caller, e.callee, e.call_kind)
                if key not in seen:
                    seen.add(key)
                    all_edges.append(e)
        return all_edges
