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
*textual* base spelling (cv/pointer/reference/array stripped). clang's
``qualType`` prints a type *as written*, not fully qualified, so a first AST
pass (:func:`_index_declared_entities`) indexes every record's qualified name
and declaring file and the second pass resolves an unqualified spelling
against the nearest enclosing scope (:func:`_resolve_type_name`) —
approximate unqualified-name lookup, not real semantic resolution, so two
same-named types in different scopes can still collide, and an edge whose
target could not be resolved is kept at reduced confidence rather than
dropped (the same accepted tradeoff ``SourceEntity.identity()`` documents for
unmangled declarations). This is a second, independent
``clang -ast-dump=json`` pass over each translation unit
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
from dataclasses import dataclass, field, replace
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
#: Non-record type declarations indexed as resolvable type targets too (Codex
#: review: a private enum/typedef field/param type was previously left
#: unqualified and un-provenanced, same as an un-indexed record).
_OTHER_TYPE_DECL_KINDS = frozenset({"EnumDecl", "TypedefDecl", "TypeAliasDecl"})
#: All declaration kinds the first pass indexes as named type targets.
_INDEXED_TYPE_DECL_KINDS = _RECORD_DECL_KINDS | _OTHER_TYPE_DECL_KINDS
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
    # clang glues a top-level cv-qualified pointer directly to the star with
    # no separating space (``"detail::Impl *const"``, not ``"* const"`` —
    # Codex review); normalize so the loop below strips it like the spaced
    # form it already handles.
    s = s.replace("*const", "* const").replace("*volatile", "* volatile")
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


def _decl_return_type_name(node: dict[str, Any]) -> str:
    """A function/method decl's return type, from its own ``type.qualType``.

    clang spells a function decl's own type as the *whole signature*
    (``"detail::Impl *(int)"``, return type immediately followed by the
    parenthesized parameter list — Codex review: this was never read at all,
    so a public factory function returning a private type produced no
    ``DECL_HAS_TYPE`` edge). Best-effort split on the first ``(``: correct
    for the overwhelmingly common case, but not real declarator parsing, so
    a return type that is itself a function pointer (parens before the outer
    parameter list) is not handled precisely — the same approximate-textual
    tradeoff the rest of this module accepts.
    """
    type_obj = node.get("type")
    if not isinstance(type_obj, dict):
        return ""
    qual_type = str(type_obj.get("qualType", ""))
    paren = qual_type.find("(")
    if paren == -1:
        return ""
    return _base_type_name(qual_type[:paren])


def _decl_identity(node: dict[str, Any]) -> str:
    """Stable identity for a decl node: mangled name when clang emits one."""
    return str(node.get("mangledName") or node.get("name") or "")


def _node_file(node: dict[str, Any]) -> str:
    """The source file a node names, if any (clang emits ``file`` only when it
    *changes* — sticky — so the caller tracks the last-seen value; mirrors
    ``call_graph._node_file``)."""
    loc = node.get("loc")
    if isinstance(loc, dict) and loc.get("file"):
        return str(loc["file"])
    rng = node.get("range")
    if isinstance(rng, dict):
        beg = rng.get("begin")
        if isinstance(beg, dict) and beg.get("file"):
            return str(beg["file"])
    return ""


def _looks_like_record_definition(node: dict[str, Any]) -> bool:
    """Whether a CXXRecordDecl-like node carries a real definition (has members),
    as opposed to a bare forward declaration."""
    return any(
        isinstance(c, dict)
        and c.get("kind") in ("FieldDecl", "CXXMethodDecl", "CXXConstructorDecl")
        for c in node.get("inner", []) or []
    )


@dataclass(frozen=True)
class TypeEdge:
    """One type/reference edge extracted from a clang AST (ADR-041 P0)."""

    src: str
    dst: str
    kind: str
    confidence: str = CONF_HIGH
    #: "base" | "field" | "param" | "return" | "ref" — the role of *dst*
    #: relative to *src*.
    role: str = ""
    #: The file *dst* is declared in, when resolvable within this TU's AST —
    #: used to mark an AST-only dependency node ``defined_in_project`` (Codex
    #: review: without this, a private-header dst node carries no visibility
    #: and ``public_to_internal_dependency`` never fires on it).
    dst_file: str = ""


