"""Performance benchmark tests.

All tests are marked with @pytest.mark.slow so they can be skipped with:
    pytest -m "not slow"
"""

from __future__ import annotations

import json
import time

import pytest
from _perf_scaling import measure_scaling_exponent

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict, compare
from abicheck.html_report import generate_html_report
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.reporter import to_json, to_markdown
from abicheck.sarif import to_sarif_str
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.suppression import Suppression, SuppressionList

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_func(i: int, prefix: str = "func") -> Function:
    return Function(
        name=f"{prefix}_{i}",
        mangled=f"_Z{len(prefix) + len(str(i)) + 1}{prefix}_{i}v",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _make_type(i: int) -> RecordType:
    return RecordType(
        name=f"Type_{i}",
        kind="struct",
        size_bits=64,
        fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ],
    )


def _make_snapshot(
    version: str,
    num_funcs: int = 0,
    num_types: int = 0,
    func_prefix: str = "func",
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libperf.so",
        version=version,
        functions=[_make_func(i, func_prefix) for i in range(num_funcs)],
        types=[_make_type(i) for i in range(num_types)],
    )


# ===========================================================================
# 1. Large snapshot comparison benchmark
# ===========================================================================


class TestLargeComparisonBenchmark:
    """Generate two AbiSnapshots with 1000 functions each and time compare()."""

    def test_compare_1000_functions_under_5_seconds(self) -> None:
        # Old: functions 0..999
        old = _make_snapshot("1.0", num_funcs=1000)

        # New: 500 unchanged (0..499), 250 removed (500..749 absent),
        #      250 added (new_0..new_249)
        new_funcs = [_make_func(i) for i in range(500)]  # unchanged
        new_funcs += [_make_func(i, prefix="new") for i in range(250)]  # added
        new = AbiSnapshot(
            library="libperf.so",
            version="2.0",
            functions=new_funcs,
        )

        start = time.monotonic()
        result = compare(old, new)
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"compare() took {elapsed:.2f}s, expected < 5s"
        assert result.verdict != Verdict.NO_CHANGE, "Expected changes to be detected"
        assert len(result.changes) > 0


# ===========================================================================
# 2. Large snapshot serialization benchmark
# ===========================================================================


class TestSerializationBenchmark:
    """Generate a large snapshot and time serialization round-trip."""

    def test_serialization_roundtrip_under_budget(self) -> None:
        # Budget deliberately carries headroom above the ~1s this round-trip
        # takes on an idle runner: a shared CI runner routinely pushed a
        # tight 2.0s threshold over by 4-8% (2.08s, 2.15s) with no code
        # change anywhere near serialization, i.e. pure scheduler noise, not
        # a regression. This still catches a real multi-x slowdown.
        budget_seconds = 4.0
        snap = _make_snapshot("1.0", num_funcs=1000, num_types=500)

        start = time.monotonic()
        json_str = snapshot_to_json(snap)
        d = json.loads(json_str)
        loaded = snapshot_from_dict(d)
        elapsed = time.monotonic() - start

        assert elapsed < budget_seconds, (
            f"Round-trip took {elapsed:.2f}s, expected < {budget_seconds:.0f}s"
        )
        assert len(loaded.functions) == 1000
        assert len(loaded.types) == 500


# ===========================================================================
# 3. Reporter scaling
# ===========================================================================


