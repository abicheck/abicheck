"""Generated ABI corpus v2: operators x seeds x configurations, three-signal oracle.

Each cell ``(operator, seed, Config)`` is built with a real toolchain, compared
by abicheck through its public API, *and executed*: consumers compiled against
the old header run against the old and the new library, layout probes measure
``sizeof``/``offsetof``, and consumers recompiled against the new header give
source-break truth (``abiforge.run.exec``). ``reconcile`` classifies the cell
from the execution result, the operator's ``expect(config)`` and abicheck's
answer; false-clean ranks first (``abiforge.model.expect``).

Tiers (``abiforge.run.tiers``), selected by marker, never by a new CI job:

- PR: ``integration`` only, a fixed handful of cells (seconds);
- nightly: ``-m edit_corpus_nightly``, a seeded pairwise covering array
  (``ABICHECK_EDIT_CORPUS_SEED``, default 0);
- weekly: ``-m edit_corpus_weekly``, every operator under the newest
  toolchains with -m32, newest standards and exotic flags.

The nightly/weekly cell lists are generated only when their marker is
selected (``pytest_generate_tests``), so collecting this module never probes
a compiler. A cell whose toolchain is missing is skipped with the reason,
never passed. A mismatch is a strict xfail only while it reproduces exactly as
recorded in ``abiforge.known``; any other failure prints the cell
minimized to (seed, edit, config) as a ready-to-paste regression test.
"""

from __future__ import annotations

import ast
import os
from dataclasses import replace
from pathlib import Path

import pytest

from abicheck.model.change_catalog.kinds import ChangeKind
from abiforge.coverage import make_record, ran_consumers, write_record
from abiforge.known import KNOWN, known_mismatch
from abiforge.model.config import (
    EVIDENCE,
    STANDARD_BY_NAME,
    STANDARDS,
    Config,
    Consumer,
    Flags,
    Toolchain,
    default_config,
    has_feature,
)
from abiforge.model.expect import (
    MISMATCH_CLASSES,
    PASSING_CLASSES,
    ExecTruth,
    Expectation,
    issue_keys,
    reconcile,
)
from abiforge.model.seeds import C_SEED, SEED_BY_NAME, SEEDS
from abiforge.operators import OP_BY_NAME, OPERATORS, matrix
from abiforge.operators.build_data import BUILD_DATA_OPERATORS
from abiforge.run.cells import (
    CellContext,
    CellResult,
    _shrink_steps,
    minimize,
    regression_test,
    run_cell,
)
from abiforge.run.exec import probe_expressions
from abiforge.run.tiers import (
    _PR,
    BUILD_DATA_PR,
    Cell,
    pairwise,
    pr_cells,
    pr_consumer_cells,
    pr_consumer_ids,
    uncovered_pairs,
)

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
    configs.append(_cfg(consumers=(Consumer(toolchain=CLANG),)))
    configs.append(_cfg(consumers=(Consumer(), Consumer(toolchain=CLANG))))
    configs.append(
        _cfg(consumers=(Consumer(toolchain=CLANG, std=STANDARD_BY_NAME["c89"]),))
    )
    ids = [c.id for c in configs]
    assert len(ids) == len(set(ids))


def test_a_clang_consumer_drops_the_gcc_only_abi_version() -> None:
    c = _cfg(flags=Flags(abi_version=11), consumers=(Consumer(toolchain=CLANG),))
    assert [u.flags.abi_version for u in c.consumer_set] == [None]
    assert [
        u.flags.abi_version for u in _cfg(flags=Flags(abi_version=11)).consumer_set
    ] == [11]


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
            _cfg(
                "c++",
                std=STANDARD_BY_NAME["c++20"],
                platform=("win32", "pe"),
                producer=CLANG,
            ),
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


