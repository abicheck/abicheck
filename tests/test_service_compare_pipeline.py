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

"""Unit tests for ``service_compare_pipeline``'s ``resolve_sides_sequentially``.

Split out of tests/test_service_unit.py (which was already past the
architecture gate's 1200-line test-file cap) rather than grown further —
one class, moved verbatim, matching the file's own debt-ledger target
("tests/unit-or-contract ownership matching production migration").
"""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot
from abicheck.service import CompareRequest, InputSpec


class TestResolveSidesSequentially:
    """ADR-050 D6 / G32 Phase E, generalised by ADR-055 D1.

    A manifest-driven dump sizes its per-TU worker pool from a live
    ``MemAvailable`` reading, so two starting concurrently size two full pools
    off the same reading and jointly overcommit. That guard used to be
    implicit — the native ``compare`` CLI simply resolved sequentially, and
    ``run_compare_request`` was documented as unable to reach a manifest at
    all. ``InputSpec.dump_manifest`` made that documentation stale: the typed
    path could reach a manifest *and* resolved concurrently. Now that both
    front ends share one resolution, the guard is explicit and lives with it.
    """

    def _request(self, tmp_path, *, old_manifest=None, new_manifest=None):
        return CompareRequest(
            old=InputSpec(path=tmp_path / "old.so", dump_manifest=old_manifest),
            new=InputSpec(path=tmp_path / "new.so", dump_manifest=new_manifest),
        )

    def test_plain_pair_may_resolve_concurrently(self, tmp_path, monkeypatch):
        from abicheck.service import resolve_sides_sequentially

        monkeypatch.delenv("ABICHECK_PARALLEL_EXTRACTION", raising=False)
        assert resolve_sides_sequentially(self._request(tmp_path)) is False

    @pytest.mark.parametrize("side", ["old", "new"])
    def test_a_dump_manifest_on_either_side_forces_sequential(
        self, tmp_path, monkeypatch, side
    ):
        from types import SimpleNamespace

        from abicheck.service import resolve_sides_sequentially

        monkeypatch.delenv("ABICHECK_PARALLEL_EXTRACTION", raising=False)
        manifest = SimpleNamespace(translation_units=[])
        request = self._request(tmp_path, **{f"{side}_manifest": manifest})
        assert resolve_sides_sequentially(request) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "NO", " 0 "])
    def test_env_opt_out_forces_sequential(self, tmp_path, monkeypatch, value):
        from abicheck.service import resolve_sides_sequentially

        monkeypatch.setenv("ABICHECK_PARALLEL_EXTRACTION", value)
        assert resolve_sides_sequentially(self._request(tmp_path)) is True

    def test_manifest_request_really_resolves_one_side_at_a_time(
        self, tmp_path, monkeypatch
    ):
        """The behavioural half: not just the predicate, but the resolution.

        Without the guard this is exactly the double-pool-sizing case — two
        manifest dumps in a ``ThreadPoolExecutor``, overlapping in time.
        """
        import time
        from types import SimpleNamespace

        import abicheck.workflows.input_resolution as _input_resolution
        from abicheck.service import resolve_compare_request

        monkeypatch.delenv("ABICHECK_PARALLEL_EXTRACTION", raising=False)
        spans: list[tuple[str, float, float]] = []

        def _fake_resolve(path, headers, includes, version, lang, **kwargs):
            start = time.monotonic()
            time.sleep(0.05)
            spans.append((version, start, time.monotonic()))
            return AbiSnapshot(library="libtest", version=version)

        monkeypatch.setattr(_input_resolution, "resolve_input", _fake_resolve)
        old_p = tmp_path / "old.so"
        new_p = tmp_path / "new.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        manifest = SimpleNamespace(translation_units=[])
        resolve_compare_request(
            CompareRequest(
                old=InputSpec(path=old_p, version="old", dump_manifest=manifest),
                new=InputSpec(path=new_p, version="new", dump_manifest=manifest),
            )
        )
        assert len(spans) == 2
        (_old_v, _old_start, old_end), (_new_v, new_start, _new_end) = spans
        assert new_start >= old_end


