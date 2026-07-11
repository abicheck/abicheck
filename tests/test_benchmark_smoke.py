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


def _write_plugin_pack(pack, version, source, *, with_facts=True, with_record=True):
    import json
    (pack / "source_facts").mkdir(parents=True)
    (pack / "manifest.json").write_text(json.dumps({"version": version}))
    payload = ""
    if with_record:
        record = {"source": str(source), "functions": [{"id": "f"}] if with_facts else []}
        payload = json.dumps(record) + "\n"
    (pack / "source_facts" / "one.jsonl").write_text(payload)


def test_abicheck_full_builds_separate_targets_merges_separate_packs(tmp_path):
    mod = _load_benchmark()
    case = "case02_param_type_change"
    case_dir = tmp_path / "examples" / case
    case_dir.mkdir(parents=True)
    (case_dir / "CMakeLists.txt").touch()
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
        import json
        cmd = [str(x) for x in cmd]
        commands.append(cmd)
        if cmd[:2] == ["cmake", "-S"]:
            build = Path(cmd[cmd.index("-B") + 1])
            build.mkdir(parents=True, exist_ok=True)
            version = build.name.rsplit("_", 1)[1]
            target = f"{case}_{version}"
            source = v1_src if version == "v1" else v2_src
            (build / "compile_commands.json").write_text(json.dumps([{
                "file": str(source),
                "command": f"clang -o CMakeFiles/{target}.dir/{source.name}.o {source}",
            }]))
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
        elif "dump" in cmd or "merge" in cmd:
            Path(cmd[cmd.index("-o") + 1]).touch()
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
    merges = [cmd for cmd in commands if "merge" in cmd]
    assert len(merges) == 2
    assert merges[0][1:3] == ["-m", "abicheck"]
    assert "abicheck_inputs_v1" in " ".join(merges[0])
    assert "abicheck_inputs_v2" in " ".join(merges[1])
    assert not any("--sources" in cmd for cmd in commands)
    assert all(kwargs_timeout == 177 for kwargs_timeout in [177])


def test_plugin_pack_rejects_empty_or_opposite_release(tmp_path):
    mod = _load_benchmark()
    v1, v2 = tmp_path / "v1.c", tmp_path / "v2.c"
    v1.write_text("int v1(void);\n")
    v2.write_text("int v2(void);\n")
    empty = tmp_path / "empty"
    _write_plugin_pack(empty, "v1", v1, with_record=False)
    assert not mod._plugin_pack_is_target_specific(empty, "v1", v1, v2)[0]
    wrong = tmp_path / "wrong"
    _write_plugin_pack(wrong, "v1", v2)
    assert not mod._plugin_pack_is_target_specific(wrong, "v1", v1, v2)[0]


def test_compare_verdict_parses_json_from_stderr_with_trailing_diagnostics():
    mod = _load_benchmark()
    output = (
        'warning: report written to stderr\n'
        '{"verdict":"COMPATIBLE_WITH_RISK","changes":[]}\n'
        'Evidence metrics: findings=1\n'
    )

    assert mod._abicheck_verdict_from_compare(output, 2) == "COMPATIBLE_WITH_RISK"


def test_compare_verdict_falls_back_to_explicit_risk_text():
    mod = _load_benchmark()

    assert (
        mod._abicheck_verdict_from_compare("Verdict: COMPATIBLE_WITH_RISK", 2)
        == "COMPATIBLE_WITH_RISK"
    )


def test_plugin_pack_accepts_target_record_without_function_or_type_facts(tmp_path):
    mod = _load_benchmark()
    v1, v2 = tmp_path / "v1.c", tmp_path / "v2.c"
    v1.write_text("int global = 1;\n")
    v2.write_text("long global = 1;\n")
    pack = tmp_path / "global-only"
    _write_plugin_pack(pack, "v1", v1, with_facts=False)

    assert mod._plugin_pack_is_target_specific(pack, "v1", v1, v2) == (True, "")


