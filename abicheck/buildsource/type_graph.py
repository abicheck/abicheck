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

"""Optional Clang type/reference-graph extraction for the L5 graph (ADR-041 P0).

The call graph (``call_graph.py``) captures *one* kind of compiler-derived
dependency — "A calls B". Many API/ABI risks are not call edges at all: a
public struct with a private field type, a public class inheriting an
internal base, or an inline function reading an internal constant. This
module extracts those from the same ``clang -ast-dump=json`` tree the call
graph already knows how to parse, producing the edge kinds
``source_graph.py`` already reserves for them (ADR-031 D2 schema) but that,
before this module, no extractor ever populated:

- :data:`EDGE_TYPE_INHERITS` — a record's base class.
- :data:`EDGE_TYPE_HAS_FIELD_TYPE` — a record's field type.
- :data:`EDGE_DECL_HAS_TYPE` — a function/method's parameter type.
- :data:`EDGE_DECL_REFERENCES_DECL` — a function body referencing a
  variable/enumerator that is not itself a call (the call graph already
  covers call-target references).

Architecture mirrors ``call_graph.py`` deliberately:

- :func:`parse_clang_ast_types` is a **pure function** over a
  ``clang -Xclang -ast-dump=json`` tree — unit-tested without a compiler.
- :class:`ClangTypeGraphExtractor` is the thin, side-effecting wrapper that
  shells out to ``clang`` for a translation unit and feeds the parser. Only
  exercised on the ``integration`` lane; a missing compiler degrades
  gracefully.
- :func:`augment_graph_with_types` folds the resulting edges into a
  :class:`~abicheck.buildsource.source_graph.SourceGraphSummary`.

Every edge is best-effort and approximate — type names are matched by their
*textual* base spelling (cv/pointer/reference/array stripped), not resolved
through clang's type-identity graph, so two same-named types in different
scopes can collide onto one node (the same accepted tradeoff
``SourceEntity.identity()`` documents for unmangled declarations). This is a
second, independent ``clang -ast-dump=json`` pass over each translation unit
(alongside the call graph's own pass) — a real compiler-facts source (a
build-integrated plugin emitting both edge families from one frontend pass)
is future work; see ADR-041 P0/P1.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 - type-graph extraction shells out to clang (never shell=True)
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .source_graph import CONF_HIGH, CONF_REDUCED, GraphEdge, GraphNode

if TYPE_CHECKING:
    from .build_evidence import BuildEvidence, CompileUnit as BuildEvidenceCompileUnit
    from .source_graph import SourceGraphSummary

# ── edge kinds (already reserved by source_graph.GRAPH_EDGE_KINDS) ─────────
EDGE_TYPE_INHERITS = "TYPE_INHERITS"
EDGE_TYPE_HAS_FIELD_TYPE = "TYPE_HAS_FIELD_TYPE"
EDGE_DECL_HAS_TYPE = "DECL_HAS_TYPE"
EDGE_DECL_REFERENCES_DECL = "DECL_REFERENCES_DECL"

_RECORD_DECL_KINDS = frozenset(
    {"CXXRecordDecl", "RecordDecl", "ClassTemplateSpecializationDecl"}
)
_FUNCTION_DECL_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)
#: clang AST decl kinds that open a named scope contributing to a qualified name.
_SCOPE_DECL_KINDS = frozenset({"NamespaceDecl", *_RECORD_DECL_KINDS})
#: referencedDecl kinds that make a DeclRefExpr a non-call reference worth
#: recording — a call target (FunctionDecl/CXXMethodDecl/...) is already
#: covered by ``call_graph.py``'s ``DECL_CALLS_DECL`` edges.
_REFERENCE_DECL_KINDS = frozenset({"VarDecl", "EnumConstantDecl"})

#: Fundamental/standard-library scalar spellings excluded from
#: DECL_HAS_TYPE/TYPE_HAS_FIELD_TYPE/TYPE_INHERITS edges — a public function
#: taking an ``int`` is not a meaningful type dependency and would otherwise
#: flood the graph with one node per primitive.
_BUILTIN_TYPES = frozenset(
    {
        "",
        "void",
        "bool",
        "char",
        "signed char",
        "unsigned char",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "short",
        "unsigned short",
        "int",
        "unsigned int",
        "long",
        "unsigned long",
        "long long",
        "unsigned long long",
        "float",
        "double",
        "long double",
        "size_t",
        "ssize_t",
        "ptrdiff_t",
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "auto",
    }
)

_LEADING_TYPE_QUALS = ("const ", "volatile ", "struct ", "class ", "enum ", "union ")


def _base_type_name(qual_type: str) -> str:
    """Strip cv/pointer/reference/array decoration down to a base type spelling.

    Best-effort textual normalization (``"const detail::Impl *"`` ->
    ``"detail::Impl"``), not a real type-identity resolution — matches the
    approximate/overapprox confidence this module labels its edges with.
    """
    s = (qual_type or "").strip()
    if not s:
        return ""
    changed = True
    while changed:
        changed = False
        for suf in ("&&", "&", "*"):
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
        for suf in (" const", " volatile"):
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
        for pre in _LEADING_TYPE_QUALS:
            if s.startswith(pre):
                s = s[len(pre) :]
                changed = True
    bracket = s.find("[")
    if bracket != -1:
        s = s[:bracket].strip()
    return s.strip()


def _is_excluded_type(name: str) -> bool:
    return name in _BUILTIN_TYPES


def _decl_type_name(node: dict[str, Any]) -> str:
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        return _base_type_name(str(type_obj.get("qualType", "")))
    return ""


def _decl_identity(node: dict[str, Any]) -> str:
    """Stable identity for a decl node: mangled name when clang emits one."""
    return str(node.get("mangledName") or node.get("name") or "")


@dataclass(frozen=True)
class TypeEdge:
    """One type/reference edge extracted from a clang AST (ADR-041 P0)."""

    src: str
    dst: str
    kind: str
    confidence: str = CONF_HIGH
    #: "base" | "field" | "param" | "ref" — the role of *dst* relative to *src*.
    role: str = ""


def _walk_types(
    node: Any, scope: list[str], enclosing_func: str, edges: list[TypeEdge]
) -> None:
    """Recursive AST walk tracking the qualified-name scope and enclosing function."""
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")

    if kind in _RECORD_DECL_KINDS and name:
        qname = "::".join([*scope, name])
        for base in node.get("bases", []) or []:
            if not isinstance(base, dict):
                continue
            base_type = base.get("type")
            base_name = _base_type_name(
                str(base_type.get("qualType", ""))
                if isinstance(base_type, dict)
                else ""
            )
            if base_name and not _is_excluded_type(base_name):
                edges.append(
                    TypeEdge(qname, base_name, EDGE_TYPE_INHERITS, CONF_HIGH, "base")
                )
        for child in node.get("inner", []) or []:
            if isinstance(child, dict) and child.get("kind") == "FieldDecl":
                field_name = _decl_type_name(child)
                if field_name and not _is_excluded_type(field_name):
                    edges.append(
                        TypeEdge(
                            qname,
                            field_name,
                            EDGE_TYPE_HAS_FIELD_TYPE,
                            CONF_HIGH,
                            "field",
                        )
                    )
        child_scope = [*scope, name]
        for child in node.get("inner", []) or []:
            _walk_types(child, child_scope, enclosing_func, edges)
        return

    if kind == "NamespaceDecl" and name:
        child_scope = [*scope, name]
        for child in node.get("inner", []) or []:
            _walk_types(child, child_scope, enclosing_func, edges)
        return

    if kind in _FUNCTION_DECL_KINDS:
        ident = _decl_identity(node)
        if ident:
            for child in node.get("inner", []) or []:
                if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
                    param_name = _decl_type_name(child)
                    if param_name and not _is_excluded_type(param_name):
                        edges.append(
                            TypeEdge(
                                ident,
                                param_name,
                                EDGE_DECL_HAS_TYPE,
                                CONF_HIGH,
                                "param",
                            )
                        )
        next_func = ident or enclosing_func
        for child in node.get("inner", []) or []:
            if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
                continue
            _walk_types(child, scope, next_func, edges)
        return

    if kind == "DeclRefExpr" and enclosing_func:
        ref = node.get("referencedDecl")
        if isinstance(ref, dict) and ref.get("kind") in _REFERENCE_DECL_KINDS:
            ref_ident = _decl_identity(ref)
            if ref_ident and ref_ident != enclosing_func:
                edges.append(
                    TypeEdge(
                        enclosing_func,
                        ref_ident,
                        EDGE_DECL_REFERENCES_DECL,
                        CONF_REDUCED,
                        "ref",
                    )
                )

    for child in node.get("inner", []) or []:
        _walk_types(child, scope, enclosing_func, edges)


def _dedupe_edges(edges: list[TypeEdge]) -> list[TypeEdge]:
    seen: set[tuple[str, str, str]] = set()
    out: list[TypeEdge] = []
    for e in edges:
        key = (e.src, e.dst, e.kind)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def parse_clang_ast_types(ast: dict[str, Any]) -> list[TypeEdge]:
    """Extract type/reference edges from a ``clang -ast-dump=json`` tree (pure).

    Walks the AST tracking the enclosing namespace/record scope (for
    qualified type names) and the nearest enclosing function (for parameter
    types and non-call body references). Edges are de-duplicated by
    ``(src, dst, kind)``.
    """
    edges: list[TypeEdge] = []
    _walk_types(ast, [], "", edges)
    return _dedupe_edges(edges)


def augment_graph_with_types(graph: SourceGraphSummary, edges: list[TypeEdge]) -> int:
    """Fold type/reference edges into *graph* (ADR-041 P0).

    Declaration endpoints map onto ``source_decl`` nodes (``decl://...``, the
    same id scheme ``augment_graph_with_calls`` uses); type endpoints map onto
    ``record_type`` nodes (``type://...``, matching
    ``source_graph._type_node_kind``'s default) since an AST-only type
    reference cannot distinguish record/enum/typedef without the L4 surface.
    A node already present (e.g. folded from L4 with the correct kind and
    visibility) is left untouched — first-writer-wins (``add_node``).
    Returns the number of edges added.
    """
    from .source_graph import _decl_node_id, _type_node_id

    added = 0
    for e in edges:
        if e.kind == EDGE_DECL_HAS_TYPE:
            src_id, dst_id = _decl_node_id(e.src), _type_node_id(e.dst)
            src_kind, dst_kind = "source_decl", "record_type"
        elif e.kind in (EDGE_TYPE_INHERITS, EDGE_TYPE_HAS_FIELD_TYPE):
            src_id, dst_id = _type_node_id(e.src), _type_node_id(e.dst)
            src_kind, dst_kind = "record_type", "record_type"
        else:  # DECL_REFERENCES_DECL
            src_id, dst_id = _decl_node_id(e.src), _decl_node_id(e.dst)
            src_kind, dst_kind = "source_decl", "source_decl"
        for node_id, node_kind, ident in (
            (src_id, src_kind, e.src),
            (dst_id, dst_kind, e.dst),
        ):
            if not graph.has_node(node_id):
                graph.add_node(
                    GraphNode(
                        id=node_id,
                        kind=node_kind,
                        label=ident,
                        provenance="type_graph",
                        confidence=e.confidence,
                    )
                )
        before = len(graph.edges)
        graph.add_edge(
            GraphEdge(
                src=src_id,
                dst=dst_id,
                kind=e.kind,
                provenance="type_graph",
                confidence=e.confidence,
                attrs={"role": e.role} if e.role else {},
            )
        )
        added += len(graph.edges) - before
    return added


# ── live clang extraction (integration only) ────────────────────────────────


@dataclass
class ClangTypeGraphExtractor:
    """Shell out to ``clang`` to emit a TU's AST and parse its type edges.

    Side-effecting and compiler-dependent: only exercised on the
    ``integration`` lane. A missing ``clang`` (or a parse failure) degrades
    gracefully — extraction returns ``[]`` and records nothing (ADR-028 D3).
    Reuses ``call_graph``'s vetted parse-only argv builder (same ABI-relevant
    flag allowlist) so the two passes stay in lockstep on what is safe to
    replay.
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)
    last_jobs: int = 0
    last_elapsed_s: float = 0.0

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def _extract_from_safe_args(
        self, argv: list[str], cwd: str | None = None
    ) -> list[TypeEdge]:
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
            return parse_clang_ast_types(json.loads(proc.stdout))
        except (ValueError, RecursionError) as exc:
            self.diagnostics.append(f"could not parse clang AST JSON: {exc}")
            return []

    def _extract_from_compile_unit(
        self, cu: BuildEvidenceCompileUnit
    ) -> list[TypeEdge]:
        from .call_graph import _safe_clang_args_from_compile_unit

        argv = _safe_clang_args_from_compile_unit(cu)
        return self._extract_from_safe_args(argv, cwd=cu.directory or None)

    def extract_from_build(self, build: BuildEvidence) -> list[TypeEdge]:
        """Extract type edges across every compile unit in *build* (best effort)."""
        from .call_graph import _call_graph_jobs

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

        all_edges: list[TypeEdge] = []
        seen: set[tuple[str, str, str]] = set()

        def add_edges(edges: Iterable[TypeEdge]) -> None:
            for e in edges:
                key = (e.src, e.dst, e.kind)
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
