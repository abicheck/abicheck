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
from .source_graph import CONF_HIGH, CONF_REDUCED, CONF_UNKNOWN, GraphEdge, GraphNode

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


def _classify_call(
    call_node: dict[str, Any], ref: dict[str, Any] | None
) -> tuple[str, str, str]:
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
    decl_files: dict[str, str],
) -> tuple[str, str]:
    """Return the ``(caller, caller_file)`` scope after a function-decl node, recording its definition file."""
    ident = _identity(node) or caller
    if ident != caller:
        # Entering a new enclosing function: its body lives in cur_file.
        caller, caller_file = ident, cur_file
    # Record the definition file so a callee-only leaf helper resolves it.
    if ident and _has_function_body(node):
        decl_files[ident] = cur_file
    return caller, caller_file


def _append_call_edge(
    node: dict[str, Any], caller: str, caller_file: str, edges: list[CallEdge]
) -> None:
    """Resolve one call expression's callee and append the edge (unresolved/self calls dropped)."""
    ref = _find_referenced_decl(node)
    callee, call_kind, resolution = _classify_call(node, ref)
    if callee and callee != caller:
        edges.append(CallEdge(caller, callee, call_kind, resolution, caller_file))


def _walk_calls(
    node: Any,
    caller: str,
    caller_file: str,
    cur_file: str,
    edges: list[CallEdge],
    decl_files: dict[str, str],
) -> None:
    """Recursive AST walk tracking the nearest enclosing function as the *caller*."""
    if not isinstance(node, dict):
        return
    f = _node_file(node)
    if f:
        cur_file = f
    kind = str(node.get("kind", ""))
    if kind in _FUNCTION_DECL_KINDS:
        caller, caller_file = _enter_function_scope(
            node, caller, caller_file, cur_file, decl_files
        )
    if kind in _CALL_EXPR_KINDS and caller:
        _append_call_edge(node, caller, caller_file, edges)
    for child in node.get("inner", []) or []:
        _walk_calls(child, caller, caller_file, cur_file, edges, decl_files)


def _fill_callee_files(
    edges: list[CallEdge], decl_files: dict[str, str]
) -> list[CallEdge]:
    """Fill ``callee_file`` from the definition-file map (the callee's own FunctionDecl)."""
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
    # identity → file of its *definition* (a FunctionDecl with a body). Lets a leaf
    # helper that only ever appears as a callee still resolve its source file: in
    # clang JSON the call's ``referencedDecl`` usually carries no ``loc.file`` (the
    # location sits on the sibling FunctionDecl), so ``callee_file`` is filled from
    # this map after the walk, not from the reference node (Codex review).
    decl_files: dict[str, str] = {}
    _walk_calls(ast, "", "", "", edges, decl_files)
    return _dedupe_edges(_fill_callee_files(edges, decl_files))


def _file_in_project(caller_file: str, project_files: frozenset[str]) -> bool:
    """Whether *caller_file* is one of the project's own compile-unit sources.

    Build-evidence sources are often repo-relative (``src/foo.cc``) while the
    clang AST emits an absolute path (``/work/src/foo.cc``); match on a path
    suffix either way (mirrors ``source_replay._path_matches``). A function whose
    body is in one of these files is project-defined; one in a third-party/system
    header (Boost/Abseil/libstdc++) is not.
    """
    if not caller_file:
        return False
    c = caller_file.replace("\\", "/").lstrip("./")
    for pf in project_files:
        n = pf.replace("\\", "/").lstrip("./")
        if c == n or c.endswith("/" + n) or n.endswith("/" + c):
            return True
    return False


def project_source_files(build: BuildEvidence) -> frozenset[str]:
    """Project-internal source files for ``defined_in_project`` provenance.

    Compile-unit sources **plus the targets' private headers** — a function whose
    body is in a project ``.cc`` *or* a project private header is internal
    implementation. Public headers are deliberately excluded: an inline function
    in a public header is consumer-visible public surface, so marking it
    ``defined_in_project`` (→ internal) would false-positive
    ``public_to_internal_dependency``. Third-party/system headers (Boost, libc++)
    are never in either list, so they stay external (Codex review).
    """
    files: set[str] = {cu.source for cu in build.compile_units if cu.source}
    for tgt in build.targets:
        files.update(h for h in tgt.private_headers if h)
    return frozenset(files)


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
