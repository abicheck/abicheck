"""Generated ABI corpus: each semantic edit must produce its declared ground truth.

Ground truth is declared per edit operator in ``_edit_corpus_operators.py`` from
the edit's meaning. The integration matrix builds every applicable
(operator, seed, compiler) cell with gcc and clang, runs ``abicheck compare``
through the public API, and asserts the expected kinds and verdict. Fast tests
check the generator itself (templates, applicability, declared kinds) without a
toolchain.

A cell that disagrees with its ground truth is a detector finding. It is not
fixed here: it is recorded in ``KNOWN_MISMATCHES`` as a strict xfail with the
observed answer, and listed in the PR and ``docs/contribute/known-gaps.md``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from abicheck.model.change_catalog.kinds import ChangeKind
from tests._edit_corpus_harness import COMPILERS, BaselineCache, run_operator
from tests._edit_corpus_operators import OPERATORS, VERDICT_RANK, matrix
from tests._edit_corpus_seeds import SEEDS

# (operator, seed, compiler) -> (reason, kind the detector is missing).
# A documented cell xfails only while its recorded mismatch still reproduces;
# any other outcome (compile error, crash, different wrong answer) fails. When
# the detector is fixed the mismatch stops reproducing and the entry must go.
_CXX_PARAM = (
    "C++ parameter change reads as FUNC_REMOVED + FUNC_ADDED (the mangled symbol changes, "
    "so the old and new functions are not paired), never FUNC_PARAMS_CHANGED; verdict is right",
    "FUNC_PARAMS_CHANGED",
)
_CASTXML_NOEXCEPT = (
    "castxml writes no exception specification, so noexcept added reads NO_CHANGE "
    "(docs/contribute/known-gaps.md: castxml output carries no noexcept)",
    "FUNC_NOEXCEPT_ADDED",
)
_CASTXML_TEMPLATE_DEFAULT = (
    "castxml reads a header-only template default change as NO_CHANGE "
    "(docs/contribute/known-gaps.md: header-only default template argument, castxml side)",
    "DEFAULT_TEMPLATE_ARG_CHANGED",
)
_VIRTUAL_REMOVED = (
    "removing a virtual reads FUNC_REMOVED plus vtable kinds; FUNC_VIRTUAL_REMOVED is never emitted",
    "FUNC_VIRTUAL_REMOVED",
)

KNOWN_MISMATCHES: dict[tuple[str, str, str], tuple[str, str]] = {
    **{
        (op, "cxx_classes", c): _CXX_PARAM
        for op in ("func_param_append", "func_param_type_change", "func_param_remove")
        for c in COMPILERS
    },
    **{("virtual_remove", "cxx_classes", c): _VIRTUAL_REMOVED for c in COMPILERS},
    **{("method_noexcept_add", "cxx_classes", c): _CASTXML_NOEXCEPT for c in COMPILERS},
    **{
        ("template_default_arg_change", "cxx_classes", c): _CASTXML_TEMPLATE_DEFAULT
        for c in COMPILERS
    },
}

CELLS = [
    pytest.param(op, seed, compiler, id=f"{op.name}-{seed.name}-{compiler}")
    for op, seed in matrix()
    for compiler in COMPILERS
]

SMOKE_CELLS = {
    ("struct_append_field", "c_core", "gcc"),
    ("func_param_type_change", "c_core", "clang"),
    ("enum_append_member", "c_core", "gcc"),
    ("virtual_reorder", "cxx_classes", "gcc"),
}


# --- fast: the generator itself, no compiler needed ------------------------


def test_operator_names_are_unique_and_nonempty() -> None:
    names = [op.name for op in OPERATORS]
    assert len(names) == len(set(names)), f"duplicate operator names: {names}"
    assert all(names), "empty operator name"


def test_seed_names_are_unique() -> None:
    names = [s.name for s in SEEDS]
    assert len(names) == len(set(names)) == 2


def test_every_template_placeholder_is_a_known_role() -> None:
    for seed in SEEDS:
        for template in (seed.header_template, seed.source_template):
            for line in template:
                if line.startswith("@@"):
                    assert line.endswith("@@")
                    assert line[2:-2] in seed.roles, (seed.name, line)


def test_each_role_text_is_placed_in_the_file_it_belongs_to() -> None:
    for seed in SEEDS:
        header_slots = {ln[2:-2] for ln in seed.header_template if ln.startswith("@@")}
        source_slots = {ln[2:-2] for ln in seed.source_template if ln.startswith("@@")}
        for role, (h, c) in seed.roles.items():
            if h:
                assert role in header_slots, (
                    f"{seed.name}: header text of {role} never rendered"
                )
            if c:
                assert role in source_slots, (
                    f"{seed.name}: source text of {role} never rendered"
                )


def test_base_render_has_both_files_and_guards() -> None:
    for seed in SEEDS:
        header, source = seed.render()
        assert header.startswith("#ifndef EDIT_SEED_")
        assert header.rstrip().endswith("#endif")
        assert source.startswith('#include "edit_seed.h"')


def test_every_operator_applies_to_at_least_one_seed() -> None:
    orphans = [op.name for op in OPERATORS if not any(op.applies_to(s) for s in SEEDS)]
    assert orphans == []


def test_every_operator_changes_the_rendered_text() -> None:
    for op, seed in matrix():
        base = seed.render()
        edited = seed.render(op.edit(seed.roles))
        assert edited != base, f"{op.name} on {seed.name} is an identity edit"


def test_edits_only_return_roles_they_declare() -> None:
    for op, seed in matrix():
        changed = set(op.edit(seed.roles))
        assert changed <= set(op.touches) | set(op.requires), (op.name, changed)


def test_declared_kinds_are_real_changekinds() -> None:
    valid = {k.name for k in ChangeKind}
    for op in OPERATORS:
        unknown = (set(op.expected) | set().union(*op.any_of)) - valid
        assert unknown == set(), (op.name, sorted(unknown))


def test_verdict_bounds_are_real_verdicts_and_ordered() -> None:
    for op in OPERATORS:
        assert op.verdict_min in VERDICT_RANK, op.name
        if op.verdict_max is not None:
            assert op.verdict_max in VERDICT_RANK, op.name
            assert VERDICT_RANK[op.verdict_min] <= VERDICT_RANK[op.verdict_max], op.name


def test_every_operator_declares_some_ground_truth() -> None:
    for op in OPERATORS:
        assert op.expected or op.any_of or op.verdict_max is not None, op.name


def test_known_mismatches_name_real_cells() -> None:
    cells = {(op.name, seed.name, c) for op, seed in matrix() for c in COMPILERS}
    stale = set(KNOWN_MISMATCHES) - cells
    assert stale == set(), (
        f"known mismatches for cells that do not exist: {sorted(stale)}"
    )


def test_matrix_is_not_silently_shrinking() -> None:
    # Guard against an applicability bug emptying the matrix: a shrink is
    # visible here rather than as a green run that tested less.
    assert len(matrix()) >= 50
    assert len(CELLS) == len(matrix()) * len(COMPILERS)


# --- integration: real compilers, real castxml dump, real compare ----------


@pytest.fixture(scope="session")
def baseline_cache(tmp_path_factory: pytest.TempPathFactory) -> BaselineCache:
    return BaselineCache(tmp_path_factory.mktemp("edit_corpus_old"))


@pytest.fixture(scope="session")
def corpus_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("edit_corpus_new")


def _skip_without_driver(seed, compiler: str) -> None:
    """Skip a cell only when it cannot observe its ground truth on this platform.

    Two reasons qualify: the driver is absent, or the clang driver targets Windows
    MSVC, whose DLLs export no symbol without __declspec(dllexport). The seeds
    declare none, so such a build reads as NO_CHANGE and proves nothing. A build
    that runs and fails is still a failure.
    """
    driver = COMPILERS[compiler][1 if seed.lang == "c++" else 0]
    if shutil.which(driver) is None:
        pytest.skip(
            f"{driver} is not installed; this compiler lane cannot build the cell"
        )
    if sys.platform == "win32" and compiler == "clang":
        pytest.skip(
            "clang targeting Windows MSVC exports no symbols without __declspec(dllexport); "
            "the corpus seeds declare none, so this cell cannot observe its ground truth"
        )


@pytest.mark.integration
@pytest.mark.parametrize("op,seed,compiler", CELLS)
def test_edit_produces_declared_ground_truth(
    op, seed, compiler, baseline_cache: BaselineCache, corpus_root: Path
) -> None:
    _skip_without_driver(seed, compiler)
    # A build or extraction exception propagates and fails the cell.
    outcome = run_operator(op, seed, compiler, baseline_cache, corpus_root)
    key = (op.name, seed.name, compiler)
    if key in KNOWN_MISMATCHES:
        reason, missing = KNOWN_MISMATCHES[key]
        assert any(missing in problem for problem in outcome.failures), (
            f"documented mismatch no longer reproduces (outcome: verdict={outcome.verdict} "
            f"kinds={sorted(outcome.kinds)} problems={list(outcome.failures)}); "
            "the detector may be fixed: remove the KNOWN_MISMATCHES entry"
        )
        pytest.xfail(reason)
    assert outcome.failures == (), (
        f"{op.name} on {seed.name} with {compiler}: verdict={outcome.verdict} "
        f"kinds={sorted(outcome.kinds)} problems={list(outcome.failures)} "
        f"(why: {op.why})"
    )


@pytest.mark.integration
def test_smoke_subset_is_exercised() -> None:
    # The fast smoke subset is the fixed set of cells that always run on a
    # toolchain lane; this asserts it still names real cells.
    cells = {(op.name, seed.name, c) for op, seed in matrix() for c in COMPILERS}
    assert SMOKE_CELLS <= cells


@pytest.mark.integration
@pytest.mark.parametrize(
    "op,seed,compiler",
    [
        p
        for p in CELLS
        if (p.values[0].name, p.values[1].name, p.values[2]) in SMOKE_CELLS
    ],
)
def test_smoke_cells(
    op, seed, compiler, baseline_cache: BaselineCache, corpus_root: Path
) -> None:
    _skip_without_driver(seed, compiler)
    outcome = run_operator(op, seed, compiler, baseline_cache, corpus_root)
    assert outcome.failures == (), list(outcome.failures)
