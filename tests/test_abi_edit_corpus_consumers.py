"""Fast tests for the edit corpus's build/consumer split and toolchain selection.

A library pair is built once per ``Config.build_id`` and checked against every
consumer of ``Config.consumer_set``; the toolchains of each role can be
restricted by name, and a requested compiler that is not installed fails.
Every compiler and external process is replaced, so these run in the default
lane; each expected value is written out by hand, not read back from the
helper under test.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from _workflow_exec import find_run_step, load_workflow, require_bash, run_step

import abiforge.run.cells as cells
import abiforge.run.exec as ex
import abiforge.run.harness as harness
import abiforge.run.tiers as tiers
import abiforge.run.toolchains as tcs
from abiforge.model.config import (
    STANDARD_BY_NAME,
    Config,
    Consumer,
    Flags,
    ResolvedConsumer,
    Toolchain,
    default_config,
)
from abiforge.model.expect import ExecTruth, PairTruth
from abiforge.model.seeds import C_SEED, SEED_BY_NAME
from abiforge.operators import OP_BY_NAME
from abiforge.run.cells import CellContext, CellResult, pair_blocked, run_cell
from abiforge.run.exec import ConsumerSet, ExecutionOracle, RunResult
from abiforge.run.harness import Built
from abiforge.run.report import load, summarize, write_cell
from abiforge.run.toolchains import ToolchainSelectionError, parse_selection

GCC13 = Toolchain("gcc", 13, "/x/gcc-13", "/x/g++-13")
GCC14 = Toolchain("gcc", 14, "/x/gcc-14", "/x/g++-14")
CLANG18 = Toolchain("clang", 18, "/x/clang-18", "/x/clang++-18")
CLANG19 = Toolchain("clang", 19, "/x/clang-19", "/x/clang++-19")
INSTALLED = (CLANG18, CLANG19, GCC13, GCC14)
STD = STANDARD_BY_NAME


def _cfg(lang: str = "c", **kw: object) -> Config:
    kw.setdefault("platform", ("linux", "elf"))
    return replace(default_config(lang, GCC13), **kw)  # type: ignore[arg-type]


def _built(d: Path) -> Built:
    return Built(d, d / "libedit.so", d / "edit_seed.h", None)


# --- toolchain selection ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("", ()),
        ("gcc-14", (("gcc", 14),)),
        (" gcc14 , clang-19,clang ", (("gcc", 14), ("clang", 19), ("clang", None))),
        ("apple-clang-15,msvc", (("apple-clang", 15), ("msvc", None))),
        ("gcc-14,gcc14", (("gcc", 14),)),
    ],
)
def test_selection_names_parse_to_family_and_major(
    raw: str, want: tuple[tuple[str, int | None], ...]
) -> None:
    assert parse_selection(raw) == want


@pytest.mark.parametrize(
    "raw", ["gcc-", "g++-14", "icc", "clang 19", "gcc-14;rm", "-14"]
)
def test_a_malformed_selection_name_is_an_error(raw: str) -> None:
    with pytest.raises(ToolchainSelectionError, match="not a toolchain name"):
        parse_selection(raw)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("", INSTALLED),
        ("gcc-14", (GCC14,)),
        ("clang19,gcc-13", (CLANG19, GCC13)),
        ("clang", (CLANG18, CLANG19)),
        ("gcc,gcc-14", (GCC13, GCC14)),
    ],
)
def test_select_picks_exactly_the_named_installed_toolchains(
    raw: str, want: tuple[Toolchain, ...]
) -> None:
    assert tcs.select(raw, INSTALLED) == want


@pytest.mark.parametrize(
    ("raw", "missing"),
    [("gcc-15", "gcc-15"), ("clang-19,gcc-9", "gcc-9"), ("msvc", "msvc")],
)
def test_an_uninstalled_selection_fails_naming_what_is_installed(
    raw: str, missing: str
) -> None:
    with pytest.raises(ToolchainSelectionError) as info:
        tcs.select(raw, INSTALLED, "VAR")
    text = str(info.value)
    assert f"asks for {missing}" in text
    assert "installed: clang18, clang19, gcc13, gcc14" in text and text.startswith(
        "VAR"
    )


@pytest.mark.parametrize(
    ("build", "users", "want_build", "want_users"),
    [
        ("", "", INSTALLED, INSTALLED),
        ("gcc-13", "", (GCC13,), (GCC13,)),
        ("gcc-13", "gcc-14,clang-18,clang-19", (GCC13,), (CLANG18, CLANG19, GCC14)),
        ("", "clang", INSTALLED, (CLANG18, CLANG19)),
    ],
)
def test_consumer_toolchains_default_to_the_build_selection(
    monkeypatch: pytest.MonkeyPatch,
    build: str,
    users: str,
    want_build: tuple[Toolchain, ...],
    want_users: tuple[Toolchain, ...],
) -> None:
    monkeypatch.setattr(tcs, "discover", lambda: INSTALLED)
    monkeypatch.setenv(tcs.TOOLCHAIN_ENV, build)
    monkeypatch.setenv(tcs.CONSUMER_TOOLCHAIN_ENV, users)
    assert (tcs.build_toolchains(), tcs.consumer_toolchains()) == (
        want_build,
        want_users,
    )


@pytest.mark.parametrize(
    ("var", "gen"),
    [
        (tcs.TOOLCHAIN_ENV, tiers.nightly_cells),
        (tcs.TOOLCHAIN_ENV, tiers.weekly_cells),
        (tcs.CONSUMER_TOOLCHAIN_ENV, tiers.nightly_cells),
        (tcs.CONSUMER_TOOLCHAIN_ENV, tiers.weekly_cells),
        (tcs.TOOLCHAIN_ENV, tiers.pr_consumer_cells),
    ],
)
def test_a_tier_with_an_uninstalled_selection_fails_instead_of_shrinking(
    monkeypatch: pytest.MonkeyPatch, var: str, gen: object
) -> None:
    monkeypatch.setattr(tcs, "discover", lambda: (GCC13, CLANG18))
    # Start from no selection: an inherited one must not decide the case.
    monkeypatch.delenv(tcs.TOOLCHAIN_ENV, raising=False)
    monkeypatch.delenv(tcs.CONSUMER_TOOLCHAIN_ENV, raising=False)
    monkeypatch.setenv(var, "gcc-14")
    with pytest.raises(ToolchainSelectionError, match="gcc-14"):
        gen()  # type: ignore[operator]


# --- the build / consumer split -------------------------------------------------


def test_the_build_id_ignores_consumers_and_the_cell_id_names_them() -> None:
    a = _cfg()
    b = _cfg(consumers=(Consumer(), Consumer(toolchain=CLANG18, std=STD["c89"])))
    c = _cfg(consumers=(Consumer(toolchain=CLANG18),))
    assert a.build_id == b.build_id == c.build_id == "c17-gcc13-O0-headers"
    assert a.id == "c17-gcc13-O0-headers"
    assert b.id == "c17-gcc13-O0-use-self,clang18@c89-headers"
    assert c.id == "c17-gcc13-O0-use-clang18-headers"


def test_consumer_set_resolves_defaults_against_the_build_and_dedups() -> None:
    cfg = _cfg(
        "c++",
        flags=Flags(abi_version=11),
        consumers=(
            Consumer(),
            Consumer(toolchain=GCC13),  # the same as the build's own consumer
            Consumer(toolchain=CLANG18, std=STD["c++20"]),
            Consumer(flags=Flags(short_enums=True)),
        ),
    )
    assert cfg.consumer_set == (
        ResolvedConsumer(GCC13, STD["c++17"], Flags(abi_version=11)),
        # clang has no -fabi-version: its consumer drops it.
        ResolvedConsumer(CLANG18, STD["c++20"], Flags()),
        ResolvedConsumer(GCC13, STD["c++17"], Flags(short_enums=True)),
    )


def test_a_library_pair_is_built_once_for_every_consumer_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    builds: list[tuple[str, str]] = []

    def fake_build(
        seed, config, out_dir, overrides=None, flags=None, link=None, evidence=None
    ):  # noqa: ANN001
        builds.append(("new" if overrides is not None else "old", config.build_id))
        return _built(out_dir)

    monkeypatch.setattr(harness, "build", fake_build)
    old_cache = harness.BaselineCache(tmp_path / "old")
    new_cache = harness.NewBuildCache(tmp_path / "new")
    op = OP_BY_NAME["struct_append_field"]
    configs = [
        _cfg(),
        _cfg(consumers=(Consumer(toolchain=CLANG18),)),
        _cfg(consumers=(Consumer(), Consumer(std=STD["c89"]))),
    ]
    olds = {id(old_cache.get(C_SEED, c)) for c in configs}
    news = {id(new_cache.get(op, C_SEED, c)) for c in configs}
    assert len(olds) == len(news) == 1
    assert builds == [("old", "c17-gcc13-O0-headers"), ("new", "c17-gcc13-O0-headers")]


def test_the_verdict_is_computed_from_the_build_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        harness,
        "build",
        lambda s, c, d, overrides=None, flags=None, link=None, evidence=None: _built(d),
    )
    monkeypatch.setattr(cells, "unavailable", lambda c: None)
    monkeypatch.setattr(cells, "capability_gap", lambda op, c: None)

    def answer(old: Built, new: Built) -> tuple[str, frozenset[str]]:
        seen.append((old.dir, new.dir))
        return "BREAKING", frozenset({"TYPE_SIZE_CHANGED"})

    monkeypatch.setattr(cells, "abicheck_answer", answer)
    ctx = CellContext.at(tmp_path, execute=False)
    op = OP_BY_NAME["struct_append_field"]
    a = run_cell(op, C_SEED, _cfg(), ctx)
    b = run_cell(op, C_SEED, _cfg(consumers=(Consumer(toolchain=CLANG18),)), ctx)
    assert (a.verdict, a.kinds) == (b.verdict, b.kinds)
    assert len(set(seen)) == 1 and len(seen) == 2


# --- infeasible and unavailable pairs -----------------------------------------


def _libs(
    monkeypatch: pytest.MonkeyPatch, libstdcxx: set[Toolchain], abi: int = 1
) -> None:
    monkeypatch.setattr(cells, "uses_libstdcxx", lambda tc: tc in libstdcxx)
    monkeypatch.setattr(cells, "default_cxx11_abi", lambda tc: abi)
    monkeypatch.setattr(cells, "consumer_unavailable", lambda *a, **k: None)


@pytest.mark.parametrize(
    ("op", "seed", "lang", "consumer", "flags", "want", "needle"),
    [
        # A C++ consumer of the C seed: no extern "C" guards, so nothing links.
        (
            "func_add",
            "c_core",
            "c",
            Consumer(std=STD["c++17"]),
            Flags(),
            "infeasible",
            'extern "C"',
        ),
        # The edited header needs a feature the consumer's standard lacks.
        (
            "concept_constraint_added",
            "cxx_classes",
            "c++",
            Consumer(std=STD["c++17"]),
            Flags(),
            "infeasible",
            "concepts",
        ),
        (
            "func_add",
            "c_core",
            "c",
            Consumer(flags=Flags(bits=32)),
            Flags(),
            "infeasible",
            "word sizes",
        ),
        (
            "func_param_append",
            "cxx_stdlib",
            "c++",
            Consumer(flags=Flags(pack=1)),
            Flags(),
            "infeasible",
            "-fpack-struct",
        ),
        # libstdc++ (gcc) library, libc++ (clang here) consumer: a std::string API cannot link.
        (
            "func_param_append",
            "cxx_stdlib",
            "c++",
            Consumer(toolchain=CLANG18),
            Flags(),
            "infeasible",
            "different C++ standard",
        ),
        # The dual ABI: an explicit =0 consumer of a default (=1) library.
        (
            "func_param_append",
            "cxx_stdlib",
            "c++",
            Consumer(flags=Flags(cxx11_abi=0)),
            Flags(),
            "infeasible",
            "_GLIBCXX_USE_CXX11_ABI=1",
        ),
        # ... and the reverse, a =0 library with a default consumer.
        (
            "func_param_append",
            "cxx_stdlib",
            "c++",
            Consumer(flags=Flags()),
            Flags(cxx11_abi=0),
            "infeasible",
            "consumer's is 1",
        ),
        # Same library on both sides, and classes without std:: types, are fine.
        (
            "func_param_append",
            "cxx_stdlib",
            "c++",
            Consumer(toolchain=GCC14),
            Flags(),
            None,
            "",
        ),
        (
            "virtual_reorder",
            "cxx_classes",
            "c++",
            Consumer(toolchain=CLANG18, std=STD["c++11"]),
            Flags(),
            None,
            "",
        ),
        (
            "func_add",
            "c_core",
            "c",
            Consumer(toolchain=CLANG18, std=STD["c89"]),
            Flags(),
            None,
            "",
        ),
    ],
)
def test_pair_blocked_names_each_infeasible_mix(
    monkeypatch: pytest.MonkeyPatch,
    op: str,
    seed: str,
    lang: str,
    consumer: Consumer,
    flags: Flags,
    want: str | None,
    needle: str,
) -> None:
    _libs(monkeypatch, libstdcxx={GCC13, GCC14})
    cfg = _cfg(lang, flags=flags, consumers=(consumer,))
    (user,) = cfg.consumer_set
    got = pair_blocked(OP_BY_NAME[op], SEED_BY_NAME[seed], cfg, user)
    if want is None:
        assert got is None
    else:
        assert got is not None and got[0] == want and needle in got[1], got


def test_a_host_gap_is_unavailable_not_infeasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _libs(monkeypatch, libstdcxx={GCC13})
    monkeypatch.setattr(
        cells,
        "consumer_unavailable",
        lambda *a, **k: "consumer toolchain gcc14 is not installed",
    )
    cfg = _cfg(consumers=(Consumer(toolchain=GCC14),))
    (user,) = cfg.consumer_set
    assert pair_blocked(OP_BY_NAME["func_add"], C_SEED, cfg, user) == (
        "unavailable",
        "consumer toolchain gcc14 is not installed",
    )


@pytest.mark.parametrize(
    ("statuses", "want"),
    [
        (("infeasible", "infeasible"), "infeasible"),
        (("unavailable", "infeasible"), "infeasible"),
        (("unavailable", "unavailable"), "unavailable"),
    ],
)
def test_a_cell_whose_every_consumer_is_blocked_never_runs_or_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    statuses: tuple[str, str],
    want: str,
) -> None:
    monkeypatch.setattr(cells, "unavailable", lambda c: None)
    monkeypatch.setattr(cells, "capability_gap", lambda op, c: None)
    monkeypatch.setattr(cells, "execution_unavailable", lambda c: None)
    order = iter(statuses)
    monkeypatch.setattr(cells, "pair_blocked", lambda *a: (next(order), "why"))

    def boom(*a: object) -> None:
        raise AssertionError("a blocked cell must not build or compare")

    monkeypatch.setattr(cells, "abicheck_answer", boom)
    cfg = _cfg(consumers=(Consumer(), Consumer(toolchain=CLANG18)))
    got = run_cell(OP_BY_NAME["func_add"], C_SEED, cfg, CellContext.at(tmp_path))
    assert got.status == want and got.reconciled is None
    assert got.reason == "gcc13-c17-O0: why; clang18-c17-O0: why"


# --- the oracle, per pair -------------------------------------------------------


def _oracle_with(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    new_output: dict[str, str],
    source_fails: set[str] = frozenset(),  # type: ignore[assignment]
) -> ExecutionOracle:
    """An oracle whose consumer *id*'s ``call`` prints ``new_output[id]`` on the new lib."""
    monkeypatch.setattr(ex, "execution_unavailable", lambda c: None)
    monkeypatch.setattr(ex, "run_probe", lambda *a: {"sizeof(point)": "8"})

    def old(self, seed, config, built, consumer):  # noqa: ANN001
        exe = tmp_path / consumer.id / "call"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.with_suffix(".c").write_text("int main(void){return 0;}\n")
        return ConsumerSet({"call": exe}, {"call": RunResult(0, "v=1\n", "")}), {
            "sizeof(point)": "8"
        }

    monkeypatch.setattr(ExecutionOracle, "_old", old)
    monkeypatch.setattr(
        ex, "_run", lambda exe, lib: RunResult(0, new_output[exe.parent.name], "")
    )

    def compile_(cmd: list[str], what: str):  # noqa: ANN202
        import subprocess

        failing = any(cid in what for cid in source_fails)
        return subprocess.CompletedProcess(
            cmd, 1 if failing else 0, "", "x.c:1: error: nope\n"
        )

    monkeypatch.setattr(ex, "_compile", compile_)
    return ExecutionOracle(tmp_path)


