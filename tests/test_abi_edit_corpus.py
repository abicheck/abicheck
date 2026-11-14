"""Generated ABI corpus: each semantic edit must produce its declared ground truth.

Ground truth is declared per edit operator in ``abiforge.operators`` from
the edit's meaning. The integration matrix builds every applicable
(operator, seed, compiler) cell with gcc and clang, runs ``abicheck compare``
through the public API, and asserts the expected kinds and verdict. Fast tests
check the generator itself (templates, applicability, declared kinds) without a
toolchain.

A cell that disagrees with its ground truth is a detector finding. It is not
fixed here: it is recorded in ``abiforge/known.py`` (shared with the
config matrix) as a strict xfail with the exact issue set and verdict observed,
and listed in ``docs/contribute/known-gaps.md``.
"""

from __future__ import annotations

import dataclasses
import shutil
import sys
from pathlib import Path

import pytest

from abicheck.model.change_catalog.kinds import ChangeKind
from abiforge.coverage import make_record, write_record
from abiforge.known import KNOWN, known_mismatch
from abiforge.model.config import (
    EVIDENCE,
    HEADER_BACKENDS,
    Toolchain,
    default_config,
)
from abiforge.model.expect import issue_keys
from abiforge.model.seeds import BUILD_ROLES, SEED_BY_NAME, SEEDS
from abiforge.operators import OP_BY_NAME, OPERATORS, VERDICT_RANK, matrix
from abiforge.run.harness import (
    COMPILERS,
    BaselineCache,
    _as_config,
    run_operator,
)
from abiforge.run.toolchains import (
    capability_gap,
    driver_width,
    elf_link_unavailable,
    header_backend_unavailable,
)


def _applies_at_default(op, seed) -> bool:
    """The operator is meaningful under the base PR's fixed config (C17/C++17, -O0, headers).

    Feature-gated operators (C++20, C23) and rebuilds that need another config
    run in the config matrix (``test_abi_edit_corpus_matrix.py``) instead.
    """
    placeholder = Toolchain("gcc", 0, "gcc", "g++")
    return op.requires(default_config(seed.lang, placeholder)) is None


def default_matrix():
    return tuple((op, seed) for op, seed in matrix() if _applies_at_default(op, seed))


# Mismatches are recorded once, in ``abiforge/known.py``, for this
# matrix and the config matrix alike. A cell xfails only while its exact issue
# set and verdict reproduce as recorded; any other outcome fails.


def _cell_id(op, seed, compiler: str, backend: str) -> str:
    # castxml is the reference backend: its cells keep their historical ids.
    base = f"{op.name}-{seed.name}-{compiler}"
    return base if backend == "castxml" else f"{base}-hb_{backend}"


