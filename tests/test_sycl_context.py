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

"""ADR-050 D5 (G32 Phase D): abicheck.sycl_context's document-boundary
decoder and kind-based selector, tested against the real DPC++ capture in
tests/fixtures/g32/dpcpp/ (see that directory's README.md)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from abicheck import deadline
from abicheck.errors import (
    AstContextAmbiguousError,
    AstContextMissingError,
    SnapshotError,
)
from abicheck.sycl_context import (
    FrontendContext,
    _iter_json_documents,
    decode_and_select_frontend_context,
    decode_and_select_frontend_context_from_path,
    decode_frontend_contexts,
    select_frontend_context,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "g32" / "dpcpp"


def _real_stdout_stderr() -> tuple[str, str]:
    return (
        (_FIXTURE_DIR / "ast_dump.json").read_text(),
        (_FIXTURE_DIR / "compiler_invocation.log").read_text(),
    )


def test_decode_real_capture_yields_device_then_host() -> None:
    stdout, stderr = _real_stdout_stderr()
    contexts = decode_frontend_contexts(stdout, stderr)
    assert [c.kind for c in contexts] == ["device", "host"]
    assert contexts[0].target == "spir64-unknown-unknown"
    assert contexts[1].target == "x86_64-unknown-linux-gnu"
    # Both passes still see the header's own declarations.
    for c in contexts:
        names = {child.get("name") for child in c.ast["inner"]}
        assert "Point" in names
        assert "add" in names


def test_select_host_from_real_capture() -> None:
    stdout, stderr = _real_stdout_stderr()
    contexts = decode_frontend_contexts(stdout, stderr)
    selected = select_frontend_context(contexts, "host")
    assert selected.kind == "host"
    assert selected.target == "x86_64-unknown-linux-gnu"


def test_select_device_from_real_capture() -> None:
    stdout, stderr = _real_stdout_stderr()
    contexts = decode_frontend_contexts(stdout, stderr)
    selected = select_frontend_context(contexts, "device")
    assert selected.kind == "device"
    assert selected.target == "spir64-unknown-unknown"


def _ctx(kind: str, target: str = "t") -> FrontendContext:
    return FrontendContext(
        kind=kind, target=target, ast={"kind": "TranslationUnitDecl"}
    )


def test_select_zero_matches_raises_missing() -> None:
    contexts = [_ctx("device")]
    with pytest.raises(AstContextMissingError, match="host"):
        select_frontend_context(contexts, "host")


def test_select_empty_contexts_raises_missing() -> None:
    with pytest.raises(AstContextMissingError):
        select_frontend_context([], "host")


def test_select_ambiguous_raises() -> None:
    contexts = [_ctx("device", "spir64"), _ctx("device", "spir64_x86_64")]
    with pytest.raises(AstContextAmbiguousError, match="spir64"):
        select_frontend_context(contexts, "device")


def test_select_is_by_kind_not_target_triple_pattern() -> None:
    """Regression: a context whose target triple happens to look
    unrelated to the requested kind string must still be selected purely
    by its `kind` field, never rejected via triple pattern-matching."""
    contexts = [_ctx("device", "spir64-unknown-unknown")]
    selected = select_frontend_context(contexts, "device")
    assert selected.target == "spir64-unknown-unknown"


def test_decode_rejects_truncated_document() -> None:
    stdout = '{"kind": "TranslationUnitDecl", "inner": ['  # truncated
    with pytest.raises(SnapshotError, match="truncated or malformed"):
        decode_frontend_contexts(stdout, "")


def test_decode_rejects_document_count_mismatch_with_invocations() -> None:
    # One well-formed document but zero correlating -cc1 lines on stderr.
    stdout = '{"kind": "TranslationUnitDecl", "inner": []}'
    with pytest.raises(SnapshotError, match="cannot correlate"):
        decode_frontend_contexts(stdout, "")


def test_decode_empty_stdout_and_stderr_yields_empty_list() -> None:
    assert decode_frontend_contexts("", "") == []


def test_decode_tolerates_trailing_whitespace_after_last_document() -> None:
    stdout = '{"kind": "TranslationUnitDecl", "inner": []}\n\n  '
    stderr = ' "clang" -cc1 -triple x86_64-unknown-linux-gnu -fsycl-is-host foo\n'
    contexts = decode_frontend_contexts(stdout, stderr)
    assert [c.kind for c in contexts] == ["host"]


def test_decode_tolerates_whitespace_between_documents() -> None:
    stdout = (
        '{"kind": "TranslationUnitDecl", "inner": []}\n\n'
        '{"kind": "TranslationUnitDecl", "inner": []}'
    )
    stderr = (
        ' "clang" -cc1 -triple spir64-unknown-unknown -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple x86_64-unknown-linux-gnu -fsycl-is-host foo\n'
    )
    contexts = decode_frontend_contexts(stdout, stderr)
    assert [c.kind for c in contexts] == ["device", "host"]


# ── decode_and_select_frontend_context: fused decode+select (Codex review) ──
# Memory-frugal production path -- must behave identically to the separate
# decode_frontend_contexts()+select_frontend_context() two-step for every
# outcome, only without retaining a non-matching pass's full AST tree.


def test_fused_select_device_matches_two_step_result() -> None:
    stdout, stderr = _real_stdout_stderr()
    fused = decode_and_select_frontend_context(stdout, stderr, "device")
    two_step = select_frontend_context(
        decode_frontend_contexts(stdout, stderr), "device"
    )
    assert fused.kind == two_step.kind == "device"
    assert fused.target == two_step.target == "spir64-unknown-unknown"
    assert fused.ast == two_step.ast


def test_fused_select_host_matches_two_step_result() -> None:
    stdout, stderr = _real_stdout_stderr()
    fused = decode_and_select_frontend_context(stdout, stderr, "host")
    two_step = select_frontend_context(decode_frontend_contexts(stdout, stderr), "host")
    assert fused.kind == two_step.kind == "host"
    assert fused.target == two_step.target == "x86_64-unknown-linux-gnu"
    assert fused.ast == two_step.ast


def test_fused_select_zero_matches_raises_missing() -> None:
    stdout, stderr = _real_stdout_stderr()
    with pytest.raises(AstContextMissingError, match="device"):
        # The real fixture has no second device pass sharing this made-up
        # kind, so this exercises the fused function's own missing-kind path
        # against real correlated stderr/stdout, not a synthetic doc.
        decode_and_select_frontend_context(stdout, stderr, "bogus-kind")
    with pytest.raises(AstContextMissingError):
        decode_and_select_frontend_context("", "", "host")


def test_fused_select_ambiguous_raises() -> None:
    stdout = (
        '{"kind": "TranslationUnitDecl", "inner": []}\n'
        '{"kind": "TranslationUnitDecl", "inner": []}'
    )
    stderr = (
        ' "clang" -cc1 -triple spir64 -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple spir64_x86_64 -fsycl-is-device foo\n'
    )
    with pytest.raises(AstContextAmbiguousError, match="spir64"):
        decode_and_select_frontend_context(stdout, stderr, "device")


def test_fused_select_ambiguous_stops_at_second_match_without_further_scan() -> None:
    """Codex review: raising as soon as a SECOND matching document is seen
    must not require scanning (or retaining) any later document -- proven
    here by making the third document truncated/malformed. If the function
    kept scanning past the second match, it would hit that malformed
    document and raise SnapshotError instead; getting AstContextAmbiguousError
    proves it stopped at exactly the second match."""
    stdout = (
        '{"kind": "TranslationUnitDecl", "inner": []}\n'
        '{"kind": "TranslationUnitDecl", "inner": []}\n'
        '{"kind": "TranslationUnitDecl", "inner": ['  # truncated -- never reached
    )
    stderr = (
        ' "clang" -cc1 -triple spir64 -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple spir64_x86_64 -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple spir64_gen -fsycl-is-device foo\n'
    )
    with pytest.raises(AstContextAmbiguousError, match="spir64"):
        decode_and_select_frontend_context(stdout, stderr, "device")


def test_fused_select_ambiguous_second_match_never_parsed_or_joined() -> None:
    """P1 (Codex review, fourth round): the SECOND matching document's mere
    existence -- known purely from stderr's positional kind info -- is
    already enough to raise AstContextAmbiguousError; it must never be
    json.loads-parsed OR have its chunks joined into one string. Proven by
    making the second (ambiguous) document itself malformed: if it were
    parsed, this would raise SnapshotError instead of
    AstContextAmbiguousError."""
    stdout = (
        '{"kind": "TranslationUnitDecl", "inner": []}\n'
        "{,}"  # the second matching (device) document -- never parsed
    )
    stderr = (
        ' "clang" -cc1 -triple spir64 -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple spir64_x86_64 -fsycl-is-device foo\n'
    )
    with pytest.raises(AstContextAmbiguousError, match="spir64"):
        decode_and_select_frontend_context(stdout, stderr, "device")


def test_fused_select_rejects_truncated_document() -> None:
    stdout = '{"kind": "TranslationUnitDecl", "inner": ['  # truncated
    with pytest.raises(SnapshotError, match="truncated or malformed"):
        decode_and_select_frontend_context(stdout, "", "host")


def test_fused_select_rejects_document_count_mismatch_with_invocations() -> None:
    stdout = '{"kind": "TranslationUnitDecl", "inner": []}'
    with pytest.raises(SnapshotError, match="cannot correlate"):
        decode_and_select_frontend_context(stdout, "", "host")


def test_fused_select_does_not_retain_non_matching_document_ast() -> None:
    """The whole point of the fused path: a non-matching pass's AST must
    never survive into the returned FrontendContext or anywhere else this
    function holds a reference to -- only the selected document's tree is
    kept, so a huge discarded pass is eligible for GC immediately."""
    stdout = (
        '{"kind": "TranslationUnitDecl", "huge": "discarded-device-payload"}\n'
        '{"kind": "TranslationUnitDecl", "small": "host-payload"}'
    )
    stderr = (
        ' "clang" -cc1 -triple spir64-unknown-unknown -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple x86_64-unknown-linux-gnu -fsycl-is-host foo\n'
    )
    selected = decode_and_select_frontend_context(stdout, stderr, "host")
    assert selected.ast == {"kind": "TranslationUnitDecl", "small": "host-payload"}
    assert "huge" not in selected.ast


def test_fused_select_tolerates_trailing_whitespace_after_last_document() -> None:
    stdout = '{"kind": "TranslationUnitDecl", "inner": []}\n\n  '
    stderr = ' "clang" -cc1 -triple x86_64-unknown-linux-gnu -fsycl-is-host foo\n'
    selected = decode_and_select_frontend_context(stdout, stderr, "host")
    assert selected.kind == "host"


# ── decode_and_select_frontend_context_from_path: incremental file reads ────
# (Codex review, second round: the first fused-decoder fix stopped retaining
# every *parsed* document, but the real caller still read the whole raw
# stream into memory via one read_text() call before any parsing began.)


def test_from_path_device_selects_real_fixture_with_tiny_chunk_size(
    tmp_path: Path,
) -> None:
    """A deliberately tiny chunk_size forces many incremental reads across
    the real two-document capture -- proving the file-based path decodes
    correctly even when no single read() call sees a whole document."""
    stdout, stderr = _real_stdout_stderr()
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text(stdout, encoding="utf-8")
    selected = decode_and_select_frontend_context_from_path(
        ast_path, stderr, "device", chunk_size=64
    )
    assert selected.kind == "device"
    assert selected.target == "spir64-unknown-unknown"
    assert selected.ast["kind"] == "TranslationUnitDecl"


def test_from_path_host_selects_real_fixture_with_tiny_chunk_size(
    tmp_path: Path,
) -> None:
    stdout, stderr = _real_stdout_stderr()
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text(stdout, encoding="utf-8")
    selected = decode_and_select_frontend_context_from_path(
        ast_path, stderr, "host", chunk_size=64
    )
    assert selected.kind == "host"
    assert selected.target == "x86_64-unknown-linux-gnu"


def test_from_path_matches_string_based_result_at_default_chunk_size(
    tmp_path: Path,
) -> None:
    stdout, stderr = _real_stdout_stderr()
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text(stdout, encoding="utf-8")
    from_path = decode_and_select_frontend_context_from_path(ast_path, stderr, "device")
    from_string = decode_and_select_frontend_context(stdout, stderr, "device")
    assert from_path.kind == from_string.kind
    assert from_path.target == from_string.target
    assert from_path.ast == from_string.ast


def test_from_path_does_not_retain_non_matching_document_ast(tmp_path: Path) -> None:
    stdout = (
        '{"kind": "TranslationUnitDecl", "huge": "discarded-device-payload"}\n'
        '{"kind": "TranslationUnitDecl", "small": "host-payload"}'
    )
    stderr = (
        ' "clang" -cc1 -triple spir64-unknown-unknown -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple x86_64-unknown-linux-gnu -fsycl-is-host foo\n'
    )
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text(stdout, encoding="utf-8")
    selected = decode_and_select_frontend_context_from_path(
        ast_path, stderr, "host", chunk_size=8
    )
    assert selected.ast == {"kind": "TranslationUnitDecl", "small": "host-payload"}
    assert "huge" not in selected.ast


def test_from_path_skips_parsing_non_matching_malformed_document(
    tmp_path: Path,
) -> None:
    """P1 (Codex review): a definitely-non-matching document's *kind* is
    known positionally from stderr alone, before its content is ever looked
    at -- so it must never be json.loads-parsed at all, not even
    transiently alongside an already-selected match. Proven by making the
    non-matching (device) document bracket-balanced but otherwise invalid
    JSON: if it were parsed, this would raise SnapshotError instead of
    returning the matching host document."""
    stdout = '{,}\n{"kind": "TranslationUnitDecl", "small": "host-payload"}'
    stderr = (
        ' "clang" -cc1 -triple spir64-unknown-unknown -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple x86_64-unknown-linux-gnu -fsycl-is-host foo\n'
    )
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text(stdout, encoding="utf-8")
    selected = decode_and_select_frontend_context_from_path(
        ast_path, stderr, "host", chunk_size=8
    )
    assert selected.ast == {"kind": "TranslationUnitDecl", "small": "host-payload"}


def test_fused_select_skips_parsing_non_matching_malformed_document() -> None:
    """Same property as the from-path variant above, over the in-memory
    fused path."""
    stdout = '{,}\n{"kind": "TranslationUnitDecl", "small": "host-payload"}'
    stderr = (
        ' "clang" -cc1 -triple spir64-unknown-unknown -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple x86_64-unknown-linux-gnu -fsycl-is-host foo\n'
    )
    selected = decode_and_select_frontend_context(stdout, stderr, "host")
    assert selected.ast == {"kind": "TranslationUnitDecl", "small": "host-payload"}


def test_iter_json_documents_want_text_false_skips_join() -> None:
    """P1 (Codex review, follow-up): skipping json.loads for a definitely-
    non-matching document isn't enough on its own -- unconditionally joining
    its chunks into one string first still materializes a full extra copy.
    want_text=False must skip the join itself, yielding None instead of the
    document's text."""
    remaining = ['{"a": 1}{"b": 2}']

    def _read_more() -> str:
        chunk, remaining[0] = remaining[0], ""
        return chunk

    docs = list(_iter_json_documents(_read_more, lambda i: i == 1))
    assert docs == [None, '{"b": 2}']


