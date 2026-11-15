"""The stripped ``{B,H}`` headers cell: the plan's primary configuration (B0).

Not gating yet. Its cells run in the nightly tier only (``edit_corpus_nightly``),
never in the PR lane, and they assert only what the harness guarantees: the
library side carries no DWARF, and the checker answers with a real verdict.
An operator whose expectation the stripped cell does not meet is *recorded*
(``cls="mismatch"``) rather than asserted, because those defects are the plan's
A2/A3 work and land in their own PRs; this file adds no ``known.py`` entry and
no xfail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abiforge.coverage import make_record, write_record
from abiforge.model.config import Config, default_config
from abiforge.model.seeds import Seed
from abiforge.operators import VERDICT_RANK, EditOp, matrix
from abiforge.run.harness import (
    BaselineCache,
    debug_sections,
    label_toolchain,
    run_operator,
)
from abiforge.run.tiers import enabled_tiers
from abiforge.run.toolchains import capability_gap, elf_link_unavailable

pytestmark = [pytest.mark.integration, pytest.mark.edit_corpus_nightly]


def _stripped(seed_lang: str) -> Config:
    return default_config(seed_lang, label_toolchain("gcc")).with_(stripped=True)


def _cells() -> list[tuple[EditOp, Seed]]:
    cells: list[tuple[EditOp, Seed]] = []
    for op, seed in matrix():
        if op.requires(_stripped(seed.lang)) is None:
            cells.append((op, seed))
    return cells


@pytest.fixture(scope="session")
def stripped_cache(tmp_path_factory: pytest.TempPathFactory) -> BaselineCache:
    return BaselineCache(tmp_path_factory.mktemp("stripped_old"))


@pytest.fixture(scope="session")
def stripped_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("stripped_new")


# Same gate as ``test_abi_edit_corpus_matrix.py``: the cells, and so the test, exist
# only when ``ABICHECK_EDIT_CORPUS_TIER`` names the nightly tier. Without it this
# module collects nothing, so no lane without the toolchain builds a cell.
if "nightly" in enabled_tiers():
    _CELLS = _cells()

    @pytest.mark.parametrize(
        "op,seed",
        _CELLS,
        ids=[f"{op.name}@{seed.name}" for op, seed in _CELLS],
    )
    def test_stripped_headers_cell_is_stripped_and_answered(
        op: EditOp, seed: Seed, stripped_cache: BaselineCache, stripped_root: Path
    ) -> None:
        config = _stripped(seed.lang)
        gap = elf_link_unavailable(seed, config) or capability_gap(op, config)
        if gap:
            pytest.skip(gap)
        outcome = run_operator(op, seed, config, stripped_cache, stripped_root)
        # The old side is built by the same harness: a leftover DWARF section there
        # would make the stripped claim false for every operator.
        assert debug_sections(stripped_cache.get(seed, config).so) == []
        assert outcome.verdict in VERDICT_RANK, outcome.verdict
        write_record(
            make_record(
                "nightly",
                op,
                seed.name,
                config,
                status="ran",
                cls="mismatch" if outcome.failures else "agree",
                verdict=outcome.verdict,
                produced=outcome.kinds,
                source="stripped",
            )
        )