@pytest.mark.parametrize(
    ("pack", "size_required"),
    [(None, False), (1, True), (2, True), (4, False), (8, False)],
)
def test_array_shrink_needs_a_size_change_only_where_packing_removes_tail_padding(
    pack: int | None, size_required: bool
) -> None:
    # Independent layout arithmetic for struct rec { int; double; char tag[N]; }
    # packed to `pack` (None = natural alignment 8).
    def size(tag: int) -> int:
        align = 8 if pack is None else min(pack, 8)
        off = 4
        off = -(-off // min(align, 8)) * min(align, 8)  # double
        end = off + 8 + tag
        return -(-end // align) * align

    assert (size(8) != size(6)) is size_required
    exp = OP_BY_NAME["struct_array_shrink_in_padding"].expect(
        _cfg(flags=Flags(pack=pack))
    )
    names_size = any("TYPE_SIZE_CHANGED" in g for g in exp.any_of)
    assert names_size is size_required


def test_no_unique_address_rule_names_the_msvc_abi_not_every_pe_target() -> None:
    std20 = STANDARD_BY_NAME["c++20"]
    pe = ("win32", "pe")
    msvc_like = _cfg("c++", std=std20, platform=pe, producer=CLANG)
    mingw = _cfg("c++", std=std20, platform=pe, producer=GCC)
    assert (
        OP_BY_NAME["no_unique_address_added"].expect(msvc_like).verdict_min
        == "NO_CHANGE"
    )
    assert OP_BY_NAME["no_unique_address_added"].expect(mingw).verdict_min == "BREAKING"


@pytest.mark.parametrize(
    ("platform", "vtt_kinds", "export_kind", "export_max"),
    [
        # Against the export table and the public header the new export is
        # undeclared, which the release may read as a risk.
        (
            ("linux", "elf"),
            {"VTT_SLOT_COUNT_CHANGED"},
            "FUNC_ADDED_ELF_ONLY",
            "COMPATIBLE_WITH_RISK",
        ),
        (("darwin", "macho"), set(), "FUNC_ADDED", "COMPATIBLE_WITH_RISK"),
        (("win32", "pe"), set(), "FUNC_ADDED", "COMPATIBLE_WITH_RISK"),
    ],
)
def test_elf_symbol_table_kinds_are_owed_only_on_elf(
    platform: tuple[str, str], vtt_kinds: set[str], export_kind: str, export_max: str
) -> None:
    # VTT size and undeclared exports are read from the ELF symbol table; a
    # Mach-O or PE build still owes the verdict, not the ELF-only kind.
    config = _cfg("c++", platform=platform, producer=CLANG)
    vtt = OP_BY_NAME["vtt_grew"].expect(config)
    assert vtt.expected - {"SONAME_BUMP_RECOMMENDED"} == vtt_kinds
    assert vtt.verdict_min == "BREAKING"
    export = OP_BY_NAME["source_only_export_added"].expect(config)
    assert export.expected == {export_kind}
    assert export.verdict_min == "COMPATIBLE"
    assert export.verdict_max == export_max


def test_a_rebuild_row_with_its_target_flag_already_set_is_repaired_not_dropped() -> (
    None
):
    from abiforge.run.tiers import _repair_rebuild

    op = OP_BY_NAME["rebuild_short_enums"]
    drawn = Flags(opt="O2", short_enums=True, pack=1)
    fixed = _repair_rebuild(op, drawn)
    assert fixed == Flags(opt="O2", pack=1)
    assert op.requires(_cfg(flags=fixed)) is None
    assert _repair_rebuild(op, Flags(opt="O2")) == Flags(opt="O2")
    assert _repair_rebuild(OP_BY_NAME["struct_append_field"], drawn) == drawn


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
        _cfg(lang, std=s, flags=f, evidence=e, platform=p, producer=tc)
        for lang in ("c", "c++")
        for s in STANDARDS
        if s.lang == lang
        for f in (
            Flags(),
            Flags(short_enums=True),
            Flags(pack=1),
            Flags(visibility="hidden"),
            Flags(bits=32),
            Flags(opt="O2"),
        )
        for e in EVIDENCE
        for p in (("linux", "elf"), ("win32", "pe"))
        for tc in (GCC, CLANG)
    ]
    for op in OPERATORS:
        for rule in op.rules:
            assert any(rule.when(c) for c in probes), (op.name, rule.why)
            assert any(not rule.when(c) for c in probes), (op.name, rule.why)


def test_every_expectation_band_is_ordered_for_every_probe_config() -> None:
    from abiforge.model.expect import VERDICT_RANK

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
_SOURCE_ONLY = Expectation(verdict_min="API_BREAK", verdict_max="API_BREAK")


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
        (_NONE, _BENIGN, "BREAKING", set(), "wrong-verdict"),
        (_BROKE, _BREAK, "REFUSED", {"ProfileMismatchError"}, "refused"),
        # A source-only (API_BREAK ceiling) edit: a binary break contradicts it.
        (_BROKE, _SOURCE_ONLY, "API_BREAK", set(), "oracle-conflict"),
        (_SRC, _SOURCE_ONLY, "API_BREAK", set(), "agree"),
        (_SRC, _SOURCE_ONLY, "NO_CHANGE", set(), "false-clean"),
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
        consumers=(Consumer(), Consumer(toolchain=CLANG)),
    )
    steps = dict(_shrink_steps(c))
    assert set(steps) == {
        "flags.opt",
        "flags.short_enums",
        "flags.pack",
        "consumers",
        "consumers[0]",
        "consumers[1]",
        "std",
        "evidence",
    }
    assert steps["consumers"].consumers == ()
    assert steps["consumers[0]"].consumers == (Consumer(toolchain=CLANG),)
    assert steps["flags.pack"].flags == Flags(opt="O2", short_enums=True)
    assert steps["evidence"].evidence == "headers"
    assert _shrink_steps(_cfg()) == []