def test_iter_json_documents_default_want_text_always_joins() -> None:
    remaining = ['{"a": 1}{"b": 2}']

    def _read_more() -> str:
        chunk, remaining[0] = remaining[0], ""
        return chunk

    docs = list(_iter_json_documents(_read_more))
    assert docs == ['{"a": 1}', '{"b": 2}']


def test_iter_json_documents_discards_earlier_chunks_for_unwanted_document() -> None:
    """P1 (Codex review, third round): want_text=False must not just skip
    the final join -- the scan itself must never accumulate more than the
    single chunk currently being read for an unwanted document, or a large
    non-matching pass still costs its own full size in memory (live at the
    same time as an already-selected match) before this generator ever gets
    around to yielding None for it. Proven via CPython refcounts: each
    chunk making up the unwanted (first) document must become unreferenced
    by the generator as soon as scanning moves past it -- only the single
    chunk still being scanned when the document ends may remain referenced.
    """
    unwanted_pieces = ['{"a": ', '"xx", ', '"b": 2}']  # 3 chunks, no nesting
    wanted_text = '{"c": 3}'
    all_chunks_src = [*unwanted_pieces, wanted_text]
    supplied: list[str] = []
    idx = [0]

    def _read_more() -> str:
        if idx[0] >= len(all_chunks_src):
            return ""
        # A fresh, non-interned string object per chunk -- refcount checks
        # below need an object identity distinct from the source list's.
        chunk = "".join(all_chunks_src[idx[0]])
        supplied.append(chunk)
        idx[0] += 1
        return chunk

    gen = _iter_json_documents(_read_more, lambda i: i == 1)
    doc0 = next(gen)
    assert doc0 is None
    # Captured into plain ints first -- pytest's assertion-rewriting keeps a
    # transient extra reference alive when getrefcount() is called directly
    # inside an assert expression, which would otherwise mask the very gap
    # this test exists to detect. Only this test's own `supplied` list
    # should reference the two EARLIER chunks by now -- the generator
    # itself must have dropped them already scanning past them, unlike the
    # pre-fix implementation, which kept every chunk of the whole document
    # (including these two) alive in its `chunks` list until the document's
    # end.
    refcount0 = sys.getrefcount(supplied[0])
    refcount1 = sys.getrefcount(supplied[1])
    assert refcount0 == 2
    assert refcount1 == 2

    doc1 = next(gen)
    assert doc1 == wanted_text


