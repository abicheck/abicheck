"""Non-streaming DPC++ frontend-context decode and select: the
independent oracle ``tests/test_sycl_context.py`` checks
``abicheck.sycl_context.decode_and_select_frontend_context_from_path``
(the fused, streaming production path) against.

These were package code that only tests called; they live here because a
second, simpler implementation of the same contract is what makes the
production decoder's parity test meaningful.
"""

from __future__ import annotations

import json
from typing import Any

from abicheck.errors import SnapshotError
from abicheck.sycl_context import (
    _CC1_INVOCATION_RE,
    FrontendContext,
    _one_match_or_raise,
    _select_from_document_stream,
)


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
