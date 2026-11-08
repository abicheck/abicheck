"""Generated ABI corpus v2: operators x seeds x configurations, three-signal oracle.

Each cell ``(operator, seed, Config)`` is built with a real toolchain, compared
by abicheck through its public API, *and executed*: consumers compiled against
the old header run against the old and the new library, layout probes measure
``sizeof``/``offsetof``, and consumers recompiled against the new header give
source-break truth (``_edit_corpus_exec``). ``reconcile`` classifies the cell
from the execution result, the operator's ``expect(config)`` and abicheck's
answer; false-clean ranks first (``_edit_corpus_expect``).

Tiers (``_edit_corpus_tiers``), selected by marker, never by a new CI job:

- PR: ``integration`` only, a fixed handful of cells (seconds);
- nightly: ``-m edit_corpus_nightly``, a seeded pairwise covering array
  (``ABICHECK_EDIT_CORPUS_SEED``, default 0);
- weekly: ``-m edit_corpus_weekly``, every operator under the newest
  toolchains with -m32, newest standards and exotic flags.

The nightly/weekly cell lists are generated only when their marker is
selected (``pytest_generate_tests``), so collecting this module never probes
a compiler. A cell whose toolchain is missing is skipped with the reason,
never passed. A mismatch is a strict xfail only while it reproduces exactly as
recorded in ``_edit_corpus_known``; any other failure prints the cell
minimized to (seed, edit, config) as a ready-to-paste regression test.
"""

from __future__ import annotations

import ast
import os
from dataclasses import replace
from pathlib import Path

import pytest

from abicheck.model.change_catalog.kinds import ChangeKind
from tests._edit_corpus_cells import (
    CellContext,
    CellResult,
    _shrink_steps,
    minimize,
    regression_test,
    run_cell,
)
from tests._edit_corpus_config import (
    EVIDENCE,
    STANDARD_BY_NAME,
    STANDARDS,
    Config,
    Flags,
    Toolchain,
    default_config,
    has_feature,
)
from tests._edit_corpus_exec import probe_expressions
from tests._edit_corpus_expect import (
    MISMATCH_CLASSES,
    PASSING_CLASSES,
    ExecTruth,
    Expectation,
    reconcile,
)
from tests._edit_corpus_known import KNOWN, known_mismatch
from tests._edit_corpus_operators import OP_BY_NAME, OPERATORS, matrix
from tests._edit_corpus_seeds import C_SEED, SEED_BY_NAME
from tests._edit_corpus_tiers import _PR, Cell, pairwise, pr_cells, uncovered_pairs

GCC = Toolchain("gcc", 13, "gcc", "g++")
CLANG = Toolchain("clang", 18, "clang", "clang++")
MSVC = Toolchain("msvc", 19, "cl", "cl")


def _cfg(lang: str = "c", **kw: object) -> Config:
    return replace(default_config(lang, GCC), **kw)  # type: ignore[arg-type]


# --- fast: configuration model ----------------------------------------------


@pytest.mark.parametrize(
    ("flags", "tc", "lang", "want"),
    [
        (Flags(), GCC, "c", ["-O0"]),
        (
            Flags(opt="O2", visibility="hidden", bits=32, short_enums=True, pack=1),
            CLANG,
            "c",
            ["-O2", "-fvisibility=hidden", "-m32", "-fshort-enums", "-fpack-struct=1"],
        ),
        (
            Flags(cxx11_abi=0, abi_version=11),
            GCC,
            "c++",
            ["-O0", "-D_GLIBCXX_USE_CXX11_ABI=0", "-fabi-version=11"],
        ),
        (Flags(opt="O2", pack=4, vtordisp=2), MSVC, "c++", ["/O2", "/Zp4", "/vd2"]),
    ],
)
def test_flags_render_per_family(
    flags: Flags, tc: Toolchain, lang: str, want: list[str]
) -> None:
    assert flags.render(tc, lang) == want