def _index_declared_entities(
    node: Any,
    scope: list[str],
    cur_file: str,
    name_index: dict[str, list[str]],
    decl_file: dict[str, str],
    ref_name_index: dict[str, list[str]],
    id_index: dict[str, str],
) -> str:
    """First pass: record every type declaration's qualified name (+declaring
    file) and every var/enum-constant's identity (+declaring file, +bare-name
    index, +clang node id) seen anywhere in the TU, so the second pass can
    resolve an unqualified type spelling against the nearest enclosing scope
    (Codex review: clang's ``qualType`` is *written*, not fully qualified — a
    field typed ``Base`` inside ``namespace ns`` prints as ``"Base"``, not
    ``"ns::Base"``) and mark AST-only dependency nodes with their declaring
    file for ``defined_in_project`` provenance. Records, enums, and
    typedef/type-alias declarations are all indexed as resolvable type
    targets (Codex review: a private enum/typedef used as a field/param type
    was previously left un-indexed, same gap as an un-indexed record).

    Returns the last-seen file after visiting *node* and its whole subtree.
    clang emits ``loc.file`` only on the *first* declaration in a file — later
    siblings just carry line/column (Codex review) — so this sticky state
    must be threaded from one sibling call to the next in every loop below,
    not just passed down independently to each child from the parent's
    value, or every sibling after the first loses its file.
    """
    if not isinstance(node, dict):
        return cur_file
    f = _node_file(node)
    if f:
        cur_file = f
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")

    if kind in _RECORD_DECL_KINDS and name:
        qname = "::".join([*scope, name])
        name_index.setdefault(name, [])
        if qname not in name_index[name]:
            name_index[name].append(qname)
        if cur_file and (qname not in decl_file or _looks_like_record_definition(node)):
            decl_file[qname] = cur_file
        child_scope = [*scope, name]
        for child in node.get("inner", []) or []:
            cur_file = _index_declared_entities(
                child,
                child_scope,
                cur_file,
                name_index,
                decl_file,
                ref_name_index,
                id_index,
            )
        return cur_file

    if kind in _OTHER_TYPE_DECL_KINDS and name:
        qname = "::".join([*scope, name])
        name_index.setdefault(name, [])
        if qname not in name_index[name]:
            name_index[name].append(qname)
        if cur_file:
            decl_file.setdefault(qname, cur_file)
        # Enum constants (and any nested decls) still need indexing; no new
        # scope is opened — an unscoped/scoped enum's own name is not part of
        # its enumerators' spelling in clang's AST dump.
        for child in node.get("inner", []) or []:
            cur_file = _index_declared_entities(
                child, scope, cur_file, name_index, decl_file, ref_name_index, id_index
            )
        return cur_file

    if kind == "NamespaceDecl" and name:
        child_scope = [*scope, name]
        for child in node.get("inner", []) or []:
            cur_file = _index_declared_entities(
                child,
                child_scope,
                cur_file,
                name_index,
                decl_file,
                ref_name_index,
                id_index,
            )
        return cur_file

    if kind in _REFERENCE_DECL_KINDS:
        ident = _decl_identity(node)
        if ident and cur_file:
            decl_file.setdefault(ident, cur_file)
        if ident and name:
            candidates = ref_name_index.setdefault(name, [])
            if ident not in candidates:
                candidates.append(ident)
        node_id = str(node.get("id") or "")
        # Keyed by clang's own per-node id (unique, unlike the bare-name
        # identity two same-named declarations in different scopes both
        # collapse to) and mapped straight to *file*, not identity — routing
        # through the shared identity->file `decl_file` dict would still pick
        # whichever same-named declaration was indexed first (Codex review).
        if node_id and cur_file:
            id_index.setdefault(node_id, cur_file)

    for child in node.get("inner", []) or []:
        cur_file = _index_declared_entities(
            child, scope, cur_file, name_index, decl_file, ref_name_index, id_index
        )
    return cur_file