def test_minimize_keeps_only_the_axis_that_reproduces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import abiforge.run.cells as cells

    def fake_run(op, seed, config, ctx):  # reproduces iff -fshort-enums is on
        cls = "false-clean" if config.flags.short_enums else "agree"
        from abiforge.model.expect import Reconciled

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
    from abiforge.model.expect import Reconciled

    cell = CellResult(
        "enum_append_wide_value",
        "c_core",
        _cfg(
            flags=Flags(short_enums=True),
            evidence="dwarf",
            consumers=(
                Consumer(),
                Consumer(toolchain=CLANG, std=STANDARD_BY_NAME["c89"]),
            ),
        ),
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
    assert (
        'Consumer(toolchain=toolchain("clang", 18), std=STANDARD_BY_NAME["c89"])'
        in text
    )


# --- fast: probes, tiers, known mismatches -----------------------------------


def test_layout_probe_reads_every_record_and_enum_of_the_c_seed() -> None:
    keys = set(probe_expressions(dict(C_SEED.roles), "c"))
    want = {
        "sizeof(point)",
        "alignof(point)",
        "offsetof(point,x)",
        "offsetof(point,y)",
        "sizeof(rec)",
        "alignof(rec)",
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
    # Every seed has a guard, so a seed whose base no longer builds fails a PR.
    assert {seed for _, seed, _, _ in _PR} == {s.name for s in SEEDS}
    # The PR tier stays a handful: one guard per axis, plus one row per L3
    # build-data operator (BUILD_DATA_PR).
    assert len(_PR) - len(BUILD_DATA_PR) <= 20
    assert {op for op, _, _, _ in BUILD_DATA_PR} == {
        op.name for op in BUILD_DATA_OPERATORS
    }
    assert {fam for _, _, fam, _ in BUILD_DATA_PR} == {"gcc", "clang"}


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


def test_every_standard_of_both_languages_is_a_nightly_axis_value() -> None:
    from abiforge.run.tiers import _STD_SLOTS, std_for_slot

    for lang in ("c", "c++"):
        drawn = {std_for_slot(lang, slot) for slot in range(_STD_SLOTS)}
        assert drawn == {s for s in STANDARDS if s.lang == lang}, lang


@pytest.mark.parametrize("n", [1, 2, 3, 4, 7])
def test_shards_partition_the_cells(n: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from abiforge.run.tiers import shard

    cells = list(range(23))
    got = []
    for k in range(1, n + 1):
        monkeypatch.setenv("ABICHECK_EDIT_CORPUS_SHARD", f"{k}/{n}")
        got.append(shard(cells))  # type: ignore[arg-type]
    flat = [c for part in got for c in part]
    assert sorted(flat) == cells and len(flat) == len(set(flat))


def test_a_malformed_shard_spec_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from abiforge.run.tiers import shard_spec

    monkeypatch.setenv("ABICHECK_EDIT_CORPUS_SHARD", "5/4")
    with pytest.raises(ValueError, match="1 <= K <= N"):
        shard_spec()


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("", set()),
        ("nightly", {"nightly"}),
        (" nightly , weekly ", {"nightly", "weekly"}),
    ],
)
def test_enabled_tiers_reads_the_env_var(
    raw: str, want: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from abiforge.run.tiers import enabled_tiers

    monkeypatch.setenv("ABICHECK_EDIT_CORPUS_TIER", raw)
    assert enabled_tiers() == want


def test_issue_keys_normalize_every_problem_shape() -> None:
    exp = Expectation(
        expected=frozenset({"B_KIND", "A_KIND"}),
        any_of=(frozenset({"Y", "X"}),),
        verdict_min="API_BREAK",
    )
    assert issue_keys(exp.problems("COMPATIBLE", frozenset())) == {
        "missing:A_KIND",
        "missing:B_KIND",
        "none-of:X|Y",
        "weaker-than:API_BREAK",
    }
    assert issue_keys(_BENIGN.problems("BREAKING", frozenset())) == {
        "stronger-than:NO_CHANGE"
    }
    assert issue_keys(
        _BENIGN.problems("REFUSED", frozenset({"ProfileMismatchError"}))
    ) == {"refused:ProfileMismatchError"}
    with pytest.raises(ValueError):
        issue_keys(["something new"])


def test_a_layout_only_break_is_labelled_a_surrogate() -> None:
    probe_only = replace(
        _BROKE, signals=("layout sizeof(rec): 24 -> 32",), layout_only=True
    )
    got = reconcile(probe_only, _BREAK, "COMPATIBLE", frozenset())
    assert got.cls == "false-clean" and "surrogate" in got.detail
    assert (
        "surrogate" not in reconcile(_BROKE, _BREAK, "COMPATIBLE", frozenset()).detail
    )


def test_known_entries_record_the_exact_finding() -> None:
    for k in KNOWN:
        assert k.issues and k.verdicts, k.op
        assert k.matches(k.cls, k.issues, next(iter(k.verdicts)))
        assert not k.matches(
            k.cls, k.issues | {"missing:SOMETHING_ELSE"}, next(iter(k.verdicts))
        )
        assert not k.matches(k.cls, k.issues, "NOT_A_VERDICT")


def test_a_false_clean_degrades_to_its_verdict_class_without_execution() -> None:
    # Reconcile the recorded issues with no execution signal and check the
    # class the registry predicts for that host (independent derivation).
    for k in KNOWN:
        if k.cls != "false-clean":
            continue
        exp = OP_BY_NAME[k.op].expect(_cfg("c++"))
        verdict = next(iter(k.verdicts))
        got = reconcile(_NONE, exp, verdict, frozenset())
        assert got.cls == k.expected_cls(execution_available=False), k.op
        assert got.cls != "false-clean"


def test_discovery_rejects_a_c_and_cxx_driver_pair_of_different_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import abiforge.run.toolchains as tcs

    paths = {
        "gcc": "/x/gcc",
        "g++": "/x/g++",
        "gcc-14": "/x/gcc-14",
        "g++-14": "/x/g++-14",
    }
    idents = {
        "/x/gcc": ("gcc", 13),
        "/x/g++": ("gcc", 14),
        "/x/gcc-14": ("gcc", 14),
        "/x/g++-14": ("gcc", 14),
    }
    monkeypatch.setattr(tcs.shutil, "which", lambda name: paths.get(name))
    monkeypatch.setattr(tcs.os.path, "realpath", lambda p: p)
    monkeypatch.setattr(tcs, "_identify", lambda path: idents.get(path))
    monkeypatch.setattr(tcs.sys, "platform", "linux")
    tcs.discover.cache_clear()
    try:
        got = [(t.id, t.cc, t.cxx) for t in tcs.discover()]
    finally:
        tcs.discover.cache_clear()
    # gcc 13 + g++ 14 is not a toolchain; the gcc-14/g++-14 pair is.
    assert got == [("gcc14", "/x/gcc-14", "/x/g++-14")]


# --- integration -------------------------------------------------------------


@pytest.fixture(scope="session")
def cell_ctx(tmp_path_factory: pytest.TempPathFactory) -> CellContext:
    return CellContext.at(tmp_path_factory.mktemp("edit_corpus_v2"))


def _minimize_enabled() -> bool:
    return os.environ.get("ABICHECK_EDIT_CORPUS_MINIMIZE", "1") != "0"


def check_cell(cell: Cell, ctx: CellContext, tier: str) -> None:
    """Run *cell*; any failure, including a harness exception, leaves a report."""
    try:
        _check_cell(cell, ctx, tier)
    except Exception as exc:  # a pytest outcome (skip/xfail/fail) is not an Exception
        _write_report(cell, f"{cell.id}: {type(exc).__name__}: {exc}")
        raise


def _check_cell(cell: Cell, ctx: CellContext, tier: str) -> None:
    result = run_cell(cell.op, cell.seed, cell.config, ctx)
    _write_pairs(cell, result)
    # Coverage facts first, so a skip, xfail or failure is recorded too.
    write_record(
        make_record(
            tier,
            cell.op,
            cell.seed.name,
            cell.config,
            status=result.status,
            cls=result.cls if result.status == "ran" else "",
            verdict=result.verdict,
            produced=result.kinds,
            expectation=result.expectation,
            source="matrix",
            ran_consumers=ran_consumers(result.exec_truth),
        )
    )
    if result.status != "ran":
        pytest.skip(f"{result.status}: {result.reason}")
    known = known_mismatch(cell.op.name, cell.seed.name, cell.config)
    if known is not None:
        issues = issue_keys(result.reconciled.problems if result.reconciled else ())
        executed = bool(result.exec_truth and result.exec_truth.available)
        assert known.matches(result.cls, issues, result.verdict, executed), (
            f"recorded {known.cls} {sorted(known.issues)} (verdict in {sorted(known.verdicts)}) "
            f"no longer reproduces as recorded: got {result.cls} {sorted(issues)} "
            f"verdict={result.verdict}. If the detector was fixed, remove the "
            f"abiforge.known entry and its known-gaps row.\n{result.describe()}"
        )
        pytest.xfail(f"{known.cls}: {known.reason}")
    if result.cls in PASSING_CLASSES:
        return
    report = result.describe()
    if _minimize_enabled():
        try:
            small = minimize(cell.op, cell.seed, result, ctx)
        except Exception as exc:
            # Keep the original mismatch: a failed shrink must not replace it.
            report += f"\n\nminimization failed: {type(exc).__name__}: {exc}"
        else:
            report += (
                "\n\nminimized:\n" + small.describe() + "\n\n" + regression_test(small)
            )
    _write_report(cell, report)
    pytest.fail(report)


def _write_pairs(cell: Cell, result: CellResult) -> None:
    """Keep every cell's per-(build, consumer) rows for the workflow's summary."""
    from abiforge.run.report import write_cell

    out = os.environ.get("ABICHECK_EDIT_CORPUS_REPORT_DIR")
    if out:
        write_cell(Path(out).parent / "pairs", cell.id, result)


def _write_report(cell: Cell, report: str) -> None:
    """Keep a failing cell's report for the scheduled workflow's artifact."""
    out = os.environ.get("ABICHECK_EDIT_CORPUS_REPORT_DIR")
    if out:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in cell.id)
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / f"{safe}.txt").write_text(report + "\n", encoding="utf-8")


