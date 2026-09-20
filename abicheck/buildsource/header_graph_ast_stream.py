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

"""Project a clang AST **from disk**, one top-level declaration at a time.

Why this module exists
----------------------
:func:`~abicheck.buildsource.header_graph_ast_projection.
project_header_graph_ast` reduces a clang AST to the four compact things the
header-only (L5) graph builder reads, and letting the caller drop the tree
before the graph is built is what PR #1338 bought. But it takes an
already-parsed tree, so it can do nothing about the parse itself -- and the
parse *is* the peak. Measured on a real 263 MiB ``clang -ast-dump=json``
document (the ``check_header_graph_perf`` memory fixture at ``n=60``, the
same shape and scale as oneDAL's ``libonedal_core``):

======================================  ==========  ========
step                                    peak RSS    wall
======================================  ==========  ========
``json.load`` of the document              674 MiB    4.5 s
``json.load`` + ``project_...``            699 MiB    4.5 s
======================================  ==========  ========

and of that 699 MiB, **263 MiB is the document held as one ``str``** while
**400 MiB of tree** is built from it. The two are resident together, inside
``json.load``, which is why PR #1338's reordering could not touch it: the
peak was never ``tree + graph``, it is ``document + tree``.

PR #1339 removed that cost for a **warm** run by caching the projection as a
sidecar, so the AST is never read at all. A **cold** run -- the first run in
CI, the first after a header edit, every cache-cold container -- still paid
the full parse. That is what this module is for.

What it does
------------
The root of a clang AST dump is one ``TranslationUnitDecl`` whose ``inner``
array holds every top-level declaration. Those elements are independent
documents as far as the *reader* is concerned, so
:func:`stream_top_level_decls` finds each one's byte extent with a scanner
that never holds more than the current element, hands it to ``json.loads``
alone, and lets it be freed before the next one starts.
:func:`project_header_graph_ast_file` then drives the *same four readers*
over that stream instead of over a whole tree.

Measured on that same document, three runs per side, fresh process each:

======================================  ==========  ========
                                        peak RSS    wall
======================================  ==========  ========
``json.load`` + ``project_...``            699 MiB    4.5 s
:func:`project_header_graph_ast_file`      298 MiB   13.1 s
======================================  ==========  ========

**-57% peak, at 2.9x the wall time of the parse it replaces.** The trade is
deliberate and is the whole point: the attach is memory-bound, not
CPU-bound -- a 2 GiB member is what makes a release fan-out overcommit and
what a cache-cold CI container has least of, while the seconds here sit
against a ``clang`` invocation that already cost tens of them.

End to end, through the real dump and attach on a cold cache
(``check_header_graph_perf.py --memory-probe``, three fresh processes per
side, a private empty ``XDG_CACHE_HOME`` each):

=================  ===================  ==================
backend            attach peak RSS      RSS after attach
=================  ===================  ==================
castxml, before       605.5 MiB            430.4 MiB
castxml, after        210.8 MiB            210.3 MiB
clang, before         554.6 MiB            420.5 MiB
clang, after          554.7 MiB            422.9 MiB
=================  ===================  ==================

**Which backend benefits is not incidental, and the clang row is not a
disappointment -- it is the mechanism working correctly.** Under
``--ast-frontend clang`` the *primary* L2 dump already parsed these same
headers and handed its tree to the attach through ``dumper_cache``'s
in-process memo, which ``load_cached_ast`` consults *before* it offers
anything to a derived consumer. So the attach never performed a second
parse there to begin with, and what that 554 MiB measures is the primary
dump's own ``json.load``, which belongs to a different owner and is
untouched by anything here. castxml -- the default, and the backend the
oneDAL investigation that started this work used -- leaves no such memo,
so the attach's clang parse is the first and only one, and is exactly the
cost this removes.

On the real library this all started from -- oneDAL 2024.7's
``libonedal_core.so.2`` against the whole ``daal.h`` surface, cold -- the
attach's peak goes **2096.8 MiB -> 877.2 MiB (-58%)** at 2.1x its wall time,
and the graph it produces is **digest-identical** across 49,482 nodes and
98,331 edges. That before-figure reproduces the 2093.5 MiB this work was
commissioned against, so it is the same peak and not a differently-shaped
one.

Why the floor is where it is
----------------------------
It is not the scanner. Top-level elements are extremely skewed: in that
document the largest single element is **53.3 MiB of JSON (20% of the whole
file)** and the top ten are 57% of it, because one STL template
instantiation cluster lands as one declaration. Streaming cannot subdivide
below one element, so the floor is that element's own tree (~81 MiB) plus
its bytes plus the accumulated indexes. Splitting *within* an element is a
different, much larger design question this module does not attempt.

The other half of the floor was ``call_graph``'s ``member_index``, which
kept every ``FunctionDecl``/``CXXMethodDecl`` node -- **and therefore its
whole body** -- alive for the entire parse, so each top-level element stayed
pinned after this module released it. That cost **+168 MiB** here; see
:func:`~abicheck.buildsource.call_graph._compact_decl_record`, which stores
only the fields such an entry is ever read through.

What is not negotiable
----------------------
The four readers are **not** reimplemented -- they are the same functions,
called on the same nodes, in the same document order. Two pieces of state
clang's format makes order-dependent are threaded across the element
boundary explicitly, because a whole-tree walk got them from sibling
recursion and a streamed one cannot:

* the **sticky declaring file**. clang emits ``loc.file`` only when it
  *changes*, so a declaration can inherit its file from a previous sibling
  -- across a top-level boundary like any other. Both
  ``_index_declared_entities`` and ``_walk_calls`` already return their
  last-seen file for exactly this reason; this module feeds each element's
  return value into the next call.
* the **anonymous-tag group**. ``enum : U { A } x;`` reaches the AST as an
  anonymous ``EnumDecl`` followed by its declarator as a *sibling*, and at
  top level those two fall either side of an element boundary whenever the
  boundary lands between them. ``type_graph._walk_child_sequence`` exists so
  that state can be handed back and forth here.

**Not every document is streamed.** The trade above is CPU for memory, and
it is only a good trade where there is memory to save, so
``service_header_graph_attach`` applies it above a document-size threshold
(``ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB``, 32 MiB) and parses smaller
documents whole. Measured, the two ends are 100x apart: this repository's
own header-graph perf fixtures produce 0.2/0.6/2.5 MiB documents at sizes
25/100/400, where document and tree together are a few MiB, against
oneDAL's 263 MiB. Streaming the small case cost 44-71% of attach wall time
for nothing, and the PR-vs-base attach gate rejected it, correctly. The
threshold is stated where the decision is made, with its reasoning.

A third rule is about what this scanner *refuses*. It must never accept a
document ``json.loads`` would reject, because then this module answers
where the whole-tree path raises -- and the two would disagree exactly when
an AST cache entry is corrupt, which is the one case the equivalence claim
most needs to hold. So the element separators are *counted*, not merely
allowed (:func:`_check_gap`), and a top-level element that is not a JSON
object is rejected rather than skipped. Both raise, and every caller
answers a raise by parsing the document the ordinary way, so strictness
here costs a slow run and never a wrong answer.

Getting either wrong is silent: the edges simply differ. So equivalence is
not argued, it is executed -- ``tests/test_header_graph_ast_stream.py``
compares this module's projection against
``project_header_graph_ast``'s own, field for field, over real
``clang``-generated ASTs built to contain both boundary shapes above.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .call_graph import (
    _dedupe_edges as _dedupe_call_edges,
    _fill_callee_files,
    _index_member_decls,
    _walk_calls,
)
from .header_graph_ast_projection import HeaderGraphAstProjection
from .type_graph import (
    _dedupe_edges as _dedupe_type_edges,
    _index_declared_entities,
    _new_ast_indexes,
    _walk_child_sequence,
)

#: Structural JSON bytes the scanner jumps between. Everything else in the
#: document -- which on a pretty-printed clang dump is **63% insignificant
#: whitespace**, measured -- is skipped by the regex engine in C rather than
#: examined a character at a time in Python. A naive per-character loop over
#: the same document took 31.1 s against this scanner's 9.4 s.
_STRUCTURAL = re.compile(rb'["{}\[\]]')

#: The end of a JSON string starting after an opening quote: a quote that is
#: not escaped. ``(?:\\\\)*`` consumes *pairs* of backslashes first, so a
#: literal trailing backslash (``"a\\\\"``) ends the string while an escaped
#: quote (``"a\\""``) does not -- both shapes occur in real clang output, in
#: template-argument and string-literal spellings.
_STRING_END = re.compile(rb'(?<!\\)(?:\\\\)*"')

#: Read granularity. Large enough that the per-read Python overhead is
#: irrelevant beside the regex scan, small enough not to overshoot the end of
#: a small element by much.
_CHUNK = 1 << 20


#: Pretty-printing whitespace, the only thing that may sit around an
#: element separator. The separating comma itself is counted rather than
#: merely allowed -- see :func:`_check_gap`.
_JSON_WHITESPACE = frozenset(b" \t\r\n")

#: The element separator itself, as the integer a ``bytearray`` iterates.
_COMMA = ord(",")


class ClangAstStreamError(ValueError):
    """The document is not a clang AST dump this scanner can walk.

    A distinct type so the caller can tell "this file is not what I was
    promised" apart from a genuine :class:`json.JSONDecodeError` inside one
    element, and degrade the same way every other AST acquisition failure
    does rather than aborting the dump.
    """


def _check_gap(
    buf: bytearray, start: int, end: int, *, expected: int, carried: int = 0
) -> None:
    """Validate the bytes between two top-level tokens.

    Two different malformations hide here, and only counting the commas
    catches both.

    A top-level element that is **not an object** is invisible to this
    scanner: element boundaries are object braces, so a bare ``1`` or
    ``true`` produces no structural token to stop on and would simply be
    skipped, leaving the projection silently short.

    A **wrong number of separators** is subtler, and is what makes this a
    count rather than a membership test. ``[{...} {...}]`` (none),
    ``[,{...}]`` (leading), ``[{...},,{...}]`` (doubled) and ``[{...},]``
    (trailing) are all rejected by ``json.loads`` and were all silently
    accepted here while any number of commas was allowed -- which would
    make this module answer where the whole-tree path raises, breaking the
    one property everything else rests on (*the same projection, however it
    was derived*) precisely when a cache entry is corrupt.

    *expected* is 1 before every element after the first, and 0 before the
    first element and before the closing ``]`` -- so a leading and a
    trailing comma are each rejected by the same rule that rejects a
    missing one.

    Raising is always safe: every caller falls back to parsing the document
    normally, so being wrong here costs a slow run, never a wrong answer.
    """
    gap = buf[start:end]
    if not _JSON_WHITESPACE.issuperset(set(gap) - {_COMMA}):
        raise ClangAstStreamError("top-level AST element is not a JSON object")
    # *carried* counts separators already seen in an earlier part of this
    # same gap that had to be discarded before the token deciding how many
    # were due had been read, so the comparison is over the whole gap.
    found = carried + gap.count(b",")
    if found != expected:
        raise ClangAstStreamError(
            f"malformed separator between top-level AST elements: expected "
            f"{expected} comma(s), found {found}"
        )


def _find_root_inner(fh: Any, buf: bytearray) -> bool:
    """Advance *buf* to just past the root object's ``"inner": [``.

    Structural rather than a ``buf.find(b'"inner"')``: it accepts the key
    only at depth 1 of the root object and only when a ``[`` actually
    follows, so neither a nested ``inner`` nor the literal text ``inner``
    inside some string value (a file path, a template argument spelling) can
    be mistaken for it. Answers ``False`` for a well-formed root that simply
    has no ``inner`` -- an empty translation unit, which projects to nothing
    rather than raising.
    """
    depth = 0
    pos = 0
    while True:
        match = _STRUCTURAL.search(buf, pos)
        if match is None:
            chunk = fh.read(_CHUNK)
            if not chunk:
                if depth == 0:
                    raise ClangAstStreamError("no root JSON object found")
                return False
            buf.extend(chunk)
            continue
        token = match.group()
        at = match.start()
        if token == b'"':
            end = _STRING_END.search(buf, at + 1)
            while end is None:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    raise ClangAstStreamError("unterminated string in AST document")
                buf.extend(chunk)
                end = _STRING_END.search(buf, at + 1)
            # A key named `inner` directly inside the root object, and only
            # there: depth 1, and followed by `:` then `[`.
            if depth == 1 and bytes(buf[at + 1 : end.start()]) == b"inner":
                tail = _after_colon_bracket(fh, buf, end.end())
                if tail is not None:
                    del buf[:tail]
                    return True
            pos = end.end()
            continue
        if token in (b"{", b"["):
            depth += 1
            pos = at + 1
            continue
        depth -= 1
        pos = at + 1
        if depth == 0:
            # The root object closed without an `inner` key.
            return False


def _after_colon_bracket(fh: Any, buf: bytearray, pos: int) -> int | None:
    """Index just past ``: [`` starting at *pos*, or ``None`` if that is not
    what follows (so the ``inner`` just matched was a value, not the array
    key -- e.g. ``"kind": "inner"``)."""
    seen_colon = False
    while True:
        while pos < len(buf):
            char = buf[pos : pos + 1]
            if char.isspace():
                pos += 1
                continue
            if not seen_colon:
                if char != b":":
                    return None
                seen_colon = True
                pos += 1
                continue
            return pos + 1 if char == b"[" else None
        chunk = fh.read(_CHUNK)
        if not chunk:
            return None
        buf.extend(chunk)


def stream_top_level_decls(
    path: Path, record_extents: list[tuple[int, int]] | None = None
) -> Iterator[dict[str, Any]]:
    """Yield each top-level declaration of the AST at *path*, one at a time.

    *record_extents*, when given, is appended with each element's ``(start,
    end)`` byte offset in the file, for :func:`stream_recorded_decls` to
    re-read without scanning again.

    Only the element currently being yielded is held: its bytes are dropped
    from the read buffer as soon as it is decoded, and the caller is expected
    to drop the element itself before asking for the next. Nothing
    accumulates here, which is the entire point -- see the module docstring
    for what that is worth and where the remaining floor comes from.

    Raises :class:`ClangAstStreamError` if the document is not shaped like a
    clang AST dump, and lets :class:`json.JSONDecodeError` from an individual
    element propagate: a malformed element is a real parse failure, and
    silently skipping one would produce a quietly incomplete edge set, which
    is the one outcome this codebase treats as worse than no evidence at all
    (ADR-028 D3).
    """
    with path.open("rb") as fh:
        buf = bytearray()
        if not _find_root_inner(fh, buf):
            return
        # File offset of ``buf[0]``, maintained across every truncation, so
        # an element's extent can be reported in the file's own coordinates.
        base = fh.tell() - len(buf)
        depth = 0
        pos = 0
        # Separator bookkeeping (see `_check_gap`). `carried_commas` holds
        # separators already seen in a gap that had to be discarded before
        # the token deciding how many were due had been read.
        yielded_any = False
        carried_commas = 0
        while True:
            match = _STRUCTURAL.search(buf, pos)
            if match is None:
                if depth == 0:
                    # Nothing in flight, so everything left is between two
                    # elements and can go -- but validate it *before*
                    # discarding it, or a bare scalar element that happens
                    # to carry no structural byte (`1`, `true`, `null`)
                    # would be dropped silently here rather than rejected.
                    # Separator counting cannot happen yet (the next token
                    # decides how many are due), so only the byte set is
                    # checked here and the commas are carried forward.
                    if not _JSON_WHITESPACE.issuperset(set(buf[pos:]) - {_COMMA}):
                        raise ClangAstStreamError(
                            "top-level AST element is not a JSON object"
                        )
                    carried_commas += buf[pos:].count(b",")
                    del buf[:pos]
                    base += pos
                    pos = 0
                else:
                    pos = len(buf)
                chunk = fh.read(_CHUNK)
                if not chunk:
                    if depth:
                        raise ClangAstStreamError("truncated AST document")
                    return
                buf.extend(chunk)
                continue
            token = match.group()
            at = match.start()
            if token == b'"':
                if depth == 0:
                    # A string *element* -- see `_check_gap`.
                    raise ClangAstStreamError(
                        "top-level AST element is not a JSON object"
                    )
                end = _STRING_END.search(buf, at + 1)
                while end is None:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        raise ClangAstStreamError("unterminated string in AST document")
                    buf.extend(chunk)
                    end = _STRING_END.search(buf, at + 1)
                pos = end.end()
                continue
            if depth == 0:
                # 1 comma before every element after the first; 0 before the
                # first and before the closing `]`.
                due = 0 if (token == b"]" or not yielded_any) else 1
                _check_gap(buf, pos, at, expected=due, carried=carried_commas)
                carried_commas = 0
            if depth == 0 and token != b"{":
                if token == b"]":
                    return  # end of the root's `inner`
                # A top-level element that is not a JSON object. clang never
                # emits one, and this scanner's element boundaries are
                # object braces, so continuing would hand `json.loads` a
                # slice that starts in the wrong place -- a silently wrong
                # projection, which is the one outcome worse than no
                # projection. Decline loudly instead; every caller falls
                # back to parsing the document normally.
                raise ClangAstStreamError("top-level AST element is not a JSON object")
            if token in (b"{", b"["):
                if depth == 0 and token == b"{":
                    # Drop the separator bytes before this element so it
                    # starts at index 0 and the buffer never carries a
                    # growing prefix of already-consumed document.
                    del buf[:at]
                    base += at
                    at = 0
                    pos = 0
                depth += 1
                pos = at + 1
                continue
            depth -= 1
            pos = at + 1
            if depth == 0:
                if record_extents is not None:
                    record_extents.append((base, base + pos))
                yield json.loads(buf[:pos])
                yielded_any = True
                del buf[:pos]
                base += pos
                pos = 0


def stream_recorded_decls(
    path: Path, extents: list[tuple[int, int]]
) -> Iterator[dict[str, Any]]:
    """Re-yield the elements :func:`stream_top_level_decls` recorded *extents* for.

    The second pass needs the same elements again, and scanning for them a
    second time costs as much as the first: the structural scan, not the
    decode, dominates. 378 ``(start, end)`` pairs are a few KiB, so the
    boundaries are kept and the bytes re-read straight from the file --
    which took this module's projection of a 263 MiB document from 21.7 s to
    13.1 s, and its peak from 336 MiB to 298 MiB: a seek-and-read holds one
    element and no read buffer around it.
    """
    with path.open("rb") as fh:
        for start, end in extents:
            fh.seek(start)
            yield json.loads(fh.read(end - start))


def project_header_graph_ast_file(path: Path) -> HeaderGraphAstProjection:
    """Read the AST at *path* into the projection, never holding the tree.

    The streaming counterpart of
    :func:`~abicheck.buildsource.header_graph_ast_projection.
    project_header_graph_ast`, and required to agree with it exactly.

    Two scans of the file rather than one, because the readers themselves are
    two-pass and the second pass needs the first's whole-translation-unit
    indexes: an unqualified type spelling resolves against every declaration
    in the TU, not only those already seen. Fusing them would mean resolving
    against a partial index, which is a different (and wrong) answer, not a
    faster one. The second scan re-reads the file rather than retaining the
    elements, which is exactly the cost being paid for the memory.

    The single ``_index_declared_entities`` pass here also replaces the
    **three** the non-streaming path runs (``index_declared_type_files``,
    ``index_declared_entity_files`` and ``parse_clang_ast_types`` each build
    their own), which is why the wall-time multiple is as low as it is.
    """
    idx = _new_ast_indexes()
    member_index: dict[str, dict[str, Any]] = {}
    cur_file = ""
    extents: list[tuple[int, int]] = []
    for element in stream_top_level_decls(path, extents):
        _index_member_decls(element, member_index)
        cur_file = _index_declared_entities(element, [], cur_file, idx)
        del element

    type_edges: list[Any] = []
    call_edges: list[Any] = []
    decl_files: dict[str, str] = {}
    id_index: dict[str, str] = {}
    pending_anon_enum: dict[str, Any] | None = None
    call_file = ""
    for element in stream_recorded_decls(path, extents):
        pending_anon_enum = _walk_child_sequence(
            (element,), [], "", type_edges, idx, pending_anon_enum
        )
        call_file = _walk_calls(
            element,
            "",
            "",
            call_file,
            [],
            call_edges,
            decl_files,
            id_index,
            member_index,
        )
        del element

    type_edges = _dedupe_type_edges(type_edges)
    # `entity_files` preserves the caller's own laziness exactly: it is
    # reported only when a DECL_REFERENCES_DECL edge actually needs it, so a
    # consumer cannot come to read an empty mapping as "this AST declares
    # nothing" on one path and "nothing needed it" on the other.
    needs_entity_files = any(e.kind == "DECL_REFERENCES_DECL" for e in type_edges)
    type_qnames = {qname for qnames in idx.name_index.values() for qname in qnames}
    return HeaderGraphAstProjection(
        type_files={q: idx.decl_file[q] for q in type_qnames if q in idx.decl_file},
        type_edges=type_edges,
        call_edges=_dedupe_call_edges(_fill_callee_files(call_edges, decl_files)),
        entity_files=dict(idx.decl_file) if needs_entity_files else {},
    )