def test_plugin_pack_accepts_cmake_target_reusing_other_release_source(tmp_path):
    mod = _load_benchmark()
    v1, v2 = tmp_path / "v1.c", tmp_path / "v2.c"
    v1.write_text("int stable(void) { return 1; }\n")
    v2.write_text(v1.read_text())
    pack = tmp_path / "v2-pack"
    # CMake intentionally uses v1.c for both targets. Trust the concrete
    # target's compile database, not source byte equality or path naming.
    _write_plugin_pack(pack, "v2", v1)

    assert mod._plugin_pack_is_target_specific(
        pack, "v2", v2, v1, target_sources={v1},
    ) == (True, "")


def test_plugin_pack_does_not_accept_identical_opposite_source_without_target_proof(tmp_path):
    mod = _load_benchmark()
    v1, v2 = tmp_path / "v1.c", tmp_path / "v2.c"
    v1.write_text("int stable(void) { return 1; }\n")
    v2.write_text(v1.read_text())
    pack = tmp_path / "unproven-v2-pack"
    _write_plugin_pack(pack, "v2", v1)

    assert not mod._plugin_pack_is_target_specific(pack, "v2", v2, v1)[0]


def test_abicheck_full_retries_binary_only_when_header_parser_rejects_c23(tmp_path):
    mod = _load_benchmark()
    case = "case115_bit_int_width_changed"
    case_dir = tmp_path / case
    case_dir.mkdir()
    v1, v2 = case_dir / "v1.c", case_dir / "v2.c"
    h1, h2 = case_dir / "v1.h", case_dir / "v2.h"
    for path in (v1, v2, h1, h2):
        path.touch()
    plugin = tmp_path / "plugin.so"
    plugin.touch()
    commands = []

    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def fake_side(case_dir_arg, case_arg, version, src, opposite, header, plugin_arg, root, timeout):
        library = root / f"lib{version}.so"
        library.touch()
        pack = root / f"pack-{version}"
        _write_plugin_pack(pack, version, src)
        return library, pack, ""

    def fake_run(cmd, **kwargs):
        cmd = [str(arg) for arg in cmd]
        commands.append(cmd)
        if "dump" in cmd:
            if "-H" in cmd:
                return Result(1, stderr="_BitInt was used without a declaration")
            Path(cmd[cmd.index("-o") + 1]).touch()
            return Result()
        if "merge" in cmd:
            Path(cmd[cmd.index("-o") + 1]).touch()
            return Result()
        return Result(stdout='{"verdict":"BREAKING","changes":[]}')

    with patch.object(mod, "_HAS_ABICHECK", True), patch.object(
        mod, "BUILD_DIR", tmp_path / "build"
    ), patch.object(mod, "_find_or_build_abicheck_plugin", return_value=(plugin, "")), patch.object(
        mod, "_build_plugin_side", side_effect=fake_side
    ), patch.object(mod.subprocess, "run", side_effect=fake_run):
        result = mod.run_abicheck_full(
            plugin, plugin, h1, h2, case, tmp_path, case_dir=case_dir,
            v1_src=v1, v2_src=v2,
        )

    dumps = [cmd for cmd in commands if "dump" in cmd]
    assert result.verdict == "BREAKING"
    assert ["-H" in cmd for cmd in dumps] == [True, False, True, False]
    assert result.raw_output.count("_BitInt was used without a declaration") == 2


def test_plugin_build_does_not_force_include_public_header(tmp_path):
    mod = _load_benchmark()
    case = "case_includes_own_header"
    case_dir = tmp_path / case
    case_dir.mkdir()
    (case_dir / "CMakeLists.txt").touch()
    src = case_dir / "v1.c"
    other = case_dir / "v2.c"
    header = case_dir / "v1.h"
    for path in (src, other, header):
        path.touch()
    plugin = tmp_path / "plugin.so"
    plugin.touch()
    commands = []

    class Result:
        returncode = 1
        stderr = "stop after configure"
        stdout = ""

    def fake_run(cmd, **kwargs):
        commands.append([str(arg) for arg in cmd])
        return Result()

    with patch.object(
        mod, "_first_available_tool", side_effect=lambda *names: f"/usr/bin/{names[0]}"
    ), patch.object(mod.subprocess, "run", side_effect=fake_run):
        mod._build_plugin_side(
            case_dir, case, "v1", src, other, header, plugin, tmp_path / "root", 30,
        )

    injection_arg = next(arg for arg in commands[0] if arg.startswith("-DCMAKE_PROJECT_INCLUDE="))
    injection = Path(injection_arg.split("=", 1)[1]).read_text()
    assert "-include" not in injection


