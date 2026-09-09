# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""The policy-disposition ledger operations a frontend legitimately performs.

Same rule and same shape as ``workflows/policy_file.py`` and
``workflows/suppression.py`` (ADR-061 Phase 4 item 2): ``policy`` is not in
``frontends.may_import``, so a CLI module that has to close the run's
disposition ledger reaches it through the workflow layer rather than
importing ``policy/disposition_ledger.py`` directly.

One frontend needed this originally, for one reason: the scoped-gate
orchestrators (``cli_helpers_compare._apply_used_by_scoping`` and
``_apply_required_symbol_scoping``) are the only code that knows the *union*
of relevant findings across every ``--used-by`` consumer, and
``close_consumer_scope`` has to be called once with that union rather than
once per consumer (``apply_scope`` only demotes, so per-consumer calls would
intersect the consumers' sets). See that function's own docstring.
``record_suppressed_change``/``override_suppressed_change`` joined it for a
second and third reason: ``cli_scan_baseline._run_baseline_compare`` records
a baseline scan's ``--crosscheck KEY=off`` disposition the same way
``checker._filter_suppressed_changes`` records an ordinary ``--suppress``
rule (AGENTS.md's "record before disposing" rule) -- the observed finding
moves to ``suppressed_changes`` with its own rule/reason rather than being
silently dropped from ``diff.changes``. Because that call happens *after*
``compare_snapshots()`` already finalized the ledger, the plain (first-write-
wins) recorder is a no-op there -- ``override_suppressed_change`` is the
dedicated, explicitly-a-revision primitive that call site actually needs;
see ``disposition_close.override_suppression``'s own docstring (that module,
not ``disposition_ledger.py``: the 800-line production-file seam).
``RuleProvenance`` joined for the same call site's fourth reason: a
``--crosscheck KEY=off`` policy is not a suppression-file rule, so it builds
its own synthetic provenance directly rather than duck-typing a fake
``Suppression`` (Codex review, fourth round). ``Disposition`` joined for a
fifth: that same call site's post-removal verdict recompute needs to tell
which of ``DiffResult.redundant_changes`` were part of the original
verdict-scored population (``Disposition.GATING``, per
``disposition_close.finalize_ledger``'s own ``verdict_scored`` handling) --
querying the ledger via ``DispositionLedger.record_for`` rather than
re-deriving the ``caused_by_type``-based rule ``checker.compare()`` itself
uses, which would be exactly the parallel-policy duplication this ledger
exists to avoid (CodeRabbit review, PR #1172).

Re-export only, deliberately: ``policy/disposition_ledger.py``/
``disposition_close.py``/``policy/disposition_types.py``/
``policy/rule_provenance.py`` remain the modules to read and to change.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..policy.disposition_close import (
    close_consumer_scope as close_consumer_scope,
    ledger_for as ledger_for,
    override_suppressed_change as override_suppressed_change,
)
from ..policy.disposition_ledger import (
    DispositionLedger,
    record_suppressed_change as record_suppressed_change,
)
from ..policy.disposition_types import Disposition as Disposition
from ..policy.rule_provenance import RuleProvenance as RuleProvenance

if TYPE_CHECKING:
    from ..checker_types import DiffResult

__all__ = [
    "Disposition",
    "RuleProvenance",
    "close_consumer_scope",
    "ledger_for",
    "override_suppressed_change",
    "record_suppressed_change",
    "supersede_as_suppressed",
]


def supersede_as_suppressed(
    result: DiffResult,
    changes: Iterable[object],
    *,
    application_point: str,
    rule_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Mutate *result*'s ``disposition_ledger`` in place, marking *changes*
    now ``SUPPRESSED`` (:meth:`~abicheck.policy.disposition_ledger.
    DispositionLedger.with_suppressed` -- see that method's own docstring
    for the full rationale: a release-level policy decision made strictly
    after a per-comparison ledger already closed, e.g.
    ``cli_compare_release_pairwise._suppress_lockstep_soname_findings``).
    *rule_id*/*reason* name the synthetic policy decision responsible (there
    is no real ``Suppression`` from a ``--suppress`` document behind it) --
    without a rule, the audit's own ``rules()`` tally silently omits the row
    entirely.

    Lives here, not in ``report/disposition_audit.py`` (Codex review, fresh
    evidence: "Move release suppression out of report projection") --
    mutating the ledger is the policy decision itself, not a projection of
    one already made, and ``report/``'s own contract is "consume completed
    workflow facts... without changing compatibility decisions"
    (``report/AGENTS.md``). A frontend applying this release-wide decision
    reaches it through the workflow layer instead, the same rule and shape
    every other entry in this module already follows: ``policy`` is not in
    ``frontends.may_import``. A no-op if *result* carries no real ledger yet
    (a hand-built ``DiffResult`` that never went through ``checker.compare``
    or ``policy.disposition_close.finalize_ledger``).
    """
    ledger = getattr(result, "disposition_ledger", None)
    if isinstance(ledger, DispositionLedger):
        rule = (
            RuleProvenance(rule_id=rule_id, reason=reason)
            if rule_id is not None
            else None
        )
        result.disposition_ledger = ledger.with_suppressed(
            changes, application_point=application_point, rule=rule
        )