# Every cell once per header backend: the default config dumps headers, and
# clang and the native gcc plugin owe the same ground truth castxml does.
CELLS = [
    pytest.param(op, seed, compiler, backend, id=_cell_id(op, seed, compiler, backend))
    for op, seed in default_matrix()
    for compiler in COMPILERS
    for backend in HEADER_BACKENDS
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
    assert len(names) == len(set(names)) == 9


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
        # An extra file places a role's header text when it is a header and
        # its source text when it is a translation unit.
        for path, template in seed.extra_files:
            slots = {ln[2:-2] for ln in template if ln.startswith("@@")}
            if path in seed.extra_sources:
                source_slots |= slots
            else:
                header_slots |= slots
        for role, (h, c) in seed.roles.items():
            if role in BUILD_ROLES:
                # Read by Seed.build_args, never placed by a template.
                assert role not in header_slots | source_slots, (seed.name, role)
                assert not c, f"{seed.name}: build role {role} has source text"
                continue
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
        if op.rebuild is not None or op.new_evidence is not None:
            continue  # a rebuild or an evidence change keeps the source
        edit = op.edit(seed.roles)
        base = (seed.render(), seed.build_args())
        edited = (seed.render(edit), seed.build_args(edit))
        assert edited != base, f"{op.name} on {seed.name} is an identity edit"


def test_edits_only_return_roles_they_declare() -> None:
    for op, seed in matrix():
        changed = set(op.edit(seed.roles))
        assert changed <= set(op.touches) | set(op.roles), (op.name, changed)


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


def test_known_mismatches_cover_real_default_cells() -> None:
    # Each recorded entry this matrix can reach names an (operator, seed) the
    # default matrix builds; the registry's own integrity is tested with it.
    # The default config is swept over every evidence level: an entry scoped
    # to one lane (DWARF, binary) is reachable at that lane's default.
    pairs = {(op.name, seed.name) for op, seed in default_matrix()}
    placeholder = Toolchain("gcc", 0, "gcc", "g++")
    reachable = [
        (k.op, seed)
        for k in KNOWN
        for seed in k.seeds
        for evidence in EVIDENCE
        if k.when(
            cfg := dataclasses.replace(
                default_config(SEED_BY_NAME[seed].lang, placeholder), evidence=evidence
            )
        )
        and OP_BY_NAME[k.op].requires(cfg) is None
    ]
    assert reachable, (
        "no recorded mismatch applies at any default-config evidence level"
    )
    assert set(reachable) <= pairs


def test_matrix_is_not_silently_shrinking() -> None:
    # Guard against an applicability bug emptying the matrix: a shrink is
    # visible here rather than as a green run that tested less.
    assert len(default_matrix()) >= 160
    assert len(CELLS) == len(default_matrix()) * len(COMPILERS) * len(HEADER_BACKENDS)


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


def _skip_wrong_width(seed, config) -> None:
    """Skip when the producer, or the header frontend's system driver, targets another width.

    The fixed matrix is declared at the default word size; building or
    parsing headers at another one would test a different ABI than it claims.
    A driver that fails for any other reason raises ``ToolchainBroken``.
    """
    drivers = [
        (
            config.producer.driver(seed.lang),
            config.flags.render(config.producer, seed.lang),
        )
    ]
    if config.evidence == "headers":
        system = "c++" if seed.lang == "c++" else "cc"
        if shutil.which(system) is not None:
            drivers.append(
                (system, config.flags.layout_flags(config.producer, seed.lang))
            )
    for driver, flags in drivers:
        width = driver_width(driver, seed.lang, tuple(flags), config.flags.bits)
        if width:
            pytest.skip(width)


@pytest.mark.integration
@pytest.mark.parametrize("op,seed,compiler,backend", CELLS)
def test_edit_produces_declared_ground_truth(
    op, seed, compiler, backend, baseline_cache: BaselineCache, corpus_root: Path
) -> None:
    config = _as_config(seed, compiler).with_(header_backend=backend)
    try:
        _skip_without_driver(seed, compiler)
        backend_gap = header_backend_unavailable(backend, seed.lang)
        if backend_gap:
            pytest.skip(backend_gap)
        # A build or extraction exception propagates and fails the cell.
        _skip_wrong_width(seed, config)
        # A seed linked with ELF-only build roles (a version script, -z,
        # --as-needed) is a host gap off ELF, as in the tier matrix.
        gap = elf_link_unavailable(seed, config) or capability_gap(op, config)
        if gap:
            pytest.skip(gap)
    except pytest.skip.Exception:
        write_record(
            make_record(
                "pr", op, seed.name, config, status="unavailable", source="default"
            )
        )
        raise
    outcome = run_operator(op, seed, config, baseline_cache, corpus_root)
    known = known_mismatch(op.name, seed.name, config)
    write_record(
        make_record(
            "pr",
            op,
            seed.name,
            config,
            status="ran",
            # No execution oracle here: a cell meeting its expectation agrees,
            # and no consumer ran, so it covers no space point.
            cls="known-xfail"
            if known
            else ("agree" if not outcome.failures else "mismatch"),
            verdict=outcome.verdict,
            produced=outcome.kinds,
            source="default",
        )
    )
    if known is not None:
        issues = issue_keys(outcome.failures)
        assert issues == known.issues and outcome.verdict in known.verdicts, (
            f"recorded mismatch {sorted(known.issues)} (verdict in {sorted(known.verdicts)}) "
            f"no longer reproduces: verdict={outcome.verdict} kinds={sorted(outcome.kinds)} "
            f"issues={sorted(issues)}. If the detector was fixed, remove the "
            "abiforge/known.py entry and its known-gaps row."
        )
        pytest.xfail(known.reason)
    assert outcome.failures == (), (
        f"{op.name} on {seed.name} with {compiler}: verdict={outcome.verdict} "
        f"kinds={sorted(outcome.kinds)} problems={list(outcome.failures)} "
        f"(why: {op.why})"
    )


@pytest.mark.integration
def test_smoke_subset_is_exercised() -> None:
    # The fast smoke subset is the fixed set of cells that always run on a
    # toolchain lane; this asserts it still names real cells.
    cells = {
        (op.name, seed.name, c) for op, seed in default_matrix() for c in COMPILERS
    }
    assert SMOKE_CELLS <= cells


@pytest.mark.integration
@pytest.mark.parametrize(
    "op,seed,compiler",
    [
        pytest.param(*p.values[:3], id=p.id)
        for p in CELLS
        if (p.values[0].name, p.values[1].name, p.values[2]) in SMOKE_CELLS
        and p.values[3] == "castxml"
    ],
)
def test_smoke_cells(
    op, seed, compiler, baseline_cache: BaselineCache, corpus_root: Path
) -> None:
    _skip_without_driver(seed, compiler)
    config = _as_config(seed, compiler)
    _skip_wrong_width(seed, config)
    outcome = run_operator(op, seed, config, baseline_cache, corpus_root)
    assert outcome.failures == (), list(outcome.failures)