@pytest.mark.parametrize(
    ("flags", "tc", "lang", "why"),
    [
        (Flags(abi_version=11), CLANG, "c", "gcc flag"),
        (Flags(cxx11_abi=0), GCC, "c", "C++ macro"),
        (Flags(vtordisp=1), GCC, "c++", "MSVC flag"),
        (Flags(short_enums=True), MSVC, "c", "no -fshort-enums"),
    ],
)
def test_flags_without_a_spelling_say_why(
    flags: Flags, tc: Toolchain, lang: str, why: str
) -> None:
    reason = flags.unsupported_reason(tc, lang)
    assert reason is not None and why in reason


def test_feature_table_matches_the_standards() -> None:
    # Independent statement of when each feature arrived (ISO publication).
    want = {
        ("noexcept_in_type", "c++14"): False,
        ("noexcept_in_type", "c++17"): True,
        ("concepts", "c++17"): False,
        ("concepts", "c++20"): True,
        ("no_unique_address", "c++20"): True,
        ("enum_underlying", "c17"): False,
        ("enum_underlying", "c23"): True,
        ("enum_underlying", "c++11"): True,
        ("inline", "c89"): False,
        ("inline", "c99"): True,
        ("enum_class", "c23"): False,
    }
    got = {(f, s): has_feature(STANDARD_BY_NAME[s], f) for f, s in want}
    assert got == want


def test_config_ids_are_unique_across_axes() -> None:
    configs = [
        _cfg(evidence=e, flags=f, std=s)
        for e in EVIDENCE
        for f in (
            Flags(),
            Flags(short_enums=True),
            Flags(pack=1),
            Flags(visibility="hidden"),
        )
        for s in (STANDARD_BY_NAME["c89"], STANDARD_BY_NAME["c23"])
    ]
    configs.append(_cfg(consumer=CLANG))
    ids = [c.id for c in configs]
    assert len(ids) == len(set(ids))


def test_a_clang_consumer_drops_the_gcc_only_abi_version() -> None:
    c = _cfg(flags=Flags(abi_version=11), consumer=CLANG)
    assert c.consumer_fl.abi_version is None
    assert _cfg(flags=Flags(abi_version=11)).consumer_fl.abi_version == 11


# --- fast: operators' config-dependent expectations -------------------------


def _verdict_band(op: str, config: Config) -> tuple[str, str | None, bool]:
    e = OP_BY_NAME[op].expect(config)
    return e.verdict_min, e.verdict_max, e.observable


@pytest.mark.parametrize(
    ("op", "config", "band"),
    [
        (
            "enum_append_wide_value",
            _cfg(),
            ("COMPATIBLE", "COMPATIBLE_WITH_RISK", True),
        ),
        (
            "enum_append_wide_value",
            _cfg(flags=Flags(short_enums=True)),
            ("BREAKING", None, True),
        ),
        ("struct_array_shrink_in_padding", _cfg(), ("API_BREAK", None, True)),
        (
            "struct_array_shrink_in_padding",
            _cfg(flags=Flags(pack=1)),
            ("BREAKING", None, True),
        ),
        ("func_lose_default_visibility", _cfg(), ("NO_CHANGE", "NO_CHANGE", True)),
        (
            "func_lose_default_visibility",
            _cfg(flags=Flags(visibility="hidden")),
            ("BREAKING", None, True),
        ),
        (
            "func_lose_default_visibility",
            _cfg(flags=Flags(visibility="hidden"), evidence="binary"),
            ("BREAKING", None, True),
        ),
        (
            "enum_underlying_narrow",
            _cfg(std=STANDARD_BY_NAME["c23"]),
            ("BREAKING", None, True),
        ),
        (
            "enum_underlying_narrow",
            _cfg(std=STANDARD_BY_NAME["c23"], flags=Flags(short_enums=True)),
            ("NO_CHANGE", "COMPATIBLE_WITH_RISK", True),
        ),
        ("struct_append_field", _cfg(evidence="binary"), ("NO_CHANGE", None, False)),
        ("struct_append_field", _cfg(evidence="dwarf"), ("BREAKING", None, True)),
        ("func_remove", _cfg(evidence="binary"), ("BREAKING", None, True)),
        (
            "no_unique_address_added",
            _cfg("c++", std=STANDARD_BY_NAME["c++20"]),
            ("BREAKING", None, True),
        ),
        (
            "no_unique_address_added",
            _cfg("c++", std=STANDARD_BY_NAME["c++20"], platform=("win32", "pe")),
            ("NO_CHANGE", "COMPATIBLE_WITH_RISK", True),
        ),
        ("method_noexcept_add", _cfg("c++"), ("API_BREAK", "API_BREAK", True)),
        (
            "method_noexcept_add",
            _cfg("c++", evidence="dwarf"),
            ("NO_CHANGE", "API_BREAK", False),
        ),
    ],
)
def test_expectation_is_a_function_of_the_config(
    op: str, config: Config, band: tuple
) -> None:
    assert _verdict_band(op, config) == band


