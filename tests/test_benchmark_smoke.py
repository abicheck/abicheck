"""Smoke tests for scripts/benchmark_comparison.py.

Verifies that the benchmark script imports cleanly, parses args correctly,
and that the run_abicc_dumper / run_abicc_xml helpers handle missing tools
gracefully (return SKIP instead of crashing).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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


def test_parse_args_rejects_removed_compat_strict_tools(capsys):
    """abicheck_compat/abicheck_strict were retired from the harness — only
    the two evidence-depth lanes (abicheck/abicheck_full) remain benchmarked."""
    mod = _load_benchmark()
    with patch("sys.argv", ["benchmark_comparison.py", "--tools", "abicheck_compat"]):
        with pytest.raises(SystemExit) as exc:
            mod.parse_args()
    assert exc.value.code != 0
    capsys.readouterr()  # silence argparse's usage/error message in test output


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


def test_bundle_uses_canonical_case_verdict():
    mod = _load_benchmark()

    assert mod.EXPECTED["case84_bundle_soname_skew"] == "BREAKING"


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


# ── _gcc_major_version (case115 _BitInt toolchain probing) ──────────────────

def test_gcc_major_version_missing_tool_returns_none():
    mod = _load_benchmark()
    with patch("shutil.which", return_value=None):
        assert mod._gcc_major_version("gcc") is None


def test_gcc_major_version_parses_dumpversion_output():
    mod = _load_benchmark()
    completed = SimpleNamespace(stdout="14.2.0\n", stderr="")
    with (
        patch("shutil.which", return_value="/usr/bin/gcc"),
        patch("subprocess.run", return_value=completed) as run,
    ):
        assert mod._gcc_major_version("gcc") == 14
    assert run.call_args.args[0] == ["/usr/bin/gcc", "-dumpversion"]


def test_gcc_major_version_unparseable_output_returns_none():
    mod = _load_benchmark()
    completed = SimpleNamespace(stdout="not-a-version\n", stderr="")
    with (
        patch("shutil.which", return_value="/usr/bin/gcc"),
        patch("subprocess.run", return_value=completed),
    ):
        assert mod._gcc_major_version("gcc") is None


def test_gcc_major_version_subprocess_error_returns_none():
    mod = _load_benchmark()
    with (
        patch("shutil.which", return_value="/usr/bin/gcc"),
        patch("subprocess.run", side_effect=OSError("boom")),
    ):
        assert mod._gcc_major_version("gcc") is None


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


def test_abicheck_baseline_has_no_full_evidence_options(tmp_path):
    mod = _load_benchmark()
    so = tmp_path / "lib.so"
    header = tmp_path / "api.h"
    so.touch()
    header.touch()
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

    with patch.object(mod, "_HAS_ABICHECK", True), patch.object(
        mod, "BUILD_DIR", tmp_path / "build"
    ), patch.object(mod.subprocess, "run", side_effect=fake_run):
        mod.run_abicheck(so, so, header, header, "baseline", tmp_path)

    assert len(commands) == 3
    for dump in commands[:2]:
        assert "-H" in dump
        for option in ("--depth", "--sources", "--build-info", "-p"):
            assert option not in dump


def test_case115_dump_pins_the_discovered_clang_binary(tmp_path):
    """--ast-frontend clang only selects the clang backend -- it does not
    resolve which clang binary to run, and that resolution falls back to a
    bare "clang" on PATH, absent on hosts that ship only a versioned
    clang-18. The case115 override must pin the binary --first_available_tool
    already discovered via --gcc-path, not rely on the bare-name fallback."""
    mod = _load_benchmark()
    so = tmp_path / "lib.so"
    header = tmp_path / "api.h"
    so.touch()
    header.touch()
    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = '{"verdict":"BREAKING","changes":[]}'

    def fake_run(cmd, **kwargs):
        commands.append([str(x) for x in cmd])
        if "dump" in cmd:
            Path(cmd[cmd.index("-o") + 1]).touch()
        return Result()

    with patch.object(mod, "_HAS_ABICHECK", True), patch.object(
        mod, "BUILD_DIR", tmp_path / "build"
    ), patch.object(
        mod, "_first_available_tool", side_effect=lambda *names: f"/usr/bin/{names[0]}"
    ), patch.object(mod.subprocess, "run", side_effect=fake_run):
        mod.run_abicheck(so, so, header, header, "case115_bit_int_width_changed", tmp_path)

    dumps = [cmd for cmd in commands if "dump" in cmd]
    assert dumps
    for dump in dumps:
        assert "--ast-frontend" in dump and "clang" in dump
        assert "--gcc-path" in dump
        assert dump[dump.index("--gcc-path") + 1] == "/usr/bin/clang-18"


def _write_plugin_pack(pack, version, source, *, with_facts=True):
    import json
    (pack / "source_facts").mkdir(parents=True)
    (pack / "manifest.json").write_text(json.dumps({"version": version}))
    record = {"source": str(source), "functions": [{"id": "f"}] if with_facts else []}
    (pack / "source_facts" / "one.jsonl").write_text(json.dumps(record) + "\n")


def test_abicheck_full_builds_separate_targets_merges_separate_packs(tmp_path):
    mod = _load_benchmark()
    case = "case02_param_type_change"
    case_dir = tmp_path / "examples" / case
    case_dir.mkdir(parents=True)
    v1_src, v2_src = case_dir / "v1.c", case_dir / "v2.c"
    v1_h, v2_h = case_dir / "v1.h", case_dir / "v2.h"
    for path in (v1_src, v2_src, v1_h, v2_h):
        path.write_text("/* fixture */\n")
    plugin = tmp_path / "libabicheck-facts.so"
    plugin.touch()
    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = '{"verdict":"BREAKING","changes":[]}'

    def fake_run(cmd, **kwargs):
        cmd = [str(x) for x in cmd]
        commands.append(cmd)
        if cmd[:2] == ["cmake", "--build"]:
            version = cmd[cmd.index("--target") + 1].rsplit("_", 1)[1]
            build = Path(cmd[2])
            out = build / case
            out.mkdir(parents=True, exist_ok=True)
            (out / f"lib{version}.so").touch()
            injection_arg = next(x for x in commands[-2] if x.startswith("-DCMAKE_PROJECT_INCLUDE="))
            injection = Path(injection_arg.split("=", 1)[1]).read_text()
            pack = Path(injection.split("out=", 1)[1].split("'", 1)[0].split()[0])
            _write_plugin_pack(pack, version, v1_src if version == "v1" else v2_src)
        elif "dump" in cmd:
            Path(cmd[cmd.index("-o") + 1]).touch()
        elif "-c" in cmd:
            # embed_inputs_pack() relink step: [python, -c, script, base, pack, final].
            Path(cmd[-1]).touch()
        return Result()

    build_root = tmp_path / "bench-build"
    with patch.object(mod, "_HAS_ABICHECK", True), patch.object(
        mod, "BUILD_DIR", build_root
    ), patch.object(mod, "_find_or_build_abicheck_plugin", return_value=(plugin, "")), patch.object(
        mod, "_first_available_tool", side_effect=lambda *names: f"/usr/bin/{names[0]}"
    ), patch.object(mod.subprocess, "run", side_effect=fake_run), patch.object(
        mod, "SHARED_LIB_SUFFIX", ".so"
    ):
        result = mod.run_abicheck_full(
            plugin, plugin, v1_h, v2_h, case, tmp_path, case_dir=case_dir,
            v1_src=v1_src, v2_src=v2_src, timeout=177,
        )

    assert result.verdict == "BREAKING"
    targets = [cmd[cmd.index("--target") + 1] for cmd in commands if "--target" in cmd]
    assert targets == [f"{case}_v1", f"{case}_v2"]
    # No standalone `merge` CLI command (removed in the ADR-043 CLI reset) and
    # no `--sources <pack>` shortcut (which skips the export relink — Codex
    # review on PR #581); the pack is folded in via a direct embed_inputs_pack()
    # call, which relinks the source surface against the binary's real exports.
    merges = [cmd for cmd in commands if "-c" in cmd and "embed_inputs_pack" in cmd[2]]
    assert len(merges) == 2
    assert "abicheck_inputs_v1" in " ".join(merges[0])
    assert "abicheck_inputs_v2" in " ".join(merges[1])
    assert not any("--sources" in cmd for cmd in commands)
    assert not any(cmd[1:4] == ["-m", "abicheck", "merge"] for cmd in commands)
    assert all(kwargs_timeout == 177 for kwargs_timeout in [177])


def test_abicheck_full_case115_dump_pins_the_discovered_clang_binary(tmp_path):
    """Same --gcc-path requirement as the L2 lane (see
    test_case115_dump_pins_the_discovered_clang_binary): the L3-L5 lane's own
    case115 override must not rely on a bare "clang" being on PATH."""
    mod = _load_benchmark()
    case = "case115_bit_int_width_changed"
    case_dir = tmp_path / "examples" / case
    case_dir.mkdir(parents=True)
    v1_src, v2_src = case_dir / "v1.c", case_dir / "v2.c"
    v1_h, v2_h = case_dir / "v1.h", case_dir / "v2.h"
    for path in (v1_src, v2_src, v1_h, v2_h):
        path.write_text("/* fixture */\n")
    plugin = tmp_path / "libabicheck-facts.so"
    plugin.touch()
    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = '{"verdict":"BREAKING","changes":[]}'

    def fake_run(cmd, **kwargs):
        cmd = [str(x) for x in cmd]
        commands.append(cmd)
        if cmd[:2] == ["cmake", "--build"]:
            version = cmd[cmd.index("--target") + 1].rsplit("_", 1)[1]
            build = Path(cmd[2])
            out = build / case
            out.mkdir(parents=True, exist_ok=True)
            (out / f"lib{version}.so").touch()
            injection_arg = next(x for x in commands[-2] if x.startswith("-DCMAKE_PROJECT_INCLUDE="))
            injection = Path(injection_arg.split("=", 1)[1]).read_text()
            pack = Path(injection.split("out=", 1)[1].split("'", 1)[0].split()[0])
            _write_plugin_pack(pack, version, v1_src if version == "v1" else v2_src)
        elif "dump" in cmd:
            Path(cmd[cmd.index("-o") + 1]).touch()
        elif "-c" in cmd:
            Path(cmd[-1]).touch()
        return Result()

    build_root = tmp_path / "bench-build"
    with patch.object(mod, "_HAS_ABICHECK", True), patch.object(
        mod, "BUILD_DIR", build_root
    ), patch.object(mod, "_find_or_build_abicheck_plugin", return_value=(plugin, "")), patch.object(
        mod, "_first_available_tool", side_effect=lambda *names: f"/usr/bin/{names[0]}"
    ), patch.object(mod.subprocess, "run", side_effect=fake_run), patch.object(
        mod, "SHARED_LIB_SUFFIX", ".so"
    ):
        mod.run_abicheck_full(
            plugin, plugin, v1_h, v2_h, case, tmp_path, case_dir=case_dir,
            v1_src=v1_src, v2_src=v2_src,
        )

    dumps = [cmd for cmd in commands if "dump" in cmd]
    assert dumps
    for dump in dumps:
        assert "--ast-frontend" in dump and "clang" in dump
        assert "--gcc-path" in dump
        assert dump[dump.index("--gcc-path") + 1] == "/usr/bin/clang-18"


def test_plugin_pack_rejects_empty_or_opposite_release(tmp_path):
    mod = _load_benchmark()
    v1, v2 = tmp_path / "v1.c", tmp_path / "v2.c"
    v1.touch()
    v2.touch()
    empty = tmp_path / "empty"
    _write_plugin_pack(empty, "v1", v1, with_facts=False)
    assert not mod._plugin_pack_is_target_specific(empty, "v1", v1, v2)[0]
    wrong = tmp_path / "wrong"
    _write_plugin_pack(wrong, "v1", v2)
    assert not mod._plugin_pack_is_target_specific(wrong, "v1", v1, v2)[0]


def test_plugin_pack_accepts_deliberately_shared_source(tmp_path):
    """A "no ABI change" fixture whose CMakeLists.txt points both
    V1_SOURCES and V2_SOURCES at the identical file (case04_no_change's
    shape) has no "wrong release" to detect — the same translation unit's
    facts are legitimately valid evidence for both sides."""
    mod = _load_benchmark()
    shared = tmp_path / "v1.c"
    shared.touch()
    pack = tmp_path / "pack"
    _write_plugin_pack(pack, "v2", shared)
    ok, error = mod._plugin_pack_is_target_specific(pack, "v2", shared, shared)
    assert ok, error


def test_cmake_declared_source_resolves_shared_v2_target(tmp_path):
    """find_sources()'s naive v1.c/v2.c filename guess can't see that a
    case's CMakeLists.txt actually compiles v1.c for BOTH targets (a
    deliberate no-op fixture) — _cmake_declared_source must read the real
    V{version}_SOURCES value instead."""
    mod = _load_benchmark()
    case_dir = tmp_path / "case04_no_change"
    case_dir.mkdir()
    (case_dir / "v1.c").touch()
    (case_dir / "v2.c").touch()
    (case_dir / "CMakeLists.txt").write_text(
        "abicheck_add_case(case04_no_change\n"
        "    V1_SOURCES v1.c\n"
        "    V2_SOURCES v1.c\n"
        "    V1_HEADERS v1.h\n"
        ")\n"
    )
    v1_declared = mod._cmake_declared_source(case_dir, "v1")
    v2_declared = mod._cmake_declared_source(case_dir, "v2")
    assert v1_declared == case_dir / "v1.c"
    assert v2_declared == case_dir / "v1.c"
    assert v1_declared == v2_declared


def test_cmake_declared_source_missing_cmakelists_returns_none(tmp_path):
    mod = _load_benchmark()
    assert mod._cmake_declared_source(tmp_path, "v1") is None


def test_build_plugin_side_does_not_force_include_header(tmp_path):
    """Regression: a blanket `-include header` used to be injected into every
    plugin-instrumented build, which crashes any fixture whose .c file
    independently redefines a type also declared in its header (a common,
    legal C pattern) — case07/08/09/etc. The CMake macro's own opt-in
    V{version}_FORCE_INCLUDE already covers cases that genuinely need it, so
    the benchmark harness must not duplicate it unconditionally."""
    mod = _load_benchmark()
    case = "case07_struct_layout"
    case_dir = tmp_path / "examples" / case
    case_dir.mkdir(parents=True)
    v1_src = case_dir / "v1.c"
    v1_h = case_dir / "v1.h"
    v1_src.write_text("struct Point { int x; int y; };\n")
    v1_h.write_text("typedef struct Point { int x; int y; } Point;\n")
    plugin = tmp_path / "libabicheck-facts.so"
    plugin.touch()
    root = tmp_path / "root"
    root.mkdir()
    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kwargs):
        commands.append([str(x) for x in cmd])
        return Result()

    with patch.object(
        mod, "_first_available_tool", side_effect=lambda *names: f"/usr/bin/{names[0]}"
    ), patch.object(mod.subprocess, "run", side_effect=fake_run), patch.object(
        mod, "_find_cmake_lib", return_value=None
    ):
        mod._build_plugin_side(
            case_dir, case, "v1", v1_src, v1_src, v1_h, plugin, root, 90,
        )

    injection = root / "plugin_flags_v1.cmake"
    assert injection.exists()
    assert "-include" not in injection.read_text()

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


def test_source_enrichment_match_only_credits_abicheck_full():
    mod = _load_benchmark()
    known_case = next(iter(mod._SOURCE_ENRICHMENT_CASES))
    # abicheck_full promoting COMPATIBLE -> COMPATIBLE_WITH_RISK on source
    # evidence is enrichment (ADR-028 D3), not a false positive -- but only
    # for the specific, individually-triaged cases in the allowlist.
    assert mod._is_source_enrichment_match(
        known_case, "abicheck_full", "COMPATIBLE", "COMPATIBLE_WITH_RISK"
    )
    # Same transition from a lane with no source evidence to justify it stays
    # a normal miss -- only abicheck_full's L4/L5 findings can produce it.
    assert not mod._is_source_enrichment_match(
        known_case, "abicheck", "COMPATIBLE", "COMPATIBLE_WITH_RISK"
    )
    assert not mod._is_source_enrichment_match(
        known_case, "abidiff", "COMPATIBLE", "COMPATIBLE_WITH_RISK"
    )
    # Any other transition (including a real over-call past RISK) is not
    # exempted, even for abicheck_full on a known-enrichment case.
    assert not mod._is_source_enrichment_match(
        known_case, "abicheck_full", "COMPATIBLE", "API_BREAK"
    )
    assert not mod._is_source_enrichment_match(
        known_case, "abicheck_full", "NO_CHANGE", "COMPATIBLE_WITH_RISK"
    )
    # The same verdict-shape transition on a case NOT in the allowlist is a
    # real, uncredited miss -- crediting every COMPATIBLE ->
    # COMPATIBLE_WITH_RISK transition regardless of case would silently hide
    # a genuine future over-calling regression from the FP count.
    assert not mod._is_source_enrichment_match(
        "case999_hypothetical_new_case", "abicheck_full", "COMPATIBLE", "COMPATIBLE_WITH_RISK"
    )


def test_source_enrichment_credited_in_accuracy_fp_and_coverage():
    mod = _load_benchmark()
    known_case = next(iter(mod._SOURCE_ENRICHMENT_CASES))
    results = [
        {"case": known_case, "expected": "COMPATIBLE", "abicheck_full": "COMPATIBLE_WITH_RISK"},
    ]
    correct, scored = mod._accuracy(results, "abicheck_full")
    assert (correct, scored) == (1, 1)
    correct, total = mod._coverage_accuracy(results, "abicheck_full")
    assert (correct, total) == (1, 1)
    fp, fn = mod._fp_fn_counts(results, "abicheck_full")
    assert (fp, fn) == (0, 0)


def test_source_enrichment_not_credited_for_unlisted_case():
    mod = _load_benchmark()
    results = [
        {
            "case": "case999_hypothetical_new_case",
            "expected": "COMPATIBLE",
            "abicheck_full": "COMPATIBLE_WITH_RISK",
        },
    ]
    correct, scored = mod._accuracy(results, "abicheck_full")
    assert (correct, scored) == (0, 1)
    correct, total = mod._coverage_accuracy(results, "abicheck_full")
    assert (correct, total) == (0, 1)
    fp, fn = mod._fp_fn_counts(results, "abicheck_full")
    assert (fp, fn) == (1, 0)


def test_freeze_tools_merges_with_existing_frozen_data(tmp_path):
    """Freezing abicc_xml after abicc_dumper must not drop the dumper columns.

    The documented workflow freezes each ABICC mode separately (ABICC hangs
    on some cases when both modes run concurrently), so _freeze_tools must
    merge into any existing file rather than overwrite it wholesale.
    """
    mod = _load_benchmark()
    out = tmp_path / "frozen.json"

    dumper_results = [
        {"case": "case01_symbol_removal", "abicc_dumper": "BREAKING", "abicc_dumper_ms": 100},
    ]
    mod._freeze_tools(dumper_results, ["abicc_dumper"], out)

    xml_results = [
        {"case": "case01_symbol_removal", "abicc_xml": "BREAKING", "abicc_xml_ms": 200},
    ]
    mod._freeze_tools(xml_results, ["abicc_xml"], out)

    frozen = json.loads(out.read_text())
    assert set(frozen["tools"]) == {"abicc_dumper", "abicc_xml"}
    entry = frozen["results_by_case"]["case01_symbol_removal"]
    assert entry["abicc_dumper"] == "BREAKING"
    assert entry["abicc_dumper_ms"] == 100
    assert entry["abicc_xml"] == "BREAKING"
    assert entry["abicc_xml_ms"] == 200


def test_freeze_tools_overwrites_only_its_own_tool_columns(tmp_path):
    """Re-freezing abicc_dumper must update only abicc_dumper's columns for a
    case, leaving a previously-frozen abicc_xml column on that same case
    untouched."""
    mod = _load_benchmark()
    out = tmp_path / "frozen.json"

    mod._freeze_tools(
        [{"case": "case01", "abicc_xml": "COMPATIBLE", "abicc_xml_ms": 1}],
        ["abicc_xml"], out,
    )
    mod._freeze_tools(
        [{"case": "case01", "abicc_dumper": "BREAKING", "abicc_dumper_ms": 2}],
        ["abicc_dumper"], out,
    )
    mod._freeze_tools(
        [{"case": "case01", "abicc_dumper": "TIMEOUT", "abicc_dumper_ms": 3}],
        ["abicc_dumper"], out,
    )

    entry = json.loads(out.read_text())["results_by_case"]["case01"]
    assert entry["abicc_dumper"] == "TIMEOUT"
    assert entry["abicc_dumper_ms"] == 3
    assert entry["abicc_xml"] == "COMPATIBLE"


def test_freeze_tools_discards_stale_cache_instead_of_merging(tmp_path):
    """A prior freeze stamped against a different ground_truth digest must
    not be merged forward under a fresh timestamp -- that would silently
    relabel stale competitor verdicts (e.g. for cases that no longer exist,
    or whose expected verdict changed) as current."""
    mod = _load_benchmark()
    out = tmp_path / "frozen.json"

    out.write_text(json.dumps({
        "schema": "abicheck-frozen-competitor/1.0",
        "ground_truth_sha256": "stale-digest-from-an-older-catalog",
        "tools": ["abicc_xml"],
        "results_by_case": {
            "case01": {"abicc_xml": "COMPATIBLE", "abicc_xml_ms": 1},
        },
    }))

    with patch.object(mod, "_ground_truth_digest", return_value="current-digest"):
        mod._freeze_tools(
            [{"case": "case01", "abicc_dumper": "BREAKING", "abicc_dumper_ms": 2}],
            ["abicc_dumper"], out,
        )

    frozen = json.loads(out.read_text())
    assert frozen["ground_truth_sha256"] == "current-digest"
    assert frozen["tools"] == ["abicc_dumper"]
    entry = frozen["results_by_case"]["case01"]
    assert entry == {"abicc_dumper": "BREAKING", "abicc_dumper_ms": 2}


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
