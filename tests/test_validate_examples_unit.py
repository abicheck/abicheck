"""Unit tests for the validate_examples CLI harness (PR #63).

Does NOT require a full compile/run of examples — tests harness logic only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import tests.validate_examples as ve  # noqa: E402
from abicheck.source_smoke import SourceSmokeResult  # noqa: E402
from tests.validate_examples import (  # noqa: E402
    ARTIFACT_VARIANTS,
    DEFAULT_ARTIFACT_VARIANT,
    CaseResult,
    _build_info_applies,
    _build_info_path,
    _build_with_cmake,
    _check_case_preconditions,
    _embedded_present_layers,
    _evaluate_verdict,
    _gap_applies,
    _json_payload,
    _normalize_verdict,
    _result_to_json,
    _run_source_smoke,
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
_EXPECTED_CASE_COUNT = 181


def test_source_smoke_run_mode_skips_without_trusted_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ABICHECK_TRUSTED_SOURCE_SMOKE_RUN", raising=False)
    monkeypatch.setattr(ve, "_find_compiler", lambda _cxx: "c++")
    run = patch.object(ve, "run_source_smoke")

    with run as mock_run:
        result = _run_source_smoke(
            "case", {"source_smoke": {"mode": "run"}}, tmp_path, tmp_path, "BREAKING"
        )

    assert result is not None and result.status == "SKIP"
    assert "ABICHECK_TRUSTED_SOURCE_SMOKE_RUN=1" in result.message
    mock_run.assert_not_called()


def test_source_smoke_run_mode_executes_with_trusted_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ABICHECK_TRUSTED_SOURCE_SMOKE_RUN", "1")
    monkeypatch.setattr(ve, "_find_compiler", lambda _cxx: "c++")
    smoke_result = SourceSmokeResult(ok=True, failures=(), proof="trusted proof")

    with patch.object(ve, "run_source_smoke", return_value=smoke_result) as mock_run:
        result = _run_source_smoke(
            "case", {"source_smoke": {"mode": "run"}}, tmp_path, tmp_path, "BREAKING"
        )

    assert result is not None and result.status == "PASS"
    assert result.message == "trusted proof"
    assert mock_run.call_args.kwargs["allow_run"] is True


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


class TestKnownGapToolchainScope:
    """Producer-scoped known_gap must only excuse a mismatch under that producer.

    Guards the clang-lane gating integrity flagged in review: case64's GCC-only
    gap must NOT mask a clang regression, and case103's clang-only gap must NOT
    mask a gcc regression.
    """

    def test_unscoped_gap_applies_everywhere(self):
        # Patch the resolved family so the test does not depend on which
        # compilers happen to be installed in the test environment.
        for fam in ("gcc", "clang", ""):
            with patch.object(ve, "_toolchain_family", lambda _is_cpp, f=fam: f):
                assert _gap_applies({"known_gap": "x"}, False) is True

    def test_gcc_scoped_gap_only_under_gcc(self):
        with patch.object(ve, "_toolchain_family", lambda _is_cpp: "gcc"):
            assert _gap_applies({"known_gap_toolchains": ["gcc"]}, False) is True
        with patch.object(ve, "_toolchain_family", lambda _is_cpp: "clang"):
            assert _gap_applies({"known_gap_toolchains": ["gcc"]}, False) is False

    def test_clang_scoped_gap_only_under_clang(self):
        with patch.object(ve, "_toolchain_family", lambda _is_cpp: "clang"):
            assert _gap_applies({"known_gap_toolchains": ["clang"]}, True) is True
        with patch.object(ve, "_toolchain_family", lambda _is_cpp: "gcc"):
            assert _gap_applies({"known_gap_toolchains": ["clang"]}, True) is False

    def test_platform_scoped_gap_only_on_that_platform(self):
        with patch.object(ve, "CURRENT_PLATFORM", "macos"):
            assert _gap_applies({"known_gap_platforms": ["macos"]}, True) is True
        with patch.object(ve, "CURRENT_PLATFORM", "linux"):
            assert _gap_applies({"known_gap_platforms": ["macos"]}, True) is False

    def test_variant_scoped_gap_only_at_that_evidence_depth(self):
        entry = {"known_gap_variants": ["release-headers", "stripped-headers"]}
        assert _gap_applies(entry, True, "release-headers") is True
        assert _gap_applies(entry, True, "stripped-headers") is True
        assert _gap_applies(entry, True, "build-source") is False

    def test_toolchain_and_platform_scopes_both_apply(self):
        entry = {
            "known_gap_toolchains": ["clang"],
            "known_gap_platforms": ["macos"],
        }
        with (
            patch.object(ve, "_toolchain_family", lambda _is_cpp: "clang"),
            patch.object(ve, "CURRENT_PLATFORM", "linux"),
        ):
            assert _gap_applies(entry, True) is False
        with (
            patch.object(ve, "_toolchain_family", lambda _is_cpp: "gcc"),
            patch.object(ve, "CURRENT_PLATFORM", "macos"),
        ):
            assert _gap_applies(entry, True) is False
        with (
            patch.object(ve, "_toolchain_family", lambda _is_cpp: "clang"),
            patch.object(ve, "CURRENT_PLATFORM", "macos"),
        ):
            assert _gap_applies(entry, True) is True

    def test_real_cases_are_scoped(self):
        gt = json.loads(_GROUND_TRUTH.read_text())["verdicts"]
        assert gt["case64_calling_convention_changed"]["known_gap_toolchains"] == ["gcc"]
        assert gt["case103_toolchain_flag_drift"]["known_gap_toolchains"] == ["clang"]
        case98 = gt["case98_cxx_standard_floor_raised"]
        assert case98["expected"] == "COMPATIBLE_WITH_RISK"
        assert case98["build_info_variants"] == ["build-source"]
        assert set(case98["known_gap_variants"]) == {
            "debug-headers",
            "release-headers",
            "stripped-headers",
        }


class TestBuildInfoVariantScope:
    def test_unscoped_build_info_applies_to_every_variant(self):
        entry = {"build_info": True}
        assert _build_info_applies(entry, "debug-headers") is True
        assert _build_info_applies(entry, "build-source") is True

    def test_scoped_build_info_only_applies_to_declared_variants(self):
        entry = {"build_info": True, "build_info_variants": ["build-source"]}
        assert _build_info_applies(entry, "debug-headers") is False
        assert _build_info_applies(entry, "build-source") is True


class TestRunSourceSmoke:
    """source_smoke's own optional "platforms" scope (distinct from the
    case-level one) — some consumer-runtime-corruption proofs assume Itanium
    C++ ABI base-class layout evolution and don't reproduce under MSVC's own
    ABI rules (case43: the same source change doesn't corrupt memory in an
    observable way there)."""

    def test_no_source_smoke_declared_returns_none(self, tmp_path):
        assert _run_source_smoke("caseX", {}, tmp_path, tmp_path, None) is None

    def test_platform_not_listed_falls_through_to_verdict_check(self, tmp_path):
        # None (not a CaseResult) — same as "no source_smoke declared" for
        # this platform, so run_case() still reaches the build/dump/compare
        # path for a case that otherwise supports this platform (Codex
        # review: a SKIP CaseResult would short-circuit run_case() before
        # the verdict check ever runs, silently un-checking the platform).
        entry = {
            "source_smoke": {
                "platforms": ["linux", "macos"],
                "v1": {"code": "int main() { return 0; }\n"},
            }
        }
        with patch.object(ve, "CURRENT_PLATFORM", "windows"):
            result = _run_source_smoke("caseX", entry, tmp_path, tmp_path, "BREAKING")
        assert result is None

    @pytest.mark.integration
    def test_platform_listed_runs_for_real(self, tmp_path):
        entry = {
            "source_smoke": {
                "platforms": ["linux"],
                "proof": "trivial",
                "v1": {"code": "int main() { return 0; }\n"},
                "v2": {"code": "int main() { return 0; }\n"},
            }
        }
        with patch.object(ve, "CURRENT_PLATFORM", "linux"):
            result = _run_source_smoke("caseX", entry, tmp_path, tmp_path, "NO_CHANGE")
        assert result.status in {"PASS", "SKIP"}
        if result.status == "SKIP":
            pytest.skip(result.message)
        assert result.message == "trivial"

    @pytest.mark.integration
    def test_no_platforms_key_runs_on_every_platform(self, tmp_path):
        entry = {
            "source_smoke": {
                "proof": "unscoped",
                "v1": {"code": "int main() { return 0; }\n"},
                "v2": {"code": "int main() { return 0; }\n"},
            }
        }
        with patch.object(ve, "CURRENT_PLATFORM", "windows"):
            result = _run_source_smoke("caseX", entry, tmp_path, tmp_path, "NO_CHANGE")
        assert result.status != "SKIP" or "no compiler" in (result.message or "")

    def test_case43_is_scoped_off_windows(self):
        gt = json.loads(_GROUND_TRUTH.read_text())["verdicts"]
        assert gt["case43_base_class_member_added"]["source_smoke"]["platforms"] == [
            "linux",
            "macos",
        ]


class TestBuildWithCmake:
    """A CMake WINDOWS_EXPORT_ALL_SYMBOLS + Ninja generator defect (confirmed
    deterministic — reproduces identically whether the v1/v2 targets build in
    parallel or serially) makes a per-target exports.def invisible to that
    target's own link step (LNK1104). A real toolchain limitation, not an
    abicheck defect — _build_with_cmake converts exactly this symptom to a
    clean SKIP rather than a hard ERROR."""

    def _mock_run(self, monkeypatch, *, build_stderr, build_returncode=1):
        calls = []

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            is_build = "--build" in cmd
            returncode = build_returncode if is_build else 0
            stderr = build_stderr if is_build else ""
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

        monkeypatch.setattr(ve.subprocess, "run", _fake_run)
        return calls

    def test_ninja_exports_def_defect_is_skip_not_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ve.shutil, "which", lambda t: f"/usr/bin/{t}")
        monkeypatch.setattr(ve.sys, "platform", "win32")
        self._mock_run(
            monkeypatch,
            build_stderr=(
                "LINK : fatal error LNK1104: cannot open file "
                "'case97\\CMakeFiles\\case97_v1.dir\\.\\exports.def'"
            ),
        )
        case_dir = tmp_path / "case97"
        case_dir.mkdir()
        v1_lib, v2_lib, err = _build_with_cmake(case_dir, tmp_path / "build")
        assert v1_lib is None
        assert v2_lib is None
        assert err.startswith("SKIP:")
        assert "exports.def" in err

    def test_other_ninja_build_failures_still_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ve.shutil, "which", lambda t: f"/usr/bin/{t}")
        monkeypatch.setattr(ve.sys, "platform", "win32")
        self._mock_run(
            monkeypatch,
            build_stderr="error C2065: 'foo': undeclared identifier",
        )
        case_dir = tmp_path / "case_other"
        case_dir.mkdir()
        v1_lib, v2_lib, err = _build_with_cmake(case_dir, tmp_path / "build")
        assert v1_lib is None
        assert v2_lib is None
        assert not err.startswith("SKIP:")
        assert err.startswith("cmake build failed:")

    def test_non_windows_exports_def_failure_still_error(self, tmp_path, monkeypatch):
        # The same symptom text on a non-Windows/non-Ninja build isn't this
        # specific toolchain defect — don't silently swallow it there too.
        monkeypatch.setattr(ve.shutil, "which", lambda t: f"/usr/bin/{t}")
        monkeypatch.setattr(ve.sys, "platform", "linux")
        self._mock_run(
            monkeypatch,
            build_stderr="LINK : fatal error LNK1104: cannot open file 'exports.def'",
        )
        case_dir = tmp_path / "case_linux"
        case_dir.mkdir()
        v1_lib, v2_lib, err = _build_with_cmake(case_dir, tmp_path / "build")
        assert not err.startswith("SKIP:")
        assert err.startswith("cmake build failed:")


class TestCasePreconditions:
    def test_architecture_filter_skips_unsupported_machine(self):
        with (
            patch.object(ve, "CURRENT_PLATFORM", "linux"),
            patch.object(ve, "CURRENT_ARCHITECTURE", "aarch64"),
        ):
            result = _check_case_preconditions(
                "case64",
                {
                    "expected": "BREAKING",
                    "platforms": ["linux"],
                    "architectures": ["x86_64"],
                },
            )

        assert result is not None
        assert result.status == "SKIP"
        assert "requires ['x86_64']" in result.message


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

    def test_real_build_info_cases_have_explicit_or_generated_build_context(self) -> None:
        # Every ground_truth case flagged build_info must either ship both
        # per-side compile DBs or have a CMake fixture whose generated
        # compile_commands.json supplies the real build flags.
        gt = json.loads(_GROUND_TRUTH.read_text())["verdicts"]
        examples_dir = _GROUND_TRUTH.parent
        bi_cases = [k for k, v in gt.items() if v.get("build_info")]
        assert bi_cases, "expected at least one build_info example case"
        for name in bi_cases:
            case_dir = examples_dir / name
            has_explicit = (
                _build_info_path(case_dir, "v1", True) is not None
                and _build_info_path(case_dir, "v2", True) is not None
            )
            has_generated = (case_dir / "CMakeLists.txt").exists()
            assert has_explicit or has_generated, name


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
