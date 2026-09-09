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

Exactly one frontend needs this, for exactly one reason: the scoped-gate
orchestrators (``cli_helpers_compare._apply_used_by_scoping`` and
``_apply_required_symbol_scoping``) are the only code that knows the *union*
of relevant findings across every ``--used-by`` consumer, and
``close_consumer_scope`` has to be called once with that union rather than
once per consumer (``apply_scope`` only demotes, so per-consumer calls would
intersect the consumers' sets). See that function's own docstring.

Re-export only, deliberately: ``policy/disposition_ledger.py`` remains the
one module to read and to change.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..policy.disposition_close import (
    close_consumer_scope as close_consumer_scope,
    ledger_for as ledger_for,
)
from ..policy.disposition_ledger import DispositionLedger
from ..policy.rule_provenance import RuleProvenance

if TYPE_CHECKING:
    from ..checker_types import DiffResult

__all__ = ["close_consumer_scope", "ledger_for", "supersede_as_suppressed"]


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
