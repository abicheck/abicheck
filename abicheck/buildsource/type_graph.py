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
import re
import shutil
import subprocess  # noqa: S404 - type-graph extraction shells out to clang (never shell=True)
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from functools import partial
from typing import TYPE_CHECKING, Any

from .. import deadline
from .graph_facts import register_fact
from .source_graph import (
    CONF_HIGH,
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
    function_decl_identity,
)

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


def _top_level_paren_index(s: str) -> int:
    """Index of the first ``(`` not nested inside a ``<...>`` template-angle
    group, or -1. Distinguishes the outer parameter-list paren of a
    callback-shaped spelling (``"detail::Impl ()"``) from one nested inside
    an enclosing template instantiation (``"std::function<detail::Impl
    ()>"`` — the first "(" there belongs to the *argument*, not the outer
    type, and must not be treated as ending the outer spelling)."""
    depth = 0
    for i, c in enumerate(s):
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
        elif c == "(" and depth == 0:
            return i
    return -1


def _base_type_name(qual_type: str) -> str:
    """Strip cv/pointer/reference/array decoration down to a base type spelling.

    Best-effort textual normalization (``"const detail::Impl *"`` ->
    ``"detail::Impl"``), not a real type-identity resolution — matches the
    approximate/overapprox confidence this module labels its edges with.
    """
    s = (qual_type or "").strip()
    if not s:
        return ""
    # clang glues a top-level cv/restrict-qualified pointer directly to the
    # star with no separating space (``"detail::Impl *const"``, not
    # ``"* const"`` — Codex review; likewise ``"*restrict"`` for a C API's
    # `T * restrict` parameter); normalize so the loop below strips it like
    # the spaced form it already handles.
    s = (
        s.replace("*const", "* const")
        .replace("*volatile", "* volatile")
        .replace("*restrict", "* restrict")
        .replace("*__restrict__", "* __restrict__")
        .replace("*__restrict", "* __restrict")
    )
    changed = True
    while changed:
        changed = False
        # Array bounds must be stripped inside the loop, not only once at the
        # end — an array of pointers ("detail::Impl *[4]") ends in "]", so
        # the suffix-stripping checks below never fire before this runs, and
        # a one-shot strip *after* the loop leaves the trailing "*" behind
        # (Codex review).
        bracket = s.find("[")
        if bracket != -1:
            s = s[:bracket].strip()
            changed = True
        # A callback-shaped template argument (``std::function<detail::Impl
        # ()>``'s single argument spells as ``"detail::Impl ()"``, the
        # written function-signature form) carries a parameter-list suffix
        # this loop's other checks don't recognize, so "Impl ()" was looked
        # up as a type literally named that instead of "Impl" (Codex
        # review). Same fix as the array-bracket case above: strip up to the
        # first *top-level* "(" (depth 0 relative to any enclosing "<...>",
        # since e.g. "std::function<detail::Impl ()>" itself must NOT be cut
        # at the nested paren — that would butcher the outer template's own
        # closing ">") and keep looping so the leftover pointer/cv decoration
        # on the return-type spelling still gets stripped too.
        paren = _top_level_paren_index(s)
        if paren != -1:
            s = s[:paren].strip()
            changed = True
        for suf in ("&&", "&", "*"):
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
        for suf in (" const", " volatile", " __restrict__", " __restrict", " restrict"):
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
        for pre in _LEADING_TYPE_QUALS:
            if s.startswith(pre):
                s = s[len(pre) :]
                changed = True
    return s.strip()


def _is_excluded_type(name: str) -> bool:
    return name in _BUILTIN_TYPES


def _split_top_level_commas(s: str) -> list[str]:
    """Split *s* on commas at depth 0 (relative to both ``<...>`` and
    ``(...)``) — shared by a template-argument list and a callback-shaped
    parameter list, which use the identical splitting rule."""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for c in s:
        if c in "<(":
            depth += 1
        elif c in ">)":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur))
    return parts