def test_one_breaking_consumer_is_a_break_and_every_pair_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _cfg(
        consumers=(
            Consumer(),
            Consumer(toolchain=CLANG18, std=STD["c89"]),
            Consumer(toolchain=CLANG19),
        )
    )
    me, c89, c19 = cfg.consumer_set
    oracle = _oracle_with(
        monkeypatch, tmp_path, {me.id: "v=1\n", c89.id: "v=2\n"}, source_fails={c89.id}
    )
    blocked = [(c19, "infeasible", "a reason")]
    b = _built(tmp_path)
    truth = oracle.truth(C_SEED, cfg, b, b, {}, Flags(), "t", blocked)
    assert truth.available and truth.binary_break and truth.source_break
    assert [(p.consumer, p.outcome) for p in truth.pairs] == [
        ("gcc13-c17-O0", "no-break"),
        ("clang18-c89-O0", "binary-break"),
        ("clang19-c17-O0", "infeasible"),
    ]
    assert truth.pairs[2].reason == "a reason"
    assert "run call [clang18-c89-O0]: output v=1 -> v=2" in truth.signals
    assert not truth.layout_only


def test_no_consumer_breaking_is_no_break(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _cfg(consumers=(Consumer(), Consumer(toolchain=CLANG18)))
    me, other = cfg.consumer_set
    oracle = _oracle_with(monkeypatch, tmp_path, {me.id: "v=1\n", other.id: "v=1\n"})
    b = _built(tmp_path)
    truth = oracle.truth(C_SEED, cfg, b, b, {}, Flags(), "t")
    assert truth.available and not truth.binary_break and not truth.source_break
    assert [p.outcome for p in truth.pairs] == ["no-break", "no-break"]


def test_an_oracle_with_every_pair_blocked_is_unavailable_not_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ex, "execution_unavailable", lambda c: None)
    cfg = _cfg(consumers=(Consumer(toolchain=CLANG18),))
    (user,) = cfg.consumer_set
    b = _built(tmp_path)
    truth = ExecutionOracle(tmp_path).truth(
        C_SEED, cfg, b, b, {}, Flags(), "t", [(user, "infeasible", "mix")]
    )
    assert not truth.available and truth.reason.endswith("clang18-c17-O0: mix")
    assert [p.outcome for p in truth.pairs] == ["infeasible"]


