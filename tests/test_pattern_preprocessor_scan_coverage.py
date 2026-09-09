# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the pattern/preprocessor scan coverage-completeness
fix (CodeRabbit review of PR #1141, fresh evidence).

Bug: ``compute_pattern_preprocessor_scan`` folded a side as "evaluated" from
``files_scanned > 0`` (pattern) or ``ran and not all_failed`` (preprocessor)
alone -- both hold even when the scan only *partially* covered its inputs
(some pattern files skipped as unreadable; some preprocessor probes failed
or were truncated by the probe cap). A pattern kind or macro divergence
absent from a partial result could simply be hiding in the unscanned/failed
portion, not genuinely absent -- so folding it as fully evaluated let
``pattern_escalation_evolution``/``macro_divergence_evolution``/
``header_leak_evolution`` report ``introduced``/``resolved`` from incomplete
evidence instead of the honest ``not_evaluated``.

General invariant, not just the reported shape: both the pattern-scan and
preprocessor-scan sibling primitives get the same completeness treatment
(``_pattern_scan_fully_covered``/``_preprocessor_scan_fully_covered``), each
exercised at the unit level directly against constructed result objects
(every combination of "some work done, but not all of it"), plus one
end-to-end ``compute_pattern_preprocessor_scan`` case per primitive proving
the fold itself changes to ``not_evaluated``.
"""

from __future__ import annotations

from unittest.mock import patch

from abicheck.buildsource.pattern_facts import (
    PatternCategory,
    PatternFact,
    PatternFactsResult,
    PatternKind,
)
from abicheck.buildsource.preprocessor_facts import PreprocessorFactsResult
from abicheck.model import AbiSnapshot
from abicheck.workflows.pattern_preprocessor_scan import (
    _fold_evolution,
    _pattern_scan_fully_covered,
    _preprocessor_scan_fully_covered,
    compute_pattern_preprocessor_scan,
)


class TestPatternScanFullyCovered:
    def test_no_files_scanned_is_not_covered(self) -> None:
        assert _pattern_scan_fully_covered(PatternFactsResult()) is False

    def test_all_scanned_no_skips_is_covered(self) -> None:
        result = PatternFactsResult(files_scanned=3, files_skipped=0)
        assert _pattern_scan_fully_covered(result) is True

    def test_any_skipped_file_is_not_covered(self) -> None:
        """Even a single unreadable file alongside otherwise-successful
        scans means the result could be hiding a real hit -- not full
        coverage, per the CodeRabbit finding."""
        result = PatternFactsResult(files_scanned=3, files_skipped=1)
        assert _pattern_scan_fully_covered(result) is False

    def test_all_skipped_is_not_covered(self) -> None:
        result = PatternFactsResult(files_scanned=0, files_skipped=2)
        assert _pattern_scan_fully_covered(result) is False


class TestPreprocessorScanFullyCovered:
    def test_did_not_run_is_not_covered(self) -> None:
        assert _preprocessor_scan_fully_covered(PreprocessorFactsResult()) is False

    def test_all_succeeded_is_covered(self) -> None:
        result = PreprocessorFactsResult(ran=True, attempted=3, succeeded=3)
        assert _preprocessor_scan_fully_covered(result) is True

    def test_partial_failure_is_not_covered(self) -> None:
        """Some (not all) clang -E invocations failing means ``all_failed``
        is False, but coverage is still incomplete -- the CodeRabbit gap."""
        result = PreprocessorFactsResult(ran=True, attempted=3, succeeded=2)
        assert _preprocessor_scan_fully_covered(result) is False

    def test_all_failed_is_not_covered(self) -> None:
        result = PreprocessorFactsResult(ran=True, attempted=3, succeeded=0)
        assert _preprocessor_scan_fully_covered(result) is False

    def test_truncated_probes_is_not_covered(self) -> None:
        """The probe-count cap silently dropping units is exactly the same
        "real coverage gap" shape as a partial failure, even when every
        attempted probe itself succeeded."""
        result = PreprocessorFactsResult(
            ran=True, attempted=3, succeeded=3, probes_truncated=1
        )
        assert _preprocessor_scan_fully_covered(result) is False


class TestFoldEvolutionIncompleteSideReverseOrientation:
    """CodeRabbit review, fresh evidence: an earlier revision only folded
    ``not_evaluated`` when the *hit itself* was on the evaluated side
    (``new_hit and not old_evaluated`` / its symmetric case) -- the reverse
    orientation, a hit only on the *incomplete* side with the evaluated
    side silent, fell through to a bare ``continue`` and the identity
    vanished from the result entirely instead of reading ``not_evaluated``.
    Incompleteness on either side means neither PERSISTENT/INTRODUCED/
    RESOLVED can be trusted for *any* identity in the union, regardless of
    which side actually flagged it -- these cases exercise exactly the
    orientation the earlier revision dropped."""

    def test_hit_only_on_incomplete_old_side_is_not_evaluated(self) -> None:
        """OLD is incomplete and flags `k`; NEW is fully evaluated and does
        NOT flag it. The naive fold reads this as neither `new_hit` (false)
        nor `old_hit and not new_evaluated` (new_evaluated is True) --
        falling through to `continue` and silently dropping `k` instead of
        reporting `not_evaluated`."""
        result = _fold_evolution(
            old_evaluated=False,
            new_evaluated=True,
            old_keys={"k"},
            new_keys=set(),
        )
        assert result == {"k": "not_evaluated"}

    def test_hit_only_on_incomplete_new_side_is_not_evaluated(self) -> None:
        """Symmetric case: NEW is incomplete and flags `k`; OLD is fully
        evaluated and does not."""
        result = _fold_evolution(
            old_evaluated=True,
            new_evaluated=False,
            old_keys=set(),
            new_keys={"k"},
        )
        assert result == {"k": "not_evaluated"}

    def test_hit_on_both_sides_one_incomplete_is_not_evaluated(self) -> None:
        """Even when both sides flag `k`, an incomplete side still means
        the comparison can't be trusted -- this must not read as
        `persistent`."""
        result = _fold_evolution(
            old_evaluated=False,
            new_evaluated=True,
            old_keys={"k"},
            new_keys={"k"},
        )
        assert result == {"k": "not_evaluated"}


def _empty_snapshot(library: str, version: str) -> AbiSnapshot:
    return AbiSnapshot(library=library, version=version)


class TestComputePatternPreprocessorScanCoverageFold:
    """End-to-end: when the fully-covered side has a hit its incomplete
    sibling doesn't, the fold must read ``not_evaluated``, never
    ``resolved`` -- the incomplete side's silence proves nothing."""

    def test_partial_pattern_scan_folds_not_evaluated_instead_of_resolved(
        self,
    ) -> None:
        """OLD fully scans and finds the construct; NEW skips a file and its
        (necessarily partial) scan of the rest finds nothing. Before this
        fix, ``files_scanned > 0`` alone marked NEW "evaluated" too, so the
        fold read the construct as ``resolved`` -- a false claim, since the
        construct could simply be sitting in NEW's one skipped file."""
        old = _empty_snapshot("libfoo.so", "1.0")
        new = _empty_snapshot("libfoo.so", "2.0")

        old_pattern = PatternFactsResult(
            facts=[
                PatternFact(
                    kind=PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION,
                    category=PatternCategory.TEMPLATE,
                    path="old.hpp",
                    line=1,
                    snippet="template class Widget<int>;",
                    escalates=True,
                    detail="explicit template instantiation",
                )
            ],
            files_scanned=1,
            files_skipped=0,
        )
        new_pattern = PatternFactsResult(
            files_scanned=1,
            files_skipped=1,  # partial: one unreadable file alongside it
        )

        with (
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_pattern_scan",
                side_effect=[old_pattern, new_pattern],
            ),
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_preprocessor_scan_for",
                return_value=PreprocessorFactsResult(),
            ),
        ):
            result = compute_pattern_preprocessor_scan(old, new)

        # A naive files_scanned>0 fold would report "resolved" here (the
        # kind is old-only); full coverage requires zero skips on NEW.
        assert (
            result.pattern_escalation_evolution.get("explicit_template_instantiation")
            == "not_evaluated"
        )

    def test_partial_preprocessor_scan_folds_not_evaluated_instead_of_resolved(
        self,
    ) -> None:
        from abicheck.buildsource.preprocessor_facts import MacroDivergence

        old = _empty_snapshot("libfoo.so", "1.0")
        new = _empty_snapshot("libfoo.so", "2.0")

        old_preproc = PreprocessorFactsResult(
            ran=True,
            attempted=2,
            succeeded=2,
            divergences=[
                MacroDivergence(macro="FOO_VERSION", values={"1": ["tu1"]}),
            ],
        )
        new_preproc = PreprocessorFactsResult(
            ran=True,
            attempted=2,
            succeeded=1,  # partial: one clang -E invocation failed
        )

        with (
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_pattern_scan",
                return_value=PatternFactsResult(),
            ),
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_preprocessor_scan_for",
                side_effect=[old_preproc, new_preproc],
            ),
        ):
            result = compute_pattern_preprocessor_scan(old, new)

        # A naive "ran and not all_failed" fold would report "resolved"
        # here; full coverage requires succeeded == attempted on NEW.
        assert result.macro_divergence_evolution.get("FOO_VERSION") == "not_evaluated"

    def test_hit_only_on_incomplete_side_folds_not_evaluated_not_dropped(
        self,
    ) -> None:
        """CodeRabbit review, fresh evidence: the reverse orientation of
        the two cases above -- the hit is on the INCOMPLETE side (NEW
        skips a file and, in the portion it did scan, finds the
        construct), while the fully-covered OLD side finds nothing. The
        pre-fix `_fold_evolution` fell through to a bare `continue` for
        this orientation, silently dropping the identity from the report
        instead of stating the honest `not_evaluated`."""
        old = _empty_snapshot("libfoo.so", "1.0")
        new = _empty_snapshot("libfoo.so", "2.0")

        old_pattern = PatternFactsResult(files_scanned=1, files_skipped=0)
        new_pattern = PatternFactsResult(
            facts=[
                PatternFact(
                    kind=PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION,
                    category=PatternCategory.TEMPLATE,
                    path="new.hpp",
                    line=1,
                    snippet="template class Widget<int>;",
                    escalates=True,
                    detail="explicit template instantiation",
                )
            ],
            files_scanned=1,
            files_skipped=1,  # partial: one unreadable file alongside it
        )

        with (
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_pattern_scan",
                side_effect=[old_pattern, new_pattern],
            ),
            patch(
                "abicheck.workflows.pattern_preprocessor_scan._run_preprocessor_scan_for",
                return_value=PreprocessorFactsResult(),
            ),
        ):
            result = compute_pattern_preprocessor_scan(old, new)

        # Before the fix, this key was silently absent from the map at all
        # (dropped by the bare `continue`) rather than reading
        # "not_evaluated" -- `.get(...)` would return `None` either way a
        # naive read might miss, so assert key presence explicitly too.
        assert "explicit_template_instantiation" in result.pattern_escalation_evolution
        assert (
            result.pattern_escalation_evolution["explicit_template_instantiation"]
            == "not_evaluated"
        )
