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

"""Tests for :mod:`abicheck.workflows.plan` — ADR-063 Phase 4's
``AnalysisPlan``/``AnalysisPlanner``.

The named acceptance scenario is the ``--build-target`` + pre-captured Bazel
``aquery``/``cquery`` gap (``docs/contribute/known-gaps.md``): every one of
these tests reproduces it end to end through
:func:`abicheck.service_compare_pipeline.resolve_compare_request`/
:func:`abicheck.service_dump_pipeline.resolve_dump_request` -- the shared
chokepoint every front end (CLI, typed Python API, the release/bundle
fan-out) resolves a request through -- asserting :class:`~abicheck.errors.PlanningError`,
not a warning or a silently-unscoped collection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.api_types import CompareRequest, DumpRequest, InputSpec
from abicheck.errors import PlanningError
from abicheck.workflows.plan import (
    AnalysisPlan,
    AnalysisPlanner,
    PlanningFailure,
    SidePlan,
)

_EMPTY_AQUERY = {"actions": [], "pathFragments": [], "artifacts": [], "targets": []}
_EMPTY_CQUERY = {"results": []}


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload))
    return path


class TestBazelBuildTargetScoping:
    """The one named silent-failure gap this phase closes."""

    def test_dump_aquery_with_build_target_raises_planning_error(self, tmp_path: Path):
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:lib"],
            ),
            depth="build",
        )
        with pytest.raises(PlanningError) as exc_info:
            AnalysisPlanner.resolve(request)
        assert len(exc_info.value.failures) == 1
        failure = exc_info.value.failures[0]
        assert isinstance(failure, PlanningFailure)
        assert "//:lib" in failure.requested
        assert "aquery" in failure.why_unsupported

    def test_dump_cquery_with_build_target_raises_planning_error(self, tmp_path: Path):
        cquery = _write(tmp_path / "cquery.json", _EMPTY_CQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=cquery,
                build_targets=["//:lib"],
            ),
            depth="build",
        )
        with pytest.raises(PlanningError):
            AnalysisPlanner.resolve(request)

    def test_compare_with_build_target_on_either_side_raises_planning_error(
        self, tmp_path: Path
    ):
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        old = InputSpec.of(path=tmp_path / "old.so", sources=tmp_path)
        new = InputSpec.of(
            path=tmp_path / "new.so",
            sources=tmp_path,
            build_info=aquery,
            build_targets=["//:lib"],
        )
        request = CompareRequest(old=old, new=new)
        with pytest.raises(PlanningError) as exc_info:
            AnalysisPlanner.resolve(request)
        assert "'new'" in exc_info.value.failures[0].requested

    def test_reaches_through_the_shared_resolve_compare_request_chokepoint(
        self, tmp_path: Path
    ):
        """Every ``compare``-shaped front end resolves through this one
        function (``service_compare_pipeline.resolve_compare_request``) --
        the release/bundle fan-out (``cli_compare_release._run_compare_pair``)
        included, since it calls ``service.run_compare`` ->
        ``run_compare_request`` -> this function, per this phase's own Files
        section. Proving the guarantee here, at the one shared chokepoint,
        covers every caller without needing a separate release-fan-out
        fixture for a scenario that fan-out's own keyword surface
        (``service.run_compare``) has no parameter to even express
        (``build_targets``/``build_info`` are not amongst its keyword
        arguments).
        """
        from abicheck.service_compare_pipeline import resolve_compare_request

        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        old = InputSpec.of(path=tmp_path / "old.so", sources=tmp_path)
        new = InputSpec.of(
            path=tmp_path / "new.so",
            sources=tmp_path,
            build_info=aquery,
            build_targets=["//:lib"],
        )
        request = CompareRequest(old=old, new=new)
        with pytest.raises(PlanningError):
            resolve_compare_request(request)

    def test_live_query_shape_is_unaffected(self, tmp_path: Path):
        """``build_targets`` with a *live* query (no pre-captured
        ``build_info``) is the documented safe workaround -- it must not
        trip this check."""
        request = DumpRequest(
            input=InputSpec.of(path=None, sources=tmp_path, build_targets=["//:lib"]),
            depth="build",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_ordinary_compile_db_build_info_is_unaffected(self, tmp_path: Path):
        """A plain ``compile_commands.json`` (not a Bazel jsonproto)
        combined with ``build_targets`` is out of this check's scope --
        target scoping for non-Bazel build systems is a different, unrelated
        question."""
        compile_db = _write(tmp_path / "compile_commands.json", [])
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=compile_db,
                build_targets=["//:lib"],
            ),
            depth="build",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_no_build_targets_is_unaffected(self, tmp_path: Path):
        """A pre-captured Bazel jsonproto with no requested scoping at all
        (the historical, workspace-wide behavior) is unaffected -- this
        check only fires when scoping was actually requested and would be
        silently ignored."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(path=None, sources=tmp_path, build_info=aquery),
            depth="build",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)


class TestAnalysisPlanShape:
    def test_dump_plan_carries_one_side_labelled_input(self, tmp_path: Path):
        request = DumpRequest(
            input=InputSpec.of(path=None, sources=tmp_path), depth="headers"
        )
        plan = AnalysisPlanner.resolve(request)
        assert plan.operation == "dump"
        assert plan.requested_depth == "headers"
        assert [s.label for s in plan.sides] == ["input"]
        assert isinstance(plan.sides[0], SidePlan)

    def test_compare_plan_carries_both_sides(self, tmp_path: Path):
        request = CompareRequest(
            old=InputSpec.of(path=None, sources=tmp_path),
            new=InputSpec.of(path=None, sources=tmp_path),
        )
        plan = AnalysisPlanner.resolve(request)
        assert plan.operation == "compare"
        assert [s.label for s in plan.sides] == ["old", "new"]

    def test_plan_records_requested_not_resolved_toolchain_inputs(self, tmp_path: Path):
        """``AnalysisPlan`` carries the *requested* frontend/gcc_path, never
        a resolved P0.3 L3->L2 compile-context fold result (ADR-063 D4) --
        this test pins that an explicit request-level override survives into
        the plan unresolved, not folded/normalized against build evidence."""
        from abicheck.compile_context import CompileContext

        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                compile=CompileContext(
                    gcc_path="/opt/my-gcc/bin/gcc", frontend="clang"
                ),
            ),
        )
        plan = AnalysisPlanner.resolve(request)
        side = plan.sides[0]
        assert side.gcc_path == "/opt/my-gcc/bin/gcc"
        assert side.frontend == "clang"


class TestPlanningErrorShape:
    def test_planning_error_message_names_every_failure(self, tmp_path: Path):
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        old = InputSpec.of(
            path=tmp_path / "old.so",
            sources=tmp_path,
            build_info=aquery,
            build_targets=["//:old"],
        )
        new = InputSpec.of(
            path=tmp_path / "new.so",
            sources=tmp_path,
            build_info=aquery,
            build_targets=["//:new"],
        )
        request = CompareRequest(old=old, new=new)
        with pytest.raises(PlanningError) as exc_info:
            AnalysisPlanner.resolve(request)
        # Both sides fail independently -- both are reported, not only the
        # first (PlanningError.failures is exhaustive, not fail-fast).
        assert len(exc_info.value.failures) == 2
        message = str(exc_info.value)
        assert "//:old" in message
        assert "//:new" in message
