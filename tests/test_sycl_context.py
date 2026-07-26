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

from pathlib import Path

import pytest

from abicheck.errors import (
    AstContextAmbiguousError,
    AstContextMissingError,
    SnapshotError,
)
from abicheck.sycl_context import (
    FrontendContext,
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
