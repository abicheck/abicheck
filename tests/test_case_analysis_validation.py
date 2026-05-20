"""Validation tests for abicheck example case analysis.

Ensures consistency between ground_truth.json, the ChangeKind registry,
the checker_policy verdict classification, and the example case directories.

These tests run without compilation — they validate metadata integrity only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.change_registry import REGISTRY, Verdict
from abicheck.checker_policy import (
    ChangeKind,
)

REPO_DIR = Path(__file__).parent.parent
EXAMPLES_DIR = REPO_DIR / "examples"
GT_PATH = EXAMPLES_DIR / "ground_truth.json"


@pytest.fixture(scope="module")
def ground_truth() -> dict:
    """Load ground_truth.json once per module."""
    return json.loads(GT_PATH.read_text())


@pytest.fixture(scope="module")
def verdicts(ground_truth: dict) -> dict[str, dict]:
    """Return the verdicts dict from ground_truth.json."""
    return ground_truth["verdicts"]


# ---------------------------------------------------------------------------
# 1. Structural integrity
# ---------------------------------------------------------------------------


class TestGroundTruthStructure:
    """Validate the structure and required fields of ground_truth.json."""

    def test_version_is_present(self, ground_truth: dict) -> None:
        assert "version" in ground_truth

    def test_verdicts_key_exists(self, ground_truth: dict) -> None:
        assert "verdicts" in ground_truth
        assert len(ground_truth["verdicts"]) > 0

    def test_every_case_has_expected_verdict(self, verdicts: dict) -> None:
        for case_name, meta in verdicts.items():
            assert "expected" in meta, f"{case_name} missing 'expected' field"
            # Bundle cases (ADR-023) intentionally set expected=null because
            # they are not single-library v1/v2 comparisons; their per-library
            # verdicts live under expected_libraries and their aggregate
            # verdict under expected_combined_verdict.
            if meta.get("category") == "bundle":
                assert meta["expected"] is None, (
                    f"{case_name}: bundle case must have expected=null"
                )
                continue
            assert meta["expected"] in {
                "NO_CHANGE",
                "COMPATIBLE",
                "COMPATIBLE_WITH_RISK",
                "API_BREAK",
                "BREAKING",
            }, f"{case_name} has invalid verdict: {meta['expected']}"

    def test_every_case_has_category(self, verdicts: dict) -> None:
        valid_categories = {
            "no_change",
            "addition",
            "quality",
            "risk",
            "api_break",
            "breaking",
            "bundle",
        }
        for case_name, meta in verdicts.items():
            assert "category" in meta, f"{case_name} missing 'category'"
            assert (
                meta["category"] in valid_categories
            ), f"{case_name} has invalid category: {meta['category']}"

    def test_every_case_has_platforms(self, verdicts: dict) -> None:
        valid_platforms = {"linux", "macos", "windows"}
        for case_name, meta in verdicts.items():
            platforms = meta.get("platforms", [])
            assert len(platforms) > 0, f"{case_name} missing 'platforms'"
            for p in platforms:
                assert p in valid_platforms, f"{case_name} has invalid platform: {p}"

    def test_every_case_has_abi_and_api_flags(self, verdicts: dict) -> None:
        for case_name, meta in verdicts.items():
            assert "abi_break" in meta, f"{case_name} missing 'abi_break'"
            assert "api_break" in meta, f"{case_name} missing 'api_break'"
            assert isinstance(meta["abi_break"], bool)
            assert isinstance(meta["api_break"], bool)


# ---------------------------------------------------------------------------
# 2. Directory ↔ ground_truth sync
# ---------------------------------------------------------------------------


class TestDirectorySync:
    """Ensure every case directory has a ground_truth entry and vice versa."""

    def test_every_directory_has_ground_truth_entry(self, verdicts: dict) -> None:
        """Every examples/caseXX_* directory must have a ground_truth entry."""
        missing = []
        for d in sorted(EXAMPLES_DIR.iterdir()):
            if d.is_dir() and d.name.startswith("case"):
                if d.name not in verdicts:
                    missing.append(d.name)
        assert not missing, f"Directories without ground_truth entries: {missing}"

    def test_every_ground_truth_entry_has_directory(self, verdicts: dict) -> None:
        """Every ground_truth entry must have a matching examples/ directory."""
        missing = []
        for case_name in verdicts:
            case_dir = EXAMPLES_DIR / case_name
            if not case_dir.is_dir():
                missing.append(case_name)
        assert not missing, f"Ground truth entries without directories: {missing}"


# ---------------------------------------------------------------------------
# 3. ChangeKind registry consistency
# ---------------------------------------------------------------------------


class TestRegistryConsistency:
    """Verify expected_kinds reference valid ChangeKind values."""

    def test_expected_kinds_are_valid_changekind_values(
        self, verdicts: dict
    ) -> None:
        """All expected_kinds must be valid ChangeKind enum values."""
        valid_kinds = {ck.value for ck in ChangeKind}
        invalid = []
        for case_name, meta in verdicts.items():
            for kind in meta.get("expected_kinds", []):
                if kind not in valid_kinds:
                    invalid.append((case_name, kind))
        assert not invalid, f"Invalid expected_kinds: {invalid}"

    def test_expected_absent_kinds_are_valid(self, verdicts: dict) -> None:
        """All expected_absent_kinds must be valid ChangeKind enum values."""
        valid_kinds = {ck.value for ck in ChangeKind}
        invalid = []
        for case_name, meta in verdicts.items():
            for kind in meta.get("expected_absent_kinds", []):
                if kind not in valid_kinds:
                    invalid.append((case_name, kind))
        assert not invalid, f"Invalid expected_absent_kinds: {invalid}"

    def test_expected_kinds_no_overlap_with_absent(self, verdicts: dict) -> None:
        """expected_kinds and expected_absent_kinds must not overlap."""
        overlaps = []
        for case_name, meta in verdicts.items():
            present = set(meta.get("expected_kinds", []))
            absent = set(meta.get("expected_absent_kinds", []))
            overlap = present & absent
            if overlap:
                overlaps.append((case_name, overlap))
        assert not overlaps, f"Overlapping expected/absent kinds: {overlaps}"


# ---------------------------------------------------------------------------
# 4. Verdict ↔ category consistency
# ---------------------------------------------------------------------------

VERDICT_TO_CATEGORIES = {
    "BREAKING": {"breaking"},
    "API_BREAK": {"api_break"},
    "COMPATIBLE_WITH_RISK": {"risk"},
    "COMPATIBLE": {"addition", "quality"},
    "NO_CHANGE": {"no_change"},
}


class TestVerdictCategoryAlignment:
    """Verify verdict and category fields are logically consistent."""

    def test_verdict_matches_category(self, verdicts: dict) -> None:
        mismatches = []
        for case_name, meta in verdicts.items():
            # Bundle cases (ADR-023) carry expected=null and category=bundle
            # by design — the aggregate verdict lives in expected_combined_verdict.
            if meta.get("category") == "bundle":
                continue
            verdict = meta["expected"]
            category = meta["category"]
            allowed = VERDICT_TO_CATEGORIES.get(verdict, set())
            if category not in allowed:
                mismatches.append(
                    f"{case_name}: verdict={verdict}, category={category}, "
                    f"expected one of {allowed}"
                )
        assert not mismatches, "Verdict/category mismatches:\n" + "\n".join(
            mismatches
        )

    def test_breaking_cases_have_abi_or_api_break(self, verdicts: dict) -> None:
        """BREAKING cases should have abi_break=true or api_break=true."""
        violations = []
        for case_name, meta in verdicts.items():
            if meta["expected"] == "BREAKING":
                if not meta["abi_break"] and not meta["api_break"]:
                    violations.append(case_name)
        assert not violations, (
            f"BREAKING cases without abi_break or api_break: {violations}"
        )

    def test_no_change_cases_have_no_breaks(self, verdicts: dict) -> None:
        """NO_CHANGE cases should have abi_break=false and api_break=false."""
        violations = []
        for case_name, meta in verdicts.items():
            if meta["expected"] == "NO_CHANGE":
                if meta["abi_break"] or meta["api_break"]:
                    violations.append(case_name)
        assert not violations, (
            f"NO_CHANGE cases with break flags set: {violations}"
        )


# ---------------------------------------------------------------------------
# 5. Registry ↔ expected_kinds verdict alignment
# ---------------------------------------------------------------------------


class TestExpectedKindsVerdictAlignment:
    """Verify expected_kinds are consistent with the case's expected verdict."""

    def test_breaking_cases_have_at_least_one_breaking_kind(
        self, verdicts: dict
    ) -> None:
        """BREAKING cases with expected_kinds should include at least one
        kind whose default_verdict is BREAKING.

        Note: Some BREAKING cases list only non-BREAKING expected_kinds
        because additional BREAKING kinds are detected at runtime (e.g.
        case35_field_rename lists field_renamed=API_BREAK, but the
        comparison engine also detects type_field_removed=BREAKING).
        These are documented exceptions.
        """
        breaking_kind_values = {
            e.kind
            for e in REGISTRY.entries.values()
            if e.default_verdict == Verdict.BREAKING
        }
        # Cases where the listed expected_kinds are subset-checks and
        # the BREAKING verdict comes from additional detected kinds.
        KNOWN_EXCEPTIONS = {
            "case35_field_rename",  # field_renamed=API_BREAK; BREAKING from type_field_removed at runtime
        }
        issues = []
        for case_name, meta in verdicts.items():
            if meta["expected"] != "BREAKING":
                continue
            if case_name in KNOWN_EXCEPTIONS:
                continue
            kinds = meta.get("expected_kinds", [])
            if not kinds:
                continue  # no kinds specified — can't validate
            if not any(k in breaking_kind_values for k in kinds):
                issues.append(
                    f"{case_name}: expected_kinds={kinds} but none are BREAKING"
                )
        assert not issues, "\n".join(issues)

    def test_compatible_cases_have_no_breaking_expected_kinds(
        self, verdicts: dict
    ) -> None:
        """COMPATIBLE/NO_CHANGE cases should not list BREAKING change kinds."""
        breaking_kind_values = {
            e.kind
            for e in REGISTRY.entries.values()
            if e.default_verdict == Verdict.BREAKING
        }
        issues = []
        for case_name, meta in verdicts.items():
            if meta["expected"] not in ("COMPATIBLE", "NO_CHANGE"):
                continue
            kinds = meta.get("expected_kinds", [])
            breaking_found = [k for k in kinds if k in breaking_kind_values]
            if breaking_found:
                issues.append(
                    f"{case_name}: COMPATIBLE but expected_kinds includes "
                    f"BREAKING kinds: {breaking_found}"
                )
        assert not issues, "\n".join(issues)