class TestReporterScaling:
    """Generate DiffResult with 500 changes and time reporter output."""

    def _make_large_diff(self) -> DiffResult:
        changes = []
        for i in range(250):
            changes.append(
                Change(
                    ChangeKind.FUNC_REMOVED,
                    f"_Z{len(str(i)) + 5}func_{i}v",
                    f"Function func_{i} removed",
                )
            )
        for i in range(250):
            changes.append(
                Change(
                    ChangeKind.FUNC_ADDED,
                    f"_Z{len(str(i)) + 4}new_{i}v",
                    f"New function new_{i} added",
                )
            )
        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libperf.so",
            changes=changes,
            verdict=Verdict.BREAKING,
        )

    def test_to_markdown_scaling(self) -> None:
        diff = self._make_large_diff()
        start = time.monotonic()
        md = to_markdown(diff)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"to_markdown took {elapsed:.2f}s, expected < 2s"
        assert len(md) > 0

    def test_to_json_scaling(self) -> None:
        diff = self._make_large_diff()
        start = time.monotonic()
        j = to_json(diff)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"to_json took {elapsed:.2f}s, expected < 2s"
        assert len(j) > 0

    def test_to_html_scaling(self) -> None:
        # The HTML renderer builds the largest output document; markdown/json
        # were guarded above but HTML was previously unbenchmarked.
        diff = self._make_large_diff()
        start = time.monotonic()
        html = generate_html_report(diff, lib_name="libperf.so")
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"generate_html_report took {elapsed:.2f}s, expected < 2s"
        assert len(html) > 0

    def test_to_sarif_scaling(self) -> None:
        diff = self._make_large_diff()
        start = time.monotonic()
        sarif = to_sarif_str(diff)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"to_sarif_str took {elapsed:.2f}s, expected < 2s"
        assert len(sarif) > 0


# ===========================================================================
# 3b. Suppression audit scaling
# ===========================================================================


class TestSuppressionAuditScaling:
    """``SuppressionList.audit`` tests every rule against every change.

    A real project keeps a roughly fixed ruleset while its library (and so its
    finding count) grows, so with the rule count held fixed the audit must stay
    linear in the number of findings. This guards against a regression that
    makes *per-finding* matching itself super-linear (e.g. recompiling a regex
    per change). Mirrors ``scripts/benchmark_scaling.py``'s ``suppression_audit``
    scenario.
    """

    _N_GROUPS = 8

    def _make_rules(self) -> SuppressionList:
        rules: list[Suppression] = []
        # Matching rules — one per module group; each finding matches exactly one.
        for j in range(self._N_GROUPS):
            rules.append(Suppression(symbol_pattern=rf"app::mod{j}::.*", reason="grp"))
        # Non-matching rules (most rules miss most findings).
        for j in range(24):
            rules.append(
                Suppression(symbol_pattern=rf".*::other{j}::.*", reason="miss")
            )
        for j in range(8):
            rules.append(Suppression(namespace=f"**::vendor{j}::*", reason="ns"))
        return SuppressionList(rules)

    def _make_changes(self, n: int) -> list[Change]:
        kinds = [
            ChangeKind.FUNC_REMOVED,
            ChangeKind.FUNC_ADDED,
            ChangeKind.TYPEDEF_REMOVED,
        ]
        return [
            Change(
                kind=kinds[i % len(kinds)],
                symbol=f"app::mod{i % self._N_GROUPS}::func{i}(int)",
                description=f"finding {i}",
            )
            for i in range(n)
        ]

    def test_audit_2000_findings_completes(self) -> None:
        supp = self._make_rules()
        changes = self._make_changes(2000)
        start = time.monotonic()
        audit = supp.audit(changes)
        elapsed = time.monotonic() - start

        # Generous bound for shared CI runners (~0.1s locally for 40 rules x 2000).
        assert elapsed < 20.0, f"audit took {elapsed:.2f}s, expected < 20s"
        assert audit.total_rules == 40
        # The workload must actually match (else the bookkeeping/high_risk paths
        # the gate is meant to exercise stay dormant — see PR #336 review).
        assert sum(audit.match_counts.values()) == 2000
        assert len(audit.high_risk_matches) > 0

    def test_audit_scaling_stays_linear_in_findings(self) -> None:
        import math

        supp = self._make_rules()
        timings: list[tuple[int, float]] = []
        for n in (1000, 2000):
            changes = self._make_changes(n)
            start = time.monotonic()
            supp.audit(changes)
            timings.append((n, max(time.monotonic() - start, 1e-3)))

        (n1, t1), (n2, t2) = timings
        exponent = math.log(t2 / t1) / math.log(n2 / n1)
        # Fixed ruleset → linear in findings (~1.0). A regression to per-finding
        # super-linear matching (exponent >= 1.6) should fail this guard.
        assert exponent < 1.6, f"audit scaling exponent {exponent:.2f} regressed"