def test_cxx_signature_edits_are_visible_from_symbols_alone() -> None:
    cxx_bin = _cfg("c++", evidence="binary")
    c_bin = _cfg("c", evidence="binary")
    e = OP_BY_NAME["func_param_append"].expect(cxx_bin)
    assert e.observable and e.any_of and "FUNC_REMOVED" in e.any_of[0]
    assert not OP_BY_NAME["func_param_append"].expect(c_bin).observable


@pytest.mark.parametrize(
    ("op", "std", "applies"),
    [
        ("no_unique_address_added", "c++17", False),
        ("no_unique_address_added", "c++20", True),
        ("concept_constraint_added", "c++17", False),
        ("concept_constraint_added", "c++20", True),
        ("enum_underlying_narrow", "c17", False),
        ("enum_underlying_narrow", "c23", True),
        ("static_inline_helper_added", "c89", False),
        ("static_inline_helper_added", "c99", True),
    ],
)
def test_requires_gates_on_language_features(op: str, std: str, applies: bool) -> None:
    s = STANDARD_BY_NAME[std]
    config = _cfg(s.lang, std=s)
    assert (OP_BY_NAME[op].requires(config) is None) is applies


def test_rebuild_preconditions_reject_a_no_op_rebuild() -> None:
    assert OP_BY_NAME["rebuild_short_enums"].requires(
        _cfg(flags=Flags(short_enums=True))
    )
    assert OP_BY_NAME["rebuild_short_enums"].requires(_cfg()) is None
    assert OP_BY_NAME["rebuild_cxx11_abi_off"].requires(
        _cfg("c++", flags=Flags(cxx11_abi=0))
    )


def test_every_rule_fires_for_some_config() -> None:
    # Vacuity guard: a rule whose predicate never holds is dead ground truth.
    probes = [
        _cfg(lang, std=s, flags=f, evidence=e, platform=p)
        for lang in ("c", "c++")
        for s in STANDARDS
        if s.lang == lang
        for f in (
            Flags(),
            Flags(short_enums=True),
            Flags(pack=1),
            Flags(visibility="hidden"),
        )
        for e in EVIDENCE
        for p in (("linux", "elf"), ("win32", "pe"))
    ]
    for op in OPERATORS:
        for rule in op.rules:
            assert any(rule.when(c) for c in probes), (op.name, rule.why)
            assert any(not rule.when(c) for c in probes), (op.name, rule.why)


def test_every_expectation_band_is_ordered_for_every_probe_config() -> None:
    from tests._edit_corpus_expect import VERDICT_RANK

    for op in OPERATORS:
        for e in EVIDENCE:
            for f in (
                Flags(),
                Flags(short_enums=True),
                Flags(pack=1),
                Flags(visibility="hidden"),
            ):
                exp = op.expect(_cfg(evidence=e, flags=f))
                if exp.verdict_max is not None:
                    assert (
                        VERDICT_RANK[exp.verdict_min] <= VERDICT_RANK[exp.verdict_max]
                    ), op.name


def test_operator_kinds_are_real_changekinds_under_every_rule() -> None:
    valid = {k.name for k in ChangeKind}
    for op in OPERATORS:
        exps = [op.base(), *(r.apply(op.base()) for r in op.rules)]
        for exp in exps:
            named = (
                set(exp.expected).union(*exp.any_of)
                if exp.any_of
                else set(exp.expected)
            )
            assert named <= valid, (op.name, sorted(named - valid))


