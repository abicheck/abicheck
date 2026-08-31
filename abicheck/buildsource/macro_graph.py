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

"""Macro/config dependency graph extraction (G29 Phase 5 item 2, G29.6's
second open graph family).

Populates two of the five edge kinds the plan originally sketched for this
family (:data:`~abicheck.buildsource.graph_facts.MACRO_DEP_EDGE_KINDS`):

- :data:`EDGE_MACRO_CONTROLS_DECL` (macro node -> source_decl node) — a
  declaration is compiled only under a simple ``#ifdef X`` / ``#ifndef X`` /
  ``#if defined(X)`` / ``#if !defined(X)`` conditional region. Structural,
  ``CONF_HIGH``: this is a pure textual fact about the raw source (which
  ``#if``/``#endif`` pair a line falls inside), not a guess. **A known,
  accepted gap for the two negated forms** (``#ifndef X``/``#if !defined(X)``,
  and the ``#else`` branch of a plain ``#ifdef X``) (Codex review, PR #708):
  this edge only fires when the graph already carries a ``macro`` node for
  ``X`` (the join-only-onto-an-existing-node rule two paragraphs below), and
  a ``macro`` node is only ever seeded from an L4 ``SourceAbiSurface``'s
  *reachable, defined* macros — a guard macro that is genuinely undefined in
  the build being scanned (the common real case for a negated region: that's
  exactly why the negated branch was taken) was never observed as a
  ``#define`` in this configuration at all, so no node exists for it to
  join onto. Deliberately not "fixed" by minting a speculative node here —
  that would override the same join-only invariant this module's own test
  suite pins down (``test_augment_never_mints_new_nodes``), and would need
  its own scoped design decision (macro nodes are cheap/bounded unlike the
  thousands of internal-only symbols ``archive_graph.py``'s identical rule
  guards against, so the tradeoff may differ — but that is a decision for a
  follow-up, not a drive-by override of a tested invariant). In practice this
  edge still fires reliably whenever the *same* macro is also referenced
  positively somewhere reachable elsewhere (seeding the node), just not for
  a guard macro that is negated-only across the whole reachable surface.
- :data:`EDGE_DECL_USES_MACRO` (source_decl node -> macro node) — a
  declaration's own signature/body span contains a word-boundary reference to
  a macro name that is ``#define``d earlier in the same file. A textual
  heuristic, not semantic preprocessing, so it is ``CONF_REDUCED`` — see
  :func:`find_decl_macro_uses`'s docstring for the concrete false-positive/
  false-negative tradeoffs this accepts.

``MACRO_EXPANDS_TO_VALUE``/``MACRO_EXPANDS_TO_TYPE``/``MACRO_CONTROLS_EDGE``
remain **reserved, unpopulated** vocabulary (registered in
``graph_facts.MACRO_DEP_EDGE_KINDS`` so a hand-built or newer graph naming one
is never rejected): the first two need real macro-*expansion* tracing (what a
macro's replacement text resolves to, as a value or a type) — a full
preprocessor substitution model, well beyond this slice's guard/reference
detection; the third needs *per-edge*, not per-declaration, conditional
attribution — knowing that one specific call/reference *inside* an otherwise
unguarded declaration's body is itself nested under a *different* ``#ifdef``
than the declaration's own guard, which needs a source-range-aware walk of
each edge's own call site, not the whole-declaration region join this slice
performs. No new **node** kind is needed for any of this family — every edge
above joins onto the ``macro``/``source_decl`` node kinds
:mod:`~abicheck.model.source_graph` already declares (populated from an
L4 ``SourceAbiSurface``'s ``reachable_macros``/declarations, or a hand-built
graph); this module never mints one that doesn't already exist (same
"join-only-onto-an-existing-node" discipline as ADR-057 D1 and
``archive_graph.py``'s ``OBJECT_DEFINES_SYMBOL``).

Architecture mirrors ``type_graph.py`` (two Clang-adjacent passes, not
``archive_graph.py``'s single disk-only pass, since this family needs both an
AST pass *and* a raw-text scan the AST alone cannot answer):

- **Pass A** (:func:`parse_clang_ast_decl_ranges`) — a pure function over a
  ``clang -Xclang -ast-dump=json`` tree indexing every top-level/namespace/
  class-member declaration's identity and ``(file, begin_line, end_line)``
  span. Declarations only — clang's AST carries no representation of
  preprocessor conditionals at all (confirmed empirically: a ``#ifdef``/
  ``#define`` leaves no trace in the dumped tree; the surrounding
  declarations simply appear or don't, with no marker of which guard
  admitted them), which is exactly why Pass B below has to work from the raw
  text instead.
- **Pass B** (:func:`scan_conditional_regions`, :func:`find_decl_macro_uses`)
  — pure line-based text scans over the same file's raw source, independent
  of Pass A's AST walk.
- :func:`augment_graph_with_macro_dependencies` joins the two: for each
  indexed declaration that already has a ``source_decl`` node in the graph,
  every conditional region containing its ``begin_line`` becomes a
  ``MACRO_CONTROLS_DECL`` edge, and every macro name its own span textually
  references (defined earlier in the same file) becomes a ``DECL_USES_MACRO``
  edge.
- :class:`ClangMacroGraphExtractor` is the live, side-effecting wrapper
  (integration-only), mirroring :class:`~abicheck.buildsource.type_graph.
  ClangTypeGraphExtractor`'s shape and reusing ``call_graph.py``'s vetted
  argv-sanitizing/deadline/parallelism helpers rather than a fresh copy.

**Empirical AST-dump finding, load-bearing for Pass A** (this module's own
discovery, verified against real ``clang -Xclang -ast-dump=json`` output,
Ubuntu clang 18): a declaration's ``range.begin``/``range.end`` objects do
**not** reliably carry a ``"line"`` key — clang's JSON node dumper prints a
*single, whole-AST-wide* sticky ``(file, line)`` cursor shared across every
node's ``loc``, then ``range.begin``, then ``range.end`` (in that field
order, confirmed via raw key-order inspection), advancing it and printing a
field only when it changes, exactly like the file-only sticky tracking
``type_graph._node_file``/``_index_children`` already thread through
*siblings* — except this one is a single cursor threaded through the *entire*
document-order traversal (every node, not just the declarations this module
cares about, including every statement/expression inside a function body),
and it tracks ``line`` as well as ``file``. Skipping any node's ``loc``/
``range`` while walking (e.g. only recursing into declaration-shaped kinds)
desyncs the cursor and returns wrong line numbers for every later sibling.
:class:`_LocationCursor` plus :func:`_walk_decl_ranges`'s unconditional
recursion into every ``"inner"`` child (regardless of kind) replicates this
exactly; verified against a real compiled fixture spanning a multi-line
struct, an inline function with a multi-statement body, and a declaration
following it, all resolving to their correct source lines end to end.

**Two more accepted, documented limitations** (best-effort, same spirit as
``type_graph.py``'s own approximate-lookup tradeoffs):

- :func:`scan_conditional_regions` only recognizes the two simple,
  single-macro-name conditional forms named above. A compound condition
  (``#if defined(X) && defined(Y)``, ``#if VALUE > 3``) or an ``#elif`` chain
  is deliberately **not** attributed at all — neither its own ``#if``
  branch nor (for a compound condition) its ``#else`` branch — while nesting
  depth is still tracked correctly across it (its own ``#endif`` still pops
  the stack), so a *sibling* or *enclosing* simple guard elsewhere in the
  file is never desynchronized by an unmodeled block nested inside or beside
  it.
- :func:`find_decl_macro_uses` is a textual, not semantic, scan: it cannot
  tell an identifier that happens to share a defined macro's name from a
  genuine macro reference (a local variable, field, or unrelated type also
  named ``FOO`` inside a declaration whose span textually contains
  ``#define FOO ...`` earlier in the file is reported as "uses ``FOO``" the
  same as a real macro reference would be) — a known, accepted over-match,
  not a bug to chase without real preprocessing. It is also strictly
  same-file: a macro ``#define``d in one header and referenced from a
  declaration in a different file/TU is not modeled (cross-file macro-use
  tracking is a stretch goal deliberately left out of this slice).

**A third accepted, documented limitation, investigated and deliberately
NOT closed this pass (Codex review, fresh evidence): backslash-newline line
splicing before a ``//`` line comment.** A real preprocessor removes every
``\\<newline>`` pair *before* tokenizing, so ``// comment continues \\`` +
a following physical line makes that WHOLE next physical line part of the
same, still-open comment — including anything that looks like a directive.
This scanner operates per *physical* line and has no concept of splicing at
all, so a genuinely live directive commented out this way (`` // ... \\ ``
followed by ``#endif``) is read as a real, live ``#endif`` on its own
physical line — reproduced empirically: nested inside an enclosing guard,
this pops that guard's frame early, the identical truncation shape every
other comment-tracking fix in this module closes for a *different*
mechanism (`` /* */ `` nesting, a leading same-line comment, a carried-over
multi-line comment closing mid-line). Deliberately not attempted here: unlike
those three, correctly handling splicing means tracking, per physical line,
whether the immediately preceding physical line's own ``//`` comment (or
any other spliced logical line) carries forward onto it — a genuinely
different propagation mechanism from the existing ``in_block`` carry-over,
and one that must be threaded through every line-number-keyed caller
(``ConditionalRegion``'s ``start_line``/``end_line``, ``DeclRange``
matching) without disturbing their existing physical-line-number contract,
since clang's own AST ranges are physical-line-based too. A narrower,
same-slice fix risks getting that line-number fidelity subtly wrong in a
way worse than the current, honest gap. Pinned by a dedicated regression
test documenting the current, known-incomplete behavior rather than
silently accepted.

**A fourth accepted, documented limitation, investigated and deliberately
NOT closed this pass (Codex review, fresh evidence): a block comment
appearing MID-directive (``#/**/ifdef X``, ``#if/**/defined(X)``).** A real
preprocessor treats ``/* */`` as whitespace anywhere it appears, including
between ``#`` and the directive keyword or between the keyword and its
operand — but every directive-family regex here only tolerates plain
whitespace (``\\s*``/``\\s+``) at those positions, and
:func:`_strip_leading_inline_comment` only strips a comment BEFORE the
leading ``#``, not one embedded after it. Reproduced empirically: neither
shape above matches any pattern, including the unmodeled ``_IF_RE``
fallback, so the directive is invisible to this scanner entirely (worse
than "unmodeled" — its own ``#endif`` can also desync an enclosing guard's
nesting the same way an unrecognized directive already does elsewhere in
this module). Deliberately not attempted here: correctly handling it needs
replacing every ``\\s*``/``\\s+`` gap in every directive-family regex with a
comment-or-whitespace equivalent — a systematic rewrite across this whole
pattern set, not a narrow addition — and mid-token block comments inside a
preprocessor directive are exceedingly rare, deliberately obfuscated
styling in practice (unlike the leading/multi-line comment placements
already fixed above, which are realistic, commonly-seen shapes). Pinned by
a dedicated regression test rather than silently accepted.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from functools import partial
from typing import TYPE_CHECKING, Any

from .. import deadline
from .clang_ast_run import run_clang_ast_dump
from .graph_facts import CONF_HIGH, CONF_REDUCED, GraphEdge
from .preprocessor_scan import _DEFINE_RE
from .type_graph import (
    _FUNCTION_DECL_KINDS,
    _OTHER_TYPE_DECL_KINDS,
    _RECORD_DECL_KINDS,
    _function_decl_ident,
)

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .build_evidence import BuildEvidence, CompileUnit as BuildEvidenceCompileUnit

# ── edge kinds (reserved by graph_facts.MACRO_DEP_EDGE_KINDS) ──────────────
EDGE_MACRO_CONTROLS_DECL = "MACRO_CONTROLS_DECL"
EDGE_DECL_USES_MACRO = "DECL_USES_MACRO"


# ── Pass A: declaration ranges from a clang AST dump ────────────────────────


@dataclass(frozen=True)
class DeclRange:
    """One indexed declaration's identity and source span (Pass A)."""

    identity: str
    file: str
    begin_line: int
    end_line: int


