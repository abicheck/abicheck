import pytest

from abicheck.buildsource.evidence_report import LayerCoverage, capability_lines
from abicheck.buildsource.model import CoverageStatus


def test_binary_and_headers_do_not_claim_signatures_or_vtables_are_off() -> None:
    coverage = [
        LayerCoverage("L0", CoverageStatus.PRESENT, detail="binary"),
        LayerCoverage("L2", CoverageStatus.PRESENT, detail="headers"),
    ]
    text = "\n".join(capability_lines(coverage, []))
    assert (
        "[on]  Public declarations, signatures, and declared virtual sequence" in text
    )
    assert "[on]  Exports, linkage, SONAME, and emitted ELF vtable-group sizes" in text
    assert "[not requested] Debug-derived compiled type layout" in text


def test_partial_and_failed_evidence_are_not_rendered_as_absence() -> None:
    coverage = [
        LayerCoverage("L1", CoverageStatus.NOT_COLLECTED, detail="extractor failed"),
        LayerCoverage("L2", CoverageStatus.PARTIAL, detail="one header failed"),
    ]
    text = "\n".join(capability_lines(coverage, []))
    assert "[failed] Debug-derived compiled type layout" in text
    assert "[partial] Public declarations, signatures" in text


def test_compact_evidence_with_all_optional_layers_has_no_empty_suffix() -> None:
    from abicheck.checker_types import DiffResult
    from abicheck.report.review_compute import compact_evidence_summary

    result = DiffResult("1", "2", "libx.so")
    result.layer_coverage = [
        {"layer": layer, "status": "present"}
        for layer in ("L0", "L1", "L2", "L4_source_abi")
    ]
    assert compact_evidence_summary(result) == (
        "binary exports, public headers/signatures, debug-derived compiled layout"
    )


class TestNotCollectedReasonReadsProseDefensively:
    """`CoverageStatus` has no member for *why* a layer was not collected, so
    the capability line reads it out of the free-text `detail`.

    A substring test is wrong for that field: a negated mention ("completed
    with no errors", "no missing headers") is a common shape in it, and the
    first version of this code reported both as problems the run did not
    have. These enumerate the vocabulary and its negations rather than
    pinning one string, and assert the conservative default for anything
    unrecognized.
    """

    @staticmethod
    def _row(detail: str):
        from abicheck.buildsource.model import CoverageStatus, LayerCoverage

        return LayerCoverage(
            layer="L1", status=CoverageStatus.NOT_COLLECTED, detail=detail
        )

    @pytest.mark.parametrize(
        ("detail", "expected"),
        [
            # Each keyword in the vocabulary, in a realistic sentence.
            ("extractor failed", "failed"),
            ("two translation units failed", "failed"),
            ("castxml error: unknown flag", "failed"),
            ("parse failure in header", "failed"),
            ("unsupported platform for this extractor", "unsupported"),
            ("disabled by project configuration", "disabled"),
            ("clang unavailable on this host", "unavailable"),
            ("compile_commands.json not found", "unavailable"),
            ("debug info absent", "unavailable"),
            ("headers missing", "unavailable"),
            # Negated mentions must NOT be read as that problem.
            ("completed with no errors", "not requested"),
            ("no failures detected", "not requested"),
            ("no missing headers", "not requested"),
            ("without errors", "not requested"),
            ("zero errors", "not requested"),
            # Unrecognized / empty -> the conservative default.
            ("", "not requested"),
            ("layer not selected for this run", "not requested"),
            ("terrorist", "not requested"),  # substring of "error", not a word
        ],
    )
    def test_reason_vocabulary(self, detail: str, expected: str) -> None:
        from abicheck.buildsource.evidence_report import _not_collected_reason

        assert _not_collected_reason(self._row(detail)) == expected

    def test_a_negated_mention_does_not_mask_a_real_one_later(self) -> None:
        """Negation suppresses that occurrence, not the whole detail."""
        from abicheck.buildsource.evidence_report import _not_collected_reason

        assert (
            _not_collected_reason(self._row("no errors, but headers missing"))
            == "unavailable"
        )

    def test_absent_row_is_not_reported_as_a_failure(self) -> None:
        from abicheck.buildsource.evidence_report import _not_collected_reason

        assert _not_collected_reason(None) == "not requested"

    def test_every_declared_reason_label_is_reachable(self) -> None:
        """Exhaustiveness: a vocabulary entry no input can produce is dead
        configuration that would never be noticed."""
        from abicheck.buildsource.evidence_report import (
            _NOT_COLLECTED_REASONS,
            _not_collected_reason,
        )

        produced = {
            _not_collected_reason(self._row(d))
            for d in (
                "extractor failed",
                "unsupported platform",
                "disabled by config",
                "clang unavailable",
            )
        }
        assert {label for _, label in _NOT_COLLECTED_REASONS} <= produced