def test_fused_select_checks_deadline_during_stream() -> None:
    """P2 (Codex review): a DPC++ AST document can take minutes to stream
    through, but before this fix the only deadline checks around this
    decode lived in the caller (dumper_clang_errors), before/after the
    whole decode -- an already-expired budget would still run the full
    scan/parse before the timeout was ever reported. Now checked once per
    underlying read, so an expired deadline is caught on the very first
    chunk instead of only after decoding completes."""
    stdout, stderr = _real_stdout_stderr()
    with deadline.deadline_scope(-1):  # already expired
        with pytest.raises(deadline.DeadlineExceeded):
            decode_and_select_frontend_context(stdout, stderr, "host")


def test_from_path_checks_deadline_during_stream(tmp_path: Path) -> None:
    stdout, stderr = _real_stdout_stderr()
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text(stdout, encoding="utf-8")
    with deadline.deadline_scope(-1):  # already expired
        with pytest.raises(deadline.DeadlineExceeded):
            decode_and_select_frontend_context_from_path(ast_path, stderr, "host")


def test_from_path_ambiguous_stops_at_second_match_with_tiny_chunk_size(
    tmp_path: Path,
) -> None:
    stdout = (
        '{"kind": "TranslationUnitDecl", "inner": []}\n'
        '{"kind": "TranslationUnitDecl", "inner": []}\n'
        '{"kind": "TranslationUnitDecl", "inner": ['  # truncated -- never reached
    )
    stderr = (
        ' "clang" -cc1 -triple spir64 -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple spir64_x86_64 -fsycl-is-device foo\n'
        ' "clang" -cc1 -triple spir64_gen -fsycl-is-device foo\n'
    )
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text(stdout, encoding="utf-8")
    with pytest.raises(AstContextAmbiguousError, match="spir64"):
        decode_and_select_frontend_context_from_path(
            ast_path, stderr, "device", chunk_size=8
        )


