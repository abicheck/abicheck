"""Fast tests for the edit corpus's execution oracle, toolchain probes and reporting.

Every external process is replaced, so these run in the default lane; the
real-compiler behaviour is covered by the corpus's integration tiers. Each
test states the rule independently of the helper it checks (a hand-written
expected value, not the helper's own output).
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import tests._edit_corpus_exec as ex
import tests._edit_corpus_toolchains as tcs
from tests._edit_corpus_cells import CellResult
from tests._edit_corpus_config import (
    STANDARD_BY_NAME,
    Config,
    Consumer,
    Flags,
    Toolchain,
    default_config,
)
from tests._edit_corpus_exec import ExecutionOracle, RunResult, binary_layout_only
from tests._edit_corpus_expect import Expectation, Reconciled
from tests._edit_corpus_harness import Built, CompileError
from tests._edit_corpus_operators import OP_BY_NAME
from tests._edit_corpus_opkit import SONAME_ADVISORY
from tests._edit_corpus_seeds import C_SEED, SEED_BY_NAME
from tests._edit_corpus_tiers import Cell
from tests._strict_process import StrictProcessRunner

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
@pytest.mark.parametrize("exe", ["/x/consumer", "/x/other"])
def test_consumer_runs_prepend_the_library_to_an_inherited_search_path(
    monkeypatch: pytest.MonkeyPatch, inherited: str | None, want: str, exe: str
) -> None:
    if inherited is None:
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    else:
        monkeypatch.setenv("LD_LIBRARY_PATH", inherited)
    runner = StrictProcessRunner()
    runner.expect(
        argv=[str(Path(exe))],
        env_contains={"LD_LIBRARY_PATH": want, "LD_BIND_NOW": "1"},
        returns=_proc(),
    )
    runner.install(monkeypatch, ex.subprocess, "run")
    ex._run(Path(exe), Path("/lib/new"))
    runner.assert_exhausted()


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
    (me,) = _cfg().consumer_set
    with pytest.raises(CompileError):
        oracle._old(C_SEED, _cfg(), old, me)
    _, got = oracle._old(C_SEED, _cfg(), old, me)
    assert got == {"sizeof(point)": "8"}
    assert calls == {"consumers": 2, "probe": 2}
    # Once both succeeded, the entry is served from the cache.
    oracle._old(C_SEED, _cfg(), old, me)
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
        # A capability-looking warning before a crash: the crash decides.
        (
            _proc(
                1,
                stderr="/usr/bin/ld: skipping incompatible /usr/lib/libc.so when searching for -lc\n"
                "collect2: fatal error: ld terminated with signal 9 [Killed]\n",
            ),
            True,
        ),
        (
            _proc(
                1,
                stderr="/usr/bin/ld: skipping incompatible /usr/lib/libc.so when searching for -lc\n",
            ),
            True,
        ),
        (
            _proc(
                1,
                stderr="/usr/bin/ld: skipping incompatible /usr/lib/gcc/x86_64/13/libgcc.a when searching for -lgcc\n"
                "/usr/bin/ld: cannot find -lgcc: No such file or directory\n",
            ),
            False,
        ),
        (subprocess.TimeoutExpired(["gcc"], 60), True),
        (OSError("exec format error"), True),
        (_proc(-11, stderr="gcc: internal compiler error: Segmentation fault\n"), True),
        (_proc(4, stderr="gcc: internal compiler error: in foo, at bar.c:1\n"), True),
        # clang's frontend crash report, with the driver exiting 1.
        (
            _proc(
                1,
                stderr="PLEASE submit a bug report to https://github.com/llvm/llvm-project/issues/\n"
                "clang: error: clang frontend command failed due to signal (use -v to see invocation)\n",
            ),
            True,
        ),
        # A warning that merely names the flag does not explain the failure.
        (
            _proc(
                1,
                stderr="cc: warning: argument unused during compilation: '-m32'\n"
                "collect2: error: ld returned 1 exit status\n",
            ),
            True,
        ),
    ],
)
def test_flag_probe_skips_only_a_failure_that_names_a_capability(
    monkeypatch: pytest.MonkeyPatch, failure: object, broken: bool
) -> None:
    # The -m32 shared-library build fails as *failure* says. Only a failure that
    # names the capability goes on to the plain baseline (library, then
    # executable); a broken toolchain stops at the first command.
    runner = StrictProcessRunner()
    drv = "/x/gcc-97"
    m32_lib = [drv, "-O0", "-m32", "-shared", "-fPIC", "-o", "libprobe.so", "lib.c"]
    if isinstance(failure, BaseException):
        runner.expect(argv=m32_lib, raises=failure)
    else:
        runner.expect(argv=m32_lib, returns=failure)
    if not broken:
        runner.expect(
            argv=[drv, "-O0", "-shared", "-fPIC", "-o", "libprobe.so", "lib.c"],
            returns=_proc(),
        )
        runner.expect(
            argv=[drv, "-O0", "-o", "main", "main.c", "-L.", "-lprobe"],
            returns=_proc(),
        )
    runner.install(monkeypatch, tcs, "_run")
    flags = Flags(bits=32)
    if broken:
        with pytest.raises(tcs.ToolchainBroken) as info:
            tcs._flags_unavailable(_PROBE_TC, "c", flags)
        # The failure carries the command, the probe source and the whole stderr.
        report = str(info.value)
        assert "-m32 -shared -fPIC" in report
        assert "--- lib.c ---\n" in report and "int probe_fn(int x)" in report
        if isinstance(failure, subprocess.CompletedProcess):
            assert report.endswith("--- stderr ---\n" + failure.stderr)
    else:
        reason = tcs._flags_unavailable(_PROBE_TC, "c", flags)
        assert isinstance(failure, subprocess.CompletedProcess)
        assert reason is not None and failure.stderr.strip().splitlines()[-1] in reason
    runner.assert_exhausted()


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
        # x86-64 aligns it to 8: packing moves `weight` from 8 to 4, and the
        # header layout records alignof(rec) 8 -> 1 as well; both are owed.
        (64, {"STRUCT_ALIGNMENT_CHANGED"}, False),
        (64, {"TYPE_FIELD_OFFSET_CHANGED"}, False),
        (64, {"TYPE_FIELD_OFFSET_CHANGED", "TYPE_ALIGNMENT_CHANGED"}, True),
    ],
)
def test_pack_rebuild_accepts_alignment_only_where_offsets_cannot_move(
    bits: int, kinds: set[str], ok: bool
) -> None:
    exp = OP_BY_NAME["rebuild_pack_struct"].expect(_cfg(flags=Flags(bits=bits)))
    assert (exp.problems("BREAKING", _answer(kinds)) == []) is ok


def _answer(kinds: set[str]) -> frozenset[str]:
    """What abicheck reports for a BREAKING rebuild: *kinds* plus its release
    advisory, which every ELF break under an unchanged SONAME carries."""
    return frozenset(kinds | {SONAME_ADVISORY})


def _rec_layout(pack: int | None) -> tuple[int, ...]:
    """sizeof and offsets of ``struct rec {int; double; char[8];}`` at *pack* (x86-64).

    ctypes' ``_pack_`` follows gcc's packing rule, so this is a layout oracle
    independent of the operator's rule.
    """
    import ctypes

    fields = [
        ("id", ctypes.c_int),
        ("weight", ctypes.c_double),
        ("tag", ctypes.c_char * 8),
    ]
    ns: dict[str, object] = {"_fields_": fields}
    if pack is not None:
        ns["_pack_"] = pack
        # Python 3.14 requires an explicit _layout_ with _pack_ and accepts only
        # "ms" there; for these int/double/char members MS packing gives gcc's
        # -fpack-struct offsets (ctypes' own docs on _pack_).
        ns["_layout_"] = "ms"
    rec = type("rec", (ctypes.Structure,), ns)
    return (ctypes.sizeof(rec), *(getattr(rec, n).offset for n, _ in fields))


@pytest.mark.skipif(
    struct.calcsize("P") != 8 or sys.platform == "win32",
    reason="the layout oracle is the x86-64 SysV host's own ctypes",
)
@pytest.mark.parametrize("old_pack", [None, 2, 4, 8])
def test_pack_rebuild_from_a_packed_old_side_matches_the_layout_oracle(
    old_pack: int | None,
) -> None:
    # Alignment-only is an accepted finding exactly when packing to 1 moves no
    # size or offset (by an independent layout computation).
    unchanged = _rec_layout(old_pack) == _rec_layout(1)
    exp = OP_BY_NAME["rebuild_pack_struct"].expect(_cfg(flags=Flags(pack=old_pack)))
    accepts_alignment_only = (
        exp.problems("BREAKING", _answer({"STRUCT_ALIGNMENT_CHANGED"})) == []
    )
    assert accepts_alignment_only is unchanged


@pytest.mark.parametrize(
    "flags",
    [Flags(bits=32), Flags(pack=2), Flags(pack=4), Flags(bits=32, pack=2)],
    ids=["i386", "old-pack2", "old-pack4", "i386-old-pack2"],
)
@pytest.mark.parametrize(
    ("evidence", "observable"), [("binary", False), ("dwarf", False), ("headers", True)]
)
def test_pack_rebuild_alignment_only_needs_the_header_dump(
    flags: Flags, evidence: str, observable: bool
) -> None:
    # Packing to 1 here moves no size or offset, so DWARF (which records no
    # alignment for an unannotated record) is byte-identical on both sides:
    # only the header dump, built with the same -fpack-struct, shows alignof
    # drop. Nightly 2026-11-10 (#1992): clang18/19 -m32 DWARF cells read
    # false-clean against a rule that demanded a finding from DWARF.
    exp = OP_BY_NAME["rebuild_pack_struct"].expect(_cfg(flags=flags, evidence=evidence))
    assert exp.observable is observable
    clean = exp.problems("NO_CHANGE", frozenset())
    if observable:
        assert clean == [
            # with_derived: an ELF break under an unchanged SONAME owes the advisory.
            "missing expected kinds ['SONAME_BUMP_RECOMMENDED']",
            "none of ['STRUCT_ALIGNMENT_CHANGED', 'STRUCT_FIELD_OFFSET_CHANGED', "
            "'STRUCT_SIZE_CHANGED', 'TYPE_ALIGNMENT_CHANGED', "
            "'TYPE_FIELD_OFFSET_CHANGED', 'TYPE_SIZE_CHANGED'] reported",
            "verdict NO_CHANGE weaker than required BREAKING",
        ]
    else:
        assert clean == []


def test_pack_rebuild_that_moves_offsets_stays_observable_in_dwarf() -> None:
    # x86-64 default: packing moves `weight` 8 -> 4, which DWARF records.
    exp = OP_BY_NAME["rebuild_pack_struct"].expect(_cfg(evidence="dwarf"))
    assert exp.observable is True
    assert exp.verdict_min == "BREAKING"


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
        matrix.check_cell(_cell(), None, "nightly")  # type: ignore[arg-type]
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
    monkeypatch.setenv("ABICHECK_EDIT_CORPUS_MINIMIZE", "1")
    monkeypatch.setattr(matrix, "run_cell", lambda *a: mismatch)
    monkeypatch.setattr(matrix, "minimize", shrink)
    with pytest.raises(pytest.fail.Exception) as info:
        matrix.check_cell(cell, None, "nightly")  # type: ignore[arg-type]
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


@pytest.mark.parametrize(
    ("evidence", "bits", "checked"),
    [
        ("headers", 64, [("g++", ("-O0",), 64), ("c++", (), 64)]),
        ("headers", 32, [("g++", ("-O0", "-m32"), 32), ("c++", ("-m32",), 32)]),
        ("binary", 64, [("g++", ("-O0",), 64)]),
    ],
)
def test_fixed_matrix_checks_producer_and_header_driver_widths(
    monkeypatch: pytest.MonkeyPatch,
    evidence: str,
    bits: int,
    checked: list[tuple[str, tuple[str, ...], int]],
) -> None:
    import tests.test_abi_edit_corpus as v1

    seen: list[tuple[str, tuple[str, ...], int]] = []

    def width(driver: str, lang: str, flags: tuple[str, ...], want: int) -> str | None:
        seen.append((driver, flags, want))
        return None

    monkeypatch.setattr(v1, "driver_width", width)
    monkeypatch.setattr(v1.shutil, "which", lambda name: f"/usr/bin/{name}")
    seed = SEED_BY_NAME["cxx_classes"]
    v1._skip_wrong_width(seed, _cfg("c++", evidence=evidence, flags=Flags(bits=bits)))
    assert seen == checked
    # Either driver at the wrong width skips the cell.
    monkeypatch.setattr(
        v1, "driver_width", lambda d, *a: f"{d} does not target {bits}-bit"
    )
    with pytest.raises(pytest.skip.Exception, match="g\\+\\+ does not target"):
        v1._skip_wrong_width(
            seed, _cfg("c++", evidence=evidence, flags=Flags(bits=bits))
        )
    if evidence == "headers":
        # The producer is right and only the header frontend's driver is not.
        monkeypatch.setattr(
            v1,
            "driver_width",
            lambda d, *a: None if d == "g++" else f"{d} does not target {bits}-bit",
        )
        with pytest.raises(pytest.skip.Exception, match="^c\\+\\+ does not target"):
            v1._skip_wrong_width(
                seed, _cfg("c++", evidence=evidence, flags=Flags(bits=bits))
            )


# --- crashes are never capabilities or source breaks ---------------------------

_CLANG_CRASH = _proc(
    1,
    stderr="PLEASE submit a bug report to https://github.com/llvm/llvm-project/issues/\n"
    "clang: error: clang frontend command failed due to signal\n",
)


@pytest.mark.usefixtures("clean_probe_caches")
@pytest.mark.parametrize(
    ("outcome", "broken"),
    [
        (_proc(0), False),
        # gcc's and clang's rejection of the probed spelling.
        (
            _proc(1, stderr="cc: error: unrecognized command-line option '{flag}'\n"),
            False,
        ),
        (_proc(1, stderr="error: invalid value 'x' in '{flag}'\n"), False),
        # A rejection of some other flag is not a rejection of this one.
        (_proc(1, stderr="error: invalid value 'c2y' in '-std=c2y'\n"), True),
        # Failures that name no -std= flag: a broken frontend or assembler.
        (
            _proc(1, stderr="cc: fatal error: cannot execute 'cc1': No such file\n"),
            True,
        ),
        (_proc(1, stderr=""), True),
        (_CLANG_CRASH, True),
        (subprocess.TimeoutExpired(["cc"], 60), True),
    ],
)
def test_standard_probe_fails_on_a_crash_and_skips_only_a_rejected_spelling(
    monkeypatch: pytest.MonkeyPatch, outcome: object, broken: bool
) -> None:
    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, subprocess.CompletedProcess)
        flag = next(a for a in cmd if a.startswith("-std="))
        return _proc(outcome.returncode, stderr=outcome.stderr.replace("{flag}", flag))

    tcs.std_flag_for_driver.cache_clear()
    monkeypatch.setattr(tcs.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tcs.subprocess, "run", run)
    try:
        if broken:
            with pytest.raises(tcs.ToolchainBroken):
                tcs.std_flag_for_driver("cc", STANDARD_BY_NAME["c17"])
        else:
            got = tcs.std_flag_for_driver("cc", STANDARD_BY_NAME["c17"])
            assert got == ("c17" if outcome.returncode == 0 else None)  # type: ignore[union-attr]
    finally:
        tcs.std_flag_for_driver.cache_clear()


@pytest.mark.parametrize(
    ("installed", "outcome", "expected"),
    [
        (False, None, False),
        (True, _proc(0), True),
        (True, _proc(1, stderr=f"<stdin>:3:2: error: {tcs._NOT_LIBSTDCXX}\n"), False),
        # Failing before the probe's #error is a broken toolchain, not
        # another library: missing headers, a frontend that cannot start.
        (
            True,
            _proc(1, stderr="<stdin>:1:10: fatal error: 'string' file not found\n"),
            "broken",
        ),
        (
            True,
            _proc(1, stderr="c++: fatal error: cannot execute 'cc1plus'\n"),
            "broken",
        ),
        (True, _CLANG_CRASH, "broken"),
        (True, subprocess.TimeoutExpired(["c++"], 60), "broken"),
    ],
)
def test_libstdcxx_probe_distinguishes_absent_other_library_and_broken(
    monkeypatch: pytest.MonkeyPatch, installed: bool, outcome: object, expected: object
) -> None:
    tc = Toolchain("clang", 96, "/x/clang-96", "/x/clang++-96")

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        assert cmd[0] == "/x/clang++-96"
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, subprocess.CompletedProcess)
        return outcome

    tcs.uses_libstdcxx.cache_clear()
    monkeypatch.setattr(tcs.shutil, "which", lambda name: name if installed else None)
    monkeypatch.setattr(tcs.subprocess, "run", run)
    try:
        if expected == "broken":
            with pytest.raises(tcs.ToolchainBroken):
                tcs.uses_libstdcxx(tc)
        else:
            assert tcs.uses_libstdcxx(tc) is expected
    finally:
        tcs.uses_libstdcxx.cache_clear()


def test_a_crashed_new_header_compile_is_a_harness_error_not_a_source_break(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "call.c"
    src.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    consumers = ex.ConsumerSet(
        {"call": tmp_path / "call"}, {"call": RunResult(0, "", "")}
    )
    monkeypatch.setattr(ex.ExecutionOracle, "_old", lambda self, *a: (consumers, {}))
    monkeypatch.setattr(ex, "_run", lambda exe, lib: RunResult(0, "", ""))
    monkeypatch.setattr(ex, "run_probe", lambda *a: {})
    monkeypatch.setattr(ex, "execution_unavailable", lambda config: None)
    monkeypatch.setattr(ex, "_compile", lambda cmd, what: _CLANG_CRASH)
    built = Built(tmp_path, tmp_path / "libedit.so", tmp_path / "edit_seed.h", None)
    oracle = ExecutionOracle(tmp_path)
    with pytest.raises(CompileError, match="crashed") as info:
        oracle.truth(C_SEED, _cfg(), built, built, {}, Flags(), "t")
    assert "--- call.c ---" in str(info.value) and "frontend command failed" in str(
        info.value
    )
    # The same compiler exiting 1 on a real diagnostic is a source break.
    monkeypatch.setattr(
        ex,
        "_compile",
        lambda cmd, what: _proc(1, stderr="call.c:1: error: too few arguments\n"),
    )
    truth = oracle.truth(C_SEED, _cfg(), built, built, {}, Flags(), "t")
    assert truth.source_break and truth.signals == (
        "source call [gcc13-c17-O0]: error: too few arguments",
    )


@pytest.mark.parametrize(
    ("producer_libstdcxx", "consumer_libstdcxx", "seed", "blocked"),
    [
        (True, False, "cxx_stdlib", True),
        (False, True, "cxx_stdlib", True),
        (True, True, "cxx_stdlib", False),
        (True, False, "cxx_classes", False),
    ],
)
def test_a_std_seed_needs_one_cxx_library_on_both_sides(
    monkeypatch: pytest.MonkeyPatch,
    producer_libstdcxx: bool,
    consumer_libstdcxx: bool,
    seed: str,
    blocked: bool,
) -> None:
    import tests._edit_corpus_cells as cells

    consumer = Toolchain("clang", 18, "clang", "clang++")
    monkeypatch.setattr(
        cells,
        "uses_libstdcxx",
        lambda tc: producer_libstdcxx if tc == GCC else consumer_libstdcxx,
    )
    cfg = _cfg("c++", consumers=(Consumer(toolchain=consumer),))
    (user,) = cfg.consumer_set
    reason = cells.stdlib_mismatch(SEED_BY_NAME[seed], cfg, user)
    assert (reason is not None) is blocked


@pytest.mark.parametrize(
    ("member", "names"),
    [
        # Data members offsetof can name, however they are spelled.
        ("int x", ["x"]),
        ("alignas(8) int x", ["x"]),
        ("_Alignas(16) int x", ["x"]),
        ("[[gnu::aligned(8)]] int x", ["x"]),
        ("int x __attribute__((aligned(8)))", ["x"]),
        ("int (*cb)(int)", ["cb"]),
        ("void (* handler )(void *, int)", ["handler"]),
        ("char tag[8]", ["tag"]),
        ("[[deprecated]] int legacy", ["legacy"]),
        ("private:\n  int hidden", ["hidden"]),
        ("protected: mutable int hits", ["hits"]),
        ("public:\n  volatile int reg", ["reg"]),
        # Not fields offsetof can take.
        ("int z()", []),
        ("virtual int z() = 0", []),
        ("int b : 4", []),
        ("alignas(4) int b : 4", []),
        ("static int count", []),
        ("private:\n  static int count", []),
        ("static const int k", []),
    ],
    ids=str,
)
def test_the_layout_probe_names_every_data_member(
    member: str, names: list[str]
) -> None:
    from tests._edit_corpus_exec import _field_names

    assert _field_names(f" {member}; ") == names
