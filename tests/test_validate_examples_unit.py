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
    ARTIFACT_VARIANTS,
    DEFAULT_ARTIFACT_VARIANT,
    CaseResult,
    _build_info_path,
    _embedded_present_layers,
    _evaluate_verdict,
    _json_payload,
    _normalize_verdict,
    _result_to_json,
    _selected_variants,
    _source_layers_for_result,
    _sources_path,
    _write_source_compile_db,
    main,
)

# ── ground_truth.json paths ───────────────────────────────────────────────

_GROUND_TRUTH = Path(__file__).parent.parent / "examples" / "ground_truth.json"
_VALID_CATEGORIES = frozenset(
    {"breaking", "addition", "quality", "no_change", "api_break", "risk", "bundle"}
)
_VALID_VERDICTS = frozenset(
    {"BREAKING", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "NO_CHANGE", "API_BREAK"}
)
_EXPECTED_CASE_COUNT = 134


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

    def test_quality_risk_can_satisfy_compatible_expected(self) -> None:
        result = _evaluate_verdict(
            "case103",
            "COMPATIBLE",
            "COMPATIBLE_WITH_RISK",
            None,
            allow_risk_for_compatible=True,
        )

        assert result.status == "PASS"


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


# ── L3 build-info detection ───────────────────────────────────────────────


class TestBuildInfoPath:
    """_build_info_path opts a case into L3 build-evidence comparison."""

    def test_none_case_dir_returns_none(self) -> None:
        assert _build_info_path(None, "v1", True) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert _build_info_path(tmp_path, "v1", True) is None

    def test_present_file_returned(self, tmp_path: Path) -> None:
        (tmp_path / "v1.compile_commands.json").write_text("[]")
        assert _build_info_path(tmp_path, "v1", True) == tmp_path / "v1.compile_commands.json"

    def test_opt_out_ignores_present_file(self, tmp_path: Path) -> None:
        # Without the ground_truth build_info flag, a stray compile DB must not
        # silently upgrade the case to L3.
        (tmp_path / "v1.compile_commands.json").write_text("[]")
        assert _build_info_path(tmp_path, "v1", False) is None
        assert _build_info_path(tmp_path, "v1") is None  # default opt-out

    def test_per_side_independent(self, tmp_path: Path) -> None:
        (tmp_path / "v2.compile_commands.json").write_text("[]")
        assert _build_info_path(tmp_path, "v1", True) is None
        assert _build_info_path(tmp_path, "v2", True) is not None

    def test_real_build_info_cases_ship_both_sides(self) -> None:
        # Every ground_truth case flagged build_info must ship both per-side
        # compile DBs so the harness actually exercises the L3 diff.
        gt = json.loads(_GROUND_TRUTH.read_text())["verdicts"]
        examples_dir = _GROUND_TRUTH.parent
        bi_cases = [k for k, v in gt.items() if v.get("build_info")]
        assert bi_cases, "expected at least one build_info example case"
        for name in bi_cases:
            case_dir = examples_dir / name
            assert _build_info_path(case_dir, "v1", True) is not None, name
            assert _build_info_path(case_dir, "v2", True) is not None, name