@dataclass
class _LocationCursor:
    """The single sticky ``(file, line)`` cursor clang's JSON AST dumper
    threads through the *whole* document-order traversal (see the module
    docstring's empirical finding). ``advance`` mutates and returns the
    resolved location for one ``loc``/``range.begin``/``range.end`` dict,
    which may supply only a subset of ``file``/``line`` (or neither).
    """

    file: str = ""
    line: int = 0

    def advance(self, loc: dict[str, Any]) -> tuple[str, int]:
        # A location produced by macro expansion nests as
        # ``{"spellingLoc": {...}, "expansionLoc": {...}}`` instead of the
        # flat ``file``/``line`` keys this method otherwise applies directly
        # (Codex review, PR #708).
        #
        # An *earlier* fix here chose one of the two nested dicts wholesale
        # (preferring ``expansionLoc``) — a follow-up review round, backed by
        # fresh real-``clang`` evidence, found this loses the file entirely
        # in a real (and common) shape: for the *first* AST node in a TU that
        # names a file at all, clang can print ``spellingLoc.file`` (the
        # macro-argument/definition site) while ``expansionLoc`` carries only
        # ``line``/``col``/``offset`` — its own ``file`` is sticky-omitted
        # against the whole-document cursor, which at that point hasn't been
        # set to anything yet. Choosing ``expansionLoc`` wholesale then left
        # ``cursor.file`` empty, discarding that declaration and, since the
        # cursor never recovered, every later sibling relying on the same
        # sticky file too.
        #
        # Empirically confirmed (three real ``clang -ast-dump=json`` cases,
        # this fix's own verification): the two nested dicts are NOT
        # alternatives to pick between — they are two more updates to the
        # *same* single sticky cursor this method already threads through
        # the flat case, applied in sequence. Advancing through
        # ``spellingLoc`` first, then ``expansionLoc``, reproduces clang's
        # real behavior in all three shapes tested: (1) a macro-argument
        # expansion, where ``spellingLoc`` alone carries the correct
        # same-file location and ``expansionLoc`` omits ``file`` (sticky —
        # unchanged); (2) a macro *body* expansion from a genuinely
        # different file (the macro's own definition site), where
        # ``expansionLoc`` explicitly restates ``file`` back to the real
        # invocation file precisely because it differs from what
        # ``spellingLoc`` just made sticky — so the second ``advance`` call
        # correctly overrides the first; (3) the plain non-macro flat case,
        # where neither nested key is present and this loop is a no-op,
        # unchanged from before. Sequencing them through the same mutating
        # ``if "file"/"line" in loc`` logic below — rather than a special
        # "pick one" branch — is what makes case (2)'s override and case
        # (1)'s carry-through both fall out for free.
        for nested_key in ("spellingLoc", "expansionLoc"):
            nested = loc.get(nested_key)
            if isinstance(nested, dict):
                if "file" in nested:
                    self.file = str(nested["file"])
                if "line" in nested:
                    self.line = int(nested["line"])
        if "spellingLoc" not in loc and "expansionLoc" not in loc:
            if "file" in loc:
                self.file = str(loc["file"])
            if "line" in loc:
                self.line = int(loc["line"])
        return (self.file, self.line)