class TestResolvedExecutionContextWiring:
    """One Semantic Pipeline PR 1, sub-phase 4B: :func:`resolve_compare_request`
    now attaches a real :class:`~abicheck.workflows.resolved_execution_context.
    ResolvedExecutionContext` built from the same :class:`~abicheck.workflows.
    plan.AnalysisPlan` the function already resolves for its pre-flight
    check, rather than discarding it. This is the "real, callable projection
    function with a real call site" requirement -- not a type nothing ever
    constructs outside its own tests -- landed additively: nothing here reads
    the new field back to change behaviour, so it must never affect
    ``old``/``new``/``old_fmt``/``new_fmt``/``old_evidence``/``new_evidence``.
    """

    def _request(self, tmp_path, *, depth=None):
        old_p = tmp_path / "old.so"
        new_p = tmp_path / "new.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        return CompareRequest(
            old=InputSpec(path=old_p, version="old"),
            new=InputSpec(path=new_p, version="new"),
            depth=depth,
        )

    def _resolve(self, request, monkeypatch):
        import abicheck.workflows.input_resolution as _input_resolution
        from abicheck.service import resolve_compare_request

        def _fake_resolve(path, headers, includes, version, lang, **kwargs):
            return AbiSnapshot(library="libtest", version=version)

        monkeypatch.setattr(_input_resolution, "resolve_input", _fake_resolve)
        return resolve_compare_request(request)

    def test_pair_carries_a_populated_resolved_execution_context(
        self, tmp_path, monkeypatch
    ):
        from abicheck.workflows.resolved_execution_context import (
            ResolvedExecutionContext,
        )

        pair = self._resolve(self._request(tmp_path), monkeypatch)

        assert isinstance(pair.resolved_execution_context, ResolvedExecutionContext)
        assert pair.resolved_execution_context.operation == "compare"

    @pytest.mark.parametrize("depth", [None, "binary"])
    def test_requested_depth_matches_the_request(self, tmp_path, monkeypatch, depth):
        # Only depths the fake binary-only `resolve_input` stand-in can
        # actually satisfy -- `enforce_requested_depth`'s floor check (see
        # its own docstring) rejects `headers`/`build`/`source` here for the
        # identical reason it would reject them against a real ELF-only
        # snapshot; that check is orthogonal to what this test verifies.
        pair = self._resolve(self._request(tmp_path, depth=depth), monkeypatch)

        assert pair.resolved_execution_context.requested_depth == depth
        assert pair.resolved_execution_context.evidence.requested_depth == depth

    def test_no_evaluation_config_resolved_here(self, tmp_path, monkeypatch):
        """This seam resolves before ADR-049 D7 evaluation config exists for
        the native CLI (see the field's own docstring on ``ResolvedComparePair``)
        -- asserted explicitly so a future change that starts silently
        fabricating it is caught rather than passing by accident."""
        pair = self._resolve(self._request(tmp_path), monkeypatch)

        assert pair.resolved_execution_context.evaluation_config is None

    def test_compile_contexts_empty_for_a_binary_only_fake_snapshot(
        self, tmp_path, monkeypatch
    ):
        """``_resolve`` fakes ``resolve_input`` with a bare, non-header-scoped
        ``AbiSnapshot`` (``from_headers`` defaults ``False``) -- ``side_
        effective_compile_context``'s own gate (ADR-063 Track 3) means no
        entry is recorded for either side. See
        ``test_compile_contexts_carries_each_sides_resolved_context`` below
        for the positive case."""
        pair = self._resolve(self._request(tmp_path), monkeypatch)

        assert dict(pair.resolved_execution_context.compile_contexts) == {}

    def test_compile_contexts_carries_each_sides_resolved_context(
        self, tmp_path, monkeypatch
    ):
        """ADR-063 Track 3 (One Semantic Pipeline plan, sub-phase 4B):
        ``resolve_compare_request`` now switched from ``resolve_side_snapshot``
        to ``_resolve_side_snapshot_impl`` so it can recover each side's own
        ``SideResolution.effective_compile_context`` and thread it into
        ``ResolvedExecutionContext.compile_contexts`` -- mirroring
        ``execute_dump_request``'s identical, already-landed dump-path fold
        (``side_effective_compile_context``, shared by both). Faking
        ``_resolve_side_snapshot_impl`` directly (rather than
        ``resolve_input``, as ``_resolve`` above does) is what lets this test
        supply a header-scoped snapshot with a real ``effective_compile_context``
        -- the fake in ``_resolve`` deliberately can't (see the test above)."""
        from abicheck import service_compare_pipeline
        from abicheck.compile_context import CompileContext
        from abicheck.workflows.artifact.execute import SideResolution

        old_ctx = CompileContext(gcc_option_tokens=("-std=c++20",))
        new_ctx = CompileContext(gcc_option_tokens=("-std=c++17",))

        def _fake_impl(side, evidence, **kwargs):
            ctx = old_ctx if side.version == "old" else new_ctx
            return SideResolution(
                snapshot=AbiSnapshot(
                    library="libtest", version=side.version, from_headers=True
                ),
                effective_includes=(),
                effective_compile_context=ctx,
            )

        monkeypatch.setattr(
            service_compare_pipeline, "_resolve_side_snapshot_impl", _fake_impl
        )
        pair = service_compare_pipeline.resolve_compare_request(self._request(tmp_path))

        assert dict(pair.resolved_execution_context.compile_contexts) == {
            "old": old_ctx,
            "new": new_ctx,
        }

    def test_compile_contexts_excludes_a_manifest_driven_side(
        self, tmp_path, monkeypatch
    ):
        """A manifest-driven side's real header-AST parse runs under its own
        manifest-authoritative ``frontend_context``, not the request-derived
        context this fold resolved (``side_effective_compile_context``'s own
        docstring) -- recording it here would risk stating a wrong
        toolchain, so that side is excluded even though a compile context
        was resolved for it."""
        from abicheck import service_compare_pipeline
        from abicheck.compile_context import CompileContext
        from abicheck.workflows.artifact.execute import SideResolution

        old_ctx = CompileContext(gcc_option_tokens=("-std=c++20",))
        new_ctx = CompileContext(gcc_option_tokens=("-std=c++17",))

        def _fake_impl(side, evidence, **kwargs):
            ctx = old_ctx if side.version == "old" else new_ctx
            return SideResolution(
                snapshot=AbiSnapshot(
                    library="libtest", version=side.version, from_headers=True
                ),
                effective_includes=(),
                effective_compile_context=ctx,
            )

        monkeypatch.setattr(
            service_compare_pipeline, "_resolve_side_snapshot_impl", _fake_impl
        )
        from dataclasses import replace

        from abicheck.dump_manifest import DumpManifest

        request = self._request(tmp_path)
        request = replace(
            request,
            old=replace(request.old, dump_manifest=DumpManifest(base_dir=tmp_path)),
        )
        pair = service_compare_pipeline.resolve_compare_request(request)

        assert dict(pair.resolved_execution_context.compile_contexts) == {
            "new": new_ctx
        }

    def test_wiring_does_not_change_the_resolved_snapshots_or_formats(
        self, tmp_path, monkeypatch
    ):
        """Behaviour-preservation: attaching the new field changes nothing
        about what the pair already carried."""
        pair = self._resolve(self._request(tmp_path), monkeypatch)

        assert pair.old.library == "libtest"
        assert pair.new.library == "libtest"
        assert pair.old.version == "old"
        assert pair.new.version == "new"

    def test_compile_context_included_for_a_followed_linker_script(
        self, tmp_path, monkeypatch
    ):
        """Codex review, PR #1047, fresh evidence: a GNU ld linker script
        side resolves ``old_fmt``/``new_fmt`` to ``None`` on its own text-file
        path (not ELF/PE/Mach-O magic bytes), but the real header-AST parse
        runs against the real target the script resolves to underneath --
        the un-followed raw format alone wrongly excluded a genuinely-
        participating compile context. ``side_effective_compile_context``
        now detects the format of the *followed* target itself (mirrors the
        identical dump-path regression,
        ``test_dump_resolved_execution_context.py::
        test_compile_context_included_for_a_followed_linker_script``)."""
        from abicheck import service_compare_pipeline
        from abicheck.compile_context import CompileContext
        from abicheck.workflows.artifact.execute import SideResolution

        target = tmp_path / "libfoo.so.1"
        target.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_p = tmp_path / "old.so"
        old_p.write_text("INPUT(libfoo.so.1)\n", encoding="utf-8")
        new_p = tmp_path / "new.so"
        new_p.write_bytes(b"\x7fELF" + b"\x00" * 200)

        old_ctx = CompileContext(gcc_option_tokens=("-std=c++20",))

        def _fake_impl(side, evidence, **kwargs):
            return SideResolution(
                snapshot=AbiSnapshot(
                    library="libtest", version=side.version, from_headers=True
                ),
                effective_includes=(),
                effective_compile_context=old_ctx,
            )

        monkeypatch.setattr(
            service_compare_pipeline, "_resolve_side_snapshot_impl", _fake_impl
        )
        request = CompareRequest(
            old=InputSpec(path=old_p, version="old"),
            new=InputSpec(path=new_p, version="new"),
        )
        pair = service_compare_pipeline.resolve_compare_request(request)

        assert pair.old_fmt is None  # the raw linker-script path itself
        assert dict(pair.resolved_execution_context.compile_contexts) == {
            "old": old_ctx,
            "new": old_ctx,
        }


