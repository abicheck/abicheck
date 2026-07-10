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


_BENCHMARK_MOD = None


def _load_benchmark():
    """Dynamically import scripts/benchmark_comparison.py (memoized).

    The script is import-only for these smoke tests — every test reads module
    attributes or uses context-managed ``patch.object`` (auto-reverted), so a
    single shared load is correct. Re-executing the module on every test was a
    pure-overhead hotspot (~0.3s × 19 ≈ 6s); load it once and cache it.

    Caveat for future tests: the module object is now shared, so a test that
    mutates a module-level global *without* reverting it (a bare assignment
    rather than ``patch.object``) would leak that state into later tests. Keep
    mutations context-managed; if a test genuinely needs a pristine module,
    reset ``_BENCHMARK_MOD = None`` to force a fresh load.
    """
    global _BENCHMARK_MOD
    if _BENCHMARK_MOD is not None:
        return _BENCHMARK_MOD
    mod_name = "benchmark_comparison"
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(
        mod_name,
        SCRIPTS_DIR / "benchmark_comparison.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # Must register in sys.modules BEFORE exec so @dataclass can resolve the module
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    _BENCHMARK_MOD = mod
    return mod


# ── Import / parse_args ───────────────────────────────────────────────────────

def test_parse_args_defaults():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py"]):
        args = mod.parse_args()
    assert args.abicc_timeout == mod.DEFAULT_ABICC_TIMEOUT
    assert args.abicheck_full_timeout == mod.DEFAULT_ABICHECK_FULL_TIMEOUT
    assert args.abicc_mode == "both"
    assert args.suite == "all"
    assert not args.skip_abicc


def test_parse_args_custom_timeout():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py", "--abicc-timeout", "60"]):
        args = mod.parse_args()
    assert args.abicc_timeout == 60


def test_parse_args_custom_abicheck_full_timeout():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py", "--abicheck-full-timeout", "180"]):
        args = mod.parse_args()
    assert args.abicheck_full_timeout == 180


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


def test_abicheck_baseline_and_full_build_distinct_commands(tmp_path):
    """Baseline is binary+headers only; full adds source/build evidence."""
    mod = _load_benchmark()
    so = tmp_path / "lib.so"
    hdr = tmp_path / "api.h"
    src = tmp_path / "old" / "lib.c"
    compile_db = tmp_path / "build" / "compile_commands.json"
    snapshot_dir = tmp_path / "snapshots"
    for path in (so, hdr, src):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    compile_db.parent.mkdir(parents=True, exist_ok=True)
    compile_db.write_text(__import__("json").dumps([
        {"directory": str(src.parent), "file": str(src), "command": "cc -c lib.c"},
    ]))
    snapshot_dir.mkdir()

    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = '{"verdict":"NO_CHANGE","changes":[]}'

    def fake_run(cmd, **kwargs):
        commands.append([str(x) for x in cmd])
        if "dump" in cmd:
            Path(cmd[cmd.index("-o") + 1]).touch()
        return Result()

    with (
        patch.object(mod, "_HAS_ABICHECK", True),
        patch.object(mod, "BUILD_DIR", snapshot_dir),
        patch.object(mod.subprocess, "run", side_effect=fake_run),
    ):
        mod.run_abicheck(so, so, hdr, hdr, "smoke_baseline", tmp_path)
        baseline = commands[:]
        commands.clear()
        mod.run_abicheck_full(
            so, so, hdr, hdr, "smoke_full", tmp_path,
            case_dir=tmp_path, v1_src=src, v2_src=src, build_dir=compile_db,
        )
        full = commands[:]

    assert len(baseline) == len(full) == 3
    for dump in baseline[:2]:
        assert "-H" in dump
        for option in ("--depth", "--sources", "--build-info", "-p"):
            assert option not in dump

    for dump in full[:2]:
        assert dump[dump.index("--depth") + 1] == "full"
        assert dump[dump.index("--sources") + 1] == str(src.parent)
        staged_db = Path(dump[dump.index("--build-info") + 1])
        assert staged_db.name == "compile_commands.json"
        assert staged_db.parent.name in {"sources_v1", "sources_v2"}
        assert dump[dump.index("-p") + 1] == str(staged_db)


def test_abicheck_full_stages_side_by_side_versions_and_filters_build_info(tmp_path):
    """Legacy v1/v2 files never expose the opposite side or project compile DB."""
    mod = _load_benchmark()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    v1_src = case_dir / "v1.c"
    v2_src = case_dir / "v2.c"
    v1_h = case_dir / "v1.h"
    v2_h = case_dir / "v2.h"
    unrelated = tmp_path / "other.c"
    for path in (v1_src, v2_src, v1_h, v2_h, unrelated):
        path.write_text("/* fixture */\n")
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(__import__("json").dumps([
        {"directory": str(case_dir), "file": str(v1_src), "command": "cc -c v1.c"},
        {"directory": str(case_dir), "file": str(v2_src), "command": "cc -c v2.c"},
        {"directory": str(tmp_path), "file": str(unrelated), "command": "cc -c other.c"},
    ]))
    build = tmp_path / "build"
    build.mkdir()
    so = tmp_path / "lib.so"
    so.touch()
    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = '{"verdict":"NO_CHANGE","changes":[]}'

    def fake_run(cmd, **kwargs):
        commands.append(([str(x) for x in cmd], kwargs))
        if "dump" in cmd:
            Path(cmd[cmd.index("-o") + 1]).touch()
        return Result()

    with (
        patch.object(mod, "_HAS_ABICHECK", True),
        patch.object(mod, "BUILD_DIR", build),
        patch.object(mod.subprocess, "run", side_effect=fake_run),
    ):
        mod.run_abicheck_full(
            so, so, v1_h, v2_h, "side_by_side", tmp_path,
            case_dir=case_dir, v1_src=v1_src, v2_src=v2_src,
            build_dir=compile_db, timeout=177,
        )

    dumps = [cmd for cmd, _kwargs in commands if "dump" in cmd]
    assert len(dumps) == 2
    roots = [Path(cmd[cmd.index("--sources") + 1]) for cmd in dumps]
    assert roots[0] != roots[1]
    assert {p.name for p in roots[0].iterdir()} == {"v1.c", "v1.h", "compile_commands.json"}
    assert {p.name for p in roots[1].iterdir()} == {"v2.c", "v2.h", "compile_commands.json"}
    for cmd, kwargs in commands:
        assert kwargs["timeout"] == 177
        if "dump" in cmd:
            db = Path(cmd[cmd.index("--build-info") + 1])
            entries = __import__("json").loads(db.read_text())
            assert len(entries) == 1
            assert Path(entries[0]["file"]).parent == db.parent


def test_default_tools_include_both_abicheck_lanes():
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py"]):
        selected = mod._resolve_selected_tools(mod.parse_args())
    assert {"abicheck", "abicheck_full"} <= selected


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
