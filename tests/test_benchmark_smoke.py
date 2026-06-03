"""Smoke tests for scripts/benchmark_comparison.py.

Verifies that the benchmark script imports cleanly, parses args correctly,
and that the run_abicc_dumper / run_abicc_xml helpers handle missing tools
gracefully (return SKIP instead of crashing).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


def _load_benchmark():
    """Dynamically import scripts/benchmark_comparison.py."""
    mod_name = "benchmark_comparison"
    # Unload any cached version to ensure a fresh import
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(
        mod_name,
        SCRIPTS_DIR / "benchmark_comparison.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # Must register in sys.modules BEFORE exec so @dataclass can resolve the module
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Import / parse_args ───────────────────────────────────────────────────────

def test_parse_args_defaults():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py"]):
        args = mod.parse_args()
    assert args.abicc_timeout == mod.DEFAULT_ABICC_TIMEOUT
    assert args.abicc_mode == "both"
    assert args.suite == "all"
    assert not args.skip_abicc


def test_parse_args_custom_timeout():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py", "--abicc-timeout", "60"]):
        args = mod.parse_args()
    assert args.abicc_timeout == 60


def test_parse_args_skip_abicc():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py", "--skip-abicc"]):
        args = mod.parse_args()
    assert args.skip_abicc


def test_parse_args_abicc_mode_xml():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py", "--abicc-mode", "xml"]):
        args = mod.parse_args()
    assert args.abicc_mode == "xml"


def test_parse_args_skip_compat():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py", "--skip-compat"]):
        args = mod.parse_args()
    assert args.skip_compat is True


def test_parse_args_skip_compat_default_false():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py"]):
        args = mod.parse_args()
    assert args.skip_compat is False


def test_parse_args_pinned_suite():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py", "--suite", "pinned74"]):
        args = mod.parse_args()
    assert args.suite == "pinned74"


def test_pinned_suite_matches_historical_74_cases():
    mod = _load_benchmark()
    cases = sorted(
        d.name for d in (Path(__file__).parent.parent / "examples").iterdir()
        if d.is_dir() and d.name.startswith("case")
    )
    pinned = [c for c in cases if mod.PINNED_74_CASE_RE.match(c)]

    assert len(pinned) == 74
    assert "case01_symbol_removal" in pinned
    assert "case26b_union_field_added_compatible" in pinned
    assert "case73_typedef_underlying_changed" in pinned
    assert "case74_detail_base_class_changed" not in pinned


def test_null_expected_verdict_is_unscored_unknown():
    mod = _load_benchmark()

    assert mod.EXPECTED["case84_bundle_soname_skew"] == "?"
    assert mod.EXPECTED_ABICC["case84_bundle_soname_skew"] == "?"


# ── case64 compiler selection ────────────────────────────────────────────────

def test_case64_auto_prefers_versioned_clang():
    mod = _load_benchmark()

    def fake_which(name):
        return {
            "clang-18": "/usr/bin/clang-18",
            "clang++-18": "/usr/bin/clang++-18",
        }.get(name)

    with patch("shutil.which", side_effect=fake_which):
        assert mod._first_available_tool("clang-18", "clang") == "/usr/bin/clang-18"
        assert mod._case64_toolchain_policy("case64_calling_convention_changed", "auto") == ("clang", True)


def test_case64_auto_no_clang_uses_default_toolchain():
    mod = _load_benchmark()
    with patch("shutil.which", return_value=None):
        assert mod._case64_toolchain_policy("case64_calling_convention_changed", "auto") == (None, False)


# ── Graceful SKIP when tool not present ──────────────────────────────────────

def test_run_abicc_dumper_skip_when_missing(tmp_path):
    """run_abicc_dumper returns SKIP if abi-dumper is not installed."""
    mod = _load_benchmark()
    dummy = tmp_path / "lib.so"
    dummy.touch()
    dummy_h = tmp_path / "v1.h"

    with patch("shutil.which", return_value=None):
        result = mod.run_abicc_dumper(dummy, dummy, dummy_h, dummy_h,
                                      "smoke_case", tmp_path)
    assert result.verdict == "SKIP"


def test_run_abicc_xml_skip_when_missing(tmp_path):
    """run_abicc_xml returns SKIP if abi-compliance-checker is not installed."""
    mod = _load_benchmark()
    dummy = tmp_path / "lib.so"
    dummy.touch()
    dummy_h = tmp_path / "v1.h"

    with patch("shutil.which", return_value=None):
        result = mod.run_abicc_xml(dummy, dummy, dummy_h, dummy_h,
                                   "smoke_case", tmp_path)
    assert result.verdict == "SKIP"


def test_run_abicheck_skip_when_missing(tmp_path):
    """run_abicheck returns SKIP if abicheck is not installed."""
    mod = _load_benchmark()
    dummy = tmp_path / "lib.so"
    dummy.touch()
    dummy_h = tmp_path / "v1.h"

    with patch.object(mod, "_HAS_ABICHECK", False):
        result = mod.run_abicheck(dummy, dummy, dummy_h, dummy_h,
                                  "smoke_case", tmp_path)
    assert result.verdict == "SKIP"


def test_run_abidiff_skip_when_missing(tmp_path):
    """run_abidiff returns SKIP if abidiff is not installed."""
    mod = _load_benchmark()
    dummy = tmp_path / "lib.so"
    dummy.touch()

    with patch("shutil.which", return_value=None):
        result = mod.run_abidiff(dummy, dummy, None, None, "smoke_case", tmp_path)
    assert result.verdict == "SKIP"


# ── ToolResult dataclass ──────────────────────────────────────────────────────

def test_tool_result_defaults():
    mod = _load_benchmark()
    r = mod.ToolResult(verdict="NO_CHANGE")
    assert r.verdict == "NO_CHANGE"
    assert r.changes == []
    assert r.raw_output == ""
    assert r.report_path == ""


# ── Release-pinned report metadata ────────────────────────────────────────────


class _FakeTool:
    name = "abicheck"
    expected_key = "expected"
    ms_key = "abicheck_ms"
    label = "abicheck compare"


def test_collect_metadata_shape_and_accuracy():
    mod = _load_benchmark()
    results = [
        {"case": "case01", "expected": "BREAKING", "abicheck": "BREAKING", "abicheck_ms": 5},
        {"case": "case02", "expected": "COMPATIBLE", "abicheck": "COMPATIBLE", "abicheck_ms": 4},
        {"case": "case03", "expected": "BREAKING", "abicheck": "COMPATIBLE", "abicheck_ms": 6},
        # SKIP rows must not be scored.
        {"case": "case04", "expected": "BREAKING", "abicheck": "SKIP", "abicheck_ms": 0},
    ]
    meta = mod._collect_metadata(results, [_FakeTool()], "pinned74")

    assert meta["schema"] == "abicheck-benchmark/1.0"
    assert meta["case_count"] == 4
    assert meta["suite"] == "pinned74"
    assert "abicheck_version" in meta
    assert set(meta["tool_versions"]) >= {"abidiff", "gcc", "castxml"}
    assert meta["results"] is results

    acc = meta["accuracy"]["abicheck"]
    assert acc["scored"] == 3          # SKIP excluded
    assert acc["correct"] == 2          # case03 wrong
    assert acc["pct"] == round(100 * 2 / 3, 1)


def test_ground_truth_digest_is_stable():
    mod = _load_benchmark()
    first = mod._ground_truth_digest()
    second = mod._ground_truth_digest()
    # Either None (file absent) or a stable 64-char hex digest.
    assert first == second
    if first is not None:
        assert len(first) == 64
        int(first, 16)  # valid hex


def test_tool_version_returns_none_for_missing_tool():
    mod = _load_benchmark()
    assert mod._tool_version(["definitely-not-a-real-tool-xyz", "--version"]) is None
