"""Fast tests for the edit corpus's execution oracle, toolchain probes and reporting.

Every external process is replaced, so these run in the default lane; the
real-compiler behaviour is covered by the corpus's integration tiers. Each
test states the rule independently of the helper it checks (a hand-written
expected value, not the helper's own output).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import tests._edit_corpus_exec as ex
import tests._edit_corpus_toolchains as tcs
from tests._edit_corpus_cells import CellResult
from tests._edit_corpus_config import (
    STANDARD_BY_NAME,
    Config,
    Flags,
    Toolchain,
    default_config,
)
from tests._edit_corpus_exec import ExecutionOracle, RunResult, binary_layout_only
from tests._edit_corpus_expect import Expectation, Reconciled
from tests._edit_corpus_harness import Built, CompileError
from tests._edit_corpus_operators import OP_BY_NAME
from tests._edit_corpus_seeds import C_SEED, SEED_BY_NAME
from tests._edit_corpus_tiers import Cell

GCC = Toolchain("gcc", 13, "gcc", "g++")


def _cfg(lang: str = "c", **kw: object) -> Config:
    # A fixed Linux/ELF config, so these checks read the same on every host.
    kw.setdefault("platform", ("linux", "elf"))
    return replace(default_config(lang, GCC), **kw)  # type: ignore[arg-type]


def _proc(
    rc: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], rc, stdout, stderr)


# --- execution oracle ---------------------------------------------------------


@pytest.mark.parametrize(
    ("inherited", "want"),
    [
        (None, "/lib/new"),
        ("/opt/rt", "/lib/new" + os.pathsep + "/opt/rt"),
    ],
)
def test_consumer_runs_prepend_the_library_to_an_inherited_search_path(
    monkeypatch: pytest.MonkeyPatch, inherited: str | None, want: str
) -> None:
    seen: dict[str, str] = {}

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        env = kw["env"]
        assert isinstance(env, dict)
        seen.update(env)
        return _proc()

    if inherited is None:
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    else:
        monkeypatch.setenv("LD_LIBRARY_PATH", inherited)
    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    ex._run(Path("/x/consumer"), Path("/lib/new"))
    assert seen["LD_LIBRARY_PATH"] == want
    assert seen["LD_BIND_NOW"] == "1"


def test_a_hanging_oracle_compile_fails_naming_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hang(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        assert kw.get("timeout"), "every oracle compile must carry a deadline"
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(ex.subprocess, "run", hang)
    with pytest.raises(CompileError, match=r"layout probe did not finish .*cc -c x\.c"):
        ex._compile(["cc", "-c", "x.c"], "layout probe")


def test_a_failed_old_side_probe_is_retried_not_half_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = {"consumers": 0, "probe": 0}

    def consumers(*a: object) -> ex.ConsumerSet:
        calls["consumers"] += 1
        return ex.ConsumerSet({}, {})

    def probe(*a: object) -> dict[str, str]:
        calls["probe"] += 1
        if calls["probe"] == 1:
            raise CompileError("transient")
        return {"sizeof(point)": "8"}

    monkeypatch.setattr(ex, "build_consumers", consumers)
    monkeypatch.setattr(ex, "run_probe", probe)
    oracle = ExecutionOracle(tmp_path)
    old = Built(tmp_path, tmp_path / "libedit.so", tmp_path / "edit_seed.h", None)
    with pytest.raises(CompileError):
        oracle._old(C_SEED, _cfg(), old)
    _, got = oracle._old(C_SEED, _cfg(), old)
    assert got == {"sizeof(point)": "8"}
    assert calls == {"consumers": 2, "probe": 2}
    # Once both succeeded, the entry is served from the cache.
    oracle._old(C_SEED, _cfg(), old)
    assert calls == {"consumers": 2, "probe": 2}


@pytest.mark.parametrize(
    ("binary_break", "signals", "layout_only"),
    [
        (True, ["layout sizeof(rec): 24 -> 16"], True),
        # A new-header compile failure is a source signal, not an observed run.
        (True, ["layout sizeof(rec): 24 -> 16", "source call: error"], True),
        (True, ["run alloc: exit 0 -> 1", "layout sizeof(rec): 24 -> 16"], False),
        (True, ["run alloc: exit 0 -> 1"], False),
        (False, ["source call: error"], False),
        (False, [], False),
    ],
)
def test_layout_only_counts_consumer_runs_alone(
    binary_break: bool, signals: list[str], layout_only: bool
) -> None:
    assert binary_layout_only(binary_break, signals) is layout_only


@pytest.mark.parametrize(
    ("want_err", "got_err", "text"),
    [
        ("warning: old\n", "", "loader diagnostics gone: warning: old"),
        ("", "", "loader diagnostics gone: (blank)"),
        ("", "a\nwarning: new\n", "loader diagnostics: warning: new"),
    ],
)
def test_describe_a_stderr_only_difference(
    want_err: str, got_err: str, text: str
) -> None:
    assert (
        ex._describe(RunResult(0, "1\n", want_err), RunResult(0, "1\n", got_err))
        == text
    )


# --- toolchain discovery and probes ---------------------------------------------


def test_discovery_keeps_an_alias_whose_pair_validates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `gcc` and `gcc-13` are one binary; `g++` is 14, `g++-13` is 13.
    paths = {
        "gcc": "/x/gcc",
        "g++": "/x/g++",
        "gcc-13": "/x/gcc-13",
        "g++-13": "/x/g++-13",
    }
    real = {"/x/gcc": "/x/gcc-13"}
    idents = {
        "/x/gcc": ("gcc", 13),
        "/x/g++": ("gcc", 14),
        "/x/gcc-13": ("gcc", 13),
        "/x/g++-13": ("gcc", 13),
    }
    monkeypatch.setattr(tcs.shutil, "which", lambda name: paths.get(name))
    monkeypatch.setattr(tcs.os.path, "realpath", lambda p: real.get(p, p))
    monkeypatch.setattr(tcs, "_identify", lambda path: idents.get(path))
    monkeypatch.setattr(tcs.sys, "platform", "linux")
    tcs.discover.cache_clear()
    try:
        got = [(t.id, t.cc, t.cxx) for t in tcs.discover()]
    finally:
        tcs.discover.cache_clear()
    assert got == [("gcc13", "/x/gcc-13", "/x/g++-13")]


_PROBE_TC = Toolchain("gcc", 97, "/x/gcc-97", "/x/g++-97")


@pytest.fixture
def clean_probe_caches() -> object:
    for fn in (
        tcs._flags_build,
        tcs.driver_width,
        tcs.lacks_feature,
        tcs.default_cxx11_abi,
    ):
        fn.cache_clear()
    yield
    for fn in (
        tcs._flags_build,
        tcs.driver_width,
        tcs.lacks_feature,
        tcs.default_cxx11_abi,
    ):
        fn.cache_clear()


def _flag_runner(failure: object) -> object:
    """Baseline (no -m32) builds; the -m32 build fails as *failure* says."""

    def run(
        cmd: list[str], cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        if "-m32" not in cmd:
            return _proc()
        if isinstance(failure, BaseException):
            raise failure
        assert isinstance(failure, subprocess.CompletedProcess)
        return failure

    return run


@pytest.mark.usefixtures("clean_probe_caches")
@pytest.mark.parametrize(
    ("failure", "broken"),
    [
        (_proc(1, stderr="/usr/bin/ld: cannot find -lgcc\n"), False),
        (
            _proc(
                1,
                stderr="/usr/include/features.h: fatal error: gnu/stubs-32.h: No such file\n",
            ),
            False,
        ),
        (
            _proc(1, stderr="gcc: error: unrecognized command-line option '-m32'\n"),
            False,
        ),
        # A non-zero exit that names no requested capability is a broken compiler.
        (
            _proc(1, stderr="gcc: fatal error: Killed signal terminated program cc1\n"),
            True,
        ),
        (_proc(1, stderr="collect2: error: ld returned 1 exit status\n"), True),
        (subprocess.TimeoutExpired(["gcc"], 60), True),
        (OSError("exec format error"), True),
        (_proc(-11, stderr="gcc: internal compiler error: Segmentation fault\n"), True),
        (_proc(4, stderr="gcc: internal compiler error: in foo, at bar.c:1\n"), True),
    ],
)
def test_flag_probe_skips_only_a_failure_that_names_a_capability(
    monkeypatch: pytest.MonkeyPatch, failure: object, broken: bool
) -> None:
    monkeypatch.setattr(tcs, "_run", _flag_runner(failure))
    flags = Flags(bits=32)
    if broken:
        with pytest.raises(tcs.ToolchainBroken):
            tcs._flags_unavailable(_PROBE_TC, "c", flags)
    else:
        reason = tcs._flags_unavailable(_PROBE_TC, "c", flags)
        assert isinstance(failure, subprocess.CompletedProcess)
        assert reason is not None and failure.stderr.strip() in reason


@pytest.mark.usefixtures("clean_probe_caches")
@pytest.mark.parametrize(
    ("proc", "outcome"),
    [
        (_proc(0), None),
        (
            _proc(
                1, stderr="error: size of array 'edit_corpus_target_width' is negative"
            ),
            "width",
        ),
        (_proc(1, stderr="cc: error: unrecognized option '-mfoo'"), "broken"),
    ],
)
def test_driver_width_reports_another_width_and_fails_a_broken_driver(
    monkeypatch: pytest.MonkeyPatch,
    proc: subprocess.CompletedProcess[str],
    outcome: str | None,
) -> None:
    monkeypatch.setattr(tcs.subprocess, "run", lambda *a, **k: proc)
    if outcome == "broken":
        with pytest.raises(tcs.ToolchainBroken):
            tcs.driver_width("cc", "c", (), 64)
        return
    got = tcs.driver_width("cc", "c", (), 64)
    assert (got is None) if outcome is None else (got == "cc  does not target 64-bit")


def _buildable_host(monkeypatch: pytest.MonkeyPatch, tc: Toolchain) -> None:
    """A host on which *tc* builds anything: only the header-frontend checks remain."""
    monkeypatch.setattr(tcs, "discover", lambda: (tc,))
    monkeypatch.setattr(tcs, "host_platform", lambda: ("linux", "elf"))
    monkeypatch.setattr(tcs.sys, "platform", "linux")
    monkeypatch.setattr(tcs, "std_flag", lambda t, s: s.name)
    monkeypatch.setattr(tcs, "std_flag_for_driver", lambda d, s: s.name)
    monkeypatch.setattr(tcs, "_flags_unavailable", lambda t, lang, f: None)
    monkeypatch.setattr(tcs.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.mark.parametrize("bits", [64, 32])
def test_headers_evidence_needs_the_frontend_driver_at_the_producer_width(
    monkeypatch: pytest.MonkeyPatch, bits: int
) -> None:
    # The producer builds at *bits*; the system cc castxml emulates defaults to
    # the other width and has no flag that changes it.
    _buildable_host(monkeypatch, GCC)
    seen: list[tuple[str, tuple[str, ...], int]] = []

    def width(driver: str, lang: str, flags: tuple[str, ...], want: int) -> str | None:
        seen.append((driver, flags, want))
        return f"{driver} does not target {want}-bit"

    monkeypatch.setattr(tcs, "driver_width", width)
    config = _cfg(flags=Flags(bits=bits))
    reason = tcs.unavailable(config)
    assert reason == f"header frontend: cc does not target {bits}-bit"
    assert seen == [("cc", ("-m32",) if bits == 32 else (), bits)]
    # Binary evidence does not go through the header frontend.
    assert tcs.unavailable(replace(config, evidence="binary")) is None


def test_a_config_for_another_host_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _buildable_host(monkeypatch, GCC)
    monkeypatch.setattr(tcs, "driver_width", lambda *a: None)
    assert tcs.unavailable(_cfg()) is None
    monkeypatch.setattr(tcs, "host_platform", lambda: ("darwin", "macho"))
    reason = tcs.unavailable(_cfg(platform=("linux", "elf")))
    assert reason is not None and "host is ('darwin', 'macho')" in reason


def test_capability_gap_probes_a_feature_the_standard_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    op = OP_BY_NAME["concept_constraint_added"]
    config = _cfg("c++", std=STANDARD_BY_NAME["c++20"])
    monkeypatch.setattr(tcs, "uses_libstdcxx", lambda tc: True)
    monkeypatch.setattr(tcs, "lacks_feature", lambda tc, std, f: f == "concepts")
    assert (
        tcs.capability_gap(op, config)
        == "gcc13 does not compile concepts in -std=c++20"
    )
    monkeypatch.setattr(tcs, "lacks_feature", lambda tc, std, f: False)
    assert tcs.capability_gap(op, config) is None


@pytest.mark.usefixtures("clean_probe_caches")
@pytest.mark.parametrize(("rc", "lacks"), [(0, False), (1, True)])
def test_lacks_feature_compiles_the_probe_in_the_standard(
    monkeypatch: pytest.MonkeyPatch, rc: int, lacks: bool
) -> None:
    seen: list[list[str]] = []

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        assert "concept" in str(kw["input"])
        return _proc(rc)

    monkeypatch.setattr(tcs, "std_flag", lambda tc, std: "c++2a")
    monkeypatch.setattr(tcs.subprocess, "run", run)
    assert tcs.lacks_feature(_PROBE_TC, STANDARD_BY_NAME["c++20"], "concepts") is lacks
    assert seen[0][:2] == ["/x/g++-97", "-std=c++2a"]


@pytest.mark.parametrize(("default", "gap"), [(1, False), (0, True), (None, False)])
def test_cxx11_rebuild_needs_a_producer_whose_default_abi_differs(
    monkeypatch: pytest.MonkeyPatch, default: int | None, gap: bool
) -> None:
    op = OP_BY_NAME["rebuild_cxx11_abi_off"]
    monkeypatch.setattr(tcs, "uses_libstdcxx", lambda tc: True)
    monkeypatch.setattr(tcs, "lacks_feature", lambda tc, std, f: False)
    monkeypatch.setattr(tcs, "default_cxx11_abi", lambda tc: default)
    reason = tcs.capability_gap(op, _cfg("c++"))
    assert (reason is not None and "already defaults" in reason) is gap
    # An explicit old-side ABI is the config's own choice, not the default's.
    monkeypatch.setattr(tcs, "default_cxx11_abi", lambda tc: 0)
    assert tcs.capability_gap(op, _cfg("c++", flags=Flags(cxx11_abi=1))) is None


# --- expectations -------------------------------------------------------------


@pytest.mark.parametrize(
    ("bits", "kinds", "ok"),
    [
        # i386 SysV aligns a member double to 4: struct rec {int; double;
        # char[8]} is 20 bytes with offsets 0/4/12 whether packed or not, and
        # only alignof drops (4 -> 1).
        (32, {"STRUCT_ALIGNMENT_CHANGED"}, True),
        (32, {"TYPE_SIZE_CHANGED"}, True),
        # x86-64 aligns it to 8: packing moves `weight` from 8 to 4.
        (64, {"STRUCT_ALIGNMENT_CHANGED"}, False),
        (64, {"TYPE_FIELD_OFFSET_CHANGED"}, True),
    ],
)
def test_pack_rebuild_accepts_alignment_only_where_offsets_cannot_move(
    bits: int, kinds: set[str], ok: bool
) -> None:
    exp = OP_BY_NAME["rebuild_pack_struct"].expect(_cfg(flags=Flags(bits=bits)))
    assert (exp.problems("BREAKING", frozenset(kinds)) == []) is ok


# --- reporting ------------------------------------------------------------------


def _cell() -> Cell:
    return Cell(OP_BY_NAME["func_remove"], SEED_BY_NAME["c_core"], _cfg())


def test_a_harness_exception_still_leaves_a_cell_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tests.test_abi_edit_corpus_matrix as matrix

    def boom(*a: object) -> CellResult:
        raise CompileError("library does not build")

    monkeypatch.setenv("ABICHECK_EDIT_CORPUS_REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(matrix, "run_cell", boom)
    with pytest.raises(CompileError):
        matrix.check_cell(_cell(), None)  # type: ignore[arg-type]
    (report,) = tmp_path.iterdir()
    assert "CompileError: library does not build" in report.read_text(encoding="utf-8")


def test_a_failed_minimization_keeps_the_original_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tests.test_abi_edit_corpus_matrix as matrix

    cell = _cell()
    mismatch = CellResult(
        cell.op.name,
        cell.seed.name,
        cell.config,
        "ran",
        verdict="NO_CHANGE",
        expectation=Expectation(),
        exec_truth=ex.ExecTruth(available=False, reason="test"),
        reconciled=Reconciled(
            "wrong-verdict", ("verdict NO_CHANGE weaker than required BREAKING",)
        ),
    )

    def shrink(*a: object) -> CellResult:
        raise CompileError("shrunk config does not build")

    monkeypatch.setenv("ABICHECK_EDIT_CORPUS_REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(matrix, "run_cell", lambda *a: mismatch)
    monkeypatch.setattr(matrix, "minimize", shrink)
    with pytest.raises(pytest.fail.Exception) as info:
        matrix.check_cell(cell, None)  # type: ignore[arg-type]
    text = (tmp_path / f"{cell.id}.txt").read_text(encoding="utf-8")
    for got in (str(info.value), text):
        assert "-> wrong-verdict" in got
        assert "minimization failed: CompileError: shrunk config does not build" in got


def test_the_pixi_integration_task_leaves_the_scheduled_tiers_out() -> None:
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    tasks = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["pixi"][
        "feature"
    ]["integration"]["tasks"]
    cmd = tasks["test-integration"]
    for marker in ("edit_corpus_nightly", "edit_corpus_weekly"):
        assert f"not {marker}" in cmd, cmd