def _normalize_mangled(mangled: str) -> str:
    """Strip a spurious macOS Mach-O leading underscore, mirroring
    ``call_graph._normalize_mangled``/``type_graph._normalize_mangled`` (same
    bug, same fix, kept as a local copy per those modules' own documented
    reasoning: this module doesn't otherwise depend on either)."""
    return mangled[1:] if mangled.startswith("__Z") else mangled


def _var_decl_identity(node: dict[str, Any], scope: list[str], name: str) -> str:
    """A namespace/class-scope variable's identity, mirroring
    ``type_graph._emit_var_decl_edge``'s identity computation (that function
    only emits an edge, it doesn't expose the identity standalone)."""
    from ..model.source_graph import function_decl_identity

    return function_decl_identity(
        _normalize_mangled(str(node.get("mangledName") or "")),
        name,
        "::".join([*scope, name]) if scope else name,
        "",
    )


def _walk_decl_ranges(
    node: Any,
    scope: list[str],
    cursor: _LocationCursor,
    out: list[DeclRange],
    in_body: bool,
) -> None:
    """Recursive AST walk producing one :class:`DeclRange` per top-level/
    namespace/class-member declaration with a resolvable range.

    Recurses into **every** child regardless of kind (statements,
    expressions, parameters — not just declaration-shaped nodes), because
    :class:`_LocationCursor` must observe every node's ``loc``/``range`` in
    document order to stay in sync (see the module docstring's empirical
    finding) — skipping a node here would silently desync every later
    sibling's line numbers, not just drop that one node's own entry.
    """
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind", ""))
    name = str(node.get("name") or "")

    loc = node.get("loc")
    if isinstance(loc, dict):
        cursor.advance(loc)
    begin_file = ""
    begin_line_n = end_line_n = 0
    rng = node.get("range")
    if isinstance(rng, dict):
        b = rng.get("begin")
        if isinstance(b, dict):
            begin_file, begin_line_n = cursor.advance(b)
        e = rng.get("end")
        if isinstance(e, dict):
            _, end_line_n = cursor.advance(e)
    has_range = bool(begin_file) and begin_line_n > 0 and end_line_n > 0

    child_scope = scope
    next_in_body = in_body

    if kind in _RECORD_DECL_KINDS and name:
        if has_range and not in_body:
            out.append(
                DeclRange(
                    "::".join([*scope, name]), begin_file, begin_line_n, end_line_n
                )
            )
        child_scope = [*scope, name]
    elif kind == "NamespaceDecl" and name:
        child_scope = [*scope, name]
    elif kind in _OTHER_TYPE_DECL_KINDS and name:
        if has_range and not in_body:
            out.append(
                DeclRange(
                    "::".join([*scope, name]), begin_file, begin_line_n, end_line_n
                )
            )
    elif kind in _FUNCTION_DECL_KINDS:
        if has_range:
            ident = _function_decl_ident(node, scope, name)
            if ident:
                out.append(DeclRange(ident, begin_file, begin_line_n, end_line_n))
        next_in_body = True
    elif kind == "VarDecl" and name and not in_body:
        if has_range:
            ident = _var_decl_identity(node, scope, name)
            if ident:
                out.append(DeclRange(ident, begin_file, begin_line_n, end_line_n))

    for child in node.get("inner", []) or []:
        _walk_decl_ranges(child, child_scope, cursor, out, next_in_body)