class TestSourcesPath:
    """_sources_path opts a case into L4/L5 source-replay comparison."""

    def test_none_case_dir_returns_none(self) -> None:
        assert _sources_path(None, "v1", True) is None

    def test_missing_dir_returns_none(self, tmp_path: Path) -> None:
        assert _sources_path(tmp_path, "v1", True) is None

    def test_present_dir_returned(self, tmp_path: Path) -> None:
        (tmp_path / "v1.sources").mkdir()
        assert _sources_path(tmp_path, "v1", True) == tmp_path / "v1.sources"

    def test_opt_out_ignores_present_dir(self, tmp_path: Path) -> None:
        (tmp_path / "v1.sources").mkdir()
        assert _sources_path(tmp_path, "v1", False) is None
        assert _sources_path(tmp_path, "v1") is None  # default opt-out

    def test_a_file_named_sources_is_not_a_tree(self, tmp_path: Path) -> None:
        (tmp_path / "v1.sources").write_text("not a dir")
        assert _sources_path(tmp_path, "v1", True) is None

    def test_real_sources_cases_ship_both_sides(self) -> None:
        # Every ground_truth case flagged sources must ship both per-side trees.
        gt = json.loads(_GROUND_TRUTH.read_text())["verdicts"]
        examples_dir = _GROUND_TRUTH.parent
        for name, v in gt.items():
            if not v.get("sources"):
                continue
            case_dir = examples_dir / name
            assert _sources_path(case_dir, "v1", True) is not None, name
            assert _sources_path(case_dir, "v2", True) is not None, name


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
            name: str,
            entry: dict,
            tmp_base: Path,
            fail_fast: bool = False,
            variant: str = DEFAULT_ARTIFACT_VARIANT,
        ) -> CaseResult:
            captured.append(name)
            return CaseResult(name, "PASS", entry.get("expected"), entry.get("expected"), "", variant)

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


