"""A generation-31 baseline with a restrict pointer parameter is not a break.

Before extractor generation 32, DWARF did not look through ``DW_TAG_restrict_type``
for a pointer's indirection, so ``int *restrict p`` was recorded at depth 0
(generation 32 records depth 1). A stored baseline from generation 31 therefore
reads as a pointer-level change against a fresh dump of the same source. That
is extractor drift, not an ABI change, and it is demoted like the other drift
cases: recorded, with the rule and reason, never silently dropped.

``fixtures/generations/restrict_depth/gen31_restrict_pointer.json`` is a real
single-file snapshot written by the generation-31 build of this repository from
``void f(int *restrict p)``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.declarations import Function, Param
from abicheck.policy.generation_drift_demotion import (
    GENERATION_DRIFT_RULE,
    RESTRICT_DEPTH_GENERATION,
    demote_restrict_depth_drift,
)
from abicheck.serialization import load_snapshot
from tests._abicheck_subprocess import abicheck_command

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "generations"
    / "restrict_depth"
    / "gen31_restrict_pointer.json"
)
_SOURCE = "void f(int *restrict p) { (void)p; }\n"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="builds ELF shared objects with GNU ld options",
)


def _fn(name: str, *params: Param) -> Function:
    return Function(name=name, mangled=name, return_type="void", params=list(params))


def _snap(generation: int | None, *functions: Function) -> AbiSnapshot:
    snap = AbiSnapshot(library="l", version="1", functions=list(functions))
    if generation is not None:
        snap.fact_generations = (generation, 3, 0)
    return snap


def _change(symbol: str) -> list[Change]:
    return [
        Change(
            kind=ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
            symbol=symbol,
            description="x",
            old_value="0",
            new_value="1",
        ),
        Change(kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol=symbol, description="x"),
    ]


def _stale_param() -> Param:
    return Param(name="p", type="int * restrict", pointer_depth=0)


def _live_param() -> Param:
    return Param(name="p", type="int * restrict", pointer_depth=1)


def test_the_stored_generation_31_snapshot_reads_as_a_restrict_artifact() -> None:
    """The fixture really is the pre-32 artifact: its pointer is at depth 0."""
    snap = load_snapshot(FIXTURE)
    (func,) = [f for f in snap.declarations.functions if f.name == "f"]
    assert func.params[0].pointer_depth == 0
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert (
        doc["sections"]["provenance"]["payload"]["fact_generations"]["extractor"] == 31
    )


@pytest.mark.integration
@pytest.mark.parametrize("order", ["stored_old", "stored_new"])
def test_fresh_dump_against_stored_generation_31_is_not_a_break(
    tmp_path: Path, order: str
) -> None:
    """Real build, real dump, real CLI compare, in both operand orders."""
    cc = shutil.which("gcc")
    if cc is None:
        pytest.skip("gcc not available")
    src = tmp_path / "live.c"
    src.write_text(_SOURCE, encoding="utf-8")
    live = tmp_path / "live.so"
    proc = subprocess.run(
        [
            cc,
            "-g",
            "-O0",
            "-shared",
            "-fPIC",
            "-Wl,-soname,libx.so.1",
            str(src),
            "-o",
            str(live),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    stored = str(FIXTURE)
    args = (
        ["compare", stored, str(live)]
        if order == "stored_old"
        else ["compare", str(live), stored]
    )
    report = tmp_path / "report.json"
    run = subprocess.run(
        abicheck_command(*args, "-o", f"json={report}"),
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert run.returncode != 4, run.stdout + run.stderr
    changes = json.loads(report.read_text(encoding="utf-8")).get("changes", [])
    restrict = [
        c
        for c in changes
        if c.get("kind") in ("func_params_changed", "param_pointer_level_changed")
    ]
    assert restrict, changes
    for change in restrict:
        assert change.get("effective_verdict") == "COMPATIBLE_WITH_RISK", change
        assert change.get("modulation_rule") == GENERATION_DRIFT_RULE, change


def test_the_generation_constant_is_the_one_that_fixed_the_depth() -> None:
    assert RESTRICT_DEPTH_GENERATION == 32


def test_a_stale_restrict_pointer_is_demoted_in_either_operand_order() -> None:
    stale = _snap(31, _fn("f", _stale_param()))
    live = _snap(32, _fn("f", _live_param()))
    demoted = demote_restrict_depth_drift(_change("f"), stale, live, 31, 32)
    assert all(c.effective_verdict is Verdict.COMPATIBLE_WITH_RISK for c in demoted)
    reverse = demote_restrict_depth_drift(_change("f"), live, stale, 32, 31)
    assert all(c.effective_verdict is Verdict.COMPATIBLE_WITH_RISK for c in reverse)


def test_no_demotion_when_both_operands_are_current_or_both_stale() -> None:
    stale = _snap(31, _fn("f", _stale_param()))
    live = _snap(32, _fn("f", _live_param()))
    assert all(
        c.effective_verdict is None
        for c in demote_restrict_depth_drift(_change("f"), live, live, 32, 32)
    )
    assert all(
        c.effective_verdict is None
        for c in demote_restrict_depth_drift(_change("f"), stale, stale, 31, 31)
    )


def test_a_depth_change_that_is_not_the_restrict_artifact_is_kept() -> None:
    """A real pointer-level change (depth 0 to 2) is still a break."""
    stale = _snap(31, _fn("f", Param(name="p", type="int * restrict", pointer_depth=0)))
    live = _snap(
        32, _fn("f", Param(name="p", type="int * * restrict", pointer_depth=2))
    )
    out = demote_restrict_depth_drift(_change("f"), stale, live, 31, 32)
    assert all(c.effective_verdict is None for c in out)


def test_an_artifact_beside_a_real_parameter_change_is_kept() -> None:
    """Every differing position must be the artifact, or none is demoted."""
    stale = _snap(
        31, _fn("f", _stale_param(), Param(name="q", type="int", pointer_depth=0))
    )
    live = _snap(
        32, _fn("f", _live_param(), Param(name="q", type="long", pointer_depth=0))
    )
    out = demote_restrict_depth_drift(_change("f"), stale, live, 31, 32)
    assert all(c.effective_verdict is None for c in out)


def test_a_return_type_change_is_kept() -> None:
    stale = _snap(31, _fn("f", _stale_param()))
    live_fn = _fn("f", _live_param())
    live_fn.return_type = "int"
    out = demote_restrict_depth_drift(_change("f"), stale, _snap(32, live_fn), 31, 32)
    assert all(c.effective_verdict is None for c in out)