# --- tiers ------------------------------------------------------------------------


def test_the_pr_consumer_array_covers_every_pair_of_its_axes() -> None:
    rows = tiers.pr_consumer_rows()
    assert tiers.uncovered_pairs(tiers._PR_CONSUMER_FACTORS, rows) == set()
    # Independent recount of every pair of axes, by brute force.
    axes = {
        "build": ("gcc", "clang"),
        "consumer": ("same", "other"),
        "std": ("same", "oldest", "newest"),
        "seed": ("c_core", "cxx_classes"),
    }
    for a, b in [
        ("build", "consumer"),
        ("consumer", "std"),
        ("build", "std"),
        ("seed", "std"),
    ]:
        seen = {(r[a], r[b]) for r in rows}
        assert seen == {(x, y) for x in axes[a] for y in axes[b]}, (a, b)
    assert len(rows) < 2 * 2 * 3 * 2  # smaller than the full product


def test_pr_consumer_cells_match_their_static_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tcs, "discover", lambda: (CLANG18, GCC13))
    monkeypatch.setattr(tiers, "std_flag", lambda tc, std: std.name)
    monkeypatch.delenv(tcs.TOOLCHAIN_ENV, raising=False)
    monkeypatch.delenv(tcs.CONSUMER_TOOLCHAIN_ENV, raising=False)
    got = tiers.pr_consumer_cells()
    ids = tiers.pr_consumer_ids()
    assert len(got) == len(ids) and all(isinstance(c, tiers.Cell) for c in got)
    # Each carrier seed gets a breaking and a compatible edit on one build.
    by_build: dict[tuple[str, str], set[str]] = {}
    for c in got:
        assert isinstance(c, tiers.Cell)
        by_build.setdefault((c.seed.name, c.config.id), set()).add(c.op.name)
    for (seed, _), ops in by_build.items():
        if seed in tiers._PR_CARRIERS:
            assert ops == set(tiers._PR_CARRIERS[seed])
    dual = got[-1]
    assert isinstance(dual, tiers.Cell)
    assert [u.flags.cxx11_abi for u in dual.config.consumer_set] == [None, 0]
    # Some cell checks one build against more than one consumer toolchain.
    assert any(
        len({u.toolchain for u in c.config.consumer_set}) > 1
        for c in got
        if isinstance(c, tiers.Cell)
    )


