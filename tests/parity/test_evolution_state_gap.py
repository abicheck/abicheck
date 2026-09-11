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

This module used to also keep a third test,
``test_f9_scan_against_has_no_per_side_crosscheck_at_all``, documenting a
narrower, still-open half of F-9: ``scan --against``'s own crosscheck pass
ran single-sided (an AST-level pin on ``scan_engine.py``'s single
``run_crosschecks(new_snap, ...)`` call). That gap was specific to
``scan_engine.py``, deleted outright with the ``scan`` command itself
(ADR-068 Phase 6) -- there is no ``compare``-side equivalent single-sided
crosscheck limitation to pin (``compare``'s own cross-source-checks wiring
already runs per side via ``workflows.cross_source_evolution``, see that
module's own coverage), so the test went with it rather than being ported.
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