def test_from_path_zero_matches_raises_missing(tmp_path: Path) -> None:
    stdout, stderr = _real_stdout_stderr()
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text(stdout, encoding="utf-8")
    with pytest.raises(AstContextMissingError):
        decode_and_select_frontend_context_from_path(ast_path, stderr, "bogus-kind")


def test_from_path_rejects_truncated_document_with_tiny_chunk_size(
    tmp_path: Path,
) -> None:
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text('{"kind": "TranslationUnitDecl", "inner": [', encoding="utf-8")
    with pytest.raises(SnapshotError, match="truncated or malformed"):
        decode_and_select_frontend_context_from_path(
            ast_path, "", "host", chunk_size=4
        )


def test_from_path_rejects_non_utf8_bytes_as_snapshot_error(tmp_path: Path) -> None:
    """CodeRabbit review: non-UTF-8 bytes in *ast_path* used to raise a bare
    ``UnicodeDecodeError`` straight out of ``open(...).read()``, inconsistent
    with every other decode failure in this module being normalized to
    ``SnapshotError``."""
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_bytes(b'{"kind": "TranslationUnitDecl", "inner": [\xff\xfe')
    with pytest.raises(SnapshotError, match="not valid UTF-8"):
        decode_and_select_frontend_context_from_path(ast_path, "", "host")