def _resolve_type_name(
    raw: str, scope: list[str], name_index: dict[str, list[str]]
) -> tuple[str, bool]:
    """Resolve a possibly-unqualified or partially-qualified type spelling to
    a fully qualified name.

    clang's ``qualType`` prints a type *as written*, not fully qualified — a
    field typed ``Base`` inside ``namespace ns { struct Widget { ... }; }``
    prints as ``"Base"``, not ``"ns::Base"`` (Codex review). The same holds
    for a *partially* qualified spelling: a field typed ``detail::Impl``
    written inside ``namespace ns { namespace detail { ... } }`` prints as
    ``"detail::Impl"``, not ``"ns::detail::Impl"`` — a naive "already has
    ``::`` so it must be fully qualified" shortcut misses this (Codex
    review). Both cases are handled uniformly: index lookups key on the
    spelling's *last* component (``"Impl"`` for either ``"Impl"`` or
    ``"detail::Impl"``), candidates are filtered to those whose full
    qualified name ends with the raw spelling (so ``"detail::Impl"`` only
    matches a candidate ending ``"::detail::Impl"``, never an unrelated
    ``"other::Impl"``), then the nearest enclosing scope is tried first,
    each enclosing scope outward next, and a unique remaining candidate last.

    Returns ``(name, matched)`` — *matched* is ``True`` only when a
    qualifying declaration was found, so a **global** declaration whose
    resolved spelling happens to equal the raw spelling (e.g. ``"Base"`` at
    namespace scope) is still reported as a real match rather than mistaken
    for "unresolved" by a naive string-equality check (Codex review). Best
    effort, not a real semantic lookup: an unmatched name is returned
    unchanged with ``matched=False``.
    """
    if not raw:
        return raw, False
    # A leading "::" is C++'s global-scope qualifier ("::ns::detail::Impl"),
    # not part of the name itself — the index stores declarations without it
    # (Codex review: matching on the unstripped spelling built "::::..." and
    # never joined the indexed "ns::detail::Impl").
    lookup = raw[2:] if raw.startswith("::") else raw
    leaf = lookup.rsplit("::", 1)[-1]
    candidates = name_index.get(leaf)
    if not candidates:
        return raw, False
    suffix = "::" + lookup
    matching = [c for c in candidates if c == lookup or c.endswith(suffix)]
    if not matching:
        return raw, False
    for k in range(len(scope), -1, -1):
        prefix = "::".join(scope[:k])
        target = f"{prefix}::{lookup}" if prefix else lookup
        if target in matching:
            return target, True
    if len(matching) == 1:
        return matching[0], True
    return raw, False


def _resolve_ref_identity(
    ref: dict[str, Any],
    decl_file: dict[str, str],
    ref_name_index: dict[str, list[str]],
    id_index: dict[str, str],
) -> tuple[str, str]:
    """Resolve a ``DeclRefExpr``'s ``referencedDecl`` to its full identity and file.

    clang commonly emits an *incomplete* stub for ``referencedDecl`` — e.g.
    ``{"kind": "VarDecl", "name": "k"}`` with no ``mangledName``/``loc`` even
    though the full ``VarDecl`` elsewhere in the same TU carries both (Codex
    review). Keying the edge from the stub's bare-name identity means it never
    matches ``decl_file`` (indexed by the *full* declaration's identity), so
    the dependency's ``dst_file``/``defined_in_project`` provenance is lost —
    the exact scenario this module exists to catch
    (``inline int f() { return detail::k; }``).

    *Identity* resolution order: the stub's own mangled-or-bare identity when
    it already resolves (i.e. it *was* complete); else the unique full
    declaration sharing its bare name, when unambiguous — same bare-name
    conflation two same-named declarations in different scopes are subject to
    everywhere else in this module (a known, documented limitation; a real
    fix needs a stable scope-qualified identity, tracked in ADR-041 P1).

    *File* resolution prefers the stub's own ``id`` — clang's internal
    per-node identifier, present even on an otherwise-incomplete stub and
    shared with the node's full declaration elsewhere in the same TU — looked
    up in *id_index* (keyed by id, not by the ambiguous bare-name identity).
    This disambiguates the file for *this specific reference* even when two
    declarations share a bare name, e.g. ``a::k`` vs ``b::k`` (Codex review):
    routing the file lookup through the shared identity would still pick
    whichever same-named declaration happened to be indexed first. Falls back
    to ``decl_file`` keyed by the resolved identity when no id match exists.
    """
    ident = _decl_identity(ref)
    if ident not in decl_file:
        name = str(ref.get("name") or "")
        if name:
            candidates = ref_name_index.get(name)
            if candidates and len(candidates) == 1:
                ident = candidates[0]
    node_id = str(ref.get("id") or "")
    file = id_index.get(node_id) or decl_file.get(ident, "")
    return ident, file


