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


def _iter_json_documents(read_more: Callable[[], str]) -> Iterator[dict[str, Any]]:
    """Yield each top-level JSON document from a stream fed by *read_more*.

    *read_more* returns the next chunk of text, or ``""`` at end of input.
    Only ever buffers as much text as the CURRENTLY-being-parsed document
    needs (plus whatever a single ``read_more()`` call over-reads into the
    next one) -- never the whole stream at once. This is what lets a
    file-backed caller (:func:`decode_and_select_frontend_context_from_path`)
    avoid holding a multi-document DPC++ capture's full combined size in
    memory just to reach one document near the end.

    Each document must be a top-level JSON **object or array** (``{...}``/
    ``[...]``) -- always true for a clang ``-ast-dump=json`` document, never
    a bare scalar. Boundary detection is a hand-rolled bracket/string-escape
    scan over ``chunks`` (a list of not-yet-fully-consumed pieces, each
    scanned exactly once) rather than repeatedly retrying
    ``json.JSONDecoder.raw_decode`` from position 0 over an ever-growing
    single ``str``: that approach was quadratic in a single document's size
    for any document spanning more than one chunk (Codex review) -- each
    incomplete ``raw_decode`` attempt re-parses the *entire* accumulated
    buffer, and ``buf += chunk`` on an immutable ``str`` additionally
    re-copies the whole accumulated buffer on every single append. A
    hundreds-of-MB or multi-GB single-pass AST (this module's whole reason
    to exist) made both costs dominate. Here, each byte is inspected exactly
    once across the whole stream, and a document's text is materialized
    (joined, then ``json.loads``-parsed) exactly once, when its closing
    bracket is found.

    A genuinely truncated stream (input ends mid-document) or a malformed
    document (bracket-balanced but not actually valid JSON) both raise
    :class:`abicheck.errors.SnapshotError`.
    """
    eof = False
    chunks: list[str] = []

    def _fill() -> bool:
        nonlocal eof
        chunk = read_more()
        if not chunk:
            eof = True
            return False
        chunks.append(chunk)
        return True

    ci = 0  # index into `chunks` of the character at (ci, co)
    co = 0

    def _current() -> str | None:
        """Character at the scan cursor, fetching more input as needed.
        ``None`` only at genuine end of stream."""
        nonlocal ci, co
        while True:
            if ci < len(chunks):
                if co < len(chunks[ci]):
                    return chunks[ci][co]
                ci += 1
                co = 0
                continue
            if eof or not _fill():
                return None

    while True:
        c = _current()
        while c is not None and c.isspace():
            co += 1
            c = _current()
        if c is None:
            return  # genuine end of stream, no partial document pending
        if c not in "{[":
            raise SnapshotError(
                "DPC++ frontend produced a truncated or malformed AST "
                f"document stream: expected an object/array, found {c!r}"
            )
        start_ci, start_co = ci, co

        depth = 0
        in_string = False
        escape = False
        end_ci = end_co = None
        while end_ci is None:
            c = _current()
            if c is None:
                raise SnapshotError(
                    "DPC++ frontend produced a truncated or malformed AST "
                    "document stream: unexpected end of input"
                )
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
                    co += 1
                    end_ci, end_co = ci, co
                    continue
            co += 1

        if start_ci == end_ci:
            doc_text = chunks[start_ci][start_co:end_co]
        else:
            doc_text = "".join(
                (
                    chunks[start_ci][start_co:],
                    *chunks[start_ci + 1 : end_ci],
                    chunks[end_ci][:end_co],
                )
            )
        try:
            doc = json.loads(doc_text)
        except json.JSONDecodeError as exc:
            raise SnapshotError(
                "DPC++ frontend produced a truncated or malformed AST "
                f"document stream: {exc}"
            ) from exc
        yield doc

        # Drop fully-consumed chunks; keep only the (typically small,
        # bounded by chunk size, not document size) unconsumed tail.
        # `end_ci` is always a valid index into the pre-deletion `chunks`
        # (it comes from a cursor that just read a real character there),
        # so at least chunks[end_ci] itself always survives the deletion.
        del chunks[:end_ci]
        chunks[0] = chunks[0][end_co:]
        ci = 0
        co = 0


def _select_from_document_stream(
    docs: Iterator[dict[str, Any]], stderr: str, requested_kind: str
) -> FrontendContext:
    """Shared core of both ``decode_and_select_frontend_context*`` entry
    points: correlates *docs* against *stderr*'s ``-cc1`` invocation lines
    positionally, and applies the same three-outcome selection logic --
    raising :class:`abicheck.errors.AstContextAmbiguousError` as soon as a
    **second** matching document is seen (Codex review) rather than
    scanning and retaining every match first, since each matching pass can
    itself be multi-GB (a device build can emit several offload-target
    passes).
    """
    invocations = list(_CC1_INVOCATION_RE.finditer(stderr))
    first_match: FrontendContext | None = None
    doc_count = 0
    for doc in docs:
        if doc_count < len(invocations):
            invocation = invocations[doc_count]
            if invocation.group("kind") == requested_kind:
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

    return _select_from_document_stream(
        _iter_json_documents(_read_more), stderr, requested_kind
    )


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
            _iter_json_documents(lambda: fh.read(chunk_size)), stderr, requested_kind
        )