def test_from_path_large_document_parses_json_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review, PR #636: the document-boundary scan used to retry
    ``json.JSONDecoder.raw_decode`` from position 0 over an ever-growing
    ``str`` on every incomplete chunk, and grow that same ``str`` via
    ``buf += chunk`` -- both re-process/re-copy the WHOLE accumulated
    buffer each time, making a single document spanning many chunks
    quadratic in its own size (a real DPC++ AST pass, the hundreds-of-MB-
    to-multi-GB case this module exists to support, is exactly where this
    would matter).

    A wall-clock timing assertion would be the obvious regression guard,
    but it turned out fragile here: this scan is a Python-level per-
    character loop, so a trace-based `coverage` backend (not the cheap
    `sys.monitoring` one CI's own coverage-instrumented lane uses, but
    still what a plain local `pytest --cov` run picks on Python < 3.12)
    slows it down by roughly 6x on its own -- enough to erase the margin
    against a hand-picked wall-clock ceiling. Asserting the actual
    complexity property directly instead: ``json.loads`` must be called
    exactly ONCE for one large document split across many small chunks,
    never once per incomplete-parse retry -- true regardless of tracing
    overhead, coverage backend, or machine speed.

    Also exercises correctness under chunking: the large string value
    embeds literal ``{``/``}``/``[``/``]``/``\\"`` characters, which a naive
    bracket counter (rather than a proper string-aware scan) would
    miscount as structural depth changes.
    """
    calls: list[int] = []
    orig_loads = json.loads

    def _counting_loads(s: str, *a: object, **kw: object) -> object:
        calls.append(len(s))
        return orig_loads(s, *a, **kw)

    monkeypatch.setattr(json, "loads", _counting_loads)

    tricky_string = '{[]}\\"' * 50_000  # brace-like noise, escaped on encode
    payload = {"kind": "TranslationUnitDecl", "tricky": tricky_string, "inner": []}
    stdout = json.dumps(payload)
    stderr = ' "clang" -cc1 -triple x86_64-unknown-linux-gnu -fsycl-is-host foo\n'
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text(stdout, encoding="utf-8")

    selected = decode_and_select_frontend_context_from_path(
        ast_path, stderr, "host", chunk_size=4096
    )

    assert selected.ast == payload
    assert calls == [len(stdout)]


def test_from_path_rejects_non_object_document(tmp_path: Path) -> None:
    """A clang AST document is always a top-level JSON object -- a bare
    scalar (or anything not starting with ``{``/``[``) is never valid AST
    output, so the incremental scanner rejects it immediately rather than
    looping forever waiting for a bracket depth that will never occur."""
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text("42", encoding="utf-8")
    with pytest.raises(SnapshotError, match="expected an object/array"):
        decode_and_select_frontend_context_from_path(ast_path, "", "host")


def test_from_path_rejects_bracket_balanced_but_invalid_json(tmp_path: Path) -> None:
    """The incremental scanner finds document boundaries via bracket depth
    alone, without validating full JSON grammar -- a bracket-balanced but
    otherwise malformed body (here, a bare comma where a key is expected)
    still reaches ``json.loads``, which is what actually rejects it."""
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text("{,}", encoding="utf-8")
    with pytest.raises(SnapshotError, match="truncated or malformed"):
        decode_and_select_frontend_context_from_path(ast_path, "", "host")


def test_fused_select_rejects_malformed_sole_match() -> None:
    """Distinct from the non-matching/extra-document malformed cases above:
    here the ONLY document actually correlates (positionally) with the
    requested kind, so it must still reach json.loads via the "confirmed
    match" branch, not the "extra document" branch -- and a bracket-
    balanced-but-invalid body is still rejected."""
    stderr = ' "clang" -cc1 -triple x86_64-unknown-linux-gnu -fsycl-is-host foo\n'
    with pytest.raises(SnapshotError, match="truncated or malformed"):
        decode_and_select_frontend_context("{,}", stderr, "host")


def test_from_path_rejects_document_count_mismatch(tmp_path: Path) -> None:
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text('{"kind": "TranslationUnitDecl", "inner": []}', encoding="utf-8")
    with pytest.raises(SnapshotError, match="cannot correlate"):
        decode_and_select_frontend_context_from_path(ast_path, "", "host")


def test_from_path_empty_file_raises_missing(tmp_path: Path) -> None:
    ast_path = tmp_path / "ast_dump.json"
    ast_path.write_text("", encoding="utf-8")
    with pytest.raises(AstContextMissingError):
        decode_and_select_frontend_context_from_path(ast_path, "", "host")