class TestDeadlineBoundaryCheck:
    """ADR-068 §3 #19 (Codex review, fresh evidence, PR #1178).

    A stored-snapshot-only pair needs no subprocess/extraction work at all,
    so nothing inside ``resolve_compare_request``/``classify_compare_pair``
    would otherwise ever call ``deadline.check()`` -- an already-expired
    ``budget_s`` (0, or exhausted by an earlier phase) silently completed
    instead of raising ``DeadlineExceeded``. Mirrors the identical fix on
    the native ``compare`` CLI, exercised at the CLI layer by
    ``tests/test_compare_budget_boundary.py``.
    """

    def _request(self, tmp_path, *, budget_s):
        old_p = tmp_path / "old.abi.json"
        new_p = tmp_path / "new.abi.json"
        from abicheck.serialization import snapshot_to_json

        old_p.write_text(
            snapshot_to_json(AbiSnapshot(library="libtest.so", version="1.0")),
            encoding="utf-8",
        )
        new_p.write_text(
            snapshot_to_json(AbiSnapshot(library="libtest.so", version="2.0")),
            encoding="utf-8",
        )
        return CompareRequest(
            old=InputSpec.of(str(old_p)),
            new=InputSpec.of(str(new_p)),
            budget_s=budget_s,
        )

    def test_zero_budget_raises_deadline_exceeded_for_typed_callers(
        self, tmp_path
    ) -> None:
        from abicheck import deadline
        from abicheck.service import run_compare_request

        request = self._request(tmp_path, budget_s=0)
        with pytest.raises(deadline.DeadlineExceeded):
            run_compare_request(request)

    def test_positive_budget_completes_normally(self, tmp_path) -> None:
        from abicheck.service import run_compare_request

        request = self._request(tmp_path, budget_s=300)
        result = run_compare_request(request)
        assert result.diff is not None