def test_a_family_outside_the_selection_skips_with_the_selection_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tcs, "discover", lambda: (CLANG18, GCC13))
    monkeypatch.setattr(tiers, "std_flag", lambda tc, std: std.name)
    monkeypatch.setenv(tcs.TOOLCHAIN_ENV, "gcc-13")
    monkeypatch.delenv(tcs.CONSUMER_TOOLCHAIN_ENV, raising=False)
    skipped = [c for c in tiers.pr_consumer_cells() if not isinstance(c, tiers.Cell)]
    assert skipped and all(tcs.TOOLCHAIN_ENV in why for _, why in skipped)
    ran = [c for c in tiers.pr_consumer_cells() if isinstance(c, tiers.Cell)]
    assert {u.toolchain for c in ran for u in c.config.consumer_set} == {GCC13}


def test_a_consumer_family_outside_the_consumer_selection_names_that_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tcs, "discover", lambda: (CLANG18, GCC13))
    monkeypatch.setattr(tiers, "std_flag", lambda tc, std: std.name)
    monkeypatch.delenv(tcs.TOOLCHAIN_ENV, raising=False)
    monkeypatch.setenv(tcs.CONSUMER_TOOLCHAIN_ENV, "gcc-13")
    skipped = dict(
        c for c in tiers.pr_consumer_cells() if not isinstance(c, tiers.Cell)
    )
    # clang is installed: only the consumer selection excludes it.
    assert skipped and all(
        why == f"no selected consumer clang toolchain ({tcs.CONSUMER_TOOLCHAIN_ENV})"
        for why in skipped.values()
    ), skipped
    # With only the build selection set, consumers inherit it and say so.
    monkeypatch.delenv(tcs.CONSUMER_TOOLCHAIN_ENV)
    monkeypatch.setenv(tcs.TOOLCHAIN_ENV, "gcc-13")
    reasons = {
        why
        for c in tiers.pr_consumer_cells()
        if not isinstance(c, tiers.Cell)
        for why in [c[1]]
    }
    assert f"no selected consumer clang toolchain ({tcs.TOOLCHAIN_ENV})" in reasons


