# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Per-side cross-source check evolution (ADR-068 D3, plan §6 Phase 2a).

``buildsource.crosscheck.run_crosschecks`` is a **single-snapshot** engine —
it diffs one snapshot's own evidence sources against each other and has no
OLD-vs-NEW pairing of its own (see that module's docstring). This is the
``workflows``-layer glue that makes one of its checks a real ``compare``
pipeline stage per ADR-068 §3 rows 3-4: run the unmodified check over EACH
side's snapshot independently, then fold the two per-side outcomes through
:func:`abicheck.compare.finding_evolution.evolve_check_findings` so the
emitted findings carry an explicit evolution state instead of the check's
own naturally single-sided contract silently reading a pre-existing problem
as newly introduced.

**Only one check is migrated in this slice** (``private_header_leak`` --
plan §3 rows 3-4, the correctness crux F-8/F-9 acceptance scenarios). The
other ten cross-source checks stay ``scan``-only; see
``tests/parity/gaps.py``'s ``EXPECTED_GAPS`` for exactly which are still
red. Extending :data:`MIGRATED_CROSSCHECKS` to cover another check is a
real, reviewed migration (own PR, own parity-gap deletion), not a config
toggle.

Import contract (ADR-061 D1): this module is physically inside
``abicheck/workflows/`` (a real, migrated ADR-061 package), whose
``may_import`` is ``[model, storage, extract, compare, policy]``.
``buildsource/crosscheck.py`` itself is classified ``workflows`` too (via
``architecture/modules.yaml``'s ``legacy_paths`` -- it already imports both
``extract``- and ``compare``-classified siblings, e.g.
``export_accounting.py``/``crosscheck_base.py``, which is exactly the mixed
extraction+comparison-orchestration shape ``workflows`` exists for, the same
reason ``checker.py`` itself is ``workflows``-classified rather than
``compare``), so importing it from here is a same-layer edge.
"""

from __future__ import annotations

from ..buildsource.crosscheck import (
    CHECK_PRIVATE_HEADER_LEAK,
    CrosscheckConfig,
    run_crosschecks,
)
from ..checker_types import Change
from ..compare.finding_evolution import evolve_check_findings
from ..model import AbiSnapshot

#: Checks migrated onto the FindingEvolution model so far (plan §6 Phase 2a
#: closes one check per PR -- see this module's own docstring). Extending
#: this tuple means: (1) the check's `EXPECTED_GAPS` row in
#: `tests/parity/gaps.py` is deleted in the same PR, (2) `tests/parity/
#: test_crosscheck_parity.py`'s generic scan-only loop excludes it, and
#: (3) a dedicated parity test proves its own F-8/F-9-shaped evolution
#: behavior -- never a bare addition to this tuple alone.
MIGRATED_CROSSCHECKS: tuple[str, ...] = (CHECK_PRIVATE_HEADER_LEAK,)


def _run_one_check(snapshot: AbiSnapshot, check: str) -> tuple[bool, list[Change]]:
    """Run *check* alone over *snapshot*.

    Returns ``(evaluated, findings)`` -- ``evaluated`` is
    ``CrosscheckResult.providers`` carrying an entry for *check*, which
    ``run_crosschecks`` only ever sets when that check's own coverage
    status was ``"present"`` (sufficient evidence; findings may still be
    empty) -- never on ``"skipped"`` (insufficient evidence). Restricting
    ``CrosscheckConfig.enabled`` to just *check* keeps this cheap: every
    other check is recorded as a disabled coverage row and never runs.
    """
    result = run_crosschecks(snapshot, CrosscheckConfig(enabled=frozenset({check})))
    evaluated = check in result.providers
    findings = [c for c in result.findings if c.kind.value == check]
    return evaluated, findings


def compute_crosscheck_evolution(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Evolution-stated findings for every migrated cross-source check.

    Returns ordinary :class:`~abicheck.checker_types.Change` objects, ready
    to fold into ``checker.compare()``'s ordinary ``changes`` list (the same
    shape its existing ``extra_changes`` parameter already accepts) --
    suppression, reporting, and verdict composition treat them uniformly,
    the same as every other detector's output. Returns ``[]`` when neither
    side has any header/origin evidence at all (the overwhelmingly common
    case for a plain binary-only comparison), so this stage is a genuine
    no-op unless header evidence is actually present -- no existing
    comparison's finding set changes just because this stage now runs.
    """
    out: list[Change] = []
    for check in MIGRATED_CROSSCHECKS:
        old_evaluated, old_findings = _run_one_check(old, check)
        new_evaluated, new_findings = _run_one_check(new, check)
        out.extend(
            evolve_check_findings(
                old_evaluated=old_evaluated,
                old_findings=old_findings,
                new_evaluated=new_evaluated,
                new_findings=new_findings,
            )
        )
    return out


__all__ = ["MIGRATED_CROSSCHECKS", "compute_crosscheck_evolution"]
