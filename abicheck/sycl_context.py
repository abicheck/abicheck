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

"""ADR-050 D5 (G32 Phase D) — SYCL/DPC++ host vs. device AST context
selection: decodes a DPC++ frontend's possibly-multi-document
``-ast-dump=json`` output into a stream of ``FrontendContext`` records, and
selects the one matching a requested ``frontend_context`` (``"host"``/
``"device"``) kind.

**Why two channels, not one.** A DPC++ driver invocation (``icpx -fsycl
... -Xclang -ast-dump=json``) spawns one ``-cc1`` sub-invocation per
compilation pass (one ``host`` pass, one or more ``device`` passes per
offload target), and each sub-invocation's own AST dump is written to
stdout back-to-back with no separator — real document-boundary streaming
is required, not a bracket/string split (a naive brace counter would
already be wrong in general: a JSON string value can itself contain a
literal ``{``/``}`` character). The raw AST JSON alone carries no
``"host"``/``"device"`` label of its own — it is ordinary ``clang
-ast-dump=json`` output, oblivious to the driver-level split. That label
(and each pass's target triple, diagnostic-only — see
:func:`select_frontend_context`) comes from the driver's own ``-v``
diagnostic output on **stderr**: each real invocation line is shaped
``... -cc1 -triple <T> ... -fsycl-is-(host|device) ...``, in the same
order as its corresponding stdout document. Confirmed against a real
``icpx`` capture in ``tests/fixtures/g32/dpcpp/`` (``ast_dump.json`` +
``compiler_invocation.log``) — not a guessed format.

Callers (``dumper.py``) are responsible for actually requesting ``-v`` and
capturing stdout/stderr separately, and for deciding *whether* to route an
invocation through this module at all (a plain, non-DPC++ clang/castxml
invocation never reaches here — see that module's fallback-gating rule).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import deadline
from .errors import AstContextAmbiguousError, AstContextMissingError, SnapshotError

#: A real DPC++ driver `-cc1` invocation line on `-v` stderr output: names
#: both the compiled target triple and which pass (`host`/`device`) it is.
#: `-triple` always precedes `-fsycl-is-(host|device)` in observed `icpx`
#: output (confirmed against tests/fixtures/g32/dpcpp/compiler_invocation.log),
#: so this doesn't need to handle the reverse order.
_CC1_INVOCATION_RE = re.compile(
    r"-cc1\b.*?-triple\s+(?P<target>\S+).*?-fsycl-is-(?P<kind>host|device)\b"
)


@dataclass(frozen=True)
class FrontendContext:
    """One decoded AST document, correlated with the driver's own `-cc1`
    invocation metadata for the pass that produced it.

    ``kind`` (``"host"``/``"device"``) is what :func:`select_frontend_context`
    matches against a requested ``frontend_context`` — never ``target``,
    which is diagnostic-only (ADR-050 D5: two toolchains could plausibly
    label the same logical device pass with different target-triple
    spellings; only the driver's own explicit ``-fsycl-is-*`` flag is
    authoritative for *kind*).
    """

    kind: str
    target: str
    ast: dict[str, Any]


def decode_frontend_contexts(stdout: str, stderr: str) -> list[FrontendContext]:
    """Decode *stdout* (a DPC++ frontend's possibly-multi-document
    ``-ast-dump=json`` output) into a list of :class:`FrontendContext`,
    correlated against *stderr*'s ``-cc1`` invocation lines in the same
    order (see this module's own docstring for why two channels).

    Real streaming decode via repeated :meth:`json.JSONDecoder.raw_decode`
    calls, not a bracket/string split. An empty *stdout* (or one with no
    complete documents) decodes to an empty list — not an error here; a
    request against zero contexts is what :func:`select_frontend_context`'s
    own three-outcome logic turns into :class:`AstContextMissingError`
    (ADR-050 D5's "decodes to zero contexts" case is handled by the
    *selector*, not by this function refusing to decode). Genuinely
    malformed input — a document that starts but never finishes, or
    trailing bytes that aren't a valid JSON value — raises
    :class:`abicheck.errors.SnapshotError` immediately; that is a decode
    failure distinct from "there were simply no documents".
    """
    decoder = json.JSONDecoder()
    docs: list[dict[str, Any]] = []
    pos = 0
    length = len(stdout)
    while pos < length:
        stripped = stdout[pos:].lstrip()
        pos += len(stdout[pos:]) - len(stripped)
        if pos >= length:
            break
        try:
            doc, end = decoder.raw_decode(stdout, pos)
        except json.JSONDecodeError as exc:
            raise SnapshotError(
                "DPC++ frontend produced a truncated or malformed AST "
                f"document stream at offset {pos}: {exc}"
            ) from exc
        docs.append(doc)
        pos = end

    invocations = list(_CC1_INVOCATION_RE.finditer(stderr))
    if len(docs) != len(invocations):
        raise SnapshotError(
            f"DPC++ frontend produced {len(docs)} AST document(s) but "
            f"{len(invocations)} `-cc1 ... -fsycl-is-(host|device)` "
            "invocation(s) were observed on its `-v` stderr output -- "
            "cannot correlate documents to a host/device kind. This "
            "frontend invocation must always pass `-v` alongside "
            "`-ast-dump=json` for DPC++-capable compilers."
        )
    return [
        FrontendContext(kind=m.group("kind"), target=m.group("target"), ast=doc)
        for m, doc in zip(invocations, docs, strict=True)
    ]


def select_frontend_context(
    contexts: list[FrontendContext], requested_kind: str
) -> FrontendContext:
    """Select the one context whose ``kind`` matches *requested_kind*.

    Three outcomes (ADR-050 D5): exactly one match selects; zero matches
    raises :class:`AstContextMissingError` (covers both "this kind was
    never produced" and "the decoded stream was empty" — the same
    underlying condition, an empty ``contexts`` list); more than one match
    raises :class:`AstContextAmbiguousError` — there is no implicit
    tiebreaker, e.g. picking the first. Selection is always by ``kind``,
    never by ``target`` triple pattern-matching (diagnostic-only, see
    :class:`FrontendContext`).
    """
    matches = _select_matches(contexts, requested_kind)
    available = sorted({c.kind for c in contexts})
    return _one_match_or_raise(matches, len(contexts), available, requested_kind)


def _select_matches(
    contexts: list[FrontendContext], requested_kind: str
) -> list[FrontendContext]:
    return [c for c in contexts if c.kind == requested_kind]


def _one_match_or_raise(
    matches: list[FrontendContext],
    total_decoded: int,
    available_kinds: list[str],
    requested_kind: str,
) -> FrontendContext:
    if not matches:
        raise AstContextMissingError(
            f"no AST context with kind={requested_kind!r} found among "
            f"{total_decoded} decoded context(s) (available kinds: "
            f"{available_kinds!r}). Did you mean --frontend-context "
            f"{'device' if requested_kind == 'host' else 'host'}?"
        )
    if len(matches) > 1:
        targets = [c.target for c in matches]
        raise AstContextAmbiguousError(
            f"{len(matches)} AST contexts share kind={requested_kind!r} "
            f"(targets: {targets!r}) -- no implicit tiebreaker; narrow the "
            "request to a specific target."
        )
    return matches[0]


def _iter_json_documents(
    read_more: Callable[[], str], want_text: Callable[[int], bool] | None = None
) -> Iterator[str | None]:
    """Yield each top-level JSON document's raw TEXT (not parsed into a
    dict) from a stream fed by *read_more* -- boundary detection only.

    *read_more* returns the next chunk of text, or ``""`` at end of input.
    Only ever buffers as much text as the CURRENTLY-being-scanned document
    needs (plus whatever a single ``read_more()`` call over-reads into the
    next one) -- never the whole stream at once. This is what lets a
    file-backed caller (:func:`decode_and_select_frontend_context_from_path`)
    avoid holding a multi-document DPC++ capture's full combined size in
    memory just to reach one document near the end.

    Each document must be a top-level JSON **object or array** (``{...}``/
    ``[...]``) -- always true for a clang ``-ast-dump=json`` document, never
    a bare scalar. Boundary detection is a hand-rolled bracket/string-escape
    scan over the CURRENT chunk only (never a growing list of chunks, never
    an ever-growing single ``str``): repeatedly retrying
    ``json.JSONDecoder.raw_decode`` from position 0 over an ever-growing
    ``str`` was quadratic in a single document's size for any document
    spanning more than one chunk (Codex review) -- each incomplete
    ``raw_decode`` attempt re-parses the *entire* accumulated buffer, and
    ``buf += chunk`` on an immutable ``str`` additionally re-copies the
    whole accumulated buffer on every single append. A hundreds-of-MB or
    multi-GB single-pass AST (this module's whole reason to exist) made
    both costs dominate. Here, each byte is inspected exactly once across
    the whole stream, and only ONE chunk is ever held onto beyond the
    currently-scanned one (the collected pieces of a *wanted* document --
    see *want_text* below).

    *want_text*, if given, is called with each document's 0-based index
    BEFORE that document is scanned at all -- when it returns ``False``,
    this function still scans through the document character-by-character
    (so the boundary of the NEXT one can be found) but discards each chunk
    the moment it's fully consumed, never retaining more than the single
    chunk currently being scanned, and yields ``None`` for that document
    instead of its text (P1, Codex review, twice: skipping ``json.loads``
    for a definitely-non-matching document, as
    :func:`_select_from_document_stream` already did, was not enough on its
    own, and neither was skipping just the final join -- the boundary scan
    itself must never accumulate an unwanted document's chunks into a list
    at all, or peak memory for a non-matching multi-GB pass is still that
    pass's full size, live at the same time as an already-selected
    multi-GB match's dict. The *kind* of the pass that produced a document
    is knowable from ``stderr`` alone, positionally, before this function's
    ``read_more`` is ever called for that document's bytes at all, so a
    definitely-non-matching multi-GB document now costs at most one
    chunk's worth of memory to scan past, not its own full size).
    ``want_text=None`` (the default) always collects and yields the text,
    same as before this parameter existed.

    Deliberately does NOT call ``json.loads`` here -- that decision belongs
    to the caller (:func:`_select_from_document_stream`). Only a genuinely
    truncated stream (input ends mid-document) or a leading non-object/array
    character is rejected here; a bracket-balanced-but-invalid-JSON document
    (e.g. ``"{,}"``) is handed to the caller as text when wanted -- whether/
    when that surfaces as an error depends on whether the caller parses it.

    Calls :func:`abicheck.deadline.check` once per underlying *read_more*
    call (P2, Codex review): a single document can take minutes to stream
    through on a multi-GB DPC++ AST pass, and the only deadline checks
    around this decode live in the caller (before/after the whole decode),
    so a budget that expired early would otherwise still burn through the
    rest of that time before the timeout is ever reported.
    """
    cur = ""
    pos = 0
    eof = False

    def _ensure_char() -> bool:
        """Ensure ``cur[pos]`` is valid, replacing ``cur`` wholesale (never
        appending to a list) as it's exhausted -- the just-exhausted chunk
        becomes unreferenced and eligible for GC immediately unless the
        caller archived a piece of it first (see *pieces* below). Returns
        ``False`` only at genuine end of stream."""
        nonlocal cur, pos, eof
        while pos >= len(cur):
            if eof:  # pragma: no cover - defensive: every call site below
                # halts (return/raise) on this function's first False,
                # so a second call after eof is already set never happens
                # in practice; guarded anyway so a future call site that
                # doesn't halt immediately can't call read_more() again
                # past genuine end of stream.
                return False
            deadline.check()
            chunk = read_more()
            if not chunk:
                eof = True
                return False
            cur = chunk
            pos = 0
        return True

    doc_index = 0
    while True:
        if not _ensure_char():
            return  # genuine end of stream, no partial document pending
        while cur[pos].isspace():
            pos += 1
            if not _ensure_char():
                return
        c = cur[pos]
        if c not in "{[":
            raise SnapshotError(
                "DPC++ frontend produced a truncated or malformed AST "
                f"document stream: expected an object/array, found {c!r}"
            )

        # Decided BEFORE scanning a single byte of this document -- purely
        # from *stderr*'s positional invocation list, never this document's
        # own content -- so an unwanted document never accumulates chunks
        # below at all, not even transiently.
        wants_this = want_text is None or want_text(doc_index)
        pieces: list[str] = []
        piece_start = pos

        depth = 0
        in_string = False
        escape = False
        end_pos: int | None = None
        while end_pos is None:
            if pos >= len(cur):
                if wants_this:
                    pieces.append(cur[piece_start:])
                if not _ensure_char():
                    raise SnapshotError(
                        "DPC++ frontend produced a truncated or malformed AST "
                        "document stream: unexpected end of input"
                    )
                piece_start = pos  # == 0, freshly replaced `cur`
                continue
            c = cur[pos]
            if in_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
            elif c == '"':
                in_string = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    pos += 1
                    end_pos = pos
                    continue
            pos += 1

        doc_text: str | None
        if wants_this:
            pieces.append(cur[piece_start:end_pos])
            doc_text = pieces[0] if len(pieces) == 1 else "".join(pieces)
        else:
            doc_text = None
        yield doc_text
        doc_index += 1


def _select_from_document_stream(
    read_more: Callable[[], str], stderr: str, requested_kind: str
) -> FrontendContext:
    """Shared core of both ``decode_and_select_frontend_context*`` entry
    points: correlates the documents streamed from *read_more* against
    *stderr*'s ``-cc1`` invocation lines positionally, and applies the same
    three-outcome selection logic -- raising
    :class:`abicheck.errors.AstContextAmbiguousError` as soon as a
    **second** matching document is seen (Codex review) rather than
    scanning and retaining every match first, since each matching pass can
    itself be multi-GB (a device build can emit several offload-target
    passes).

    A document is only ``json.loads``-parsed into a dict when its
    correlated invocation's *kind* matches *requested_kind*, or when it has
    no correlated invocation at all (an "extra" beyond *stderr*'s own count,
    whose kind can't be known -- must still be validated so a genuinely
    malformed extra document is reported directly instead of only via the
    less-specific count-mismatch check below). A definitely-non-matching
    document's kind is known positionally from *stderr* alone, before its
    text is even looked at -- computed here as ``invocations`` and handed to
    :func:`_iter_json_documents` as *want_text* so it never even joins that
    document's chunks into one string, let alone parses it (P1, Codex
    review, twice): without this, a non-matching multi-GB pass's full text
    (and then its dict) would be built and briefly live in memory *at the
    same time* as an already-selected multi-GB match's dict.
    """
    invocations = list(_CC1_INVOCATION_RE.finditer(stderr))

    def _want_text(doc_index: int) -> bool:
        return (
            doc_index >= len(invocations)
            or invocations[doc_index].group("kind") == requested_kind
        )

    first_match: FrontendContext | None = None
    doc_count = 0
    for doc_text in _iter_json_documents(read_more, _want_text):
        invocation = invocations[doc_count] if doc_count < len(invocations) else None
        kind = invocation.group("kind") if invocation is not None else None
        if invocation is None or kind == requested_kind:
            assert doc_text is not None
            try:
                doc = json.loads(doc_text)
            except json.JSONDecodeError as exc:
                raise SnapshotError(
                    "DPC++ frontend produced a truncated or malformed AST "
                    f"document stream: {exc}"
                ) from exc
            if kind == requested_kind:
                assert invocation is not None
                if first_match is None:
                    first_match = FrontendContext(
                        kind=requested_kind,
                        target=invocation.group("target"),
                        ast=doc,
                    )
                else:
                    raise AstContextAmbiguousError(
                        f"2+ AST contexts share kind={requested_kind!r} "
                        f"(targets: {[first_match.target, invocation.group('target')]!r}) "
                        "-- no implicit tiebreaker; narrow the request to a "
                        "specific target."
                    )
        doc_count += 1
    if doc_count != len(invocations):
        raise SnapshotError(
            f"DPC++ frontend produced {doc_count} AST document(s) but "
            f"{len(invocations)} `-cc1 ... -fsycl-is-(host|device)` "
            "invocation(s) were observed on its `-v` stderr output -- "
            "cannot correlate documents to a host/device kind. This "
            "frontend invocation must always pass `-v` alongside "
            "`-ast-dump=json` for DPC++-capable compilers."
        )
    matches = [first_match] if first_match is not None else []
    available = sorted({inv.group("kind") for inv in invocations})
    return _one_match_or_raise(matches, doc_count, available, requested_kind)


def decode_and_select_frontend_context(
    stdout: str, stderr: str, requested_kind: str
) -> FrontendContext:
    """Fused decode+select over an already-in-memory *stdout* string.

    Never retains a non-matching (or second-matching) document's full AST
    tree (see :func:`_select_from_document_stream`), but *stdout* itself is
    assumed already fully materialized by the caller -- this is a thin
    convenience wrapper for tests and any caller that already has the
    decoded text in hand. The real production caller with a file on disk
    should use :func:`decode_and_select_frontend_context_from_path`
    instead, which never loads the whole stream into memory at all.

    Behaves identically to ``select_frontend_context(decode_frontend_
    contexts(stdout, stderr), requested_kind)`` for the exactly-one-match
    and zero-matches outcomes (including the count-mismatch/truncated-
    document errors); for the ambiguous outcome, it reports only the first
    two matching targets rather than every one found.
    """
    remaining = [stdout]

    def _read_more() -> str:
        chunk, remaining[0] = remaining[0], ""
        return chunk

    return _select_from_document_stream(_read_more, stderr, requested_kind)


def decode_and_select_frontend_context_from_path(
    ast_path: Path, stderr: str, requested_kind: str, *, chunk_size: int = 1 << 20
) -> FrontendContext:
    """Like :func:`decode_and_select_frontend_context`, but reads *ast_path*
    incrementally in *chunk_size*-byte increments instead of loading the
    whole concatenated stream into memory up front (Codex review, second
    round: the first fused-decoder fix stopped retaining every *parsed*
    document, but ``dumper_clang_errors._parse_clang_ast_result`` still read
    the entire raw stream via one ``ast_path.read_text()`` call before any
    parsing began -- a DPC++ header's combined host+device stream can
    itself be a multiple of any single pass's already-multi-GB size). Peak
    buffered text is bounded by roughly one document's size (plus at most
    one chunk of over-read into the next document), not the combined size
    of every pass in the stream.
    """
    with open(ast_path, encoding="utf-8") as fh:
        return _select_from_document_stream(
            lambda: fh.read(chunk_size), stderr, requested_kind
        )