# --- fast: reconciliation ----------------------------------------------------

_BREAK = Expectation(any_of=(frozenset({"TYPE_SIZE_CHANGED"}),))
_BENIGN = Expectation(verdict_min="NO_CHANGE", verdict_max="NO_CHANGE")
_BROKE = ExecTruth(available=True, binary_break=True, signals=("run alloc: x",))
_SRC = ExecTruth(available=True, source_break=True, signals=("source x",))
_QUIET = ExecTruth(available=True)
_NONE = ExecTruth(available=False, reason="no loader")
_HIDDEN = replace(_BREAK, any_of=(), verdict_min="NO_CHANGE", observable=False)


@pytest.mark.parametrize(
    ("truth", "exp", "verdict", "kinds", "cls"),
    [
        (_BROKE, _BREAK, "BREAKING", {"TYPE_SIZE_CHANGED"}, "agree"),
        (_BROKE, _BREAK, "COMPATIBLE", set(), "false-clean"),
        (
            _SRC,
            replace(_BREAK, verdict_min="API_BREAK"),
            "COMPATIBLE_WITH_RISK",
            set(),
            "false-clean",
        ),
        (_BROKE, _HIDDEN, "NO_CHANGE", set(), "unobservable"),
        (_QUIET, _BENIGN, "BREAKING", set(), "false-positive"),
        (_BROKE, _BENIGN, "NO_CHANGE", set(), "oracle-conflict"),
        (_QUIET, _BREAK, "BREAKING", {"TYPE_SIZE_CHANGED"}, "non-observable"),
        (_QUIET, _BREAK, "BREAKING", {"FUNC_REMOVED"}, "wrong-kind"),
        (_QUIET, _BREAK, "API_BREAK", {"TYPE_SIZE_CHANGED"}, "wrong-verdict"),
        (_NONE, _BREAK, "COMPATIBLE", set(), "wrong-verdict"),
        (_NONE, _BREAK, "BREAKING", {"TYPE_SIZE_CHANGED"}, "agree"),
        (_BROKE, _BREAK, "REFUSED", {"ProfileMismatchError"}, "refused"),
    ],
)
def test_reconcile_classifies_the_three_signals(
    truth: ExecTruth, exp: Expectation, verdict: str, kinds: set[str], cls: str
) -> None:
    assert reconcile(truth, exp, verdict, frozenset(kinds)).cls == cls


def test_unavailable_execution_is_never_read_as_no_break() -> None:
    # A false-clean needs execution evidence; without it the same answer is only
    # a verdict mismatch against the expectation, never a pass.
    got = reconcile(_NONE, _BREAK, "NO_CHANGE", frozenset())
    assert got.cls == "wrong-verdict" and got.is_mismatch


# --- fast: covering array, minimizer, printer --------------------------------


def test_pairwise_covers_every_pair_by_an_independent_count() -> None:
    factors = {"a": [1, 2, 3], "b": ["x", "y"], "c": [True, False], "d": [0, 1, 2, 3]}
    rows = pairwise(factors, seed=7)
    assert uncovered_pairs(factors, rows) == set()
    # Brute-force recount, not the generator's own bookkeeping.
    for x, y in [("a", "d"), ("b", "c"), ("a", "b")]:
        seen = {(r[x], r[y]) for r in rows}
        assert seen == {(vx, vy) for vx in factors[x] for vy in factors[y]}
    assert len(rows) < 3 * 2 * 2 * 4  # strictly smaller than the full product


def test_pairwise_is_reproducible_from_its_seed() -> None:
    factors = {"a": list(range(5)), "b": list(range(4)), "c": list(range(3))}
    assert pairwise(factors, 3) == pairwise(factors, 3)
    assert pairwise(factors, 3) != pairwise(factors, 4)


