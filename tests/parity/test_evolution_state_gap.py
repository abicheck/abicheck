# SPDX-License-Identifier: Apache-2.0
"""F-8/F-9 (plan §7): the ``FindingEvolution`` axis doesn't exist yet.

F-8: a private-header leak present in **both** OLD and NEW, with OLD's
evidence too weak to have proven the leak at the time, must read as
``not_evaluated`` -- never as ``introduced`` (that would be a manufactured
finding, ADR-068's "second risk"). F-9: a leak present in OLD and fixed in
NEW must read as ``resolved``, and that must be visible on a passing run.

Unlike every other module in this package, this is **not** "scan has it,
compare doesn't" -- ADR-068 D3/plan §5 P2 state plainly that this
vocabulary (``introduced``/``resolved``/``persistent``/``not_evaluated``)
is *new* Phase-1 work. Even ``scan --against``'s own crosscheck pass runs
single-sided today: it evaluates the *candidate* snapshot only and has no
per-side evolution tracking at all, baseline-lacking-evidence case
included. So this module documents an absence on both tools, tracked
separately in ``gaps.py``'s ``NOT_YET_IMPLEMENTED_ANYWHERE`` (not
``EXPECTED_GAPS``, which is specifically "scan-only today").
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from abicheck.model import AbiSnapshot  # noqa: E402
from abicheck.serialization import load_snapshot  # noqa: E402

from .gaps import NOT_YET_IMPLEMENTED_ANYWHERE  # noqa: E402
from .runner import crosscheck_finding_set, kinds_of  # noqa: E402


def _g20_snapshot(case_name: str, filename: str = "snapshot.abi.json") -> AbiSnapshot:
    path = example_catalog.case_dir(case_name) / filename
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return load_snapshot(path)


def test_finding_evolution_is_registered_as_not_yet_implemented() -> None:
    assert "finding_evolution" in NOT_YET_IMPLEMENTED_ANYWHERE


def test_f8_pre_existing_leak_has_no_not_evaluated_state_to_assert() -> None:
    """F-8: with a fixed, single (candidate-only) snapshot -- case144's
    private-header leak -- there is no OLD-side evidence-weakness input to
    give run_crosschecks at all: it takes one snapshot, not an OLD/NEW
    pair, so it cannot distinguish "leak is new" from "leak is
    pre-existing but OLD's evidence couldn't see it" even in principle.
    This is the concrete shape of the Phase-1 gap: the finding fires
    (correctly, per crosscheck's own single-sided contract) with no
    evolution axis attached at all -- neither `introduced` nor
    `not_evaluated` exists on the `Change` object to check.
    """
    snapshot = _g20_snapshot("case144_audit_private_header_leak")
    findings = crosscheck_finding_set(snapshot)
    assert "private_header_leak" in kinds_of(findings)

    leak = next(f for f in findings if f.kind == "private_header_leak")
    # The gap, made concrete: Change has no evolution field at all.
    from abicheck.checker_types import Change

    field_names = {f.name for f in __import__("dataclasses").fields(Change)}
    assert "evolution" not in field_names, (
        "Change gained an `evolution` field -- Phase 1 "
        f"({NOT_YET_IMPLEMENTED_ANYWHERE['finding_evolution'].plan_phase}) "
        "has landed. Delete the 'finding_evolution' entry from "
        "tests/parity/gaps.py's NOT_YET_IMPLEMENTED_ANYWHERE and replace "
        "this module with a real not_evaluated/resolved assertion over a "
        "baseline pair, per plan F-8/F-9."
    )
    assert leak.identity if hasattr(leak, "identity") else leak.kind  # sanity


def test_f9_scan_against_has_no_per_side_crosscheck_at_all() -> None:
    """F-9: `resolved` (present in OLD, fixed in NEW) needs crosscheck to
    run over BOTH sides and diff the two outcomes. `scan --against` runs
    the always-on crosscheck tier over the **candidate** snapshot only
    (scan_engine.py's single `run_crosschecks(new_snap, ...)` call) --
    there is no second, OLD-side crosscheck pass to diff against, so
    "resolved" cannot be expressed even in scan's own report today."""
    import inspect

    from abicheck import scan_engine

    source = inspect.getsource(scan_engine)
    # A crude but honest count: run_crosschecks is called exactly once
    # per scan_engine.py's own pipeline (the candidate side), never twice
    # (which a baseline-diffed crosscheck pass would require).
    assert source.count("run_crosschecks(") == 1, (
        "scan_engine.py now calls run_crosschecks() more than once -- "
        "if this is a baseline-side crosscheck pass landing, the F-9 gap "
        "may be closing; update this test and reconsider the "
        "'finding_evolution' entry in tests/parity/gaps.py accordingly."
    )