class TestRequestEnvMatrixReachesClassification:
    """``CompareRequest.env_matrix`` is the one environment-matrix channel.

    The former ``env_matrix_path`` alternative (a path the request carried
    and ``effective_env_matrix()`` loaded lazily, plus the
    ``ResolvedComparePair.resolved_env_matrix`` sentinel that existed only
    to tell "resolved to no matrix" from "never resolved") is gone: the
    field holds the already-resolved :class:`EnvironmentMatrix` that
    ``.abicheck.yml``'s ``deployment:`` key resolves to, so there is no load
    to order against side acquisition and nothing for a caller-built pair to
    drop. What still has to hold -- and is what these tests assert, through
    the real classification path rather than an intermediate value -- is that
    a declared runtime floor actually reaches ``compare_snapshots`` and flags
    the violation, whether the pair was built by a caller directly or
    resolved through ``resolve_compare_request``.
    """

    def _snapshot(self, *, version: str, glibc: str | None) -> AbiSnapshot:
        from abicheck.model.elf_facts import ElfMetadata

        elf = ElfMetadata(
            versions_required=(
                {"libc.so.6": [f"GLIBC_{glibc}"]} if glibc is not None else {}
            )
        )
        return AbiSnapshot(library="libtest.so", version=version, elf=elf)

    def _matrix(self):
        from abicheck.environment_matrix import EnvironmentMatrix

        return EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})

    def _request(self, tmp_path, old: AbiSnapshot, new: AbiSnapshot, **kwargs):
        """A real ``CompareRequest`` pointing at real snapshot files on disk
        -- ``classify_compare_pair`` reads each side's own path for
        library-identity metadata even when the snapshots themselves are
        supplied out of band via the caller-built pair below."""
        from abicheck.serialization import snapshot_to_json

        old_p = tmp_path / "old.abi.json"
        new_p = tmp_path / "new.abi.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        return CompareRequest(
            old=InputSpec.of(str(old_p)), new=InputSpec.of(str(new_p)), **kwargs
        )

    def _pair(self, old: AbiSnapshot, new: AbiSnapshot):
        """A caller-built ``ResolvedComparePair`` -- direct construction,
        never through ``resolve_compare_request``."""
        from abicheck.service_compare_evidence import SideEvidence
        from abicheck.service_compare_pipeline import ResolvedComparePair

        evidence = SideEvidence(
            headers=[], compile=None, collect_mode="off", dump_manifest=None
        )
        return ResolvedComparePair(
            old=old,
            new=new,
            old_fmt="elf",
            new_fmt="elf",
            old_evidence=evidence,
            new_evidence=evidence,
        )

    def test_caller_built_pair_honors_the_requests_env_matrix(self, tmp_path) -> None:
        """A caller-built pair must still classify against the request's own
        declared floor -- the resulting comparison actually flags the
        runtime-floor violation as ``BREAKING``, proving the matrix reaches
        real classification rather than a non-``None`` intermediate value."""
        from abicheck.model.change_catalog.registry import Verdict
        from abicheck.service_compare_pipeline import classify_compare_pair

        old = self._snapshot(version="1.0", glibc="2.28")
        # The new binary requires a strictly newer GLIBC symbol version than
        # the declared 2.28 floor allows -- a genuine platform-baseline-floor
        # violation (`check_platform_baseline_floor`), unconditionally
        # promoted to BREAKING regardless of any old/new delta.
        new = self._snapshot(version="1.1", glibc="2.34")
        request = self._request(tmp_path, old, new, env_matrix=self._matrix())
        pair = self._pair(old, new)

        result = classify_compare_pair(request, pair)

        assert result.diff is not None
        assert result.diff.verdict == Verdict.BREAKING
        kinds = {c.kind for c in result.diff.changes}
        assert "platform_baseline_floor_raised" in kinds

    def test_no_declared_matrix_leaves_the_same_delta_unflagged(self, tmp_path) -> None:
        """No declared floor at all: the same GLIBC delta must NOT be flagged
        as a runtime-floor violation -- the vacuity guard for the test above."""
        from abicheck.model.change_catalog.registry import Verdict
        from abicheck.service_compare_pipeline import classify_compare_pair

        old = self._snapshot(version="1.0", glibc="2.28")
        new = self._snapshot(version="1.1", glibc="2.34")
        request = self._request(tmp_path, old, new)
        pair = self._pair(old, new)

        result = classify_compare_pair(request, pair)

        assert result.diff is not None
        assert result.diff.verdict != Verdict.BREAKING
        kinds = {c.kind for c in result.diff.changes}
        assert "platform_baseline_floor_raised" not in kinds

    def test_resolve_compare_request_path_flags_the_same_violation(
        self, tmp_path
    ) -> None:
        """The normal two-phase path (``resolve_compare_request`` then
        ``classify_compare_pair``) flags the identical violation the
        caller-built path does."""
        from abicheck.model.change_catalog.registry import Verdict
        from abicheck.service_compare_pipeline import (
            classify_compare_pair,
            resolve_compare_request,
        )

        old = self._snapshot(version="1.0", glibc="2.28")
        new = self._snapshot(version="1.1", glibc="2.34")
        request = self._request(tmp_path, old, new, env_matrix=self._matrix())

        pair = resolve_compare_request(request)
        result = classify_compare_pair(request, pair)
        assert result.diff is not None
        assert result.diff.verdict == Verdict.BREAKING
        kinds = {c.kind for c in result.diff.changes}
        assert "platform_baseline_floor_raised" in kinds

    def test_construction_performs_no_file_io_for_the_snapshot_paths(
        self, tmp_path
    ) -> None:
        """Building a ``CompareRequest`` reads nothing from disk -- the
        request carries intent, resolution happens at the plan boundary."""
        request = CompareRequest(
            old=InputSpec.of(str(tmp_path / "never-written-old.abi.json")),
            new=InputSpec.of(str(tmp_path / "never-written-new.abi.json")),
            env_matrix=self._matrix(),
        )
        assert request.env_matrix is not None