def test_shrink_steps_move_one_axis_towards_the_default() -> None:
    c = _cfg(
        flags=Flags(opt="O2", short_enums=True, pack=1),
        evidence="binary",
        std=STANDARD_BY_NAME["c89"],
        consumer=CLANG,
    )
    steps = dict(_shrink_steps(c))
    assert set(steps) == {
        "flags.opt",
        "flags.short_enums",
        "flags.pack",
        "consumer",
        "std",
        "evidence",
    }
    assert steps["flags.pack"].flags == Flags(opt="O2", short_enums=True)
    assert steps["evidence"].evidence == "headers"
    assert _shrink_steps(_cfg()) == []


def test_minimize_keeps_only_the_axis_that_reproduces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tests._edit_corpus_cells as cells

    def fake_run(op, seed, config, ctx):  # reproduces iff -fshort-enums is on
        cls = "false-clean" if config.flags.short_enums else "agree"
        from tests._edit_corpus_expect import Reconciled

        return CellResult(
            op.name, seed.name, config, "ran", reconciled=Reconciled(cls, ())
        )

    monkeypatch.setattr(cells, "run_cell", fake_run)
    start = _cfg(flags=Flags(opt="O2", short_enums=True, pack=1), evidence="dwarf")
    failing = fake_run(OP_BY_NAME["enum_append_wide_value"], C_SEED, start, None)
    got = minimize(OP_BY_NAME["enum_append_wide_value"], C_SEED, failing, None)  # type: ignore[arg-type]
    assert got.config.flags == Flags(short_enums=True)
    assert got.config.evidence == "headers"