def parse_clang_ast_decl_ranges(ast: dict[str, Any]) -> list[DeclRange]:
    """Extract declaration source spans from a ``clang -ast-dump=json`` tree
    (pure). See the module docstring for the empirical AST-dump quirk this
    depends on and :func:`_walk_decl_ranges` for the walk itself.
    """
    out: list[DeclRange] = []
    _walk_decl_ranges(ast, [], _LocationCursor(), out, False)
    return out


# ── Pass B: raw-text conditional-region and macro-definition scans ─────────


@dataclass(frozen=True)
class ConditionalRegion:
    """One simple ``#ifdef``/``#ifndef``/``#if defined``/``#if !defined``
    region's line span (Pass B) — ``[start_line, end_line]`` inclusive, the
    lines *between* the opening directive and the matching ``#else``/
    ``#endif`` (exclusive of the directive lines themselves)."""

    macro: str
    negated: bool
    start_line: int
    end_line: int


# A trailing same-line comment (`// ...` or a single-line `/* ... */`) after
# an otherwise-simple guard must not defeat the end-anchored match below —
# `#ifdef FEATURE_X // enabled by build` is still a simple, single-macro
# guard, not a compound condition (Codex review, PR #708: the original
# end-anchored patterns had no allowance for one, so a real-world guard
# carrying an explanatory comment silently produced zero regions/edges for
# its whole body). Anchored to the true end of line either way — a `/* ...
# */` that doesn't close on the same line is not matched by this suffix,
# which correctly falls through to `_IF_RE` below (unmodeled) rather than
# guessing where a multi-line comment ends.
_TRAILING_COMMENT = r"(?:\s*(?://.*|/\*.*?\*/\s*))?"
_IFDEF_RE = re.compile(rf"^\s*#\s*ifdef\s+(\w+)\s*{_TRAILING_COMMENT}$")
_IFNDEF_RE = re.compile(rf"^\s*#\s*ifndef\s+(\w+)\s*{_TRAILING_COMMENT}$")
#: `defined`'s operand may be parenthesized (`defined(X)`) or bare (`defined
#: X`) -- both are valid, real-compiler-accepted preprocessor syntax (Codex
#: review, fresh evidence: `#if defined FEATURE_X` fell through to the
#: unmodeled `_IF_RE` fallback, silently omitting a real guard's
#: `MACRO_CONTROLS_DECL` edge with no diagnostic to signal it). The
#: alternation is written so a MISMATCHED form (`defined(X` with no closing
#: paren, or `defined X)` with a stray one) still fails to match either
#: branch, falling through to the unmodeled fallback rather than accepting
#: malformed input as if it were one of the two valid forms.
_IF_DEFINED_RE = re.compile(
    rf"^\s*#\s*if\s+defined\s*(?:\(\s*(\w+)\s*\)|(\w+))\s*{_TRAILING_COMMENT}$"
)
_IF_NOT_DEFINED_RE = re.compile(
    rf"^\s*#\s*if\s+!\s*defined\s*(?:\(\s*(\w+)\s*\)|(\w+))\s*{_TRAILING_COMMENT}$"
)
#: The unmodeled-frame fallback -- also matches `#ifdef`/`#ifndef` (Codex
#: review, fresh evidence), not just bare/compound `#if`: a malformed-but-
#: compiler-accepted directive with trailing tokens (`#ifdef FEATURE_X
#: extra`) or a line continuation fails all four simple-form patterns above,
#: and the original `#\s*if\b` alone doesn't match `#ifdef`/`#ifndef` either
#: (`\b` fails right after "if", since "d"/"n" are both word characters) --
#: so no frame was pushed at all, and the matching `#endif` popped the
#: *enclosing* guard's frame instead, truncating it early. This still
#: correctly matches plain `#if`/`#ifdef`/`#ifndef` alike as an unmodeled
#: frame push when the four simple patterns didn't already claim the line.
_IF_RE = re.compile(r"^\s*#\s*if(?:def|ndef)?\b")
#: Also matches C++23's `#elifdef`/`#elifndef` (Codex review, fresh
#: evidence), not just bare/compound `#elif` -- the identical `\b`-after-
#: "elif" gap the `_IF_RE` fix above already closed for `#ifdef`/`#ifndef`
#: ("d"/"n" are word characters too, so `\b` fails right after "elif").
#: Unrecognized, `#elifdef`/`#elifndef` matched no pattern at all (not even
#: this fallback), so the ORIGINAL `#if`'s frame stayed open across it --
#: reproduced empirically: `#if defined(A) ... #elifdef B ... #endif`
#: incorrectly attributed the `#elifdef B` branch's declarations to `A`
#: with `negated=False`, a wrong high-confidence `MACRO_CONTROLS_DECL` edge,
#: not merely a missing one.
_ELIF_RE = re.compile(r"^\s*#\s*elif(?:def|ndef)?\b")
_ELSE_RE = re.compile(r"^\s*#\s*else\b")
_ENDIF_RE = re.compile(r"^\s*#\s*endif\b")

#: A same-line-CLOSED leading block comment, greedy-minimal so
#: ``/* a */ /* b */ #ifdef X`` strips both in one pass via repeated
#: application (see :func:`_strip_leading_inline_comment`). Deliberately
#: does not attempt a ``//`` line comment: a genuine ``// #ifdef X`` line
#: has its directive commented out for real (nothing follows to strip down
#: to), unlike a real compiler which still parses whatever text follows a
#: ``/* ... */`` that closes on the same line.
_LEADING_INLINE_BLOCK_COMMENT_RE = re.compile(r"^\s*/\*.*?\*/")


def _strip_leading_inline_comment(line: str) -> str:
    """Strip every same-line-closed leading ``/* ... */`` block comment (and
    the whitespace around it) from *line* (Codex review, fresh evidence): a
    real compiler treats a comment as whitespace, so ``/* note */ #ifdef
    X`` is a perfectly live directive -- but every directive-family regex
    in this module is anchored ``^\\s*#``, which a leading, still-present
    comment defeats outright. Reproduced empirically: this silently omitted
    the guarded declaration's macro edge, and — worse — when nested, left
    no stack frame for the un-recognized inner directive, so its own
    ``#endif`` popped the *enclosing* guard's frame instead, truncating it
    early (the same class of desync the ``#ifdef``/``#ifndef`` fallback fix
    closed for a differently-shaped input). Only ever strips a comment that
    both opens and closes on this one line — cross-line block-comment state
    is :func:`_lines_starting_inside_block_comment`'s own, separate job,
    already applied by every caller before this function ever runs, so a
    comment left open past this line was already skipped upstream.
    """
    stripped = line
    while True:
        new = _LEADING_INLINE_BLOCK_COMMENT_RE.sub("", stripped, count=1)
        if new == stripped:
            return stripped
        stripped = new