def test_weekly_checks_each_build_against_the_full_consumer_cross(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = {"c89", "c99", "c11", "c17"}  # this fake host has no c23
    monkeypatch.setattr(
        tiers, "std_flag", lambda tc, std: std.name if std.name in accepted else None
    )
    got = tiers.weekly_consumers(OP_BY_NAME["func_add"], "c", GCC13, (CLANG18, GCC13))
    assert got == (
        Consumer(),
        Consumer(toolchain=CLANG18),
        Consumer(toolchain=CLANG18, std=STD["c89"]),
        Consumer(toolchain=CLANG18, std=STD["c17"]),
        Consumer(std=STD["c89"]),
        Consumer(std=STD["c17"]),
    )
    # An edit that needs `inline` never draws a consumer standard without it.
    inl = tiers.weekly_consumers(
        OP_BY_NAME["func_out_of_line_to_inline"], "c++", GCC13, (GCC13,)
    )
    assert all(c.std is None or c.std.year >= 2011 for c in inl)


def test_a_nightly_row_checks_the_build_and_the_drawn_consumer() -> None:
    row = {
        "cell": "func_add@c_core",
        "producer": "gcc13",
        "consumer": "clang18",
        "consumer_std": "same",
        "std": 3,
        "opt": "O0",
        "visibility": "default",
        "bits": 64,
        "short_enums": False,
        "pack": None,
        "cxx11_abi": None,
        "abi_version": None,
        "consumer_flags": "same",
        "evidence": "headers",
    }
    cell = tiers.row_to_cell(row, (GCC13,), (CLANG18,))
    assert cell is not None
    assert [u.toolchain for u in cell.config.consumer_set] == [GCC13, CLANG18]
    same = tiers.row_to_cell({**row, "consumer": "same"}, (GCC13,), (CLANG18,))
    assert same is not None and same.config.consumers == ()


# --- reporting ----------------------------------------------------------------------


def test_cells_sharing_an_id_keep_every_pair_file(tmp_path: Path) -> None:
    def cell(op: str) -> CellResult:
        return CellResult(op, "c_core", _cfg(), "unavailable", f"why {op}")

    # The same id twice, and two ids that sanitize to one name.
    paths = [
        write_cell(tmp_path, "a-b", cell("one")),
        write_cell(tmp_path, "a-b", cell("two")),
        write_cell(tmp_path, "x/y", cell("three")),
        write_cell(tmp_path, "x_y", cell("four")),
    ]
    assert [p.name for p in paths] == [
        "a-b.json",
        "a-b~2.json",
        "x_y.json",
        "x_y~2.json",
    ]
    assert sorted(r["op"] for r in load(tmp_path)) == ["four", "one", "three", "two"]


def test_the_summary_keeps_a_hyphenated_toolchain_id() -> None:
    rows = [
        {
            "op": "o",
            "seed": "s",
            "build": "b",
            "producer": "apple-clang15",
            "consumer": "apple-clang15-c17-O0+se",
            "outcome": "no-break",
            "cell": "agree",
            "detail": "",
        },
        {
            "op": "o",
            "seed": "s",
            "build": "b",
            "producer": "apple-clang15",
            "consumer": "apple-clang15-c17-O0",
            "outcome": "binary-break",
            "cell": "agree",
            "detail": "",
        },
    ]
    text = summarize(rows)
    assert "| `apple-clang15-c17` | 1 | 1 |" in text
    assert "| apple-clang15 | apple-clang15 | 1 | 1 |" in text


def test_the_pair_report_counts_outcomes_and_lists_every_blocked_pair(
    tmp_path: Path,
) -> None:
    cfg = _cfg(
        consumers=(Consumer(), Consumer(toolchain=CLANG18), Consumer(toolchain=CLANG19))
    )
    truth = ExecTruth(
        available=True,
        binary_break=True,
        pairs=(
            PairTruth(
                "gcc13-c17-O0", "ran", binary_break=True, signals=("run call: x",)
            ),
            PairTruth("clang18-c17-O0", "ran"),
            PairTruth("clang19-c17-O0", "infeasible", reason="libc++ vs libstdc++"),
        ),
    )
    ran = CellResult("func_add", "c_core", cfg, "ran", exec_truth=truth)
    skipped = CellResult(
        "func_add", "c_core", _cfg(std=STD["c89"]), "unavailable", "no c89 here"
    )
    write_cell(tmp_path, "a", ran)
    write_cell(tmp_path, "b", skipped)
    rows = load(tmp_path)
    assert [(r["consumer"], r["outcome"]) for r in rows] == [
        ("gcc13-c17-O0", "binary-break"),
        ("clang18-c17-O0", "no-break"),
        ("clang19-c17-O0", "infeasible"),
        ("*", "unavailable"),
    ]
    assert (
        json.loads((tmp_path / "a.json").read_text())[0]["build"]
        == "c17-gcc13-O0-headers"
    )
    text = summarize(rows)
    assert text.startswith("4 (build, consumer) pairs over 2 library builds.")
    assert "| gcc13 | clang19 | 0 | 1 | 0 | 0 |" in text
    assert "- infeasible x1: libc++ vs libstdc++" in text
    assert "- unavailable x1: no c89 here" in text


# --- the workflow ---------------------------------------------------------------------

_WF = "edit-corpus-tiers.yml"


def test_the_workflow_schedules_both_tiers_with_minimal_permissions() -> None:
    wf = load_workflow(_WF)
    on = wf[True] if True in wf else wf["on"]
    assert {c["cron"] for c in on["schedule"]} == {"17 3 * * *", "43 5 * * 0"}
    assert {"toolchains", "consumer_toolchains", "tier", "seed"} <= set(
        on["workflow_dispatch"]["inputs"]
    )
    assert wf["permissions"] == {"contents": "read"}
    jobs = wf["jobs"]
    writers = {n for n, j in jobs.items() if "permissions" in j}
    assert writers == {"notify"} and jobs["notify"]["permissions"] == {
        "issues": "write"
    }
    assert "github.event_name == 'schedule'" in jobs["notify"]["if"]
    # A failed plan, corpus or report job each reaches the tracking issue.
    for job in ("plan", "corpus", "report"):
        assert f"needs.{job}.result == 'failure'" in jobs["notify"]["if"]
    assert set(jobs["notify"]["needs"]) == {"plan", "corpus", "report"}
    assert "refs/heads/main" in jobs["plan"]["if"]
    uploads = [
        s
        for j in jobs.values()
        for s in j["steps"]
        if "upload-artifact" in s.get("uses", "")
    ]
    assert uploads and all(s.get("if") == "always()" for s in uploads)
    assert "always()" in jobs["report"]["if"]
    # Only GITHUB_TOKEN: no secrets referenced anywhere.
    assert "secrets." not in Path(".github/workflows", _WF).read_text(encoding="utf-8")


def _plan(tmp_path: Path, **inputs: str) -> object:
    require_bash()
    step = find_run_step(_WF, "plan", "Select tier, seed and toolchains")
    env = {
        "BASE_PACKAGES": "gcc g++",
        "EVENT_NAME": "workflow_dispatch",
        "EVENT_SCHEDULE": "",
        "INPUT_TIER": "nightly",
        "INPUT_SEED": "4",
        "INPUT_TOOLCHAINS": "",
        "INPUT_CONSUMERS": "",
        **inputs,
    }
    ws = tmp_path / "ws"
    ws.mkdir()
    return run_step(step, workspace=ws, env=env)


@pytest.mark.parametrize(
    ("toolchains", "consumers", "extra"),
    [
        ("", "", ""),
        ("gcc-12", "", " gcc-12 g++-12"),
        ("gcc13", "clang-17, clang", " gcc-13 g++-13 clang-17"),
        # A name already in the base set is not installed twice.
        ("gcc-12,gcc-12", "gcc", " gcc-12 g++-12"),
    ],
)
def test_the_plan_step_installs_each_requested_versioned_toolchain(
    tmp_path: Path, toolchains: str, consumers: str, extra: str
) -> None:
    res = _plan(tmp_path, INPUT_TOOLCHAINS=toolchains, INPUT_CONSUMERS=consumers)
    assert res.returncode == 0, res.stderr  # type: ignore[attr-defined]
    out = res.outputs  # type: ignore[attr-defined]
    assert out["packages"] == "gcc g++" + extra
    # The outputs are the validated names, rejoined without whitespace.
    norm = {
        t: ",".join(x.strip() for x in t.split(",") if x.strip())
        for t in (toolchains, consumers)
    }
    assert (out["toolchains"], out["consumer_toolchains"]) == (
        norm[toolchains],
        norm[consumers],
    )
    # Exactly the five records, whatever the inputs.
    assert len(res.output_lines) == 5  # type: ignore[attr-defined]
    # Every name the step accepts, the harness accepts too.
    parse_selection(toolchains)
    parse_selection(consumers)


@pytest.mark.parametrize(
    "bad",
    [
        "gcc-14;rm -rf /",
        "$(whoami)",
        "g++-14",
        "icc",
        "gcc-14\nseed=1",
        "gcc-14\rseed=1",
        "clang-",
        "gcc-14,packages=evil",
    ],
)
def test_the_plan_step_rejects_a_malformed_toolchain_name(
    tmp_path: Path, bad: str
) -> None:
    res = _plan(tmp_path, INPUT_TOOLCHAINS=bad)
    assert res.returncode != 0  # type: ignore[attr-defined]
    # Nothing is written: no injected record, no package list.
    assert res.output_lines == []  # type: ignore[attr-defined]
    assert "::error::" in res.stdout  # type: ignore[attr-defined]


# --- link options -------------------------------------------------------------


def test_link_options_follow_the_defaults_and_their_files_are_written_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    seen: list[list[str]] = []

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        # The version script exists when the linker runs.
        assert (tmp_path / "out" / "edit.map").read_text() == "V1 { global: *; };\n"
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(harness.subprocess, "run", run)
    monkeypatch.setattr(harness.sys, "platform", "linux")
    link = harness.LinkSpec(
        flags=(
            "-Wl,--version-script={dir}/edit.map",
            "-Wl,-soname,libedit.so.2",
            "-lm",
        ),
        files=(("edit.map", "V1 { global: *; };\n"),),
    )
    harness._compile_library(
        C_SEED, _cfg(), Flags(), tmp_path / "out", "int x;\n", "int x;\n", link
    )
    (cmd,) = seen
    out = str(tmp_path / "out")
    assert cmd[-4:] == [
        "-Wl,-soname,libedit.so",
        f"-Wl,--version-script={out}/edit.map",
        "-Wl,-soname,libedit.so.2",  # after the default, so it wins
        "-lm",
    ]
    # Without link options the command ends at the harness's own SONAME.
    seen.clear()
    harness._compile_library(C_SEED, _cfg(), Flags(), tmp_path / "plain", "", "")
    assert seen[0][-1] == "-Wl,-soname,libedit.so"
