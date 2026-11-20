"""Entity claims end to end: real builds through ``run_cell``.

The real answer must pass; the same real answer with one injected false
finding (an unchanged function reported removed, a private record blamed for
a public break) must fail as ``false-attribution``, while an injected
permitted note passes. The oracle is the edit: ``nm`` independently confirms
the witness is exported by both built libraries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abiforge.model.config import default_config
from abiforge.model.finding import Finding
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.operators import OP_BY_NAME
from abiforge.run import cells
from abiforge.run.binfacts import exported_functions
from abiforge.run.cells import CellContext, run_cell
from abiforge.run.toolchains import build_toolchains

pytestmark = pytest.mark.integration


def _toolchain():
    tcs = build_toolchains()
    if not tcs:
        pytest.skip("no build toolchain installed")
    return tcs[0]


def _run(op: str, seed: str, tmp: Path, inject: tuple[Finding, ...] = ()):
    real = cells.abicheck_judgement

    def judged(old, new):
        verdict, kinds, disclosed, findings = real(old, new)
        findings = findings + inject
        return verdict, kinds | {f.kind for f in inject}, disclosed, findings

    cells.abicheck_judgement = judged
    try:
        s = SEED_BY_NAME[seed]
        cfg = default_config(s.lang, _toolchain())
        return run_cell(OP_BY_NAME[op], s, cfg, CellContext.at(tmp))
    finally:
        cells.abicheck_judgement = real


_G = Finding("FUNC_REMOVED", "add_ints", impact="BREAKING")
_S_BREAK = Finding("TYPE_SIZE_CHANGED", "S", impact="BREAKING")
_S_NOTE = Finding("TYPE_FIELD_ADDED_COMPATIBLE", "S", impact="COMPATIBLE")


@pytest.mark.parametrize(
    ("op", "seed", "inject", "cls"),
    [
        ("func_remove", "c_core", (), "agree"),
        ("func_remove", "c_core", (_G,), "false-attribution"),
        ("func_removed_and_private_record_grew", "c_opaque", (), "agree"),
        (
            "func_removed_and_private_record_grew",
            "c_opaque",
            (_S_BREAK,),
            "false-attribution",
        ),
        ("func_removed_and_private_record_grew", "c_opaque", (_S_NOTE,), "agree"),
        ("private_record_grew", "c_opaque", (), "agree"),
        # The verdict stays the real NO_CHANGE: only the attribution is false.
        ("private_record_grew", "c_opaque", (_S_BREAK,), "false-attribution"),
        ("public_record_grew", "c_byval", (), "agree"),
    ],
)
def test_claims_through_the_runner(op, seed, inject, cls, tmp_path: Path) -> None:
    cell = _run(op, seed, tmp_path, inject)
    if cell.status != "ran":
        pytest.skip(cell.reason)
    assert cell.cls == cls, cell.describe()


def test_witness_exported_by_both_libraries(tmp_path: Path) -> None:
    cell = _run("func_remove", "c_core", tmp_path)
    if cell.status != "ran":
        pytest.skip(cell.reason)
    built = [
        p
        for p in tmp_path.rglob("libedit.*")
        if p.is_file() and p.suffix in (".so", ".dylib") and ".dSYM" not in str(p)
    ]
    assert built, "no built library found"
    for lib in built:
        syms = exported_functions(lib)
        assert syms is not None and "add_ints" in syms
    witnesses = [c for c in cell.expectation.claims if not c.required]
    assert [c.entity.name for c in witnesses] == ["add_ints"]


def test_public_record_change_is_measured_independently(tmp_path: Path) -> None:
    """C: the layout probe (not abicheck) measures the size and offset change,
    and the client run diverges; the source still compiles (binary-only)."""
    cell = _run("public_record_grew", "c_byval", tmp_path)
    if cell.status != "ran" or not cell.exec_truth.available:
        pytest.skip(cell.reason or cell.exec_truth.reason)
    sig = " ".join(cell.exec_truth.signals)
    assert "sizeof(S): 8 -> 12" in sig and "offsetof(S,b): 4 -> 8" in sig
    assert cell.exec_truth.binary_break and not cell.exec_truth.source_break
    assert not cell.exec_truth.layout_only


def test_opaque_client_runs_unchanged(tmp_path: Path) -> None:
    """B: the old client creates, uses and frees S with the new library."""
    cell = _run("private_record_grew", "c_opaque", tmp_path)
    if cell.status != "ran" or not cell.exec_truth.available:
        pytest.skip(cell.reason or cell.exec_truth.reason)
    assert not cell.exec_truth.binary_break and not cell.exec_truth.source_break
    assert any(p.outcome == "no-break" for p in cell.pairs)