def _line_after_carryover_comment_closes(raw_line: str) -> str | None:
    """For *raw_line*, already known (per :func:`_lines_starting_inside_
    block_comment`) to START inside a block comment carried over from an
    earlier line -- the live remainder of *raw_line* after that SAME
    comment closes, or ``None`` if it doesn't close on this line at all
    (the whole line stays commented, the caller's existing skip-the-line
    behavior).

    Codex review, fresh evidence: a valid multi-line-opened comment that
    closes mid-line before a real directive (``/* opening\\n*/ #ifdef
    FEATURE_X``) was previously skipped wholesale -- every directive-family
    regex only ever saw the ORIGINAL line, never the text that survives
    after the carried-over comment ends -- omitting the guard, and, nested,
    letting its ``#endif`` pop the *enclosing* guard's frame instead (the
    same desync shape the leading-inline-comment fix closed for a
    same-line-only comment). Finding the first ``"*/"`` from the very start
    of the line is unambiguously this comment's own close: nothing inside
    an already-open block comment carries special meaning, so no
    string/char-literal tracking is needed for this narrower question (the
    normal per-character scan's own quote-awareness starts fresh only once
    we're back to live code, which is exactly the remainder this function
    returns).
    """
    idx = raw_line.find("*/")
    if idx == -1:
        return None
    return raw_line[idx + 2 :]


def _lines_starting_inside_block_comment(text: str) -> list[bool]:
    """For each line of *text* (1-indexed, matching
    ``enumerate(text.splitlines(), start=1)``), whether that line's very
    first character is already inside an unterminated ``/* ... */`` block
    comment carried over from an earlier line (Codex review, fresh evidence:
    a block-commented-out ``#ifdef``/``#endif`` pair around an ordinary,
    real declaration was previously treated as a live directive, since
    directive matching had no notion of comment state at all — a false
    ``CONF_HIGH`` ``MACRO_CONTROLS_DECL`` edge from documentation, not code).

    A simple, single-pass toggle scan that also tracks ordinary ``"..."``/
    ``'...'`` string/char-literal state (Codex review, fresh evidence,
    second round): a comment delimiter appearing inside a string literal
    (``const char *token = "/*";``) was previously mis-tracked as a real
    block-comment open, silently hiding every following line — including a
    real ``#ifdef``/``#endif`` pair — until some later, unrelated ``*/``
    happened to close it, with no diagnostic to signal the desync
    (reproduced empirically: exactly this shape swallowed a live ``#ifdef``
    entirely). String/char state resets at the start of every line — a
    literal is assumed not to span multiple lines (true for an ordinary
    literal; a backslash-newline line-spliced continuation is a documented,
    accepted residual gap, same discipline as the rest of this heuristic
    scanner) — and an escape character (``\\``) inside either always skips
    its following character so an escaped quote (``'\\''``, ``"\\""``)
    never closes the literal early. Deliberately NOT attempted: a raw
    string literal (``R"(...)"``) — its arbitrary, un-lookahead-able
    delimiter is a materially different parsing problem, and a raw string
    containing a literal ``/*``/``//`` inside a header controlling
    conditional compilation is not a shape this module has empirical
    evidence for. A same-line-closed comment before a real trailing
    directive (``/* note */ #ifdef X``) already can't match the ``^\\s*#``-
    anchored directive patterns regardless, so that shape needs no special
    handling here. ``//`` line comments need no cross-line tracking: they
    only ever affect the one line they start on, and a directive-family
    regex already requires the line to *start* with (optional whitespace
    then) ``#``, which a ``// #ifdef X`` line can never satisfy.
    """
    starts_in_comment: list[bool] = []
    in_block = False
    # Split the same way the caller enumerates lines (``splitlines()``,
    # keeping line-ending characters so a mid-line ``//`` skip-to-end-of-line
    # below has something to skip to) — guarantees this function's per-line
    # output aligns exactly with every caller's ``enumerate(text.
    # splitlines(), start=1)`` regardless of line-ending convention, rather
    # than hand-tracking only ``"\n"``.
    for raw_line in text.splitlines(keepends=True):
        starts_in_comment.append(in_block)
        i = 0
        n = len(raw_line)
        in_string = False
        in_char = False
        while i < n:
            ch = raw_line[i]
            if in_block:
                if ch == "*" and i + 1 < n and raw_line[i + 1] == "/":
                    in_block = False
                    i += 2
                    continue
                i += 1
                continue
            if in_string or in_char:
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                if (in_string and ch == '"') or (in_char and ch == "'"):
                    in_string = False
                    in_char = False
                i += 1
                continue
            if ch == '"':
                in_string = True
                i += 1
                continue
            if ch == "'":
                in_char = True
                i += 1
                continue
            if ch == "/" and i + 1 < n and raw_line[i + 1] == "*":
                in_block = True
                i += 2
                continue
            if ch == "/" and i + 1 < n and raw_line[i + 1] == "/":
                break  # rest of the line is a line comment
            i += 1
    return starts_in_comment


@dataclass
class _GuardFrame:
    """One open ``#if``-family directive on :func:`scan_conditional_regions`'s
    nesting stack."""

    #: True for a compound condition, a bare/expression #if, or once an
    #: #elif/second #else is seen for this frame — no region is ever emitted
    #: for an unmodeled frame, but its nesting is still tracked and popped.
    unmodeled: bool
    macro: str = ""
    negated: bool = False
    #: First line of the branch currently open (the line after the directive
    #: that opened it — the initial #if*/#ifdef*, or the #else that flipped it).
    branch_start: int = 0
    seen_else: bool = False


