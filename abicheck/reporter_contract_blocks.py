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

"""The report-level blocks every JSON path attaches after the findings.

Split out of :mod:`abicheck.reporter`, which is at the 2000-line hard cap --
same reason ``reporter_markdown``/``report_summary`` live beside it. Every
import here is function-local for the reason the original site documented:
``contract_evidence``/``contract_context_io`` reach
``compatibility_evaluation_config`` -> ``checker_policy``, which a module-level
import would pull into every consumer of a ``DiffResult``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .impact.use_case_impact import add_use_case_impact

if TYPE_CHECKING:
    from .checker_types import DiffResult


def add_contract_context(d: dict[str, Any], result: DiffResult) -> None:
    """ADR-049 Phase 4's persisted contract blocks, plus P0.4's unconditional
    ``analysis_assurance`` (piggybacked here, unguarded below, to stay under
    the file-size cap). ``contract_context`` itself stays opt-in
    (``compare(..., contract_evaluation=True)``), serialized via
    :mod:`abicheck.contract_context_io` to match
    :func:`~abicheck.contract_replay.replay_original_decisions`. Called from
    all three JSON paths, same as ``_add_surface_scope``/``_add_reconciled``.
    """
    from .analysis_assurance import analysis_assurance_report_dict

    if (block := analysis_assurance_report_dict(result)) is not None:
        d["analysis_assurance"] = block
    add_use_case_impact(d, result)

    ctx = result.contract_context
    if ctx is None:
        return
    from .contract_context_io import persisted_context_to_dict
    from .contract_evidence import PersistedContractContext

    # `DiffResult.contract_context` is typed `object` (its real type reaches
    # `compatibility_evaluation_config` -> `checker_policy`, which every
    # consumer of `DiffResult` would then import), so narrow it here rather
    # than suppressing the argument type -- an `isinstance` check is also a
    # real guard against a caller having stuffed something else into an
    # untyped field (CodeRabbit review).
    if not isinstance(ctx, PersistedContractContext):
        return
    d["contract_context"] = persisted_context_to_dict(ctx)
    # ADR-049 Phase 5's *sibling* ledger (plan Section 6.1). It sits beside
    # the findings, not among them, because that is what makes it
    # unsuppressible: a coverage failure is not a `Change`, so
    # `checker._filter_suppressed_changes` -- the one place suppression is
    # applied -- can never see one, and "ordinary change suppressions ...
    # cannot suppress a provider/domain coverage failure" (Section 6.2) is a
    # structural fact rather than a rule something has to remember to
    # enforce. Emitted as `[]` rather than omitted when there are none: an
    # empty ledger is the real, checkable answer "this domain closed", which
    # an absent key could not distinguish from "not computed".
    from .contract_coverage_exit import coverage_exit_for_context
    from .contract_coverage_ledger import coverage_failures_for_context

    failures = coverage_failures_for_context(ctx)
    d["contract_coverage_failures"] = [f.to_dict() for f in failures]
    # ADR-049 Phase 7: what the ledger contributes to the exit code, now
    # actually applied rather than merely stated. Derived by the same
    # function the exit path uses, so the number a user reads is the one
    # that gated them -- including `contract.unresolved=warn` zeroing it
    # while the failures above stay listed, which is what accepting
    # incomplete assurance means as opposed to hiding it.
    d["contract_coverage_exit_contribution"] = coverage_exit_for_context(ctx)