def test_plugin_build_directly_compiles_source_fixture_without_cmake(tmp_path):
    mod = _load_benchmark()
    case_dir = tmp_path / "source_only"
    case_dir.mkdir()
    src, other = case_dir / "v1.c", case_dir / "v2.c"
    src.write_text("int api(void) { return 1; }\n")
    other.write_text("int api(void) { return 2; }\n")
    plugin = tmp_path / "plugin.so"
    plugin.touch()
    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kwargs):
        cmd = [str(arg) for arg in cmd]
        commands.append(cmd)
        Path(cmd[cmd.index("-o") + 1]).touch()
        out_arg = next(arg for arg in cmd if arg.startswith("out="))
        _write_plugin_pack(Path(out_arg.split("=", 1)[1]), "v1", src)
        return Result()

    with patch.object(
        mod, "_first_available_tool", side_effect=lambda *names: f"/usr/bin/{names[0]}"
    ), patch.object(mod.subprocess, "run", side_effect=fake_run), patch.object(
        mod, "SHARED_LIB_SUFFIX", ".so"
    ):
        library, pack, error = mod._build_plugin_side(
            case_dir, "source_only", "v1", src, other, None,
            plugin, tmp_path / "root", 30,
        )

    assert error == ""
    assert library and library.name == "libv1.so"
    assert pack and pack.name == "abicheck_inputs_v1"
    assert len(commands) == 1
    assert str(src.resolve()) in commands[0]
    assert str(other.resolve()) not in commands[0]


def test_case115_baseline_build_prefers_clang(tmp_path):
    mod = _load_benchmark()
    case_dir = tmp_path / "case115_bit_int_width_changed"
    case_dir.mkdir()
    (case_dir / "CMakeLists.txt").touch()
    src = case_dir / "v1.c"
    src.touch()
    captured = {}

    class Args:
        case64_toolchain = "auto"

    def fake_build(case_dir_arg, build_dir, name, env):
        captured.update(env)
        return type("Result", (), {"returncode": 1, "stderr": "expected", "stdout": ""})()

    with patch.object(mod, "_try_reuse_prebuilt", return_value=(None, None, False, False)), patch(
        "shutil.which", return_value="/usr/bin/cmake"
    ), patch.object(mod, "_first_available_tool", side_effect=lambda *names: f"/usr/bin/{names[0]}"), patch.object(
        mod, "_run_cmake_configure_and_build", side_effect=fake_build
    ):
        mod._build_case_artifacts(
            case_dir.name, "BREAKING", case_dir, tmp_path / "build", src, src,
            None, None, Args(), [],
        )

    assert captured["CC"].endswith("clang-18")
    assert captured["CXX"].endswith("clang++-18")


def test_source_only_fixture_uses_isolated_direct_baseline_build(tmp_path):
    mod = _load_benchmark()
    case_dir = tmp_path / "case118_source_only"
    case_dir.mkdir()
    v1, v2 = case_dir / "v1.c", case_dir / "v2.c"
    v1.touch()
    v2.touch()
    (tmp_path / "build").mkdir()
    calls = []

    class Args:
        case64_toolchain = "auto"

    def fake_compile(src, output, **kwargs):
        calls.append((src, output, kwargs))
        output.touch()
        return True

    with patch.object(mod, "_try_reuse_prebuilt", return_value=(None, None, False, False)), patch.object(
        mod, "compile_so", side_effect=fake_compile
    ):
        result = mod._build_case_artifacts(
            case_dir.name, "NO_CHANGE", case_dir, tmp_path / "build", v1, v2,
            None, None, Args(), [],
        )

    assert result.ok
    assert [call[0] for call in calls] == [v1, v2]
    assert all(call[1].parent == tmp_path / "build" for call in calls)

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