def _matching_close_paren(s: str, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at *open_idx*, or -1."""
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


#: A parenthesized group that contains *only* a function-pointer/reference
#: declarator (``"*"``, ``"&"``, or a pointer-to-member-function
#: ``"Widget::*"``), not any real parameter. A direct (non-template)
#: callback parameter like ``"void (*)(detail::Impl)"`` has *two* top-level
#: paren groups — the declarator wrapper ``"(*)"`` and the actual parameter
#: list ``"(detail::Impl)"`` — and only the second is the real parameter
#: list (Codex review).
_DECLARATOR_ONLY_RE = re.compile(r"^(\*|&|(?P<owner>[\w:]+)::\*)?$")

#: A pointer-to-*member-data* spelling (no parens at all — distinct from a
#: pointer-to-member-*function*, which is parenthesized and matched by
#: ``_DECLARATOR_ONLY_RE`` instead): ``"int detail::Impl::*"`` names both a
#: member type (``int``) and an owner class (``detail::Impl``) — the owner
#: is the actual dependency a public field/parameter of this shape exposes,
#: but the plain trailing-``"*"``-stripping in ``_base_type_name`` leaves a
#: dangling ``"detail::Impl::"`` that matches no indexed declaration (Codex
#: review).
_PTR_TO_MEMBER_DATA_RE = re.compile(
    r"^(?P<member>.+?)\s+(?P<owner>[\w:]+)::\*\s*(?:const|volatile)?\s*$"
)


def _resolve_nested_type_names(raw: str) -> list[str]:
    """Every type name reachable from one (possibly callback-shaped)
    type spelling: its own base name, its own template arguments
    (recursively), and — for a callback-shaped spelling, whether written as
    a template argument (``"void (detail::Impl)"``,
    ``std::function<void(detail::Impl)>``'s single argument), a direct
    function-pointer declarator (``"void (*)(detail::Impl)"``), or a
    pointer-to-member(-function) (``"void (Owner::*)(Args)"`` /
    ``"int Owner::*"``) — each of its written parameter/owner types too.

    Truncating a callback-shaped spelling at its first top-level ``(`` (the
    same split ``_base_type_name`` applies to a *non-nested* callback type)
    would discard the parameter list entirely, so a private type appearing
    only as a callback *parameter* — not the whole instantiation, not the
    return type — produced no edge at all (Codex review).
    """
    ptm = _PTR_TO_MEMBER_DATA_RE.match(raw.strip())
    if ptm:
        names: list[str] = []
        for group in ("owner", "member"):
            base = _base_type_name(ptm.group(group))
            if base:
                names.append(base)
                names.extend(_template_arg_types(base))
        return names
    paren = _top_level_paren_index(raw)
    ret_raw = raw[:paren] if paren != -1 else raw
    names = []
    base = _base_type_name(ret_raw)
    if base:
        names.append(base)
        names.extend(_template_arg_types(base))
    if paren == -1:
        return names
    close = _matching_close_paren(raw, paren)
    inner = raw[paren + 1 : close] if close != -1 else raw[paren + 1 :]
    declarator = _DECLARATOR_ONLY_RE.match(inner.strip()) if close != -1 else None
    if declarator:
        # This first paren group is just a pointer/reference/pointer-to-
        # member-function declarator ("(*)", "(Owner::*)"), not the
        # parameter list — the real one is the *next* top-level paren group.
        owner = declarator.group("owner")
        if owner:
            owner_base = _base_type_name(owner)
            if owner_base:
                names.append(owner_base)
                names.extend(_template_arg_types(owner_base))
        next_rel = _top_level_paren_index(raw[close + 1 :])
        if next_rel == -1:
            return names
        next_paren = close + 1 + next_rel
        next_close = _matching_close_paren(raw, next_paren)
        params = (
            raw[next_paren + 1 : next_close]
            if next_close != -1
            else raw[next_paren + 1 :]
        )
    else:
        params = inner
    for p in _split_top_level_commas(params):
        if p.strip():
            names.extend(_resolve_nested_type_names(p))
    return names


def _template_arg_types(base_type: str) -> list[str]:
    """This type's template argument spellings (recursively), each resolved
    via :func:`_resolve_nested_type_names`.

    A field typed ``std::unique_ptr<detail::Impl>`` — the common PImpl/
    container pattern this module exists to catch — resolves the *whole*
    instantiation spelling as one endpoint (``std::unique_ptr<detail::Impl>``,
    which never matches an indexed declaration and stays unprovenanced), so
    the actual private-type dependency on ``detail::Impl`` was invisible
    (Codex review's exact example). Best-effort bracket/depth matching over
    the textual spelling, not real template-argument parsing: extracts each
    top-level comma-separated argument inside the outermost ``<...>``, then
    recurses so a nested instantiation (``std::vector<std::unique_ptr<X>>``)
    still reaches ``X``.
    """
    start = base_type.find("<")
    if start == -1:
        return []
    depth = 0
    end = -1
    for i in range(start, len(base_type)):
        c = base_type[i]
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return []
    inner = base_type[start + 1 : end]
    args: list[str] = []
    for raw in _split_top_level_commas(inner):
        args.extend(_resolve_nested_type_names(raw))
    return args


def _decl_type_name(node: dict[str, Any]) -> str:
    """A field/parameter decl's raw ``type.qualType`` spelling, unprocessed.

    Deliberately *not* run through :func:`_base_type_name` here — a direct
    (non-template) callback parameter like ``void (*)(detail::Impl)`` needs
    its parameter-list region intact for :func:`_resolve_nested_type_names`
    to still reach ``detail::Impl``; stripping to a bare base name this early
    would discard it before that extraction ever runs (Codex review).
    """
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        return str(type_obj.get("qualType", ""))
    return ""


def _decl_return_type_name(node: dict[str, Any]) -> str:
    """A function/method decl's raw return-type spelling, from its own
    ``type.qualType`` — unprocessed, like :func:`_decl_type_name`.

    clang spells a function decl's own type as the *whole signature*
    (``"detail::Impl *(int)"``, return type immediately followed by the
    parenthesized parameter list — Codex review: this was never read at all,
    so a public factory function returning a private type produced no
    ``DECL_HAS_TYPE`` edge). A **trailing** return type instead spells as
    ``"auto (Args) -> RetType"`` — the region before the parameter list is
    just the literal ``auto`` placeholder, not the real return type, so this
    checks for ``"->"`` first and uses what follows it when present (Codex
    review). Otherwise, best-effort split on the first *top-level* ``(``
    (not nested inside a ``<...>`` template-angle group — Codex review: a
    return type like ``std::function<detail::Impl ()>`` prints the whole
    function's own type as ``"std::function<detail::Impl ()> ()"``, and a
    naive ``find("(")`` stops at the callback's *inner* parameter list,
    truncating mid-template instead of at the function's own outer
    parameter list): correct for the overwhelmingly common case, but not
    real declarator parsing.

    A function *returning a function pointer* (``void (*make_cb())(detail::
    Impl)``) is a further wrinkle: clang nests the outer decl's own
    parameter list *inside* the first top-level paren group, alongside the
    return type's own declarator (``"void (*())(detail::Impl)"`` — the
    empty ``"()"`` right after ``"*"`` is ``make_cb``'s own args, not part
    of the return type). If that first group contains its own nested paren
    pair, this strips it out — collapsing ``"(*())"`` to ``"(*)"`` — leaving
    the properly-shaped ``"void (*)(detail::Impl)"`` return-type spelling
    :func:`_resolve_nested_type_names` already parses correctly (Codex
    review). Own-args content itself is discarded; the outer decl's real
    parameters are separately read from its ``ParmVarDecl`` children, not
    from this string.
    """
    type_obj = node.get("type")
    if not isinstance(type_obj, dict):
        return ""
    qual_type = str(type_obj.get("qualType", ""))
    arrow = qual_type.find("->")
    if arrow != -1:
        return qual_type[arrow + 2 :].strip()
    paren = _top_level_paren_index(qual_type)
    if paren == -1:
        return ""
    close = _matching_close_paren(qual_type, paren)
    if close == -1:
        return qual_type[:paren].strip()
    group = qual_type[paren + 1 : close]
    inner_paren = group.find("(")
    if inner_paren == -1:
        return qual_type[:paren].strip()
    inner_close = _matching_close_paren(group, inner_paren)
    declarator = group[:inner_paren] + (
        group[inner_close + 1 :] if inner_close != -1 else ""
    )
    return (qual_type[:paren] + "(" + declarator + ")" + qual_type[close + 1 :]).strip()


def _decl_identity(node: dict[str, Any]) -> str:
    """Stable identity for a decl node: mangled name when clang emits one."""
    return _normalize_mangled(str(node.get("mangledName") or node.get("name") or ""))


def _normalize_mangled(mangled: str) -> str:
    """Strip a spurious macOS Mach-O ABI leading underscore from an Itanium
    mangled name clang reports (``__ZN...`` -> ``_ZN...``).

    Mirrors ``call_graph._normalize_mangled`` (same bug, same fix, kept as a
    local copy rather than a cross-import since neither module currently
    imports the other and both sit near the AI-readiness line-count cap).
    On Darwin, clang's own AST dump reports a C++ decl's ``mangledName`` with
    the platform's extra linker-symbol-table underscore still attached, but
    the ``Function``/``Variable`` objects this identity must join against
    (``header_graph._decl_identity``, seeded from the flat ``AbiSnapshot``)
    carry the already-stripped, one-underscore form (normalized upstream in
    ``macho_metadata.py``/``dumper._dump_macho``). Left unstripped, a
    header-only graph's ``DECL_HAS_TYPE``/``TYPE_INHERITS`` edge rooted at a
    public *function* (as opposed to a type -- types don't get this
    decoration) never joins its ``SOURCE_DECLARES``-seeded counterpart, so
    it can never be recognized as a public graph entry
    (``is_public_dependency_node``) -- silently dropping every
    ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` finding rooted at a function on
    macOS. ``__Z`` is an unambiguous, platform-independent marker (a real
    Itanium mangled name always starts with ``_Z``; a literal C++ identifier
    starting with two underscores is reserved and never emitted here), so
    this is a no-op on Linux/Windows, where clang's ``mangledName`` is
    already the bare ``_Z...`` form.
    """
    return mangled[1:] if mangled.startswith("__Z") else mangled


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
    #: How *dst*'s name/identity was resolved (richer confidence/provenance,
    #: ADR-041 addendum + P1 #4): for `TYPE_INHERITS`/`TYPE_HAS_FIELD_TYPE`/
    #: `DECL_HAS_TYPE`, one of :func:`_resolve_type_name`'s
    #: :data:`RESOLUTION_SCOPE` (matched via the nearest-enclosing-scope walk
    #: — real C++ lookup order, the confident case), :data:`RESOLUTION_UNIQUE_CANDIDATE`
    #: (no scope matched, but exactly one same-bare-name declaration exists
    #: anywhere in the TU — a last-resort guess, weaker than a scope match
    #: even though both were previously folded into the same flat
    #: ``CONF_HIGH`` tier), or :data:`RESOLUTION_UNRESOLVED` (no candidate at
    #: all; the raw spelling is kept). For `DECL_REFERENCES_DECL`, one of
    #: :func:`_resolve_ref_identity`'s own :data:`RESOLUTION_REF_EXACT`/
    #: :data:`RESOLUTION_REF_UNIQUE_CANDIDATE`/:data:`RESOLUTION_REF_UNRESOLVED`
    #: — a distinct vocabulary (the underlying mechanism differs: stub
    #: completeness/id-index lookup, not C++ scope-qualified name lookup),
    #: but the same confidence pattern (only the "exact"-equivalent tier
    #: earns ``CONF_HIGH``). Excluded from equality/hash (``compare=False``):
    #: it's a purely informational refinement of *why* ``confidence`` (a
    #: normally-compared field) already came out what it did — the many
    #: existing tests asserting exact ``TypeEdge(...) ==`` equality against
    #: ``confidence``/``role``/``dst_file`` alone should not need updating
    #: for a field that adds detail without changing what they already
    #: verify.
    resolution: str = field(default="", compare=False)


def _index_declared_entities(
    node: Any,
    scope: list[str],
    cur_file: str,
    name_index: dict[str, list[str]],
    decl_file: dict[str, str],
    ref_name_index: dict[str, list[str]],
    id_index: dict[str, tuple[str, str]],
    in_body: bool = False,
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
                in_body,
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
                child,
                scope,
                cur_file,
                name_index,
                decl_file,
                ref_name_index,
                id_index,
                in_body,
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
                in_body,
            )
        return cur_file

    if kind == "FieldDecl" and name and not in_body:
        # A field's own qualified identity (``Widget::x``), mirroring
        # ``_walk_types``'s own ``field_ident`` computation — so a
        # DECL_REFERENCES_DECL edge whose *source* is a field (a default
        # member initializer, ``int x = detail::k;``) has a declaring file
        # to resolve, not just its target (Codex review: without this, such
        # a field was never seeded as a `source_decl` and never backfilled
        # either, so it carried no visibility at all and a public struct's
        # dependency on a private constant through it was silently dropped).
        # Deliberately NOT added to ``name_index``/``ref_name_index`` —
        # fields are not resolvable *type* targets or reference targets in
        # their own right, only entities with their own file to look up.
        field_ident = "::".join([*scope, name])
        if cur_file:
            decl_file.setdefault(field_ident, cur_file)
        for child in node.get("inner", []) or []:
            cur_file = _index_declared_entities(
                child,
                scope,
                cur_file,
                name_index,
                decl_file,
                ref_name_index,
                id_index,
                in_body,
            )
        return cur_file

    if kind in _FUNCTION_DECL_KINDS:
        # Everything declared inside a function/method body — including a
        # plain block-scope local (``int api() { int x; return x; }``) — is
        # a private implementation detail, not a meaningful dependency
        # target: it can never be reached from outside the function, so
        # indexing it as a resolvable reference the same way a namespace-
        # scope global is indexed would let a public function's *ordinary
        # local variables* get marked ``defined_in_project`` and reported by
        # ``public_to_internal_dependency`` as a hidden internal dependency
        # (Codex review).
        for child in node.get("inner", []) or []:
            cur_file = _index_declared_entities(
                child,
                scope,
                cur_file,
                name_index,
                decl_file,
                ref_name_index,
                id_index,
                True,
            )
        return cur_file

    if kind in _REFERENCE_DECL_KINDS and not in_body:
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
        # collapse to) and mapped to *both* the full identity and the file —
        # routing either through the shared identity->file `decl_file` dict,
        # or through the ambiguous bare-name `ref_name_index`, would still
        # pick whichever same-named declaration was indexed first (Codex
        # review). A stub that only carries an ``id`` (no ``mangledName``)
        # can then resolve its *identity* precisely too, not just its file —
        # e.g. disambiguating a reference to ``a::k`` from one to ``b::k``.
        if node_id and ident:
            id_index.setdefault(node_id, (ident, cur_file))

    for child in node.get("inner", []) or []:
        cur_file = _index_declared_entities(
            child,
            scope,
            cur_file,
            name_index,
            decl_file,
            ref_name_index,
            id_index,
            in_body,
        )
    return cur_file


#: :func:`_resolve_type_name` resolution labels (ADR-041 richer-confidence
#: addendum), ordered strongest first.
RESOLUTION_SCOPE = "scope"
RESOLUTION_UNIQUE_CANDIDATE = "unique_candidate"
RESOLUTION_UNRESOLVED = "unresolved"

#: :func:`_resolve_ref_identity` resolution labels (ADR-041 P1 #4): a
#: distinct vocabulary from the three above, since the underlying mechanism
#: differs (a ``DeclRefExpr`` stub's own completeness / the id-index's
#: unambiguous per-node match, vs. C++ scope-qualified name lookup) --
#: `RESOLUTION_REF_EXACT` covers both "the stub was already complete" and
#: "the id-index pinned an unambiguous match", since neither involves any
#: guessing, unlike `RESOLUTION_SCOPE`'s single decisive path.
RESOLUTION_REF_EXACT = "exact"
RESOLUTION_REF_UNIQUE_CANDIDATE = "unique_candidate"
RESOLUTION_REF_UNRESOLVED = "unresolved"


def _resolve_type_name(
    raw: str, scope: list[str], name_index: dict[str, list[str]]
) -> tuple[str, str]:
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

    Returns ``(name, resolution)`` — *resolution* is one of
    :data:`RESOLUTION_SCOPE` (a qualifying declaration was found via the
    scope walk — real C++ lookup order; this includes a **global**
    declaration whose resolved spelling happens to equal the raw spelling,
    e.g. ``"Base"`` at namespace scope, which is still a real scope match,
    not "unresolved" — Codex review), :data:`RESOLUTION_UNIQUE_CANDIDATE` (no
    scope in the walk matched, but exactly one same-bare-name declaration
    exists anywhere in the TU — a last-resort guess, weaker than a scope
    match even though a caller may still choose to trust it), or
    :data:`RESOLUTION_UNRESOLVED` (no candidate matched at all; the raw
    spelling is returned unchanged). Best effort, not a real semantic
    lookup.
    """
    if not raw:
        return raw, RESOLUTION_UNRESOLVED
    # A leading "::" is C++'s global-scope qualifier ("::ns::detail::Impl"),
    # not part of the name itself — the index stores declarations without it
    # (Codex review: matching on the unstripped spelling built "::::..." and
    # never joined the indexed "ns::detail::Impl").
    lookup = raw[2:] if raw.startswith("::") else raw
    leaf = lookup.rsplit("::", 1)[-1]
    candidates = name_index.get(leaf)
    if not candidates:
        return raw, RESOLUTION_UNRESOLVED
    suffix = "::" + lookup
    matching = [c for c in candidates if c == lookup or c.endswith(suffix)]
    if not matching:
        return raw, RESOLUTION_UNRESOLVED
    for k in range(len(scope), -1, -1):
        prefix = "::".join(scope[:k])
        target = f"{prefix}::{lookup}" if prefix else lookup
        if target in matching:
            return target, RESOLUTION_SCOPE
    if len(matching) == 1:
        return matching[0], RESOLUTION_UNIQUE_CANDIDATE
    return raw, RESOLUTION_UNRESOLVED


def _emit_type_edges(
    edges: list[TypeEdge],
    src: str,
    raw: str,
    kind: str,
    role: str,
    scope: list[str],
    name_index: dict[str, list[str]],
    decl_file: dict[str, str],
) -> None:
    """Resolve *raw* — a **raw, unprocessed** ``qualType`` spelling — and
    every nested type name :func:`_resolve_nested_type_names` finds inside
    it (template arguments, callback parameter/return types, recursively),
    appending a :class:`TypeEdge` for each resolved, non-excluded name.

    A field typed ``std::unique_ptr<detail::Impl>`` — the common PImpl/
    container pattern — would otherwise only produce an edge to the whole,
    never-resolvable instantiation spelling, hiding the actual private-type
    dependency on ``detail::Impl`` (Codex review). Duplicate names (e.g. the
    same private type appearing as two template arguments) are only emitted
    once per call site.
    """
    seen: set[str] = set()
    for candidate in _resolve_nested_type_names(raw):
        name, resolution = _resolve_type_name(candidate, scope, name_index)
        if not name or _is_excluded_type(name) or name in seen:
            continue
        seen.add(name)
        edges.append(
            TypeEdge(
                src,
                name,
                kind,
                CONF_HIGH if resolution == RESOLUTION_SCOPE else CONF_REDUCED,
                role,
                decl_file.get(name, ""),
                resolution,
            )
        )


def _resolve_ref_identity(
    ref: dict[str, Any],
    decl_file: dict[str, str],
    ref_name_index: dict[str, list[str]],
    id_index: dict[str, tuple[str, str]],
) -> tuple[str, str, str]:
    """Resolve a ``DeclRefExpr``'s ``referencedDecl`` to its full identity, file,
    and resolution tier (:data:`RESOLUTION_REF_EXACT`/
    :data:`RESOLUTION_REF_UNIQUE_CANDIDATE`/:data:`RESOLUTION_REF_UNRESOLVED`,
    ADR-041 P1 #4 — this edge kind used to be flat ``CONF_REDUCED`` regardless
    of how confidently its target was identified, the one edge kind the
    scope/unique_candidate/unresolved confidence pattern the rest of this
    module already uses hadn't reached).

    clang commonly emits an *incomplete* stub for ``referencedDecl`` — e.g.
    ``{"kind": "VarDecl", "name": "k"}`` with no ``mangledName``/``loc`` even
    though the full ``VarDecl`` elsewhere in the same TU carries both (Codex
    review). Keying the edge from the stub's bare-name identity means it never
    matches ``decl_file`` (indexed by the *full* declaration's identity), so
    the dependency's ``dst_file``/``defined_in_project`` provenance is lost —
    the exact scenario this module exists to catch
    (``inline int f() { return detail::k; }``).

    *Identity* resolution order: the stub's own mangled-or-bare identity when
    it already resolves (i.e. it *was* complete) — :data:`RESOLUTION_REF_EXACT`;
    else — before falling back to the ambiguous bare-name candidate list — the
    stub's own ``id`` looked up in *id_index*, which (like the file lookup
    below) is keyed by clang's per-node id rather than the shared bare name two
    same-named declarations in different scopes both collapse to (Codex
    review: this disambiguates ``a::k`` from ``b::k`` for *identity*, not only
    for ``dst_file`` as the id lookup previously did — a stub with no
    ``mangledName`` used to stay ambiguous even though the id already pinned
    down exactly which declaration it was) — this is *also*
    :data:`RESOLUTION_REF_EXACT`: a per-node id match is deterministic and
    unambiguous by construction, no guessing involved, exactly like an
    already-complete stub. Only when neither resolves does this fall back to
    the unique full declaration sharing the bare name, when unambiguous —
    :data:`RESOLUTION_REF_UNIQUE_CANDIDATE`, a genuine best-effort guess.
    Genuinely ambiguous bare names (no id match, more than one same-named
    candidate) are left unresolved (:data:`RESOLUTION_REF_UNRESOLVED`) rather
    than guessed, a known, documented limitation; a fully general fix needs a
    stable scope-qualified identity for every declaration, not just ones an
    ``id`` happens to disambiguate, tracked in ADR-041 P1.

    *File* resolution prefers the same *id_index* lookup — present even on
    an otherwise-incomplete stub and shared with the node's full declaration
    elsewhere in the same TU. Falls back to ``decl_file`` keyed by the
    resolved identity when no id match exists.
    """
    ident = _decl_identity(ref)
    node_id = str(ref.get("id") or "")
    id_hit = id_index.get(node_id)
    # The id-index match must win over "ident already happens to be a key in
    # decl_file" (Codex review): `decl_file` is keyed by bare identity across
    # the whole TU, so a same-named declaration in an unrelated scope can make
    # `ident in decl_file` true even though *this* stub names a different
    # declaration — the id-index lookup is per-node and unambiguous by
    # construction, so it must be tried first regardless.
    if id_hit and id_hit[0]:
        ident = id_hit[0]
        resolution = RESOLUTION_REF_EXACT
    elif ref.get("mangledName") and ident in decl_file:
        # The stub was already complete (a real mangledName, not just a bare
        # name that coincidentally matches another declaration) and resolves.
        resolution = RESOLUTION_REF_EXACT
    else:
        name = str(ref.get("name") or "")
        candidates = ref_name_index.get(name) if name else None
        if candidates and len(candidates) == 1:
            ident = candidates[0]
            resolution = RESOLUTION_REF_UNIQUE_CANDIDATE
        else:
            resolution = RESOLUTION_REF_UNRESOLVED
    file = (id_hit[1] if id_hit else "") or decl_file.get(ident, "")
    return ident, file, resolution


def _walk_types(
    node: Any,
    scope: list[str],
    enclosing_func: str,
    edges: list[TypeEdge],
    name_index: dict[str, list[str]],
    decl_file: dict[str, str],
    ref_name_index: dict[str, list[str]],
    id_index: dict[str, tuple[str, str]],
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
                raw_base = (
                    str(base_type.get("qualType", ""))
                    if isinstance(base_type, dict)
                    else ""
                )
                _emit_type_edges(
                    edges,
                    qname,
                    raw_base,
                    EDGE_TYPE_INHERITS,
                    "base",
                    child_scope,
                    name_index,
                    decl_file,
                )
            for child in node.get("inner", []) or []:
                if isinstance(child, dict) and child.get("kind") == "FieldDecl":
                    raw_field = _decl_type_name(child)
                    _emit_type_edges(
                        edges,
                        qname,
                        raw_field,
                        EDGE_TYPE_HAS_FIELD_TYPE,
                        "field",
                        child_scope,
                        name_index,
                        decl_file,
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

    if kind in _OTHER_TYPE_DECL_KINDS and name:
        # A public alias's *underlying* type was never emitted as a
        # dependency at all — only the alias's own name was indexed as a
        # resolvable target (Codex review: `using Handle = detail::Impl *;`
        # produced no edge from `Handle` to the private `detail::Impl` it
        # actually wraps, so `public_to_internal_dependency` had nothing to
        # report for APIs that only ever spell the public alias name).
        qname = "::".join([*scope, name])
        raw_underlying = _decl_type_name(node)
        _emit_type_edges(
            edges,
            qname,
            raw_underlying,
            EDGE_TYPE_HAS_FIELD_TYPE,
            "alias",
            scope,
            name_index,
            decl_file,
        )

    if kind == "VarDecl" and name and not enclosing_func:
        # A public/exported data declaration's *own* type was never emitted
        # either — this module only ever read a VarDecl's type when it was
        # the *target* of a DeclRefExpr, never at its own declaration site
        # (Codex review: `extern detail::Impl *g;` or a public static data
        # member produced no DECL_HAS_TYPE edge for the private pointee).
        # Block-scope locals are excluded the same way
        # `_index_declared_entities`'s `in_body` tracking excludes them from
        # provenance — `enclosing_func` is only truthy inside a function/
        # method body, never for a namespace- or class-scope declaration.
        #
        # Identity must be scope-qualified when unmangled (Codex review): a
        # public `extern "C"` variable inside a namespace (`namespace api {
        # extern "C" detail::Impl *g; }`) reports mangledName == name (no
        # real Itanium mangling), so SourceEntity.identity() falls back to
        # the qualified name "api::g" -- but the bare _decl_identity(node)
        # used here gives just "g", landing this edge's src on a different
        # decl:// node than the public SOURCE_DECLARES node, breaking
        # reachability from the public variable to its private pointee.
        # function_decl_identity() with an empty type_qual falls through to
        # the same bare-qualified-name case (a variable's SourceEntity never
        # sets signature_hash, unlike a function's), so it doubles as the
        # right fallback here too.
        qualified_name = "::".join([*scope, name]) if scope else name
        ident = function_decl_identity(
            _normalize_mangled(str(node.get("mangledName") or "")),
            name,
            qualified_name,
            "",
        )
        if ident:
            raw_var = _decl_type_name(node)
            _emit_type_edges(
                edges,
                ident,
                raw_var,
                EDGE_DECL_HAS_TYPE,
                "var",
                scope,
                name_index,
                decl_file,
            )

    if kind in _FUNCTION_DECL_KINDS:
        qualified_name = "::".join([*scope, name]) if scope else name
        type_obj = node.get("type")
        type_qual = (
            str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
        )
        ident = (
            function_decl_identity(
                _normalize_mangled(str(node.get("mangledName") or "")),
                name,
                qualified_name,
                type_qual,
            )
            if name
            else ""
        )
        if ident:
            raw_return = _decl_return_type_name(node)
            if raw_return:
                _emit_type_edges(
                    edges,
                    ident,
                    raw_return,
                    EDGE_DECL_HAS_TYPE,
                    "return",
                    scope,
                    name_index,
                    decl_file,
                )
            for child in node.get("inner", []) or []:
                if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
                    raw_param = _decl_type_name(child)
                    _emit_type_edges(
                        edges,
                        ident,
                        raw_param,
                        EDGE_DECL_HAS_TYPE,
                        "param",
                        scope,
                        name_index,
                        decl_file,
                    )
        next_func = ident or enclosing_func
        # Recurse into every child, including a ParmVarDecl — its type was
        # already recorded as a "param" edge above, but a default-argument
        # expression (e.g. `int f(int x = detail::k)`) lives *under* the
        # ParmVarDecl node itself, so skipping it entirely (rather than just
        # not re-emitting its type edge) silently dropped any
        # DECL_REFERENCES_DECL edge for a private constant read only in a
        # default argument (Codex review). ParmVarDecl isn't a case this
        # walk special-cases, so recursing into it just walks its children
        # with the enclosing function scope — the same as every other node.
        for child in node.get("inner", []) or []:
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

    if kind == "FieldDecl" and name:
        # A default member initializer (``int x = detail::k;``) lives under
        # the FieldDecl node itself, not inside a function body — without a
        # truthy enclosing_func of its own, this recursed with whatever the
        # *record's* enclosing_func was (empty for a top-level record), so
        # the DeclRefExpr guard below never fired and a reference in a
        # default member initializer produced no edge at all (CodeRabbit
        # review). Give it a scope-qualified identity, mirroring how a
        # function's own identity becomes the enclosing_func for its body.
        field_ident = "::".join([*scope, name])
        for child in node.get("inner", []) or []:
            _walk_types(
                child,
                scope,
                field_ident or enclosing_func,
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
            ref_ident, ref_file, ref_resolution = _resolve_ref_identity(
                ref, decl_file, ref_name_index, id_index
            )
            if ref_ident and ref_ident != enclosing_func:
                edges.append(
                    TypeEdge(
                        enclosing_func,
                        ref_ident,
                        EDGE_DECL_REFERENCES_DECL,
                        CONF_HIGH
                        if ref_resolution == RESOLUTION_REF_EXACT
                        else CONF_REDUCED,
                        "ref",
                        ref_file,
                        ref_resolution,
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
    """Dedup on ``(src, dst, kind, role)``, not just ``(src, dst, kind)``
    (Codex review, fresh evidence): a function that both returns and takes
    the same private type emits two real, role-distinct ``DECL_HAS_TYPE``
    edges sharing ``(src, dst, kind)`` (``role="return"`` vs.
    ``role="param"``) -- deduping on the coarser triple silently dropped
    the second role before it ever reached ``augment_graph_with_types``/
    ``add_edge``, so the ADR-046 D1 relation-key split downstream could
    never actually observe both roles from this producer no matter how
    ``add_edge`` itself was keyed.
    """
    seen: set[tuple[str, str, str, str]] = set()
    out: list[TypeEdge] = []
    for e in edges:
        key = (e.src, e.dst, e.kind, e.role)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def index_declared_type_files(ast: dict[str, Any]) -> dict[str, str]:
    """Public wrapper: qualified type/enum/typedef name -> declaring file.

    Reuses the same first-indexing pass :func:`parse_clang_ast_types` already
    runs over the AST (:func:`_index_declared_entities`), exposed standalone
    for a caller that only needs the declaring-file index (e.g. a header-only
    graph builder resolving a type's public/private origin) and not the
    type/reference edges themselves. Duplicates the one AST walk rather than
    threading an output parameter through the hardened, heavily-reviewed
    ``_index_declared_entities``/``_walk_types`` pair — an acceptable, cheap
    cost for a header-only pass (ADR-041 header-only-graph addendum).

    ``_index_declared_entities``'s ``decl_file`` output is a dict shared
    between two unrelated uses: type declarations (records/enums/typedefs,
    also indexed in ``name_index``) *and* var/enum-constant identities (used
    only to resolve ``DECL_REFERENCES_DECL`` edge targets, never indexed in
    ``name_index``). Returning it unfiltered would let a caller (Codex
    review: caught in ``header_graph.py``, which treats every key as a type)
    mistake a public constant or enum value for a record/enum/typedef
    declaration. Filtered here to exactly the qualified names
    ``name_index`` actually collected — the type-only subset.
    """
    name_index: dict[str, list[str]] = {}
    decl_file: dict[str, str] = {}
    ref_name_index: dict[str, list[str]] = {}
    id_index: dict[str, tuple[str, str]] = {}
    _index_declared_entities(
        ast, [], "", name_index, decl_file, ref_name_index, id_index
    )
    type_qnames = {qname for qnames in name_index.values() for qname in qnames}
    return {qname: decl_file[qname] for qname in type_qnames if qname in decl_file}


def index_declared_entity_files(ast: dict[str, Any]) -> dict[str, str]:
    """Public wrapper: every declared identity's own declaring file.

    Unlike :func:`index_declared_type_files`, deliberately **unfiltered** —
    every entity kind :func:`_index_declared_entities` indexes into
    ``decl_file`` (record/enum/typedef, var/enum-constant, and field), not
    just the type-only subset. Safe for a caller that only ever backfills a
    *declaration node's own origin* (always seeded as a ``source_decl``,
    never a type node) — the "public constant mistaken for a record/enum/
    typedef" risk :func:`index_declared_type_files`'s filtering guards
    against does not apply to that use.

    For a header-only graph, this resolves the *source* side of a
    ``DECL_REFERENCES_DECL`` edge whose source is a field's default member
    initializer (``struct Widget { int x = detail::k; };`` — ``Widget::x``
    is never seeded from ``snapshot.functions``/``snapshot.variables``, the
    flat model has no per-field entity to iterate) — mirroring the existing
    per-edge ``dst_file``/``caller_file``/``callee_file`` backfill for each
    edge's *target* (Codex review).
    """
    name_index: dict[str, list[str]] = {}
    decl_file: dict[str, str] = {}
    ref_name_index: dict[str, list[str]] = {}
    id_index: dict[str, tuple[str, str]] = {}
    _index_declared_entities(
        ast, [], "", name_index, decl_file, ref_name_index, id_index
    )
    return decl_file


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
    id_index: dict[str, tuple[str, str]] = {}
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
                # ADR-046 D2: route through register_fact, not a direct
                # existing.attrs[...] mutation — see source_graph.py's
                # identical fix (fold_source_edges) for why a direct
                # mutation is silently lost on the next to_dict()/from_dict()
                # round-trip.
                register_fact(
                    existing,
                    "type_graph",
                    e.confidence,
                    {"defined_in_project": True, "def_file": e.dst_file},
                )
        before = len(graph.edges)
        edge_attrs: dict[str, Any] = {}
        if e.role:
            edge_attrs["role"] = e.role
        if e.resolution:
            edge_attrs["resolution"] = e.resolution
        graph.add_edge(
            GraphEdge(
                src=src_id,
                dst=dst_id,
                kind=e.kind,
                provenance="type_graph",
                confidence=e.confidence,
                attrs=edge_attrs,
            )
        )
        added += len(graph.edges) - before
    return added


#: Confidence label -> rank, for picking the stronger of two duplicate edges.
_CONFIDENCE_RANK = {CONF_HIGH: 2, CONF_REDUCED: 1, "unknown": 0}


def _merge_type_edges(existing: TypeEdge, new: TypeEdge) -> TypeEdge:
    """Merge two edges sharing a ``(src, dst, kind, role)`` key from different TUs.

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
        local_cap = 120.0
        scan_remaining = deadline.remaining()
        effective_timeout = (
            local_cap if scan_remaining is None else min(local_cap, scan_remaining)
        )
        try:
            # Bound by min(local_cap, active --budget deadline), process-group-
            # safe on timeout, degrades to the same diagnostic+[] contract on
            # overflow — mirrors
            # call_graph.ClangCallGraphExtractor._extract_from_safe_args
            # (Codex review, PR #591, round 8).
            with deadline.deadline_scope(effective_timeout):
                proc = deadline.run_bounded(  # noqa: S603 - fixed argv, never shell=True
                    cmd,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=local_cap,
                )
        except (OSError, subprocess.SubprocessError, deadline.DeadlineExceeded) as exc:
            self.diagnostics.append(f"clang invocation failed: {exc}")
            return []
        if not proc.stdout.strip():
            self.diagnostics.append(
                f"clang produced no AST (stderr: {proc.stderr[:200]})"
            )
            return []
        # A non-zero exit (real compile errors in the replayed, necessarily
        # approximate flag subset) does not stop clang's AST dump from still
        # printing a partial, error-recovered tree. Still salvage any edges
        # from that best-effort AST, but record a diagnostic regardless so
        # extractor_pass_fully_covered (ADR-041 P0 slice 3, ninth Codex
        # review) never treats this TU as cleanly, fully parsed — mirrors
        # call_graph.ClangCallGraphExtractor._extract_from_safe_args.
        if proc.returncode != 0:
            self.diagnostics.append(
                f"clang exited {proc.returncode} (stderr: {proc.stderr[:200]})"
            )
        try:
            # clang can exit successfully right as the budget expires; recheck
            # before the CPU/RSS-heavy parse+walk, same as the L2/L4 post-run
            # checks and call_graph's identical fix (Codex review, PR #591).
            deadline.check()
        except deadline.DeadlineExceeded as exc:
            self.diagnostics.append(
                f"scan deadline exceeded before parsing clang AST: {exc}"
            )
            return []
        try:
            ast = json.loads(proc.stdout)
        except (ValueError, RecursionError) as exc:
            self.diagnostics.append(f"could not parse clang AST JSON: {exc}")
            return []
        try:
            # The JSON load itself can consume the rest of the budget on a
            # huge AST; re-check before the recursive walk (Codex review,
            # PR #591, round 4).
            deadline.check()
        except deadline.DeadlineExceeded as exc:
            self.diagnostics.append(
                f"scan deadline exceeded before walking clang AST: {exc}"
            )
            return []
        try:
            return parse_clang_ast_types(ast)
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

        all_edges: list[TypeEdge] = []
        # Role-aware key (Codex review, fresh evidence) -- same fix as
        # _dedupe_edges above, applied to the cross-TU merge: two TUs
        # emitting the same private type as a function's return type in one
        # and its parameter type in the other must not collapse onto a
        # single role, the same way one TU's own return+param edges must not.
        seen: dict[tuple[str, str, str, str], int] = {}

        def add_edges(edges: Iterable[TypeEdge]) -> None:
            for e in edges:
                key = (e.src, e.dst, e.kind, e.role)
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
                pool_worker = partial(
                    _deadline_bound_worker,
                    deadline.current_deadline_ts(),
                    self._extract_from_compile_unit,
                )
                with ThreadPoolExecutor(max_workers=self.last_jobs) as pool:
                    for edges in pool.map(pool_worker, units):
                        add_edges(edges)
            else:
                for cu in units:
                    add_edges(self._extract_from_compile_unit(cu))
        finally:
            self.last_elapsed_s = time.monotonic() - start

        return all_edges
