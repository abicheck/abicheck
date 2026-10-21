"""Unit tests for abicheck.service_compare_pipeline: run_compare, run_compare_request and the compare pipeline phases."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker_types import DiffResult
from abicheck.comparability import compute_extraction_contract
from abicheck.errors import ScopeMismatchError, SnapshotError, ValidationError
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import load_snapshot, save_snapshot
from abicheck.service_compare_pipeline import (
    run_compare,
    run_compare_request,
)
from abicheck.workflows.compare_policy import (
    compare_snapshots,
)
from abicheck.workflows.request_inputs import (
    InputSpec,
)
from abicheck.workflows.service_requests import (
    CompareRequest,
    CompareResult,
)

# ── run_compare() ───────────────────────────────────────────────────────────


class TestRunCompare:
    def _make_snap_file(self, tmp_path, name, version="1.0"):
        """Create a minimal JSON snapshot file."""
        snap = AbiSnapshot(
            library=name,
            version=version,
            functions=[
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    is_extern_c=True,
                ),
            ],
        )
        p = tmp_path / f"{name}_{version}.json"
        save_snapshot(snap, p)
        return p

    def test_compare_two_snapshots(self, tmp_path):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        result, old, new = run_compare(old_p, new_p).as_tuple()
        assert isinstance(result, DiffResult)
        assert (result.old_version, result.new_version, result.library) == (
            "1.0",
            "2.0",
            "libtest",
        )
        assert (old.library, old.version) == ("libtest", "1.0")
        assert (new.library, new.version) == ("libtest", "2.0")

    def test_compare_with_suppression(self, tmp_path):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        sf = tmp_path / "suppress.yaml"
        sf.write_text(
            "version: 1\nsuppressions:\n  - symbol: foo\n    change_kind: func_removed\n"
        )
        result, _, _ = run_compare(old_p, new_p, suppress=sf).as_tuple()
        assert isinstance(result, DiffResult)
        assert (result.old_version, result.new_version, result.library) == (
            "1.0",
            "2.0",
            "libtest",
        )

    def test_headers_passed_as_public_headers(self, tmp_path, monkeypatch):
        """run_compare_request (the CompareRequest chokepoint used by the
        compare-release/directory-package fan-out) must thread each side's
        headers through as its public-header set for provenance tagging —
        same rule as the single-pair CLI's compare --header fix. Regression:
        this was silently dropped, unlike the single-pair path."""
        import abicheck.workflows.input_resolution as _input_resolution
        from abicheck import service as service_mod

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        old_h = tmp_path / "old.h"
        new_h = tmp_path / "new.h"

        calls: list[dict] = []
        original_resolve = service_mod.resolve_input

        def _spy(path, headers, includes, version, lang, **kwargs):
            calls.append({"path": path, "version": version, **kwargs})
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(_input_resolution, "resolve_input", _spy)

        run_compare(
            old_p,
            new_p,
            old_headers=[old_h],
            new_headers=[new_h],
        )
        assert len(calls) == 2
        # Matched by path rather than unpacked positionally: old/new resolution
        # runs concurrently on two threads (service.run_compare_request), so
        # the order the spy observes calls in is not guaranteed.
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["public_headers"] == [old_h]
        assert calls_by_path[new_p]["public_headers"] == [new_h]

    def test_debuginfod_url_appended_last_preserves_positional_order(self):
        # Codex review (PR #551): debuginfod_url was originally inserted right
        # after enable_debuginfod, ahead of scope_to_public_surface — any
        # caller invoking run_compare positionally that far would have every
        # later positional argument silently shift by one slot. It was the
        # LAST parameter at the time; diagnostic_comparison (ADR-050 D2) was
        # later appended after it by the same rule, so "last" itself moved on
        # — see test_diagnostic_comparison_appended_last_preserves_positional_order
        # for that -- but debuginfod_url's own position relative to its
        # pre-existing neighbors must still be unchanged.
        import inspect

        params = list(inspect.signature(run_compare).parameters)
        assert params.index("scope_to_public_surface") == (
            params.index("enable_debuginfod") + 1
        )
        assert (
            params.index("debuginfod_url")
            == params.index("public_surface_allowlist") + 1
        )


class TestCompareRequestAllowBuildQuery:
    """ADR-068 Phase 4's one absorbed field: ``CompareRequest.allow_build_query``.

    Absorbed from the retired ``ScanRequest.allow_build_query``. Two things
    have to hold for it to be a real field rather than an inert one (the
    failure mode ``pack_application.UNAPPLIED_PACK_FIELDS`` exists to reject
    for pack-assignable fields, applied here to request surface):

    1. It *reaches* the resolution primitive that acts on it, together with
       the per-side ``InputSpec.build_config`` nothing on this path read
       before -- an authorization with no config to authorize is inert.
    2. Its default *changes nothing*: the standing rule is that resolving an
       input never executes a build system as a side effect, so every
       pre-existing request must resolve exactly as it did.

    Asserted against ``_resolve_side_snapshot_impl``'s own observed keyword
    arguments, not against a re-derivation of the gate -- the oracle is
    "what the primitive was told", which is independent of
    ``_gated_build_query_inputs``' own logic.
    """

    def _request(self, tmp_path, **kwargs):
        from abicheck.workflows.request_inputs import InputSpec
        from abicheck.workflows.service_requests import CompareRequest

        old = AbiSnapshot(library="libtest", version="1.0")
        new = AbiSnapshot(library="libtest", version="2.0")
        old_p, new_p = tmp_path / "old.json", tmp_path / "new.json"
        save_snapshot(old, old_p)
        save_snapshot(new, new_p)
        cfg = kwargs.pop("build_config", None)
        return CompareRequest(
            old=InputSpec.of(old_p, build_config=cfg),
            new=InputSpec.of(new_p, build_config=cfg),
            **kwargs,
        )

    def _spy(self, monkeypatch):
        import abicheck.service_compare_pipeline as pipeline

        calls: list[dict] = []
        original = pipeline._resolve_side_snapshot_impl

        def _spied(side, evidence, **kwargs):
            calls.append(dict(kwargs))
            return original(side, evidence, **kwargs)

        monkeypatch.setattr(pipeline, "_resolve_side_snapshot_impl", _spied)
        return calls

    def test_default_is_false_and_forwards_none(self, tmp_path, monkeypatch):
        from abicheck.service_compare_pipeline import run_compare_request

        request = self._request(tmp_path)
        assert request.allow_build_query is False
        calls = self._spy(monkeypatch)
        run_compare_request(request)
        assert calls, "no side was resolved"
        # `None`, not `False`: the primitive keeps its own documented default
        # rather than this request restating it.
        assert all(c["allow_build_query"] is None for c in calls)

    def test_true_reaches_the_resolution_primitive(self, tmp_path, monkeypatch):
        from abicheck.service_compare_pipeline import run_compare_request

        calls = self._spy(monkeypatch)
        run_compare_request(self._request(tmp_path, allow_build_query=True))
        assert calls, "no side was resolved"
        assert all(c["allow_build_query"] is True for c in calls)

    def test_the_side_build_config_travels_with_it(self, tmp_path, monkeypatch):
        """The authorization and the thing it authorizes arrive together.

        `InputSpec.build_config` existed before this slice but nothing on the
        `compare` path read it, so `allow_build_query` alone would have been
        an authorization with no config to act on.
        """
        from abicheck.service_compare_pipeline import run_compare_request

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text('build:\n  query: "true"\n', encoding="utf-8")
        calls = self._spy(monkeypatch)
        run_compare_request(
            self._request(tmp_path, build_config=cfg, allow_build_query=True)
        )
        assert calls, "no side was resolved"
        assert all(c["build_config"] == cfg for c in calls)

    def test_an_unauthorized_config_is_forwarded_and_then_nulled_by_the_gate(
        self, tmp_path, monkeypatch
    ):
        """The *gate* lives in `_gated_build_query_inputs`, not here.

        The request forwards both values unconditionally; deciding what an
        un-consented config may contribute is the primitive's own job. Asserted
        through that primitive rather than in prose (Codex review, PR #1186:
        an earlier revision of this docstring claimed passive settings stayed
        readable without consent, which was the retired `scan`'s behaviour,
        not `compare`'s). Under `compare`'s
        default the *whole* config is nulled, `build.compile_db` included; that
        is not a narrowing, because nothing on this path read `build_config`
        before the field existed.
        """
        from abicheck.service_compare_pipeline import run_compare_request
        from abicheck.workflows.artifact.resolve import _gated_build_query_inputs

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text('build:\n  query: "true"\n', encoding="utf-8")
        calls = self._spy(monkeypatch)
        run_compare_request(self._request(tmp_path, build_config=cfg))
        assert calls, "no side was resolved"
        assert all(c["build_config"] == cfg for c in calls)
        assert all(c["allow_build_query"] is None for c in calls)
        # What the primitive then does with that pair, both ways -- the real
        # observable contract, independent of this call site's own wording.
        assert _gated_build_query_inputs(cfg, None, allow_build_query=False) == (
            None,
            None,
        )
        assert _gated_build_query_inputs(cfg, None, allow_build_query=True) == (
            cfg,
            None,
        )

    def test_the_retired_scan_request_fields_were_not_absorbed(self) -> None:
        """Every other open `ScanRequest` field is ruled (b) -- dropped, not
        mirrored onto `CompareRequest` (ADR-068's second 2026-09-09 amendment;
        ADR-055's amendment carries the per-field ledger). Pinned as a set so
        a later slice cannot quietly re-add one without restating the ruling.
        """
        import dataclasses

        from abicheck.workflows.service_requests import CompareRequest

        fields = {f.name for f in dataclasses.fields(CompareRequest)}
        assert not fields & {
            "risk_rules_path",
            "bundle_system_providers",
            "bundle_manifest",
            "enabled_checks",
            "severities",
            "max_findings",
            "changed_src",
            "seeded",
            "mode",
        }


class TestDiagnosticComparisonThreading:
    """ADR-050 D2's escape hatch, threaded through every service.py
    chokepoint (rollout-risk follow-up, PR #624): checker.compare()'s
    comparability gate was already live from Tier-1, but unreachable from
    every real front-end (CLI/MCP/scan/appcompat all route through
    service.py, never checker.compare directly -- ADR-037 D1/D10.1), since
    none of compare_snapshots/run_compare_request/run_compare forwarded
    diagnostic_comparison. These tests build a genuinely scope-mismatched
    contract pair (by hand, since dumper.py wiring is separate work) and
    confirm the escape hatch reaches checker.compare from each entry
    point."""

    def _mismatched_pair(self, tmp_path):
        # 2-header declared set: a single header's own name is no longer
        # load-bearing scope identity (Codex review, PR #624 follow-up —
        # the CI-red incident once the gate went live on real dumps at
        # scale). A changed header set alone is API evolution, not a
        # scope mismatch (oneDAL 2026.0 header removal), so the drift that
        # still triggers the gate here is manifest translation-unit drift.
        a_old = tmp_path / "old" / "a.h"
        a_new = tmp_path / "new" / "a.h"
        old_h = tmp_path / "old" / "foo.h"
        new_h = tmp_path / "new" / "bar.h"
        old_h.parent.mkdir(parents=True)
        new_h.parent.mkdir(parents=True)
        a_old.write_text("int g(void);\n")
        a_new.write_text("int g(void);\n")
        old_h.write_text("int f(void);\n")
        new_h.write_text("int f(void);\n")

        def _snap(version, headers, tu):
            return AbiSnapshot(
                library="libtest.so",
                version=version,
                functions=[
                    Function(
                        name="f",
                        mangled="f",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                        is_extern_c=True,
                    )
                ],
                contract=compute_extraction_contract(
                    declared_headers=headers, manifest_tu_scope=tu
                ),
            )

        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(_snap("1.0", [a_old, old_h], '[{"name": "core"}]'), old_p)
        save_snapshot(_snap("2.0", [a_new, new_h], '[{"name": "core2"}]'), new_p)
        return old_p, new_p

    def test_compare_snapshots_raises_by_default(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        with pytest.raises(ScopeMismatchError):
            compare_snapshots(load_snapshot(old_p), load_snapshot(new_p))

    def test_compare_snapshots_diagnostic_comparison_downgrades(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        result = compare_snapshots(
            load_snapshot(old_p), load_snapshot(new_p), diagnostic_comparison=True
        )
        assert result.assurance == "none"

    def test_run_compare_request_raises_by_default(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        request = CompareRequest(old=InputSpec(path=old_p), new=InputSpec(path=new_p))
        with pytest.raises(ScopeMismatchError):
            run_compare_request(request)

    def test_run_compare_request_diagnostic_comparison_downgrades(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        request = CompareRequest(
            old=InputSpec(path=old_p),
            new=InputSpec(path=new_p),
            diagnostic_comparison=True,
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert result.assurance == "none"

    def test_run_compare_shim_raises_by_default(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        with pytest.raises(ScopeMismatchError):
            run_compare(old_p, new_p)

    def test_run_compare_shim_diagnostic_comparison_downgrades(self, tmp_path):
        old_p, new_p = self._mismatched_pair(tmp_path)
        result, _, _ = run_compare(old_p, new_p, diagnostic_comparison=True).as_tuple()
        assert result.assurance == "none"

    def test_diagnostic_comparison_appended_last_preserves_positional_order(self):
        # Same rule as debuginfod_url (Codex review, PR #551): appended after
        # every pre-existing parameter so a positional caller's existing
        # bindings don't shift. contract_evaluation (ADR-049 Phase 3) was
        # later appended after it by the same rule, so "last" itself moved
        # on again -- see
        # TestContractEvaluationThreading.test_contract_evaluation_appended_last_preserves_positional_order
        # for that -- but diagnostic_comparison's own position relative to
        # its pre-existing neighbor must still be unchanged.
        import inspect

        params = list(inspect.signature(run_compare).parameters)
        assert params.index("diagnostic_comparison") == (
            params.index("debuginfod_url") + 1
        )


class TestContractEvaluationThreading:
    """ADR-049 Phase 3's shadow contract evaluator (contract_evaluation.py)
    was wired into checker.compare() (stamping Change.contract_relevance/
    contract_reason_code/contract_assurance) but unreachable from every real
    front-end: none of compare_snapshots/run_compare_request/run_compare
    forwarded a contract_evaluation flag, and no front-end may call
    checker.compare directly (cli-contract AI-readiness gate, ADR-037
    D10.1). These tests build a genuine function-signature change and
    confirm the flag reaches checker.compare from each Tier-2 entry point
    and actually stamps the finding, mirroring
    TestDiagnosticComparisonThreading's pattern for the sibling
    diagnostic_comparison escape hatch."""

    def _changed_pair(self, tmp_path):
        def _snap(version, return_type):
            return AbiSnapshot(
                library="libtest.so",
                version=version,
                functions=[
                    Function(
                        name="f",
                        mangled="f",
                        return_type=return_type,
                        visibility=Visibility.PUBLIC,
                        is_extern_c=True,
                    )
                ],
            )

        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(_snap("1.0", "int"), old_p)
        save_snapshot(_snap("2.0", "long"), new_p)
        return old_p, new_p

    def test_compare_snapshots_opt_out_leaves_contract_relevance_unset(self, tmp_path):
        old_p, new_p = self._changed_pair(tmp_path)
        result = compare_snapshots(
            load_snapshot(old_p), load_snapshot(new_p), contract_evaluation=False
        )
        assert result.changes
        assert all(c.contract_relevance is None for c in result.changes)

    def test_compare_snapshots_contract_evaluation_stamps_relevance(self, tmp_path):
        old_p, new_p = self._changed_pair(tmp_path)
        result = compare_snapshots(
            load_snapshot(old_p), load_snapshot(new_p), contract_evaluation=True
        )
        assert result.changes
        assert any(c.contract_relevance is not None for c in result.changes)

    def test_run_compare_request_contract_evaluation_stamps_relevance(self, tmp_path):
        old_p, new_p = self._changed_pair(tmp_path)
        request = CompareRequest(
            old=InputSpec(path=old_p),
            new=InputSpec(path=new_p),
            contract_evaluation=True,
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert result.changes
        assert any(c.contract_relevance is not None for c in result.changes)

    def test_run_compare_shim_contract_evaluation_stamps_relevance(self, tmp_path):
        old_p, new_p = self._changed_pair(tmp_path)
        result, _, _ = run_compare(old_p, new_p, contract_evaluation=True).as_tuple()
        assert result.changes
        assert any(c.contract_relevance is not None for c in result.changes)

    def test_contract_evaluation_appended_last_preserves_positional_order(self):
        # Same rule as debuginfod_url/diagnostic_comparison (Codex review,
        # PR #551 and ADR-050 D2 follow-up): appended after every
        # pre-existing parameter so a positional caller's existing bindings
        # don't shift. include_dependencies (dependency-scope default-
        # filtering parity fix) was appended after this one in turn.
        import inspect

        params = list(inspect.signature(run_compare).parameters)
        # pack_policy_overrides/pack_internal_namespaces (CLI cleanup phase
        # two, "PR B" slice 1) were appended after contract_mode in turn,
        # following the same rule; compile_context (both-sides L2 compile
        # context for the release fan-out), then depth (D1, CLI-audit), then
        # severity_preset (ADR-064/PR G2). Its sibling `exit_code_scheme` was
        # appended at the same time but later deleted outright (PR G2's
        # manual-override removal). public_header_dirs (PR #1138) is next,
        # then collapse_versioned_symbols (Codex review: the release
        # fan-out's own gap -- see service_compare_pipeline.run_compare's
        # docstring), then project_policy_overrides (findings-analysis-fixes
        # batch, second review round, defect 4: the release fan-out's own
        # `.abicheck.yml` `policy.overrides` gap), same rule.
        assert params[-17:] == [
            "diagnostic_comparison",
            "contract_evaluation",
            "include_dependencies",
            "contract_mode",
            "pack_policy_overrides",
            "pack_internal_namespaces",
            "compile_context",
            "depth",
            "severity_preset",
            "public_header_dirs",
            "collapse_versioned_symbols",
            "project_policy_overrides",
            "env_matrix",
            # The release fan-out's own `--exclude-header` gap: a
            # directory/package `compare` reaches this shim rather than
            # `cli_resolve._resolve_compare_snapshots`, so the rules had no
            # channel to a member's own comparison at all. Appended last,
            # same rule as every entry above it.
            "exclude_headers",
            # ADR-067 D5/D6's acknowledgment inputs for the release fan-out
            # and typed API, appended last by the same rule.
            "acknowledgments_path",
            "acknowledgment_unacknowledged_additions",
            # ADR-067 D5 `breaks: gate|accept`, appended last by the same rule.
            "acknowledgment_breaks",
        ]

    def test_new_gate_params_are_keyword_only_without_breaking_older_ones(self):
        """CodeRabbit review, fresh evidence, PR #1032, then corrected by a
        P1 Codex finding on the first fix: this docstring has claimed every
        param from `debuginfod_url` onward is "keyword-only" since PR #551,
        but no `*` separator ever enforced it -- `severity_preset` (ADR-064/
        PR G2) was added under that same claim and, like every param before
        it, still silently accepted positional binding (its sibling
        `exit_code_scheme` was added then but later deleted outright by
        PR G2's own manual-override removal). The first fix added `*`
        before `debuginfod_url`, which closed the gap for the new param but
        retroactively made every *pre-existing* param from `debuginfod_url` onward (e.g.
        `compile_context`, `depth`) keyword-only too -- a public-API break
        for any real caller still passing one of them positionally, which
        this same docstring's "keeps binding positionally" guarantee
        forbids. Corrected by moving the `*` to right before
        `severity_preset`: only the newest, never-previously-released
        param is actually enforced; every older param keeps accepting
        positional binding exactly as before."""
        import inspect

        params = inspect.signature(run_compare).parameters
        assert params["severity_preset"].kind is inspect.Parameter.KEYWORD_ONLY
        for name in (
            "debuginfod_url",
            "diagnostic_comparison",
            "contract_evaluation",
            "include_dependencies",
            "contract_mode",
            "pack_policy_overrides",
            "pack_internal_namespaces",
            "compile_context",
            "depth",
        ):
            assert params[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, name

    def test_get_type_hints_resolves_without_nameerror(self):
        """Codex review: `run_compare` moved from `service.py` into
        `service_compare_pipeline.py` in this same PR, and `Path` was only
        imported there under `TYPE_CHECKING` -- with `from __future__ import
        annotations` (PEP 563), `typing.get_type_hints(run_compare)` (a
        schema generator or docs tool introspecting this public function)
        raised `NameError: name 'Path' is not defined`. Fixed by importing
        `pathlib.Path` unconditionally in `service_compare_pipeline.py`."""
        import typing

        hints = typing.get_type_hints(run_compare)
        assert hints["old_input"] is Path
        assert hints["new_input"] is Path


class TestParallelOldNewExtraction:
    """run_compare_request resolves old/new concurrently (two threads) since
    neither side depends on the other until compare_snapshots(). These tests
    guard the concurrency itself, exception propagation, and the
    ABICHECK_PARALLEL_EXTRACTION=0 escape hatch."""

    def _snap_files(self, tmp_path):
        from abicheck.serialization import save_snapshot

        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(AbiSnapshot(library="l", version="1.0"), old_p)
        save_snapshot(AbiSnapshot(library="l", version="2.0"), new_p)
        return old_p, new_p

    def test_both_sides_extracted_concurrently(self, tmp_path, monkeypatch):
        import threading

        import abicheck.workflows.input_resolution as _input_resolution
        from abicheck import service as service_mod

        old_p, new_p = self._snap_files(tmp_path)
        original_resolve = service_mod.resolve_input
        # A 2-party barrier: this call only returns once BOTH old and new
        # resolution have reached it. If run_compare_request serialized them
        # (regressing to the old sequential behavior) this would deadlock —
        # the second call can never start until the first returns, but the
        # first is stuck waiting on the barrier — surfacing as a
        # BrokenBarrierError after the timeout rather than a flaky race.
        barrier = threading.Barrier(2, timeout=5)

        def _synced_resolve(path, headers, includes, version, lang, **kwargs):
            barrier.wait()
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(_input_resolution, "resolve_input", _synced_resolve)

        result, old, new = run_compare(old_p, new_p).as_tuple()
        assert isinstance(result, DiffResult)

    def test_exception_from_one_side_propagates(self, tmp_path, monkeypatch):
        import abicheck.workflows.input_resolution as _input_resolution
        from abicheck import service as service_mod

        old_p, new_p = self._snap_files(tmp_path)
        original_resolve = service_mod.resolve_input

        def _boom(path, headers, includes, version, lang, **kwargs):
            if Path(path) == Path(old_p):
                raise SnapshotError("boom - old side failed")
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(_input_resolution, "resolve_input", _boom)

        with pytest.raises(SnapshotError, match="boom - old side failed"):
            run_compare(old_p, new_p)

    def test_env_var_disables_parallel_extraction(self, tmp_path, monkeypatch):
        import threading

        import abicheck.workflows.input_resolution as _input_resolution
        from abicheck import service as service_mod

        old_p, new_p = self._snap_files(tmp_path)
        original_resolve = service_mod.resolve_input
        threads_seen: set[int] = set()

        def _spy(path, headers, includes, version, lang, **kwargs):
            threads_seen.add(threading.get_ident())
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(_input_resolution, "resolve_input", _spy)
        monkeypatch.setenv("ABICHECK_PARALLEL_EXTRACTION", "0")

        run_compare(old_p, new_p)

        # Both calls ran on the current (main) thread — no pool was used.
        assert threads_seen == {threading.get_ident()}


class TestRunCompareRequestTypedResult:
    """ADR-055 D2: the one typed entry point and what it returns.

    ``run_compare_request`` returned a bare 3-tuple until 0.6; it and the
    ``run_compare`` shim both return the typed result now.
    """

    def _pair(self, tmp_path):
        old = AbiSnapshot(library="libtest", version="1.0")
        new = AbiSnapshot(library="libtest", version="2.0")
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(old, old_p)
        save_snapshot(new, new_p)
        return CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))

    def test_returns_a_compare_result(self, tmp_path):
        result = run_compare_request(self._pair(tmp_path))
        assert isinstance(result, CompareResult)
        assert isinstance(result.diff, DiffResult)
        assert result.old_snapshot.version == "1.0"
        assert result.new_snapshot.version == "2.0"

    def test_as_tuple_reproduces_the_pre_0_6_shape(self, tmp_path):
        """The documented one-line migration for a positional caller."""
        result = run_compare_request(self._pair(tmp_path))
        diff, old, new = result.as_tuple()
        assert diff is result.diff
        assert old is result.old_snapshot
        assert new is result.new_snapshot

    def test_the_kwargs_shim_returns_the_same_type(self, tmp_path):
        # run_compare is the documented one-call entry point; it must not be
        # the one place still handing back a tuple.
        request = self._pair(tmp_path)
        assert isinstance(
            run_compare(request.old.path, request.new.path), CompareResult
        )

    def test_suppression_is_none_when_the_request_names_no_file(self, tmp_path):
        assert run_compare_request(self._pair(tmp_path)).suppression is None

    def test_carries_the_resolved_suppression_list(self, tmp_path):
        """What ADR-055 D4 needs: the resolved list, without a second load.

        ``DiffResult`` carries the resolved policy file but never the
        suppression list, so before this a front end applying post-
        classification scoping (``appcompat.scope_diff_to_app``) had to load
        the same file again itself.
        """
        supp = tmp_path / "suppress.yaml"
        supp.write_text(
            "version: 1\nsuppressions:\n  - symbol: _Z1gone\n    reason: test\n",
            encoding="utf-8",
        )
        request = self._pair(tmp_path).replace(suppress=supp)
        result = run_compare_request(request)
        assert result.suppression is not None
        assert len(result.suppression.rule_identities()) == 1

    def test_follow_linker_scripts_is_forwarded_per_side(self, tmp_path, monkeypatch):
        """ADR-055 D4: the MCP server's resource guard is request surface.

        ``resolve_input`` follows a GNU ld linker script by default; the MCP
        tools must not, because they size-check only the caller-supplied path.
        """
        import abicheck.service as service_mod
        import abicheck.workflows.input_resolution as _input_resolution

        captured: dict[str, object] = {}
        original = service_mod.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured[version] = kwargs.get("follow_linker_scripts")
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(_input_resolution, "resolve_input", _spy)
        import dataclasses

        base = self._pair(tmp_path)
        run_compare_request(
            base.replace(
                old=dataclasses.replace(
                    base.old, version="old", follow_linker_scripts=False
                ),
                new=dataclasses.replace(base.new, version="new"),
            )
        )
        assert captured["old"] is False
        # Unset stays the historical default rather than inheriting the other side.
        assert captured["new"] is True


class TestRunCompareRequestResolutionParity:
    """ADR-055 D1, second slice: the last concepts the CLI's own resolution
    (``cli_resolve._resolve_compare_snapshots``) could express and the typed
    request could not — ``--dwarf-only``, ``--debug-format``, ADR-050 D1's
    include labels, and ``--follow-deps``."""

    def _elf_pair(self, tmp_path) -> tuple[Path, Path]:
        old_p = tmp_path / "old.so"
        new_p = tmp_path / "new.so"
        for p in (old_p, new_p):
            p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        return old_p, new_p

    def _stub_dump(self, monkeypatch) -> dict[str, dict[str, object]]:
        """Record resolve_input's debug kwargs per side, without a real parse."""
        import abicheck.workflows.input_resolution as input_resolution_mod

        seen: dict[str, dict] = {}

        def _fake(path, headers=None, *args, **kwargs):
            seen[Path(path).name] = {
                k: kwargs.get(k)
                for k in ("dwarf_only", "debug_format", "include_labels")
            }
            return AbiSnapshot(library=Path(path).name, version="x")

        monkeypatch.setattr(input_resolution_mod, "run_dump", _fake)
        return seen

    def _request(self, tmp_path, **kwargs) -> CompareRequest:
        old_p, new_p = self._elf_pair(tmp_path)
        return CompareRequest(
            old=InputSpec(path=old_p, version="old"),
            new=InputSpec(path=new_p, version="new"),
            **kwargs,
        )

    def test_debug_parse_fields_reach_both_sides(self, tmp_path, monkeypatch):
        seen = self._stub_dump(monkeypatch)
        run_compare_request(
            self._request(tmp_path, dwarf_only=True, debug_format="dwarf")
        )
        assert set(seen) == {"old.so", "new.so"}
        for side in seen.values():
            assert side["dwarf_only"] is True
            assert side["debug_format"] == "dwarf"

    def test_include_labels_are_converted_back_to_a_mapping(
        self, tmp_path, monkeypatch
    ):
        # Carried as a tuple of pairs so the request stays hashable, but
        # `resolve_input` takes the mapping — the conversion must happen.
        seen = self._stub_dump(monkeypatch)
        run_compare_request(
            self._request(tmp_path, include_labels=((Path("/inc"), "proj"),))
        )
        assert seen["old.so"]["include_labels"] == {Path("/inc"): "proj"}

    def test_include_labels_default_passes_none_not_an_empty_mapping(
        self, tmp_path, monkeypatch
    ):
        seen = self._stub_dump(monkeypatch)
        run_compare_request(self._request(tmp_path))
        assert seen["old.so"]["include_labels"] is None

    def test_request_stays_hashable_with_include_labels_set(self, tmp_path):
        # The property InputSpec's own docstring claims for the whole request.
        request = self._request(tmp_path, include_labels=((Path("/inc"), "proj"),))
        assert hash(request) == hash(request.replace())

    def _stub_dependency_population(
        self, monkeypatch
    ) -> list[tuple[str, list[Path], str]]:
        import abicheck.dependency_info as dep_mod

        calls: list[tuple] = []
        monkeypatch.setattr(
            dep_mod,
            "populate_dependency_info",
            lambda snap, so_path, search_paths, sysroot, ld_library_path: calls.append(
                (Path(so_path).name, list(search_paths), ld_library_path)
            ),
        )
        return calls

    def test_follow_dependencies_populates_both_elf_sides(self, tmp_path, monkeypatch):
        self._stub_dump(monkeypatch)
        calls = self._stub_dependency_population(monkeypatch)
        run_compare_request(
            self._request(
                tmp_path,
                follow_dependencies=True,
                dependency_search_paths=(Path("/opt/lib"),),
                ld_library_path="/usr/lib",
            )
        )
        assert [c[0] for c in calls] == ["old.so", "new.so"]
        assert calls[0][1] == [Path("/opt/lib")]
        assert calls[0][2] == "/usr/lib"

    def test_follow_dependencies_is_opt_in(self, tmp_path, monkeypatch):
        # It costs a full dependency-graph resolution per side, so an
        # unrelated caller must not start paying for it silently.
        self._stub_dump(monkeypatch)
        calls = self._stub_dependency_population(monkeypatch)
        run_compare_request(self._request(tmp_path))
        assert calls == []

    def test_non_elf_sides_are_skipped(self, tmp_path, monkeypatch):
        # resolve_dependencies reads ELF DT_NEEDED entries; a PE/Mach-O side
        # has nothing for it to do.
        self._stub_dump(monkeypatch)
        calls = self._stub_dependency_population(monkeypatch)
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(AbiSnapshot(library="lib", version="1.0"), old_p)
        save_snapshot(AbiSnapshot(library="lib", version="2.0"), new_p)
        run_compare_request(
            CompareRequest(
                old=InputSpec(path=old_p),
                new=InputSpec(path=new_p),
                follow_dependencies=True,
            )
        )
        assert calls == []


class TestDebugFormatResolution:
    """ADR-055 D1 second slice, Codex review round 2: what `debug_format`
    actually reaches (and doesn't reach) the extraction layer as."""

    def _request(self, tmp_path, **kwargs) -> CompareRequest:
        old_p = tmp_path / "old.so"
        new_p = tmp_path / "new.so"
        for p in (old_p, new_p):
            p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        return CompareRequest(
            old=InputSpec(path=old_p), new=InputSpec(path=new_p), **kwargs
        )

    def _spy(self, monkeypatch) -> dict[str, object]:
        import abicheck.workflows.input_resolution as input_resolution_mod

        seen: dict[str, object] = {}

        def _fake(path, headers=None, *args, **kwargs):
            seen["debug_format"] = kwargs.get("debug_format")
            return AbiSnapshot(library=Path(path).name, version="x")

        monkeypatch.setattr(input_resolution_mod, "run_dump", _fake)
        return seen

    def test_auto_becomes_none_not_the_literal_string(self, tmp_path, monkeypatch):
        # `_resolve_debug_metadata` raises ValueError for anything outside
        # dwarf/btf/ctf and treats None as auto-detect, so forwarding the
        # accepted "auto" verbatim crashed during extraction. The CLI does the
        # same translation (cli_compare_helpers/cli_dump_helpers).
        seen = self._spy(monkeypatch)
        run_compare_request(self._request(tmp_path, debug_format="auto"))
        assert seen["debug_format"] is None

    def test_auto_is_case_insensitive_too(self, tmp_path, monkeypatch):
        seen = self._spy(monkeypatch)
        run_compare_request(self._request(tmp_path, debug_format="AUTO"))
        assert seen["debug_format"] is None

    def test_an_explicit_format_is_lowercased_and_forwarded(
        self, tmp_path, monkeypatch
    ):
        seen = self._spy(monkeypatch)
        run_compare_request(self._request(tmp_path, debug_format="BTF"))
        assert seen["debug_format"] == "btf"

    def test_forced_elf_format_is_rejected_for_a_non_elf_side(self, tmp_path):
        # The PE/Mach-O dump paths take no debug-format argument, so it would
        # be silently dropped and the run would report success having ignored
        # what was asked. The CLI rejects this up front; so does this now.
        old_p = tmp_path / "old.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        pe = tmp_path / "new.dll"
        pe.write_bytes(b"MZ" + b"\x00" * 200)
        request = CompareRequest(
            old=InputSpec(path=old_p), new=InputSpec(path=pe), debug_format="dwarf"
        )
        with pytest.raises(ValidationError, match="only supported for ELF"):
            run_compare_request(request)

    def test_auto_is_not_rejected_for_a_non_elf_side(self, tmp_path, monkeypatch):
        # "auto" forces nothing, so there is nothing for a PE side to ignore.
        seen = self._spy(monkeypatch)
        old_p = tmp_path / "old.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        pe = tmp_path / "new.dll"
        pe.write_bytes(b"MZ" + b"\x00" * 200)
        req = CompareRequest(
            old=InputSpec(path=old_p), new=InputSpec(path=pe), debug_format="auto"
        )
        run_compare_request(req)
        assert seen["debug_format"] is None

    def test_snapshot_inputs_are_unaffected(self, tmp_path, monkeypatch):
        # A JSON snapshot has no detected binary format; same as the CLI, that
        # is not a rejection case.
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(AbiSnapshot(library="l", version="1"), old_p)
        save_snapshot(AbiSnapshot(library="l", version="2"), new_p)
        result = run_compare_request(
            CompareRequest(
                old=InputSpec(path=old_p),
                new=InputSpec(path=new_p),
                debug_format="dwarf",
            )
        )
        assert (result.old_snapshot.version, result.new_snapshot.version) == ("1", "2")


class TestComparePipelinePhases:
    """ADR-055 D1: ``run_compare_request`` is the composition of the two
    phases in ``service_compare_pipeline``, and those phases are what the
    native ``compare`` CLI now shares instead of its own resolution copy."""

    def _snap_file(self, tmp_path, name, version):
        from abicheck.serialization import save_snapshot

        path = tmp_path / f"{name}-{version}.json"
        save_snapshot(
            AbiSnapshot(
                library=name,
                version=version,
                functions=[
                    Function(
                        name="foo",
                        mangled="foo",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                        is_extern_c=True,
                    )
                ],
            ),
            path,
        )
        return path

    def test_run_compare_request_equals_resolve_then_classify(self, tmp_path):
        from abicheck.service import (
            classify_compare_pair,
            resolve_compare_request,
            run_compare_request,
        )

        old_p = self._snap_file(tmp_path, "libtest", "1.0")
        new_p = self._snap_file(tmp_path, "libtest", "2.0")
        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))

        composed = classify_compare_pair(request, resolve_compare_request(request))
        one_call = run_compare_request(request)

        assert [c.kind for c in composed.diff.changes] == [
            c.kind for c in one_call.diff.changes
        ]
        assert composed.diff.verdict == one_call.diff.verdict
        assert composed.old_snapshot.version == one_call.old_snapshot.version
        assert composed.new_snapshot.version == one_call.new_snapshot.version

    def test_resolve_phase_returns_both_sides_and_their_evidence(self, tmp_path):
        from abicheck.service import resolve_compare_request

        old_p = self._snap_file(tmp_path, "libtest", "1.0")
        new_p = self._snap_file(tmp_path, "libtest", "2.0")
        pair = resolve_compare_request(
            CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        )
        assert pair.old.version == "1.0"
        assert pair.new.version == "2.0"
        # A JSON snapshot has no detected binary format, same as on the CLI.
        assert pair.old_fmt is None and pair.new_fmt is None
        assert pair.old_evidence.collect_mode == pair.new_evidence.collect_mode == "off"

    def test_layer_coverage_rows_reach_the_result(self, tmp_path, monkeypatch):
        """The one branch in `classify_compare_pair` nothing else exercises.

        ``prepare_embedded_build_source`` only returns layer-coverage rows when
        the snapshots actually carry embedded L3-L5 evidence, so a plain
        snapshot pair leaves this assignment unexecuted. Stubbing that helper
        checks the wiring itself: rows it produces are attached to the
        ``DiffResult``, and the metrics call still receives the extra changes.
        """
        from abicheck.buildsource import evidence_report
        from abicheck.service import classify_compare_pair, resolve_compare_request

        rows = [{"layer": "L3", "covered": 1}]
        attached: list[object] = []

        def _fake_prepare(*_args, **_kwargs):
            return [], rows, {"elapsed": 0.0}, []

        def _fake_attach(result, metrics, extra, **_kwargs):
            attached.append((result, metrics, extra))

        # ADR-061 Phase 3: patch the owner -- these moved to the engine, and
        # `classify_compare_pair` imports them from there.
        monkeypatch.setattr(
            evidence_report, "prepare_embedded_build_source", _fake_prepare
        )
        monkeypatch.setattr(evidence_report, "attach_evidence_metrics", _fake_attach)

        old_p = self._snap_file(tmp_path, "libtest", "1.0")
        new_p = self._snap_file(tmp_path, "libtest", "2.0")
        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        result = classify_compare_pair(request, resolve_compare_request(request))

        assert result.diff.layer_coverage == rows
        assert len(attached) == 1
        assert attached[0][0] is result.diff
