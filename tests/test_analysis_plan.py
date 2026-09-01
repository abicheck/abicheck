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

    def test_dump_config_sourced_target_scope_raises_planning_error(
        self, tmp_path: Path
    ):
        """ADR-063 Phase 4's second slice: no explicit ``build_targets`` on
        the request at all -- the scope comes only from an auto-discovered
        ``.abicheck.yml``'s ``build.targets:`` at ``sources``, mirroring
        ``embed_build_source``'s own ``cfg.targets`` fallback. Closes the
        dry-run/execution parity gap this module's own docstring and
        ``docs/contribute/known-gaps.md`` name as open: previously
        ``AnalysisPlanner`` could not see this value at all."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
            encoding="utf-8",
        )
        request = DumpRequest(
            input=InputSpec.of(path=None, sources=tmp_path, build_info=aquery),
            depth="build",
        )
        with pytest.raises(PlanningError) as exc_info:
            AnalysisPlanner.resolve(request)
        failure = exc_info.value.failures[0]
        assert "//:from_config" in failure.requested
        assert "auto-discovered .abicheck.yml" in failure.requested

    def test_explicit_build_config_wording_is_not_called_auto_discovered(
        self, tmp_path: Path
    ):
        """CodeRabbit review: the failure message must say "explicit build
        config", not "auto-discovered .abicheck.yml", when *build_config*
        names the file directly (``scan``'s own ``ScanRequest.build_config``,
        or dump/compare's own future seam) rather than being found by
        searching ``sources`` -- ``dump``/``compare`` have no request-level
        seam for this today, so this calls
        :func:`~abicheck.workflows.plan.bazel_target_scoping_failure`
        directly, the same free function ``scan``'s own call sites use."""
        from abicheck.workflows.plan import bazel_target_scoping_failure

        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        cfg = tmp_path / "explicit-config.yml"
        cfg.write_text(
            "build:\n  system: bazel\n  targets:\n    - //:from_explicit_config\n",
            encoding="utf-8",
        )
        failure = bazel_target_scoping_failure(
            "candidate", aquery, (), sources=None, build_config=cfg
        )
        assert failure is not None
        assert "//:from_explicit_config" in failure.requested
        assert "explicit build config" in failure.requested
        assert "auto-discovered .abicheck.yml" not in failure.requested

    def test_compare_config_sourced_target_scope_raises_planning_error(
        self, tmp_path: Path
    ):
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
            encoding="utf-8",
        )
        old = InputSpec.of(path=tmp_path / "old.so", sources=tmp_path)
        new = InputSpec.of(
            path=tmp_path / "new.so", sources=tmp_path, build_info=aquery
        )
        request = CompareRequest(old=old, new=new)
        with pytest.raises(PlanningError) as exc_info:
            AnalysisPlanner.resolve(request)
        assert "'new'" in exc_info.value.failures[0].requested

    def test_explicit_build_target_wins_over_config_wording(self, tmp_path: Path):
        """When both an explicit ``build_targets`` and an auto-discovered
        ``.abicheck.yml`` are present, the explicit value is what's actually
        checked/reported (``embed_build_source``'s own "CLI overrides win"
        precedence) -- the failure message must not carry the "auto-
        discovered" qualifier in this case."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
            encoding="utf-8",
        )
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:explicit"],
            ),
            depth="build",
        )
        with pytest.raises(PlanningError) as exc_info:
            AnalysisPlanner.resolve(request)
        failure = exc_info.value.failures[0]
        assert "//:explicit" in failure.requested
        assert "from_config" not in failure.requested
        assert "auto-discovered .abicheck.yml" not in failure.requested

    def test_config_sourced_scope_respects_depth_binary_exemption(self, tmp_path: Path):
        """The config-sourced fallback doesn't defeat the pre-existing
        depth=binary exemption -- that depth resolves to a collect_mode
        that never consults build_info/build_targets regardless of where
        the requested scope came from."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
            encoding="utf-8",
        )
        request = DumpRequest(
            input=InputSpec.of(path=None, sources=tmp_path, build_info=aquery),
            depth="binary",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_flow2_inputs_pack_with_bundled_config_is_unaffected_headerless(
        self, tmp_path: Path
    ):
        """Codex review, fresh evidence: auto-discovery must recognize
        *both* pack shapes (a classic ``BuildSourcePack`` and a Flow-2
        ``abicheck_inputs`` pack), not just the classic one --
        ``embed_build_source``'s own ``raw_sources`` is ``None`` for either
        shape (``src_is_pack``/``src_is_inputs``), so real execution's *main*
        L3/L4/L5 collection never discovers a config at *either* kind of pack
        directory. A first version of this check used ``is_pack_dir`` alone,
        which only recognizes the classic shape -- a Flow-2 pack whose
        bundled ``.abicheck.yml`` happens to declare ``build.targets:`` was
        therefore falsely treated as a source checkout and rejected, even
        though the real run's main collection never looks at that file. No
        headers here, so the L2 seed's own independent reading of a pack's
        bundled config (see the sibling test below) doesn't apply either --
        genuinely nothing consults it in this shape."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        pack = tmp_path / "inputs_pack"
        pack.mkdir()
        (pack / "manifest.json").write_text(
            json.dumps({"kind": "abicheck_inputs"}), encoding="utf-8"
        )
        (pack / ".abicheck.yml").write_text(
            "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
            encoding="utf-8",
        )
        request = DumpRequest(
            input=InputSpec.of(path=None, sources=pack, build_info=aquery),
            depth="build",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_pack_with_bundled_config_and_real_headers_raises_planning_error(
        self, tmp_path: Path
    ):
        """Codex review, fresh evidence beyond the headerless pack fix above:
        when real headers ARE present, ``buildsource.l2_seed._l2_seed_config``
        calls ``discover_build_config`` on the *original* pack path before
        pack recognition nulls ``raw_sources`` for the main collection --
        and that seed only runs when headers are present
        (``seed_includes_and_fold_compile_context``'s own ``... or not
        headers: return ...`` gate), independent of collect_mode. So a
        pack's bundled ``.abicheck.yml`` *is* genuinely consulted by the L2
        seed in this shape, even though the main L3/L4/L5 collection still
        ignores it -- unconditionally skipping pack recognition (the first
        version of this fix) would have missed this real, reachable path."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        pack = tmp_path / "inputs_pack"
        pack.mkdir()
        (pack / "manifest.json").write_text(
            json.dumps({"kind": "abicheck_inputs"}), encoding="utf-8"
        )
        (pack / ".abicheck.yml").write_text(
            "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
            encoding="utf-8",
        )
        header = tmp_path / "api.h"
        header.write_text("void f();\n", encoding="utf-8")
        request = DumpRequest(
            input=InputSpec.of(
                path=None, sources=pack, build_info=aquery, headers=[header]
            ),
            depth="build",
        )
        with pytest.raises(PlanningError) as exc_info:
            AnalysisPlanner.resolve(request)
        assert "//:from_config" in exc_info.value.failures[0].requested

    def test_no_abicheck_yml_present_is_unaffected(self, tmp_path: Path):
        """No config file at ``sources`` at all -- the auto-discovery
        fallback must not raise/crash and must leave an otherwise-valid,
        unscoped request alone (identical to the pre-existing
        ``test_no_build_targets_is_unaffected`` case, just spelled with a
        ``sources`` tree that genuinely has no ``.abicheck.yml``)."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(path=None, sources=tmp_path, build_info=aquery),
            depth="build",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_malformed_abicheck_yml_is_unaffected_by_this_check(self, tmp_path: Path):
        """A malformed ``.abicheck.yml`` is not this check's problem to
        diagnose -- ``embed_build_source`` already raises a correctly-typed
        ``ValidationError`` for it at real-execution time. Duplicating that
        diagnosis pre-flight would be a second, independently-worded error
        for a case that already fails loudly downstream, so this check
        degrades to "no config found" instead of raising a confusing YAML
        parse error from inside a Bazel-scoping check."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        (tmp_path / ".abicheck.yml").write_text(
            "build: [this is not a mapping\n", encoding="utf-8"
        )
        request = DumpRequest(
            input=InputSpec.of(path=None, sources=tmp_path, build_info=aquery),
            depth="build",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_nonexistent_build_info_path_is_unaffected(self, tmp_path: Path):
        """A ``build_info`` path that does not exist on disk cannot be
        sniffed as a Bazel jsonproto -- this check must not raise (or crash
        trying to read it), leaving that failure to whatever later step
        actually needs the file to exist."""
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=tmp_path / "does-not-exist.json",
                build_targets=["//:lib"],
            ),
            depth="build",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_depth_binary_is_unaffected(self, tmp_path: Path):
        """Codex review, fresh evidence: ``depth="binary"`` resolves to
        collect_mode ``"off"``, and ``embed_build_source`` no-ops before
        ever calling ``collect_inline_pack`` at that mode -- ``build_info``/
        ``build_targets`` are never actually consulted, so rejecting them
        here would be a false positive against an otherwise-valid request
        (e.g. one that also carries ``--sources``/``--build-info`` for a
        *different* depth the caller might switch to later)."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:lib"],
            ),
            depth="binary",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_depth_binary_case_insensitive_is_unaffected(self, tmp_path: Path):
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:lib"],
            ),
            depth="BINARY",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_other_depths_still_rejected_alongside_binary(self, tmp_path: Path):
        """A depth other than ``binary``/headerless ``headers`` still
        triggers the check -- pins that the fix above is scoped to the
        depths that actually no-op the collection, not a blanket exemption.
        ``headers`` is excluded here (see the pair of tests below): unlike
        ``binary``, it doesn't clear headers, so whether it's exempt depends
        on whether real headers are present."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        for depth in ("build", "source"):
            request = DumpRequest(
                input=InputSpec.of(
                    path=None,
                    sources=tmp_path,
                    build_info=aquery,
                    build_targets=["//:lib"],
                ),
                depth=depth,
            )
            with pytest.raises(PlanningError):
                AnalysisPlanner.resolve(request)

    def test_headerless_depth_headers_is_exempt(self, tmp_path: Path):
        """Codex review, fresh evidence: ``depth="headers"`` resolves to
        collect mode ``"off"`` too (not just ``"binary"``), and with no real
        headers, neither ``embed_build_source`` (collect mode ``"off"``) nor
        the L2 seed (nothing to seed) would ever consult ``build_info`` --
        so this must be exempt, unlike the always-rejected depths above."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:lib"],
            ),
            depth="headers",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_depth_headers_with_real_headers_is_still_rejected(self, tmp_path: Path):
        """The converse of the test above: real headers at ``depth="headers"``
        mean the L2 seed's own independent header-seeding pass still runs
        regardless of the "off" collect mode, so this stays rejected --
        unlike ``depth="binary"``, which clears headers before this point."""
        header = tmp_path / "lib.h"
        header.write_text("void f();\n", encoding="utf-8")
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                headers=[header],
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:lib"],
            ),
            depth="headers",
        )
        with pytest.raises(PlanningError):
            AnalysisPlanner.resolve(request)

    def test_resolved_collect_mode_override_defeats_the_binary_exemption(
        self, tmp_path: Path
    ):
        """Codex review, fresh evidence: raw ``depth="binary"`` alone is not
        the whole story. ``DumpRequest.resolved_collect_mode``, when set,
        overrides what ``depth`` alone would resolve to, and
        ``resolve_dump_request_evidence`` honors that override -- so a
        request with ``depth="binary"`` but ``resolved_collect_mode="build"``
        still runs ``collect_inline_pack`` for real. Exempting it here on the
        strength of the raw depth alone would let this request reach
        ``resolve``, then fail deep inside ``collect_inline_pack`` as a
        flattened ``SnapshotError`` instead of the promised ``PlanningError``."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:lib"],
            ),
            depth="binary",
            resolved_collect_mode="build",
        )
        with pytest.raises(PlanningError):
            AnalysisPlanner.resolve(request)

    def test_resolved_collect_mode_off_override_is_exempt_even_at_other_depths(
        self, tmp_path: Path
    ):
        """The converse: an explicit ``resolved_collect_mode="off"`` override
        means ``build_info`` is never consulted regardless of what ``depth``
        alone would otherwise resolve to (the raw-depth-only check would
        wrongly reject this, since ``depth="build"`` alone is in the
        "still rejected" set above)."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:lib"],
            ),
            depth="build",
            resolved_collect_mode="off",
        )
        plan = AnalysisPlanner.resolve(request)
        assert isinstance(plan, AnalysisPlan)

    def test_resolved_collect_mode_off_does_not_exempt_real_headers(
        self, tmp_path: Path
    ):
        """Codex review, fresh evidence: even a genuine ``"off"`` collect mode
        (here via an explicit override; ``depth="binary"`` would clear
        headers to empty and stay exempt) is not enough on its own when real
        headers are present -- the L2 seed's own independent header-seeding
        pass (``_seeded_includes_and_compile_context``/``collect_inline_pack``)
        still consumes ``build_info`` regardless of collect mode, mirroring
        the identical gap already fixed for ``scan_bazel_scoping_failure``.
        Exempting this on the strength of collect mode alone would let
        resolution/``--dry-run`` succeed, then fail later inside
        ``collect_inline_pack`` as a flattened ``ValidationError`` instead of
        the promised pre-flight ``PlanningError``."""
        header = tmp_path / "lib.h"
        header.write_text("void f();\n", encoding="utf-8")
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                headers=[header],
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:lib"],
            ),
            depth="headers",
            resolved_collect_mode="off",
        )
        with pytest.raises(PlanningError):
            AnalysisPlanner.resolve(request)

    def test_resolved_collect_mode_off_with_no_headers_stays_exempt(
        self, tmp_path: Path
    ):
        """Sibling of the test above: with no real headers, ``"off"`` still
        exempts -- pins that the headers check above doesn't over-reject."""
        aquery = _write(tmp_path / "aquery.json", _EMPTY_AQUERY)
        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                build_info=aquery,
                build_targets=["//:lib"],
            ),
            depth="headers",
            resolved_collect_mode="off",
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

    def test_side_compile_context_left_at_auto_falls_back_to_request_frontend(
        self, tmp_path: Path
    ):
        """A side's own ``compile`` context left at the default ``"auto"``
        frontend carries no per-side override -- the plan's ``frontend``
        falls back to the request-level value, exactly like a side with no
        ``compile`` context at all."""
        from abicheck.compile_context import CompileContext

        request = DumpRequest(
            input=InputSpec.of(
                path=None,
                sources=tmp_path,
                compile=CompileContext(gcc_path="/opt/my-gcc/bin/gcc"),
            ),
            frontend="clang",
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


class TestPlanningErrorCliTranslation:
    def test_native_compare_cli_maps_planning_error_to_usage_error(self, monkeypatch):
        """The native ``compare`` CLI's own resolution boundary
        (``cli_resolve._resolve_compare_snapshots``) must translate a
        ``PlanningError`` from ``service.resolve_compare_request`` into a
        ``click.UsageError`` (exit 64, AGENTS.md's documented usage-error
        contract) -- not let it propagate as a raw, untranslated exception,
        and not the operational-failure ``click.ClickException`` (exit 1)
        the same boundary maps a ``SnapshotError`` to. No real Bazel input
        is needed here: the boundary's own translation is what's under
        test, so ``service.resolve_compare_request`` is monkeypatched to
        raise directly."""
        import click

        from abicheck import service
        from abicheck.cli_resolve import _resolve_compare_snapshots

        def _raise(*args, **kwargs):
            raise PlanningError((PlanningFailure("build_targets=['//:lib']", "boom"),))

        monkeypatch.setattr(service, "resolve_compare_request", _raise)
        with pytest.raises(click.UsageError, match="boom"):
            _resolve_compare_snapshots(
                old_input=Path("old.so"),
                new_input=Path("new.so"),
                old_fmt="elf",
                new_fmt="elf",
                old_h=[],
                new_h=[],
                old_inc=[],
                new_inc=[],
                old_version="",
                new_version="",
                lang="c++",
                pdb_path=None,
                old_pdb_path=None,
                new_pdb_path=None,
                dwarf_only=False,
                debug_format=None,
                follow_deps=False,
                search_paths=(),
                ld_library_path="",
            )
