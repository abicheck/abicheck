"""abiforge's strict expectations for two operators, built and checked on macOS.

``pure_virtual_added`` and ``primary_base_grew`` lost their Mach-O platform
rules in this PR, so on a Mach-O target they must now meet the strict expected
kinds. This test runs the real cell path (``run_operator``, the same one
``test_abi_edit_corpus.py`` uses): it builds both sides with the host's Apple
clang, asks abicheck for a verdict, and checks it against the operator's own
``expect(config)``. Nothing here restates the expected kinds.

Integration-marked, macOS only: it compiles, so it runs in the macOS
integration lane.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from abicheck.macho_metadata import parse_macho_metadata
from abiforge.operators import OP_BY_NAME, matrix
from abiforge.run.harness import BaselineCache, _as_config, run_operator
from abiforge.run.toolchains import capability_gap, header_backend_unavailable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "darwin" or shutil.which("clang++") is None,
        reason="Requires macOS with Apple clang++",
    ),
]

_OPERATORS = ("pure_virtual_added", "primary_base_grew")
#: The evidence levels this PR's macOS expectations are meant to hold at.
_EVIDENCE = ("dwarf", "headers")


def _seed_for(op_name: str):
    for op, seed in matrix():
        if op.name == op_name:
            return seed
    raise AssertionError(f"{op_name} is in no matrix seed; the matrix changed")


def _thunk_exports(root: Path) -> list[str]:
    """Mach-O export names of thunks (``_ZThn``/``_ZTv``) in every dylib under *root*.

    A diagnostic for a failing cell only: it shows whether a thunk offset change
    renames an exported thunk, which would surface as a removal plus an addition.
    """
    names: list[str] = []
    for lib in sorted(root.rglob("*.dylib")):
        meta = parse_macho_metadata(lib)
        names += [
            f"{lib.parent.name}/{lib.name}:{e.name}"
            for e in meta.exports
            # Mach-O export names are normalized without the leading underscore
            # (macho_metadata strips it), so match the Itanium prefix after it.
            if e.name.lstrip("_").startswith(("ZThn", "ZTv"))
        ]
    return names


@pytest.mark.parametrize("evidence", _EVIDENCE)
@pytest.mark.parametrize("op_name", _OPERATORS)
def test_macho_cell_meets_its_strict_expectation(
    op_name: str, evidence: str, tmp_path: Path
) -> None:
    op = OP_BY_NAME[op_name]
    seed = _seed_for(op_name)
    cfg = _as_config(seed, "clang").with_(
        platform=("darwin", "macho"),
        evidence=evidence,
        # castxml is the reference header backend and the macOS integration lane
        # installs it (setup-castxml, conda-forge 0.7.0); clang is not used here.
        header_backend="castxml",
    )
    gap = capability_gap(op, cfg)
    if gap:
        pytest.skip(gap)
    if evidence == "headers":
        backend_gap = header_backend_unavailable("castxml", seed.lang)
        if backend_gap:
            pytest.skip(backend_gap)
    outcome = run_operator(
        op,
        seed,
        cfg,
        BaselineCache(tmp_path / "old"),
        tmp_path / "new",
    )
    assert outcome.failures == (), (
        f"{op_name} on {seed.name} at evidence={evidence}: verdict={outcome.verdict} "
        f"kinds={sorted(outcome.kinds)} problems={list(outcome.failures)} "
        f"(why: {op.why})\n"
        "function findings, emitted by abicheck:\n  "
        + "\n  ".join(outcome.findings)
        + "\n"
        "debug evidence per side:\n  " + "\n  ".join(outcome.evidence) + "\n"
        f"thunk exports, old: {_thunk_exports(tmp_path / 'old')}\n"
        f"thunk exports, new: {_thunk_exports(tmp_path / 'new')}"
    )