class TestArtifactVariants:
    def test_default_variant_selector(self) -> None:
        assert _selected_variants(DEFAULT_ARTIFACT_VARIANT) == (DEFAULT_ARTIFACT_VARIANT,)

    def test_all_variant_selector(self) -> None:
        assert _selected_variants("all") == ARTIFACT_VARIANTS

    def test_main_passes_selected_variant(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tests.validate_examples as ve

        gt_file = _make_gt(
            tmp_path,
            {"case01": {"expected": "BREAKING", "category": "breaking"}},
        )
        monkeypatch.setattr(ve, "GROUND_TRUTH", gt_file)
        monkeypatch.setattr(ve, "EXAMPLES_DIR", tmp_path)
        monkeypatch.setattr(shutil, "which", lambda t: f"/usr/bin/{t}")

        captured: list[str] = []

        def fake_run(
            name: str,
            entry: dict,
            tmp_base: Path,
            fail_fast: bool = False,
            variant: str = DEFAULT_ARTIFACT_VARIANT,
        ) -> CaseResult:
            captured.append(variant)
            return CaseResult(name, "PASS", entry.get("expected"), entry.get("expected"), "", variant)

        with patch.object(ve, "run_case", side_effect=fake_run):
            rc = main(["--artifact-variant", "stripped-headers", "--json"])

        assert rc == 0
        assert captured == ["stripped-headers"]

    def test_source_compile_db_preserves_cmake_flags(self, tmp_path: Path) -> None:
        src = tmp_path / "case104" / "v1.cpp"
        src.parent.mkdir()
        src.write_text("int f() { return 0; }\n")
        cmake_build = tmp_path / "cmake_build"
        cmake_build.mkdir()
        compile_db = cmake_build / "compile_commands.json"
        compile_db.write_text(json.dumps([{
            "directory": str(cmake_build),
            "file": str(src),
            "arguments": [
                "c++", "-D_GLIBCXX_USE_CXX11_ABI=0", "-std=c++20",
                "-c", str(src),
            ],
        }]))

        out = _write_source_compile_db(
            tmp_path,
            "old",
            src,
            src.parent,
            fallback_compiler="c++",
            target_suffix="v1",
        )

        entries = json.loads(out.read_text())
        assert entries[0]["arguments"] == [
            "c++", "-D_GLIBCXX_USE_CXX11_ABI=0", "-std=c++20",
            "-c", str(src),
        ]

    def test_result_json_includes_remeasurement_metadata(self) -> None:
        result = CaseResult(
            "case04_no_change",
            "PASS",
            "NO_CHANGE",
            "NO_CHANGE",
            "",
            "build-source",
            1.25,
        )

        payload = _result_to_json(result)

        assert payload["component"] == "synthetic-example"
        assert payload["case_id"] == "case04_no_change"
        assert payload["mode"] == "build-source"
        assert payload["source_layers"] == ["L0", "L1", "L2", "L3", "L4", "L5"]
        assert payload["evidence_asymmetry"] == "symmetric"
        assert payload["seconds"] == 1.25

    def test_source_layers_reflect_actual_headers(self, tmp_path: Path) -> None:
        header = tmp_path / "v1.h"
        header.write_text("int f(void);\n")
        pack = tmp_path / "pack"
        pack.mkdir()

        assert _source_layers_for_result(
            "debug-headers",
            v1_hdr=header,
            v2_hdr=None,
            old_build_source=None,
            new_build_source=None,
        ) == ("L0", "L1")
        assert _source_layers_for_result(
            "build-source",
            v1_hdr=header,
            v2_hdr=header,
            old_build_source=pack,
            new_build_source=pack,
        ) == ("L0", "L1", "L2", "L3", "L4", "L5")

    def test_source_layers_reflect_inline_sources(self, tmp_path: Path) -> None:
        # ground_truth `sources: true` runs `dump --sources`, folding L3/L4/L5
        # inline — the result must report them, not under-count as L0/L2 (Codex).
        header = tmp_path / "v1.h"
        header.write_text("int f(void);\n")
        inline = _source_layers_for_result(
            DEFAULT_ARTIFACT_VARIANT,
            v1_hdr=header,
            v2_hdr=header,
            old_build_source=None,
            new_build_source=None,
            sources=True,
        )
        assert set(inline) >= {"L0", "L2", "L3", "L4", "L5"}
        # `--build-info` (without --sources) supplies L3 but not L4/L5.
        bi = _source_layers_for_result(
            DEFAULT_ARTIFACT_VARIANT,
            v1_hdr=header,
            v2_hdr=header,
            old_build_source=None,
            new_build_source=None,
            build_info=True,
        )
        assert "L3" in bi and "L4" not in bi and "L5" not in bi
        # No double-count when build-source pack and inline --sources coincide.
        pack2 = tmp_path / "pack2"
        pack2.mkdir()
        assert _source_layers_for_result(
            "build-source",
            v1_hdr=header,
            v2_hdr=header,
            old_build_source=pack2,
            new_build_source=pack2,
            sources=True,
        ) == ("L0", "L1", "L2", "L3", "L4", "L5")

    def test_embedded_present_layers_reads_real_coverage(self, tmp_path: Path) -> None:
        # Codex: a degraded `dump --sources` embeds source_abi coverage as
        # partial/not_collected — only `present` rows count as real L4/L5.
        snap = tmp_path / "snap.json"
        snap.write_text(json.dumps({"build_source": {"manifest": {"coverage": [
            {"layer": "L3_build", "status": "present"},
            {"layer": "L4_source_abi", "status": "present"},
            {"layer": "L5_source_graph", "status": "partial"},
        ]}}}), encoding="utf-8")
        assert _embedded_present_layers(snap) == {"L3", "L4"}

        # No build_source / missing file → no layers claimed.
        bare = tmp_path / "bare.json"
        bare.write_text(json.dumps({"library": "l"}), encoding="utf-8")
        assert _embedded_present_layers(bare) == set()
        assert _embedded_present_layers(tmp_path / "nonexistent.json") == set()

    def test_json_payload_includes_run_metadata(self) -> None:
        result = CaseResult("case01", "FAIL", "BREAKING", "NO_CHANGE", "mismatch")

        payload = _json_payload(
            [result],
            names=["case01"],
            variants=("debug-headers",),
            argv=["case01", "--json"],
            total_ground_truth_cases=129,
        )

        assert payload["schema_version"] == "validate_examples.v2"
        assert payload["runner"] == "tests/validate_examples.py"
        assert payload["selected_cases"] == 1
        assert payload["ground_truth_cases"] == 129
        assert payload["artifact_variants"] == ["debug-headers"]
        assert payload["summary"] == {"FAIL": 1}