def scan_conditional_regions(text: str) -> list[ConditionalRegion]:
    """Line-based scan of raw preprocessor conditionals into
    :class:`ConditionalRegion`\\ s (Pass B). See the module docstring for
    exactly which forms are modeled vs. deliberately skipped.

    Nesting is tracked with an explicit stack so a declaration nested inside
    two stacked simple guards (``#ifdef A`` around ``#ifdef B`` around
    ``void f();``) correctly yields **two** independent regions (one per
    level) — both cover ``f``'s line, so a caller matching on line
    containment naturally attributes both, rather than only the innermost.
    An unmodeled frame (compound condition, bare/expression ``#if``, or one
    that later sees ``#elif``/a second ``#else``) contributes no region for
    either of its branches, but its ``#endif`` still pops the stack — an
    unmodeled block nested inside or beside a simple guard never desyncs
    that guard's own nesting depth.

    A line that starts inside an unterminated ``/* ... */`` block comment
    (:func:`_lines_starting_inside_block_comment`) is skipped entirely —
    none of the directive-family regexes below are even attempted for it —
    so a block-commented-out example directive never opens/closes a real
    region or desyncs real nesting depth (Codex review, fresh evidence).
    """
    stack: list[_GuardFrame] = []
    regions: list[ConditionalRegion] = []
    in_comment = _lines_starting_inside_block_comment(text)
    for lineno, line in enumerate(text.splitlines(), start=1):
        if in_comment[lineno - 1]:
            remainder = _line_after_carryover_comment_closes(line)
            if remainder is None:
                continue
            line = remainder
        line = _strip_leading_inline_comment(line)
        m = _IFDEF_RE.match(line)
        if m:
            stack.append(_GuardFrame(False, m.group(1), False, lineno + 1))
            continue
        m = _IFNDEF_RE.match(line)
        if m:
            stack.append(_GuardFrame(False, m.group(1), True, lineno + 1))
            continue
        m = _IF_DEFINED_RE.match(line)
        if m:
            stack.append(
                _GuardFrame(False, m.group(1) or m.group(2), False, lineno + 1)
            )
            continue
        m = _IF_NOT_DEFINED_RE.match(line)
        if m:
            stack.append(_GuardFrame(False, m.group(1) or m.group(2), True, lineno + 1))
            continue
        if _IF_RE.match(line):
            # A bare/compound #if (not one of the four simple forms above,
            # which are each tried first) — deliberately unmodeled.
            stack.append(_GuardFrame(True))
            continue
        if _ELIF_RE.match(line):
            if stack:
                # #elif is unsupported outright (module docstring): retroactively
                # unmodel the whole chain rather than guess which branch a
                # not-yet-closed region belongs to.
                stack[-1].unmodeled = True
            continue
        if _ELSE_RE.match(line):
            if stack:
                frame = stack[-1]
                if frame.unmodeled:
                    continue
                if frame.seen_else:
                    # A second #else is malformed input; give up on this frame
                    # rather than guess.
                    frame.unmodeled = True
                    continue
                if frame.branch_start <= lineno - 1:
                    regions.append(
                        ConditionalRegion(
                            frame.macro, frame.negated, frame.branch_start, lineno - 1
                        )
                    )
                frame.negated = not frame.negated
                frame.branch_start = lineno + 1
                frame.seen_else = True
            continue
        if _ENDIF_RE.match(line):
            if stack:
                frame = stack.pop()
                if not frame.unmodeled and frame.branch_start <= lineno - 1:
                    regions.append(
                        ConditionalRegion(
                            frame.macro, frame.negated, frame.branch_start, lineno - 1
                        )
                    )
            continue
    return regions


def _macro_definition_lines(text: str) -> dict[str, int]:
    """``{macro_name: first #define line}`` over raw source text (Pass B).

    Reuses ``preprocessor_scan._DEFINE_RE`` (same ``#define NAME[(...)]
    [VALUE]`` grammar, whether the text is raw source or ``clang -E -dM``
    output) rather than a second copy of the regex. First definition wins —
    a later ``#undef``/redefinition of the same name is not modeled, the
    same "don't guess at partial semantics" discipline the module docstring
    already applies to compound conditionals.

    Unlike ``preprocessor_scan.parse_defined_macros`` (which skips a
    function-like macro, ``#define F(x) ...``, since it has "no single ABI
    *value* to diff" — a different question that module answers), this
    function keeps a function-like macro's name and definition line too
    (Codex review, fresh evidence): a function-like macro is still a real,
    stably-named entity the graph's own ``macros_from_preprocessor()`` seeds
    a reachable ``macro`` node for, and this function's only job —
    :func:`find_decl_macro_uses`'s textual join — needs exactly the name and
    line, never the macro's value or parameter list. Dropping it here made
    ``DECL_USES_MACRO`` structurally unable to ever fire for a function-like
    macro, even though the name-based join target the rest of this module
    uses is identical for both macro kinds.

    Same block-comment awareness as :func:`scan_conditional_regions`
    (Codex review, fresh evidence's same root cause, applied consistently
    here too): a ``#define`` line that starts inside an unterminated
    ``/* ... */`` block comment is not a real definition and must not be
    recorded, or a later real declaration could be treated as textually
    "using" a macro that was only ever mentioned in a comment. Also strips a
    same-line-closed leading block comment (:func:`_strip_leading_inline_
    comment`) before matching, so ``/* note */ #define X 1`` is recognized
    the same way a real compiler (which treats the comment as whitespace)
    would.
    """
    out: dict[str, int] = {}
    in_comment = _lines_starting_inside_block_comment(text)
    for lineno, line in enumerate(text.splitlines(), start=1):
        if in_comment[lineno - 1]:
            remainder = _line_after_carryover_comment_closes(line)
            if remainder is None:
                continue
            line = remainder
        line = _strip_leading_inline_comment(line)
        m = _DEFINE_RE.match(line)
        if m is None:
            continue
        name = m.group(1)
        if name not in out:
            out[name] = lineno
    return out


def find_decl_macro_uses(
    text: str,
    decl: DeclRange,
    known_macro_names: frozenset[str],
    defined_before_line: dict[str, int],
) -> frozenset[str]:
    """Every name in *known_macro_names* that (a) is ``#define``d (per
    *defined_before_line*) strictly before *decl*'s own ``begin_line`` in the
    same file, and (b) appears as a word-boundary match somewhere in
    *decl*'s own ``[begin_line, end_line]`` text span (Pass B).

    Textual, not semantic — see the module docstring's "accepted, documented
    limitations" for the concrete over-match/cross-file tradeoffs this
    accepts. The macro's own ``#define`` line is never itself counted as a
    "use": *defined_before_line* is strictly less-than *decl.begin_line*, and
    a ``#define`` line is never part of any declaration's own span in valid
    source, so the two can't coincide.
    """
    lines = text.splitlines()
    if decl.begin_line < 1 or decl.end_line < decl.begin_line:
        return frozenset()
    end = min(decl.end_line, len(lines))
    if end < decl.begin_line:
        return frozenset()
    span = "\n".join(lines[decl.begin_line - 1 : end])
    found: set[str] = set()
    for name in known_macro_names:
        def_line = defined_before_line.get(name)
        if def_line is None or def_line >= decl.begin_line:
            continue
        if re.search(rf"\b{re.escape(name)}\b", span):
            found.add(name)
    return frozenset(found)