# ===========================================================================
# 4. Many change kinds
# ===========================================================================


class TestManyChangeKinds:
    """Generate DiffResult with one of each ChangeKind and verify all render."""

    def test_all_changekind_values_render_without_error(self) -> None:
        changes = [
            Change(
                kind=kind,
                symbol=f"_sym_{kind.value}",
                description=f"Change: {kind.value}",
            )
            for kind in ChangeKind
        ]
        diff = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so",
            changes=changes,
            verdict=Verdict.BREAKING,
        )

        # to_json should not raise
        j = to_json(diff)
        assert len(j) > 0

        # to_markdown should not raise
        md = to_markdown(diff)
        assert len(md) > 0

    def test_all_changekind_sarif_render_without_error(self) -> None:
        from abicheck.sarif import to_sarif_str

        changes = [
            Change(
                kind=kind,
                symbol=f"_sym_{kind.value}",
                description=f"Change: {kind.value}",
            )
            for kind in ChangeKind
        ]
        diff = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so",
            changes=changes,
            verdict=Verdict.BREAKING,
        )

        sarif = to_sarif_str(diff)
        assert len(sarif) > 0


# ===========================================================================
# 4b. Type-churn scaling regression guard
# ===========================================================================


def _build_type_churn(n_funcs: int) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Snapshot pair where every public function takes a *changed* struct by
    pointer.

    Unlike the add/remove workload above, this exercises the post-processing
    detectors that relate each type change back to the functions that use it
    (affected-symbol enrichment, opaque/pointer-only filtering, namespace
    detection). That O(functions x types) path — not the core symbol diff — is
    what makes ``compare`` blow up on large real libraries
    (see docs/contribute/performance.md). Mirrors
    ``scripts/benchmark_scaling.py``'s ``type_churn`` scenario.
    """
    n_types = max(50, n_funcs // 20)
    types_old, types_new = [], []
    for i in range(n_types):
        base = [
            TypeField(name="a", type="int", offset_bits=0),
            TypeField(name="b", type="int", offset_bits=32),
        ]
        grown = base + [TypeField(name="c", type="int", offset_bits=64)]
        types_old.append(
            RecordType(name=f"Type_{i}", kind="struct", size_bits=64, fields=base)
        )
        types_new.append(
            RecordType(name=f"Type_{i}", kind="struct", size_bits=96, fields=grown)
        )
    funcs = [
        Function(
            name=f"use_Type_{i % n_types}_{i}",
            mangled=f"_Z4use_{i}P6Type_{i % n_types}",
            return_type="int",
            params=[Param(name="p", type=f"Type_{i % n_types} *")],
            visibility=Visibility.PUBLIC,
        )
        for i in range(n_funcs)
    ]
    old = AbiSnapshot(
        library="libperf.so", version="1.0", functions=list(funcs), types=types_old
    )
    new = AbiSnapshot(
        library="libperf.so", version="2.0", functions=list(funcs), types=types_new
    )
    return old, new


class TestTypeChurnScaling:
    """Guard the realistic hot path that the add/remove benchmark does not hit.

    Thresholds are deliberately generous: these tests catch a catastrophic
    regression (e.g. a detector becoming truly O(n^2)), not a few-percent
    drift, so they stay stable across CI runner speeds.
    """

    def test_type_churn_2000_functions_completes(self) -> None:
        old, new = _build_type_churn(2000)
        start = time.monotonic()
        result = compare(old, new)
        elapsed = time.monotonic() - start

        # ~3.5s locally; allow a wide margin for slow shared CI runners.
        assert elapsed < 30.0, f"type-churn compare took {elapsed:.2f}s, expected < 30s"
        assert result.verdict == Verdict.BREAKING
        assert len(result.changes) > 0

    def test_type_churn_scaling_stays_subquadratic(self) -> None:
        # Pre-build the snapshot pair per size once; only `compare()` itself
        # is inside the timed/repeated measurement. Deliberately *not* evenly
        # log-spaced (see _perf_scaling.py's docstring) -- a plain doubling
        # progression makes the middle point(s) contribute nothing to the
        # least-squares slope. Floor kept at the original test's 1000, not
        # lowered to 500: fixed per-call `compare()` overhead (C + a*n^2)
        # flattens the *apparent* exponent at small n, so including a
        # smaller size measurably dilutes sensitivity to a real quadratic
        # regression (Codex review: verified numerically that a synthetic
        # C + a*n^2 regression fits to ~1.88 -- under this test's own 1.9
        # ceiling -- with a 500-inclusive sweep, against ~1.95 -- correctly
        # over it -- with the original 1000-floor sweep).
        snapshots = {n: _build_type_churn(n) for n in (1000, 1300, 1650, 2000)}

        def _measure(n: int) -> float:
            old, new = snapshots[n]
            start = time.monotonic()
            compare(old, new)
            return max(time.monotonic() - start, 1e-3)

        # Four sizes, median-of-3 per size, least-squares exponent (see
        # ``_perf_scaling.py``) instead of one size pair and one timing each.
        exponent = measure_scaling_exponent(_measure, tuple(snapshots))
        # True quadratic would be ~2.0; current behaviour is ~1.3. A regression
        # to genuine O(n^2) (exponent >= 1.9) should fail this guard.
        assert exponent < 1.9, (
            f"compare scaling exponent {exponent:.2f} regressed toward O(n^2)"
        )

    def test_scaling_sizes_still_catch_a_synthetic_quadratic_regression(self) -> None:
        """Regression guard for the guard: pin the sensitivity argument in
        the comment above with an executable check, so a future change back
        to a smaller-inclusive sweep (e.g. re-adding 500) is caught here
        directly rather than only by comment (Codex review, fresh evidence).

        Models fixed-overhead-plus-quadratic ``compare()`` cost as
        ``C + a*n**2`` with ``C`` set to the same proportion of ``a*1000**2``
        used to verify this sizing choice, and asserts the *current* size
        sweep still fits that synthetic regression above the 1.9 ceiling --
        i.e. this test would actually fail if the regression it exists to
        catch really happened.
        """
        sizes = (1000, 1300, 1650, 2000)
        a = 1.0
        c = 0.05 * a * 1000**2

        def synthetic_regressed_cost(n: int) -> float:
            return c + a * n**2

        exponent = measure_scaling_exponent(synthetic_regressed_cost, sizes, repeats=1)
        assert exponent >= 1.9, (
            f"the current size sweep {sizes} fits a synthetic C + a*n^2 "
            f"regression to exponent {exponent:.3f}, under the 1.9 ceiling "
            "the real test asserts against -- it would silently pass a real "
            "quadratic regression"
        )


# ===========================================================================
# 5. Memory efficiency hint
# ===========================================================================


class TestMemoryEfficiency:
    """Verify result.changes length is bounded by input size."""

    def test_changes_bounded_by_input_size(self) -> None:
        old = _make_snapshot("1.0", num_funcs=1000)
        new_funcs = [_make_func(i, prefix="new") for i in range(1000)]
        new = AbiSnapshot(
            library="libperf.so",
            version="2.0",
            functions=new_funcs,
        )

        result = compare(old, new)
        # Changes should be exactly bounded: at most 2000 (1000 removed + 1000 added)
        assert len(result.changes) <= 2000, (
            f"Expected changes <= 2000, got {len(result.changes)}"
        )
