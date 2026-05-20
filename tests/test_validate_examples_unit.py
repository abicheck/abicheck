"""Unit tests for the validate_examples CLI harness (PR #63).

Does NOT require a full compile/run of examples — tests harness logic only.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.validate_examples import (  # noqa: E402
    CaseResult,
    _normalize_verdict,
    main,
)

# ── ground_truth.json paths ───────────────────────────────────────────────

_GROUND_TRUTH = Path(__file__).parent.parent / "examples" / "ground_truth.json"
_VALID_CATEGORIES = frozenset(
    {"breaking", "addition", "quality", "no_change", "api_break", "risk"}
)
_VALID_VERDICTS = frozenset(
    {"BREAKING", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "NO_CHANGE", "API_BREAK"}
)
_EXPECTED_CASE_COUNT = 77


# ── _normalize_verdict ────────────────────────────────────────────────────


class TestNormalizeVerdict:
    """_normalize_verdict normalizes verdicts for cross-check comparison.

    API_BREAK and COMPATIBLE are treated as equivalent (both normalize to
    COMPATIBLE) because the checker may return either depending on header
    availability. All other verdicts are preserved as-is.
    """

    _EXPECTED_NORMALIZED = {
        "API_BREAK": "COMPATIBLE",
        "BREAKING": "BREAKING",
        "COMPATIBLE": "COMPATIBLE",
        "COMPATIBLE_WITH_RISK": "COMPATIBLE_WITH_RISK",
        "NO_CHANGE": "NO_CHANGE",
    }

    @pytest.mark.parametrize("verdict", sorted(_VALID_VERDICTS))
    def test_normalizes_verdict(self, verdict: str) -> None:
        assert _normalize_verdict(verdict) == self._EXPECTED_NORMALIZED[verdict]


# ── ground_truth.json structural integrity ────────────────────────────────


class TestGroundTruthIntegrity:
    """ground_truth.json must be well-formed and complete."""

    @pytest.fixture(scope="class")
    def verdicts(self) -> dict:
        return json.loads(_GROUND_TRUTH.read_text())["verdicts"]

    def test_has_expected_case_count(self, verdicts: dict) -> None:
        assert len(verdicts) == _EXPECTED_CASE_COUNT

    def test_all_entries_have_category(self, verdicts: dict) -> None:
        missing = [k for k, v in verdicts.items() if "category" not in v]
        assert not missing

    def test_all_categories_are_valid(self, verdicts: dict) -> None:
        invalid = {
            k: v["category"]
            for k, v in verdicts.items()
            if v.get("category") not in _VALID_CATEGORIES
        }
        assert not invalid

    def test_all_verdicts_are_valid(self, verdicts: dict) -> None:
        invalid = {
            k: v["expected"]
            for k, v in verdicts.items()
            if v.get("expected") not in _VALID_VERDICTS
            and v.get("expected") is not None
        }
        assert not invalid


# ── CLI entry-point ───────────────────────────────────────────────────────


def _make_gt(tmp_path: Path, cases: dict) -> Path:
    """Write a minimal ground_truth.json and return its path."""
    gt_file = tmp_path / "ground_truth.json"
    gt_file.write_text(
        json.dumps({"version": "1", "description": "", "verdicts": cases})
    )
    return gt_file


class TestMainCategoryFilter:
    """--category must restrict processed cases to the matching category."""

    def test_filters_out_other_categories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tests.validate_examples as ve

        gt_file = _make_gt(
            tmp_path,
            {
                "case_breaking": {"expected": "BREAKING", "category": "breaking"},
                "case_compatible": {"expected": "COMPATIBLE", "category": "compatible"},
            },
        )
        monkeypatch.setattr(ve, "GROUND_TRUTH", gt_file)
        monkeypatch.setattr(ve, "EXAMPLES_DIR", tmp_path)
        monkeypatch.setattr(shutil, "which", lambda t: f"/usr/bin/{t}")

        captured: list[str] = []

        def fake_run(
            name: str, entry: dict, tmp_base: Path, fail_fast: bool = False
        ) -> CaseResult:
            captured.append(name)
            return CaseResult(
                name, "PASS", entry.get("expected"), entry.get("expected"), ""
            )

        with patch.object(ve, "run_case", side_effect=fake_run):
            main(["--category", "breaking", "--json"])

        assert "case_breaking" in captured
        assert "case_compatible" not in captured


class TestMainExitCodes:
    """CLI exit codes: 0=all pass, 1=failures, 2=preflight error."""

    def test_exits_0_when_all_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tests.validate_examples as ve

        gt_file = _make_gt(
            tmp_path,
            {
                "case01": {"expected": "BREAKING", "category": "breaking"},
            },
        )
        monkeypatch.setattr(ve, "GROUND_TRUTH", gt_file)
        monkeypatch.setattr(ve, "EXAMPLES_DIR", tmp_path)
        monkeypatch.setattr(shutil, "which", lambda t: f"/usr/bin/{t}")

        with patch.object(
            ve,
            "run_case",
            return_value=CaseResult("case01", "PASS", "BREAKING", "BREAKING", ""),
        ):
            rc = main(["--json"])
        assert rc == 0

    def test_exits_1_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tests.validate_examples as ve

        gt_file = _make_gt(
            tmp_path,
            {
                "case01": {"expected": "BREAKING", "category": "breaking"},
            },
        )
        monkeypatch.setattr(ve, "GROUND_TRUTH", gt_file)
        monkeypatch.setattr(ve, "EXAMPLES_DIR", tmp_path)
        monkeypatch.setattr(shutil, "which", lambda t: f"/usr/bin/{t}")

        with patch.object(
            ve,
            "run_case",
            return_value=CaseResult(
                "case01", "FAIL", "BREAKING", "COMPATIBLE", "mismatch"
            ),
        ):
            rc = main(["--json"])
        assert rc == 1

    def test_exits_2_when_tool_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _t: None)
        rc = main(["--json"])
        assert rc == 2