# ── graph augmentation ───────────────────────────────────────────────────────


@dataclass
class MacroGraphResult:
    """What one :func:`augment_graph_with_macro_dependencies` pass did, for
    the coverage-honesty stamp and the extractor row its caller records."""

    decls_seen: int = 0
    decls_joined: int = 0
    controls_edges: int = 0
    uses_edges: int = 0
    files_scanned: int = 0
    diagnostics: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Every file an indexed declaration named was scanned with no
        read/parse diagnostic — the only shape that may claim a confirmed
        ``extractor_passes`` entry."""
        return self.files_scanned > 0 and not self.diagnostics


def augment_graph_with_macro_dependencies(
    graph: SourceGraphSummary,
    decl_ranges: list[DeclRange],
    *,
    source_lookup: Mapping[str, str] | Callable[[str], str | None],
) -> MacroGraphResult:
    """Fold macro/config-dependency edges into *graph* (G29 Phase 5 item 2).

    **A ``MACRO_CONTROLS_DECL``/``DECL_USES_MACRO`` edge is only ever emitted
    between a ``macro``/``source_decl`` node *graph already carries*.** Same
    "one shared node id is the whole join mechanism" rule ADR-057 D1 and
    ``archive_graph.augment_graph_with_archives`` both apply — this pass
    never mints a node for a declaration or macro it discovers in the AST/text
    scan but the graph doesn't already know about, keeping it genuinely
    additive.

    *decl_ranges* is Pass A's output (:func:`parse_clang_ast_decl_ranges`,
    typically aggregated across every TU in a build). For each file the
    surviving (already-joined) declarations name, *source_lookup* supplies
    that file's raw text — a plain mapping for a small/precomputed set, or a
    callable (e.g. reading from disk) for a larger one. A file that cannot be
    read (the callable raises ``OSError``/``UnicodeDecodeError``/
    ``ValueError``, or the lookup answers ``None``) is recorded once as a
    diagnostic and skipped — this function never raises (ADR-028 D3).

    Confidence: ``MACRO_CONTROLS_DECL`` is ``CONF_HIGH`` (an exact structural
    fact about the raw text — which ``#if``/``#endif`` region a line falls
    in), ``DECL_USES_MACRO`` is ``CONF_REDUCED`` (a textual heuristic — see
    :func:`find_decl_macro_uses`).
    """
    from ..model.graph_facts import _decl_node_id
    from .redaction import DEFAULT_REDACTION

    result = MacroGraphResult()
    if not decl_ranges:
        return result

    macro_name_to_id = {
        n.label: n.id for n in graph.nodes if n.kind == "macro" and n.label
    }
    known_decl_ids = frozenset(n.id for n in graph.nodes if n.kind == "source_decl")

    # Group only declarations that can actually join onto an existing
    # source_decl node (see the join-only-onto-an-existing-node discipline
    # above) — a TU's full declaration set from Pass A routinely includes
    # every system/dependency header it transitively includes, none of
    # which the graph carries a node for (Codex review, fresh evidence: a
    # single real TU including <vector> produced 4,258 ranges across 47
    # files, none joinable). Filtering *before* grouping by file, rather
    # than after reading each file's text, means this pass never opens a
    # source file it has no use for, and an unreadable/irrelevant header
    # with zero joinable declarations no longer contributes a diagnostic
    # that falsely marks the whole pass degraded.
    by_file: dict[str, list[DeclRange]] = {}
    for d in decl_ranges:
        result.decls_seen += 1
        if _decl_node_id(d.identity) not in known_decl_ids:
            continue
        by_file.setdefault(d.file, []).append(d)

    for file in sorted(by_file):
        decls = by_file[file]
        try:
            text = (
                source_lookup(file)
                if callable(source_lookup)
                else source_lookup.get(file)
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            # A raised OSError/etc. can embed the real, resolved (i.e.
            # un-redacted) path in both the filename operand and the
            # exception's own text — *file* itself may already be
            # un-redacted too, since ClangMacroGraphExtractor resolves a
            # relative source path against the compile unit's own replayed
            # cwd before this function ever sees it. Redact both before
            # this diagnostic reaches `merged.diagnostics`/the persisted
            # build-source artifact — same leak class + fix as
            # archive_graph.augment_graph_with_archives' identical
            # try/except (Codex review, fresh evidence).
            result.diagnostics.append(
                f"{DEFAULT_REDACTION.path(file)}: unreadable: "
                f"{DEFAULT_REDACTION.path(str(exc))}"
            )
            continue
        if text is None:
            result.diagnostics.append(
                f"{DEFAULT_REDACTION.path(file)}: no source text available"
            )
            continue
        result.files_scanned += 1
        regions = scan_conditional_regions(text)
        defines = _macro_definition_lines(text)
        known_macro_names = frozenset(defines) & frozenset(macro_name_to_id)

        for d in decls:
            decl_id = _decl_node_id(d.identity)
            # Guaranteed true now that `by_file` is pre-filtered to joinable
            # declarations above — kept as a defensive no-op rather than
            # removed, so a future refactor that stops pre-filtering doesn't
            # silently regress the join-only-onto-an-existing-node rule.
            if decl_id not in known_decl_ids:
                continue
            result.decls_joined += 1
            for region in regions:
                macro_id = macro_name_to_id.get(region.macro)
                if macro_id is None:
                    continue
                if region.start_line <= d.begin_line <= region.end_line:
                    graph.add_edge(
                        GraphEdge(
                            src=macro_id,
                            dst=decl_id,
                            kind=EDGE_MACRO_CONTROLS_DECL,
                            provenance="macro_graph",
                            confidence=CONF_HIGH,
                            attrs={"negated": region.negated},
                        )
                    )
                    result.controls_edges += 1
            for name in sorted(
                find_decl_macro_uses(text, d, known_macro_names, defines)
            ):
                graph.add_edge(
                    GraphEdge(
                        src=decl_id,
                        dst=macro_name_to_id[name],
                        kind=EDGE_DECL_USES_MACRO,
                        provenance="macro_graph",
                        confidence=CONF_REDUCED,
                    )
                )
                result.uses_edges += 1
    return result


# ── live clang extraction (integration only) ────────────────────────────────


@dataclass
class ClangMacroGraphExtractor:
    """Shell out to ``clang`` to emit a TU's AST and parse its declaration
    ranges (Pass A only — the raw-text Pass B needs no compiler at all and is
    driven separately by :func:`augment_graph_with_macro_dependencies`'s
    caller, e.g. :func:`~abicheck.buildsource.inline_graph_fold.
    fold_macro_graph`).

    Side-effecting and compiler-dependent: only exercised on the
    ``integration`` lane. A missing ``clang`` (or a parse failure) degrades
    gracefully — extraction returns ``[]`` and records nothing (ADR-028 D3).
    Reuses ``call_graph``'s vetted parse-only argv builder and deadline/
    parallelism helpers (same shape as
    :class:`~abicheck.buildsource.type_graph.ClangTypeGraphExtractor`) rather
    than a fresh copy — this is a fourth independent
    ``clang -ast-dump=json`` pass per TU alongside the call/type/template
    graph passes ``inline_graph_fold.fold_semantic_graphs`` already runs; no
    per-TU AST-JSON cache is shared across them today (confirmed: each pass's
    own ``extract_from_build`` re-invokes clang independently), a pre-existing
    inefficiency this module does not attempt to fix.
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
    ) -> list[DeclRange]:
        diag = self.diagnostics if diagnostics is None else diagnostics
        if not self.available():
            diag.append(f"{self.clang_bin} not found in PATH")
            return []
        ast = run_clang_ast_dump(self.clang_bin, argv, cwd=cwd, diagnostics=diag)
        if ast is None:
            return []
        try:
            return parse_clang_ast_decl_ranges(ast)
        except (TypeError, ValueError, RecursionError) as exc:
            # TypeError (CodeRabbit review, fresh evidence): _LocationCursor.
            # advance() calls int(loc["line"]) with no type check -- a
            # malformed line value (null, a list, ...) in adversarial or
            # corrupted AST JSON raises TypeError, which must degrade to a
            # diagnostic like every other malformed-input case here, not
            # escape and abort extraction for the whole build.
            diag.append(f"could not parse clang AST JSON: {exc}")
            return []

    def _extract_from_compile_unit(
        self, cu: BuildEvidenceCompileUnit, *, diagnostics: list[str] | None = None
    ) -> list[DeclRange]:
        from .call_graph import _replay_cwd, _safe_clang_args_from_compile_unit

        argv = _safe_clang_args_from_compile_unit(cu)
        cwd = _replay_cwd(cu)
        ranges = self._extract_from_safe_args(argv, cwd=cwd, diagnostics=diagnostics)
        if not cwd:
            return ranges
        # clang echoes a relative source path (e.g. an out-of-source build's
        # ``../src/a.cpp``) back into the AST dump's ``file`` fields exactly
        # as given on the command line — it never resolves it against *cwd*
        # itself (Codex review, PR #708). augment_graph_with_macro_dependencies's
        # caller (fold_macro_graph) reads source text by that same ``file``
        # string against the *process*'s cwd, not this compile unit's own —
        # so a relative path here would silently resolve to nothing (or, far
        # worse, to an unrelated file that happens to exist at that relative
        # path from the process cwd) unless resolved against the *replayed*
        # cwd right here, while we still know it. An already-absolute path
        # (the common case — most compile DBs record one) is left untouched.
        return [
            r
            if os.path.isabs(r.file)
            else replace(r, file=os.path.normpath(os.path.join(cwd, r.file)))
            for r in ranges
        ]

    def extract_from_build(self, build: BuildEvidence) -> list[DeclRange]:
        """Extract decl ranges across every compile unit in *build* (best
        effort), deduped by ``(identity, file, begin_line, end_line)`` — the
        first-seen span for an exact repeat (e.g. the same header parsed
        identically from two TUs) wins, but two declarations sharing an
        identity with *different* spans (a forward declaration and its own
        later definition in the same file) are both kept, since only the
        definition's span may carry the real macro guard/reference this
        module exists to find (Codex review, PR #708 — a same-``(identity,
        file)`` dedup discarded the definition's span whenever the forward
        declaration was visited first). A later exact duplicate is harmless
        either way since :func:`augment_graph_with_macro_dependencies` only
        reads each joined declaration's own line span to test region
        containment, not accumulate a range union. Each unit's diagnostics
        are collected into a fresh per-call list and only folded into
        ``self.diagnostics`` on the single driving thread, in ``pool.map``'s
        own input-ordered iteration — see
        ``call_graph.ClangCallGraphExtractor.extract_from_build``'s
        docstring for the full rationale.
        """
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

        all_ranges: list[DeclRange] = []
        seen: set[tuple[str, str, int, int]] = set()

        def add_ranges(ranges: list[DeclRange]) -> None:
            for r in ranges:
                key = (r.identity, r.file, r.begin_line, r.end_line)
                if key not in seen:
                    seen.add(key)
                    all_ranges.append(r)

        def _probe(cu: BuildEvidenceCompileUnit) -> tuple[list[DeclRange], list[str]]:
            local_diagnostics: list[str] = []
            ranges = self._extract_from_compile_unit(cu, diagnostics=local_diagnostics)
            return ranges, local_diagnostics

        try:
            if self.last_jobs > 1 and len(units) > 1:
                pool_worker = partial(
                    _deadline_bound_worker,
                    deadline.current_deadline_ts(),
                    _probe,
                )
                with ThreadPoolExecutor(max_workers=self.last_jobs) as pool:
                    for ranges, local_diagnostics in pool.map(pool_worker, units):
                        add_ranges(ranges)
                        self.diagnostics.extend(local_diagnostics)
            else:
                for cu in units:
                    ranges, local_diagnostics = _probe(cu)
                    add_ranges(ranges)
                    self.diagnostics.extend(local_diagnostics)
        finally:
            self.last_elapsed_s = time.monotonic() - start

        return all_ranges