def test_regression_printer_emits_valid_python_naming_the_cell() -> None:
    from tests._edit_corpus_expect import Reconciled

    cell = CellResult(
        "enum_append_wide_value",
        "c_core",
        _cfg(flags=Flags(short_enums=True), evidence="dwarf"),
        "ran",
        verdict="COMPATIBLE",
        reconciled=Reconciled(
            "false-clean", ("verdict COMPATIBLE weaker than required BREAKING",)
        ),
    )
    text = regression_test(cell)
    tree = ast.parse(text)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert len(fns) == 1 and fns[0].name.startswith("test_edit_corpus_regression_")
    # Paste-ready: every module-level name the body uses is imported by the text.
    imported = {
        a.asname or a.name.split(".")[0]
        for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    used = {n.id for n in ast.walk(fns[0]) if isinstance(n, ast.Name)}
    assert used - imported <= {"tmp_path", "config", "cell"}
    assert 'OP_BY_NAME["enum_append_wide_value"]' in text
    assert "Flags(short_enums=True)" in text and 'evidence="dwarf"' in text


# --- fast: probes, tiers, known mismatches -----------------------------------


def test_layout_probe_reads_every_record_and_enum_of_the_c_seed() -> None:
    keys = set(probe_expressions(dict(C_SEED.roles), "c"))
    want = {
        "sizeof(point)",
        "offsetof(point,x)",
        "offsetof(point,y)",
        "sizeof(rec)",
        "offsetof(rec,id)",
        "offsetof(rec,weight)",
        "offsetof(rec,tag)",
        "sizeof(enum color)",
        "color::RED",
        "color::GREEN",
        "color::BLUE",
    }
    assert keys == want


def test_layout_probe_skips_templates_and_reads_scoped_enums() -> None:
    keys = set(probe_expressions(dict(SEED_BY_NAME["cxx_classes"].roles), "c++"))
    assert "sizeof(Buf)" not in keys
    assert {
        "mode::fast",
        "mode::slow",
        "sizeof(enum mode)",
        "sizeof(Shape)",
        "offsetof(Tagged,v)",
    } <= keys


def test_pr_tier_guards_every_axis() -> None:
    changes = [ch for _, _, _, ch in _PR]
    flag_sets = [ch["flags"] for ch in changes if "flags" in ch]
    assert any(f.short_enums for f in flag_sets)
    assert any(f.pack for f in flag_sets)
    assert any(f.visibility == "hidden" for f in flag_sets)
    assert {ch.get("evidence") for ch in changes} >= {"binary", "dwarf"}
    assert any("consumer" in ch for ch in changes)
    assert {ch.get("std") for ch in changes} >= {"c++20", "c23", "c89"}
    assert {fam for _, _, fam, _ in _PR} == {"gcc", "clang"}
    assert all(op in OP_BY_NAME and seed in SEED_BY_NAME for op, seed, _, _ in _PR)
    assert len(_PR) <= 16  # the PR tier stays a handful


def test_known_mismatches_name_real_cells_and_failing_classes() -> None:
    pairs = {(op.name, seed.name) for op, seed in matrix()}
    for k in KNOWN:
        assert k.cls in MISMATCH_CLASSES and k.cls != "oracle-conflict", k
        for seed in k.seeds:
            assert (k.op, seed) in pairs, k
        assert k.gap, k


def test_known_mismatches_point_at_a_known_gaps_heading() -> None:
    page = (
        Path(__file__).resolve().parents[1] / "docs/contribute/known-gaps.md"
    ).read_text(encoding="utf-8")
    headings = {
        line.lstrip("#").strip().rsplit(" (", 1)[0]
        for line in page.splitlines()
        if line.startswith("#### ")
    }
    missing = sorted({k.gap for k in KNOWN} - headings)
    assert missing == []


# --- integration -------------------------------------------------------------


@pytest.fixture(scope="session")
def cell_ctx(tmp_path_factory: pytest.TempPathFactory) -> CellContext:
    return CellContext.at(tmp_path_factory.mktemp("edit_corpus_v2"))


def _minimize_enabled() -> bool:
    return os.environ.get("ABICHECK_EDIT_CORPUS_MINIMIZE", "1") != "0"


def check_cell(cell: Cell, ctx: CellContext) -> None:
    result = run_cell(cell.op, cell.seed, cell.config, ctx)
    if result.status != "ran":
        pytest.skip(f"{result.status}: {result.reason}")
    known = known_mismatch(cell.op.name, cell.seed.name, cell.config)
    if known is not None:
        assert result.cls == known.cls, (
            f"recorded {known.cls} no longer reproduces as recorded; the detector may be "
            f"fixed: remove the _edit_corpus_known entry and its known-gaps row.\n{result.describe()}"
        )
        pytest.xfail(f"{known.cls}: {known.reason}")
    if result.cls in PASSING_CLASSES:
        return
    report = result.describe()
    if _minimize_enabled():
        small = minimize(cell.op, cell.seed, result, ctx)
        report += (
            "\n\nminimized:\n" + small.describe() + "\n\n" + regression_test(small)
        )
    pytest.fail(report)


@pytest.mark.integration
@pytest.mark.parametrize(
    "index", range(len(_PR)), ids=[f"{o}-{s}-{f}" for o, s, f, _ in _PR]
)
def test_pr_tier_cell(index: int, cell_ctx: CellContext) -> None:
    cell = pr_cells()[index]
    if not isinstance(cell, Cell):
        pytest.skip(cell[1])
    check_cell(cell, cell_ctx)


def _tier_selected(config: pytest.Config, marker: str) -> bool:
    expr = config.option.markexpr or ""
    return marker in expr and f"not {marker}" not in expr


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    from tests import _edit_corpus_tiers as tiers

    for name, marker, gen in (
        ("nightly_cell", "edit_corpus_nightly", tiers.nightly_cells),
        ("weekly_cell", "edit_corpus_weekly", tiers.weekly_cells),
    ):
        if name in metafunc.fixturenames:
            cells = gen() if _tier_selected(metafunc.config, marker) else []
            metafunc.parametrize(name, cells, ids=[c.id for c in cells])


@pytest.mark.integration
@pytest.mark.edit_corpus_nightly
def test_nightly_tier_cell(nightly_cell: Cell, cell_ctx: CellContext) -> None:
    check_cell(nightly_cell, cell_ctx)


@pytest.mark.integration
@pytest.mark.edit_corpus_weekly
def test_weekly_tier_cell(weekly_cell: Cell, cell_ctx: CellContext) -> None:
    check_cell(weekly_cell, cell_ctx)