# ---------------------------------------------------------------------------
# 6. Case count and coverage statistics
# ---------------------------------------------------------------------------


class TestCoverageSummary:
    """Enforce minimum coverage thresholds for example case metadata."""

    def test_total_case_count(self, verdicts: dict) -> None:
        """Verify we have the expected number of cases."""
        count = len(verdicts)
        assert count >= 62, f"Expected at least 62 cases, got {count}"

    def test_verdict_distribution(self, verdicts: dict) -> None:
        """Print verdict distribution for visibility."""
        distribution: dict[str, int] = {}
        for meta in verdicts.values():
            v = meta["expected"]
            distribution[v] = distribution.get(v, 0) + 1

        # Verify we have cases in every verdict bucket
        for verdict in ["BREAKING", "API_BREAK", "COMPATIBLE_WITH_RISK",
                        "COMPATIBLE", "NO_CHANGE"]:
            assert distribution.get(verdict, 0) > 0, (
                f"No cases with verdict {verdict}"
            )

    def test_platform_coverage(self, verdicts: dict) -> None:
        """Verify platform coverage counts."""
        platform_counts: dict[str, int] = {}
        for meta in verdicts.values():
            for p in meta.get("platforms", []):
                platform_counts[p] = platform_counts.get(p, 0) + 1

        # Linux should cover all cases
        assert platform_counts.get("linux", 0) == len(verdicts), (
            "Not all cases support Linux"
        )

    def test_changekind_coverage_in_examples(self, verdicts: dict) -> None:
        """Count how many distinct ChangeKinds are exercised by examples."""
        exercised: set[str] = set()
        for meta in verdicts.values():
            exercised.update(meta.get("expected_kinds", []))
            exercised.update(meta.get("expected_absent_kinds", []))

        total_kinds = len(REGISTRY)
        # At least 10% of change kinds should be exercised
        assert len(exercised) >= total_kinds * 0.10, (
            f"Only {len(exercised)} of {total_kinds} ChangeKinds exercised "
            f"in example cases"
        )

    def test_known_gap_cases_documented(self, verdicts: dict) -> None:
        """Every case with a known_gap should have a description."""
        for case_name, meta in verdicts.items():
            if "known_gap" in meta:
                assert len(meta["known_gap"]) > 10, (
                    f"{case_name} has a known_gap that is too short"
                )