def _walk_types(
    node: Any,
    scope: list[str],
    enclosing_func: str,
    edges: list[TypeEdge],
    name_index: dict[str, list[str]],
    decl_file: dict[str, str],
    ref_name_index: dict[str, list[str]],
    id_index: dict[str, str],
) -> None:
    """Recursive AST walk tracking the qualified-name scope and enclosing function."""
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")

    if kind in _RECORD_DECL_KINDS and name:
        qname = "::".join([*scope, name])
        # Resolve base/field types against the record's *own* scope
        # (child_scope), not just the enclosing one — a field/base naming a
        # type nested in this same record (``Outer::Inner`` referenced as
        # bare "Inner" from inside ``Outer``) must be looked up starting from
        # the record's own body outward, matching real C++ member lookup
        # order (Codex review). ``_resolve_type_name`` still tries every
        # shorter prefix down to the enclosing scopes, so this is a strict
        # superset of the previous (enclosing-scope-only) lookup.
        child_scope = [*scope, name]
        # A ClassTemplateSpecializationDecl's own "name" is the *primary*
        # template's bare name (clang does not fold template arguments into
        # it), so qname here collides with the generic template's node id.
        # Emitting base/field edges from a specific *internal* instantiation
        # (e.g. Holder<detail::Impl>) would misattribute that one
        # instantiation's dependency to the public generic template itself
        # (Codex review) — skip edge emission for specializations, but still
        # recurse into their children below (nested decls still resolve).
        if kind != "ClassTemplateSpecializationDecl":
            for base in node.get("bases", []) or []:
                if not isinstance(base, dict):
                    continue
                base_type = base.get("type")
                raw_base = _base_type_name(
                    str(base_type.get("qualType", ""))
                    if isinstance(base_type, dict)
                    else ""
                )
                base_name, matched = _resolve_type_name(
                    raw_base, child_scope, name_index
                )
                if base_name and not _is_excluded_type(base_name):
                    edges.append(
                        TypeEdge(
                            qname,
                            base_name,
                            EDGE_TYPE_INHERITS,
                            CONF_HIGH if matched else CONF_REDUCED,
                            "base",
                            decl_file.get(base_name, ""),
                        )
                    )
            for child in node.get("inner", []) or []:
                if isinstance(child, dict) and child.get("kind") == "FieldDecl":
                    raw_field = _decl_type_name(child)
                    field_name, matched = _resolve_type_name(
                        raw_field, child_scope, name_index
                    )
                    if field_name and not _is_excluded_type(field_name):
                        edges.append(
                            TypeEdge(
                                qname,
                                field_name,
                                EDGE_TYPE_HAS_FIELD_TYPE,
                                CONF_HIGH if matched else CONF_REDUCED,
                                "field",
                                decl_file.get(field_name, ""),
                            )
                        )
        for child in node.get("inner", []) or []:
            _walk_types(
                child,
                child_scope,
                enclosing_func,
                edges,
                name_index,
                decl_file,
                ref_name_index,
                id_index,
            )
        return

    if kind == "NamespaceDecl" and name:
        child_scope = [*scope, name]
        for child in node.get("inner", []) or []:
            _walk_types(
                child,
                child_scope,
                enclosing_func,
                edges,
                name_index,
                decl_file,
                ref_name_index,
                id_index,
            )
        return

    if kind in _FUNCTION_DECL_KINDS:
        ident = _decl_identity(node)
        if ident:
            raw_return = _decl_return_type_name(node)
            if raw_return:
                return_name, matched = _resolve_type_name(raw_return, scope, name_index)
                if return_name and not _is_excluded_type(return_name):
                    edges.append(
                        TypeEdge(
                            ident,
                            return_name,
                            EDGE_DECL_HAS_TYPE,
                            CONF_HIGH if matched else CONF_REDUCED,
                            "return",
                            decl_file.get(return_name, ""),
                        )
                    )
            for child in node.get("inner", []) or []:
                if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
                    raw_param = _decl_type_name(child)
                    param_name, matched = _resolve_type_name(
                        raw_param, scope, name_index
                    )
                    if param_name and not _is_excluded_type(param_name):
                        edges.append(
                            TypeEdge(
                                ident,
                                param_name,
                                EDGE_DECL_HAS_TYPE,
                                CONF_HIGH if matched else CONF_REDUCED,
                                "param",
                                decl_file.get(param_name, ""),
                            )
                        )
        next_func = ident or enclosing_func
        for child in node.get("inner", []) or []:
            if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
                continue
            _walk_types(
                child,
                scope,
                next_func,
                edges,
                name_index,
                decl_file,
                ref_name_index,
                id_index,
            )
        return

    if kind == "DeclRefExpr" and enclosing_func:
        ref = node.get("referencedDecl")
        if isinstance(ref, dict) and ref.get("kind") in _REFERENCE_DECL_KINDS:
            ref_ident, ref_file = _resolve_ref_identity(
                ref, decl_file, ref_name_index, id_index
            )
            if ref_ident and ref_ident != enclosing_func:
                edges.append(
                    TypeEdge(
                        enclosing_func,
                        ref_ident,
                        EDGE_DECL_REFERENCES_DECL,
                        CONF_REDUCED,
                        "ref",
                        ref_file,
                    )
                )

    for child in node.get("inner", []) or []:
        _walk_types(
            child,
            scope,
            enclosing_func,
            edges,
            name_index,
            decl_file,
            ref_name_index,
            id_index,
        )


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

    A first pass (:func:`_index_declared_entities`) indexes every type
    declaration's (record/enum/typedef/type-alias) qualified name, every
    var/enum-constant's declaring file, a bare-name index of var/enum-constant
    declarations, and a clang-node-id index of var/enum-constant declarations
    — all across the *whole* TU before any edge is built — so an unqualified
    type spelling (clang's ``qualType`` is written, not fully qualified) can
    be resolved against the nearest enclosing scope, an incomplete
    ``DeclRefExpr`` reference stub can be resolved to its full declaration
    (by node id first, disambiguating two declarations that share a bare
    name), and an AST-only dependency node can carry its declaring file
    regardless of declaration order. The second pass (:func:`_walk_types`)
    then walks the AST tracking the enclosing namespace/record scope (for
    qualified type names) and the nearest enclosing function (for parameter
    types and non-call body references). Edges are de-duplicated by
    ``(src, dst, kind)``.
    """
    name_index: dict[str, list[str]] = {}
    decl_file: dict[str, str] = {}
    ref_name_index: dict[str, list[str]] = {}
    id_index: dict[str, str] = {}
    _index_declared_entities(
        ast, [], "", name_index, decl_file, ref_name_index, id_index
    )
    edges: list[TypeEdge] = []
    _walk_types(ast, [], "", edges, name_index, decl_file, ref_name_index, id_index)
    return _dedupe_edges(edges)


def augment_graph_with_types(
    graph: SourceGraphSummary,
    edges: list[TypeEdge],
    project_files: frozenset[str] | None = None,
) -> int:
    """Fold type/reference edges into *graph* (ADR-041 P0).

    Declaration endpoints map onto ``source_decl`` nodes (``decl://...``, the
    same id scheme ``augment_graph_with_calls`` uses); type endpoints map onto
    ``record_type`` nodes (``type://...``, matching
    ``source_graph._type_node_kind``'s default) since an AST-only type
    reference cannot distinguish record/enum/typedef without the L4 surface.
    A node already present (e.g. folded from L4 with the correct kind and
    visibility) is left untouched — first-writer-wins (``add_node``).

    When *project_files* (the project's compile-unit sources + private
    headers, see ``call_graph.project_source_files``) is supplied, a ``dst``
    node whose ``dst_file`` is one of them is marked ``defined_in_project`` —
    the exact case this module exists for (a public decl/type reaching a
    private-header type/variable) would otherwise create an unannotated node
    that ``crosscheck.public_to_internal_dependency`` cannot classify as
    internal. This applies whether the node is created fresh by this edge
    *or* was already added by an earlier edge in this same call (e.g. the
    private type was first seen as another edge's ``src`` and had no
    provenance yet) — the marker is backfilled onto the existing node rather
    than only being set at creation time (Codex review), unless the node
    already carries a ``visibility`` attr (real L4 evidence, never
    overridden by this best-effort AST-only marker).

    Returns the number of edges added.
    """
    from .call_graph import _file_in_project
    from .source_graph import _decl_node_id, _type_node_id

    node_by_id: dict[str, GraphNode] = {n.id: n for n in graph.nodes}

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
        dst_in_project = bool(
            project_files and e.dst_file and _file_in_project(e.dst_file, project_files)
        )
        for node_id, node_kind, ident, is_dst in (
            (src_id, src_kind, e.src, False),
            (dst_id, dst_kind, e.dst, True),
        ):
            existing = node_by_id.get(node_id)
            if existing is None:
                attrs = (
                    {"defined_in_project": True, "def_file": e.dst_file}
                    if is_dst and dst_in_project
                    else {}
                )
                node = GraphNode(
                    id=node_id,
                    kind=node_kind,
                    label=ident,
                    provenance="type_graph",
                    confidence=e.confidence,
                    attrs=attrs,
                )
                graph.add_node(node)
                node_by_id[node_id] = node
            elif (
                is_dst
                and dst_in_project
                and not existing.attrs.get("defined_in_project")
                and not existing.attrs.get("visibility")
            ):
                existing.attrs["defined_in_project"] = True
                existing.attrs["def_file"] = e.dst_file
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


#: Confidence label -> rank, for picking the stronger of two duplicate edges.
_CONFIDENCE_RANK = {CONF_HIGH: 2, CONF_REDUCED: 1, "unknown": 0}


def _merge_type_edges(existing: TypeEdge, new: TypeEdge) -> TypeEdge:
    """Merge two edges sharing a ``(src, dst, kind)`` key from different TUs.

    Keeps the stronger ``confidence`` and fills a missing ``dst_file`` from
    whichever edge has one — a TU that doesn't include the header declaring a
    private ``dst`` sees no file for it, while another TU that does include
    it resolves it fully; picking whichever ran first would silently drop the
    richer provenance the graph needs to mark the node ``defined_in_project``
    (Codex review).
    """
    dst_file = existing.dst_file or new.dst_file
    if _CONFIDENCE_RANK.get(new.confidence, 0) > _CONFIDENCE_RANK.get(
        existing.confidence, 0
    ):
        return replace(new, dst_file=dst_file)
    if dst_file != existing.dst_file:
        return replace(existing, dst_file=dst_file)
    return existing


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
        seen: dict[tuple[str, str, str], int] = {}

        def add_edges(edges: Iterable[TypeEdge]) -> None:
            for e in edges:
                key = (e.src, e.dst, e.kind)
                idx = seen.get(key)
                if idx is None:
                    seen[key] = len(all_edges)
                    all_edges.append(e)
                else:
                    # A different TU may see the same logical edge with richer
                    # provenance (one TU doesn't include the header declaring
                    # the private dst, another does) — merge in the stronger
                    # confidence and any dst_file the first-seen edge lacked,
                    # rather than silently keeping whichever TU happened to
                    # run first (Codex review).
                    all_edges[idx] = _merge_type_edges(all_edges[idx], e)

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