@pytest.mark.integration
@pytest.mark.parametrize(
    "index", range(len(_PR)), ids=[f"{o}-{s}-{f}" for o, s, f, _ in _PR]
)
def test_pr_tier_cell(index: int, cell_ctx: CellContext) -> None:
    cell = pr_cells()[index]
    if not isinstance(cell, Cell):
        pytest.skip(cell[1])
    check_cell(cell, cell_ctx, "pr")


@pytest.mark.integration
@pytest.mark.parametrize("index", range(len(pr_consumer_ids())), ids=pr_consumer_ids())
def test_pr_tier_consumer_cell(index: int, cell_ctx: CellContext) -> None:
    cell = pr_consumer_cells()[index]
    if not isinstance(cell, Cell):
        pytest.skip(cell[1])
    check_cell(cell, cell_ctx, "pr")


_TIER_OFF = (
    "set ABICHECK_EDIT_CORPUS_TIER={tier} to generate this tier's cells "
    "(and select them with -m edit_corpus_{tier})"
)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Generate a tier's cells only when ``ABICHECK_EDIT_CORPUS_TIER`` names it.

    Otherwise the test gets one placeholder that skips with the reason, so
    collecting this module never probes a compiler and a forgotten env var is
    visible as a skip, not as an empty parameter set.
    """
    from abiforge.run import tiers as tiers

    for name, tier, gen in (
        ("nightly_cell", "nightly", tiers.nightly_cells),
        ("weekly_cell", "weekly", tiers.weekly_cells),
    ):
        if name not in metafunc.fixturenames:
            continue
        if tier in tiers.enabled_tiers():
            cells = gen()
            metafunc.parametrize(name, cells, ids=[c.id for c in cells])
        else:
            metafunc.parametrize(name, [None], ids=[f"{tier}-tier-disabled"])


def _tier_cell(cell: Cell | None, tier: str, ctx: CellContext) -> None:
    if cell is None:
        pytest.skip(_TIER_OFF.format(tier=tier))
    check_cell(cell, ctx, tier)


@pytest.mark.integration
@pytest.mark.edit_corpus_nightly
def test_nightly_tier_cell(nightly_cell: Cell | None, cell_ctx: CellContext) -> None:
    _tier_cell(nightly_cell, "nightly", cell_ctx)


@pytest.mark.integration
@pytest.mark.edit_corpus_weekly
def test_weekly_tier_cell(weekly_cell: Cell | None, cell_ctx: CellContext) -> None:
    _tier_cell(weekly_cell, "weekly", cell_ctx)
