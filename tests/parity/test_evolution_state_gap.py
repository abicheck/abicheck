# SPDX-License-Identifier: Apache-2.0
"""F-8/F-9 (plan §7): the ``FindingEvolution`` axis, now landed.

ADR-068 Phase 1 item 2 (``docs/contribute/plans/one-comparison-product.md``
"Phase 1") added the generic vocabulary this module used to document the
absence of: ``checker_policy.FindingEvolution``,
``Change.evolution``/``DiffResult.resolved_findings``, and the
correspondence primitive in ``policy.finding_evolution``
(``compute_finding_evolution``/``compute_resolved_findings``/
``apply_finding_evolution``). This module now demonstrates F-8 and F-9
directly against real production code, instead of asserting the vocabulary
doesn't exist.

**F-8** (a pre-existing issue must never be manufactured as ``introduced``):
demonstrated against ``run_crosschecks``' own real ``Change`` objects for
case144's private-header leak -- a single, candidate-only snapshot has no
OLD-side evidence to compare against, so the *safe* default
(``NOT_EVALUATED``) is exactly what a real production finding carries
today, never a guessed ``INTRODUCED``.

**F-9** (a fixed issue must read as ``resolved``, and that must be visible
on a passing run): demonstrated against two real ``checker.compare()``
results composed through ``apply_finding_evolution`` -- the primitive that
gives a caller with real chain context (a longitudinal history, or a CI job
diffing today's findings against a stored prior run) exactly this
vocabulary.

What remains a **real, separate** gap -- unrelated to whether the generic
vocabulary exists -- is that ``scan --against``'s own crosscheck pass still
runs single-sided (Phase 2a: "cross-source checks ... per side,
evolution-stated" is what wires this primitive *into* crosscheck itself).
``test_f9_scan_against_has_no_per_side_crosscheck_at_all`` keeps documenting
that narrower, still-open gap; it is intentionally not registered in
``gaps.py`` any more, since ``NOT_YET_IMPLEMENTED_ANYWHERE`` is specifically
for "neither tool has this vocabulary at all", which is no longer true.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from abicheck.checker import compare  # noqa: E402
from abicheck.checker_policy import ChangeKind, FindingEvolution  # noqa: E402
from abicheck.model import AbiSnapshot, Function, Visibility  # noqa: E402
from abicheck.policy.finding_evolution import apply_finding_evolution  # noqa: E402
from abicheck.serialization import load_snapshot  # noqa: E402


def _g20_snapshot(case_name: str, filename: str = "snapshot.abi.json") -> AbiSnapshot:
    path = example_catalog.case_dir(case_name) / filename
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return load_snapshot(path)


def test_f8_pre_existing_leak_defaults_to_not_evaluated_never_manufactured() -> None:
    """F-8: with a fixed, single (candidate-only) snapshot -- case144's
    private-header leak -- ``run_crosschecks`` has no OLD-side evidence at
    all, so it cannot know whether the leak is new or pre-existing. The
    *safe* answer is ``NOT_EVALUATED`` (default), never a manufactured
    ``INTRODUCED`` -- and that is exactly what the real ``Change`` object
    carries, since crosscheck itself (Phase 2a work) does not compute
    evolution yet.
    """
    from abicheck.buildsource.cross_source_checks import run_crosschecks

    snapshot = _g20_snapshot("case144_audit_private_header_leak")
    result = run_crosschecks(snapshot, None)
    leak = next(c for c in result.findings if c.kind == ChangeKind.PRIVATE_HEADER_LEAK)
    assert leak.evolution is FindingEvolution.NOT_EVALUATED
    assert leak.evolution is not FindingEvolution.INTRODUCED


def test_f9_resolved_is_expressible_via_the_generic_primitive() -> None:
    """F-9: a finding present in one comparison and gone from the next must
    read as ``resolved``, visible on the *later* (passing) run. Demonstrated
    over two real ``compare()`` results -- ``apply_finding_evolution`` is
    the primitive a chain-aware caller (longitudinal history, a CI job
    diffing against a stored prior run) applies to get exactly this.
    """
    baseline = AbiSnapshot(library="libfoo", version="0.0")
    with_issue = AbiSnapshot(library="libfoo", version="1.0")
    with_issue.functions.append(
        Function(
            name="foo::gone",
            mangled="_ZN3foo4goneEv",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
    )
    without_issue = AbiSnapshot(library="libfoo", version="1.1")
    # Run 1 (an earlier point in the chain, against the same fixed
    # baseline): the finding is present.
    previous = compare(baseline, with_issue)
    # Run 2 (the current, later point, same baseline): the finding is gone
    # -- fixed.
    current = compare(baseline, without_issue)

    apply_finding_evolution(current, previous)

    assert any(
        c.evolution is FindingEvolution.RESOLVED for c in current.resolved_findings
    )
    # The fix is visible on a passing run's own resolved_findings list, not
    # only inferable by absence -- exactly F-9's "must be visible" half.
    resolved_symbols = {c.symbol for c in current.resolved_findings}
    assert "_ZN3foo4goneEv" in resolved_symbols


def test_f9_scan_against_has_no_per_side_crosscheck_at_all() -> None:
    """F-9's narrower, still-open half: `resolved` needs crosscheck itself
    to run over BOTH sides and diff the two outcomes -- unrelated to
    whether the generic vocabulary exists (it does, see the test above).
    `scan --against` runs the always-on crosscheck tier over the
    **candidate** snapshot only (scan_engine.py's single
    `run_crosschecks(new_snap, ...)` call): there is no second, OLD-side
    crosscheck pass for scan's own report to diff against. Phase 2a
    ("cross-source checks ... per side, evolution-stated") is what closes
    this; it is not part of Phase 1 item 2."""
    import ast
    import inspect

    from abicheck import scan_engine

    tree = ast.parse(inspect.getsource(scan_engine))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_crosschecks"
    ]
    # A real AST match, not a textual count that a comment or docstring
    # mentioning "run_crosschecks(" could inflate (Codex review): exactly
    # one call expression, over the *candidate* snapshot (new_snap) --
    # never a second, OLD-side pass, which a baseline-diffed crosscheck
    # would require to express "resolved".
    assert len(calls) == 1, (
        f"scan_engine.py calls run_crosschecks() {len(calls)} time(s), not 1 -- "
        "if this is a baseline-side crosscheck pass landing, Phase 2a may be "
        "closing; update this test to demonstrate crosscheck itself is now "
        "evolution-stated per side."
    )
    (call,) = calls
    assert call.args and isinstance(call.args[0], ast.Name)
    assert call.args[0].id == "new_snap", (
        f"run_crosschecks() is now called with {call.args[0].id!r}, not "
        "new_snap -- re-examine whether it now runs over the baseline side too"
    )
