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

"""One Semantic Pipeline plan, sub-phase 4B, ``dump``'s own slice.

Mirrors ``test_service_compare_pipeline.py::TestResolvedExecutionContextWiring``
for :func:`abicheck.service_dump_pipeline.resolve_dump_request` /
:func:`abicheck.service_dump_pipeline.execute_dump_request`: both closed the
identical "no real caller" gap ``ResolvedComparePair.resolved_execution_context``
closed for ``compare`` (``resolve_dump_request`` wires the pre-execution view;
``execute_dump_request`` is the real post-execution ``with_assurance()``
caller the plan's own tracking row named as still open for every operation).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.api_types import DumpRequest, InputSpec
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import snapshot_to_json
from abicheck.service_dump_pipeline import (
    ResolvedDumpRequest,
    execute_dump_request,
    resolve_dump_request,
)
from abicheck.workflows.resolved_execution_context import ResolvedExecutionContext


def _snapshot(version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version=version,
        functions=[Function(name="foo", mangled="foo", return_type="void", params=[])],
    )


@pytest.fixture()
def snap_path(tmp_path: Path) -> Path:
    p = tmp_path / "lib.abi.json"
    p.write_text(snapshot_to_json(_snapshot()), encoding="utf-8")
    return p


class TestResolveDumpRequestWiring:
    """``resolve_dump_request`` now attaches a real ``ResolvedExecutionContext``
    (the pre-execution view) built from the same ``AnalysisPlan`` it already
    resolves for its ADR-063 Phase 4 pre-flight check — not a second
    resolution, and additive: nothing here reads the field back to change
    behaviour, so it must not affect any other field ``resolve_dump_request``
    already returns.
    """

    def test_resolved_request_carries_a_populated_context(self, snap_path: Path):
        resolved = resolve_dump_request(DumpRequest(input=InputSpec(path=snap_path)))

        assert isinstance(resolved.resolved_execution_context, ResolvedExecutionContext)
        assert resolved.resolved_execution_context.operation == "dump"

    @pytest.mark.parametrize("depth", [None, "binary"])
    def test_requested_depth_matches_the_request(self, snap_path: Path, depth):
        resolved = resolve_dump_request(
            DumpRequest(input=InputSpec(path=snap_path), depth=depth)
        )

        assert resolved.resolved_execution_context.requested_depth == depth
        assert resolved.resolved_execution_context.evidence.requested_depth == depth
        # Pre-execution: nothing has run yet, so the achieved-depth axis is
        # still unknown (see EvidenceView's own docstring).
        assert resolved.resolved_execution_context.evidence.effective_depth is None
        assert resolved.resolved_execution_context.evidence.depth_satisfied is None

    def test_no_evaluation_config_or_compile_contexts_resolved_here(
        self, snap_path: Path
    ):
        """Neither resolves at this seam, for the identical reason
        ``ResolvedComparePair.resolved_execution_context`` carries neither
        (see that field's own docstring)."""
        resolved = resolve_dump_request(DumpRequest(input=InputSpec(path=snap_path)))

        assert resolved.resolved_execution_context.evaluation_config is None
        assert dict(resolved.resolved_execution_context.compile_contexts) == {}

    def test_wiring_does_not_change_other_resolved_fields(self, snap_path: Path):
        request = DumpRequest(input=InputSpec(path=snap_path), depth="binary")
        resolved = resolve_dump_request(request)

        assert resolved.request is request
        assert resolved.requested_depth == "binary"
        assert resolved.collect_mode == "off"

    def test_two_independent_resolutions_of_the_same_request_still_compare_equal(
        self, snap_path: Path
    ):
        """``resolved_execution_context`` is excluded from the generated
        ``__eq__`` (``compare=False``), the same way ``artifact_plan`` already
        is — a fresh, distinct ``ResolvedExecutionContext`` instance must not
        make two structurally identical resolutions compare unequal."""
        request = DumpRequest(input=InputSpec(path=snap_path))

        a = resolve_dump_request(request)
        b = resolve_dump_request(request)

        assert a.resolved_execution_context is not b.resolved_execution_context
        assert a == b


class TestExecuteDumpRequestWithAssurance:
    """``execute_dump_request`` is the real post-execution ``with_assurance()``
    caller the plan's tracking row named as still open — ``dump`` has no
    comparison pair, so there is no real ``AnalysisAssurance`` to attach, but
    the one axis it shares with a comparison (whether the requested ``--depth``
    was reached) is a genuine post-execution fact, sourced from this same
    call's own ``effective_depth``.
    """

    def test_result_carries_a_completed_context(self, snap_path: Path):
        request = DumpRequest(input=InputSpec(path=snap_path))
        result = execute_dump_request(resolve_dump_request(request))

        assert isinstance(result.resolved_execution_context, ResolvedExecutionContext)
        assert result.resolved_execution_context.operation == "dump"
        assert (
            result.resolved_execution_context.evidence.effective_depth
            == result.effective_depth
        )

    def test_depth_satisfied_true_when_requested_depth_is_reached(
        self, snap_path: Path
    ):
        request = DumpRequest(input=InputSpec(path=snap_path), depth="binary")
        result = execute_dump_request(resolve_dump_request(request))

        assert result.resolved_execution_context.requested_depth == "binary"
        assert result.resolved_execution_context.evidence.depth_satisfied is True

    def test_depth_satisfied_none_when_no_depth_was_requested(self, snap_path: Path):
        request = DumpRequest(input=InputSpec(path=snap_path))
        result = execute_dump_request(resolve_dump_request(request))

        assert result.resolved_execution_context.requested_depth is None
        assert result.resolved_execution_context.evidence.depth_satisfied is None

    def test_context_is_none_when_resolved_carries_none(self, snap_path: Path):
        """A caller that hand-builds a ``ResolvedDumpRequest`` bypassing
        ``resolve_dump_request`` (``resolved_execution_context=None``, the
        dataclass default) gets ``None`` back too, rather than a crash."""
        from dataclasses import replace

        request = DumpRequest(input=InputSpec(path=snap_path))
        resolved = resolve_dump_request(request)
        bare = replace(resolved, resolved_execution_context=None)
        assert isinstance(bare, ResolvedDumpRequest)

        result = execute_dump_request(bare)

        assert result.resolved_execution_context is None

    def test_result_carries_the_resolved_compile_context(self, snap_path, monkeypatch):
        """Codex review, PR #1037: `with_assurance()` alone leaves
        `compile_contexts` empty even when the P0.3 fold produced a real
        `effective_compile_context` -- `DumpResult.resolved_execution_context`
        must carry it too, under the `"input"` label, so a consumer reading
        through the unified context sees the same toolchain
        `DumpResult.effective_compile_context` itself exposes."""
        from abicheck.compile_context import CompileContext
        from abicheck.workflows.artifact.execute import SideResolution

        request = DumpRequest(input=InputSpec(path=snap_path))
        resolved = resolve_dump_request(request)
        fake_ctx = CompileContext(gcc_option_tokens=("-std=c++20",))

        def _fake_resolve(*args, **kwargs):
            return SideResolution(
                snapshot=_snapshot(),
                effective_includes=(),
                effective_compile_context=fake_ctx,
            )

        monkeypatch.setattr(
            "abicheck.service_dump_pipeline._resolve_side_snapshot_impl",
            _fake_resolve,
        )

        result = execute_dump_request(resolved)

        assert result.effective_compile_context == fake_ctx
        assert dict(result.resolved_execution_context.compile_contexts) == {
            "input": fake_ctx
        }
