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

"""ADR-049's per-finding contract-decision fields, as a leaf projection.

Lifted verbatim out of ``reporter.py`` (where it was
``_add_contract_evaluation_fields``, re-exported from there for its existing
callers) so that *every* audit-ledger serializer can reach it without
importing ``reporter`` itself. That mattered the moment the suppression
ledger moved into this package for ADR-067: a ``report`` module importing
``reporter``, which imports this package back, is a real import cycle -- and
the fix the AI-readiness gate itself prescribes is to move the shared logic
to a leaf both sides can depend on, not to allowlist the cycle.

Leaf by construction: it reaches only ``finding_identity`` and
``contract_gating``, both of which are themselves leaves.
"""

from __future__ import annotations


def add_contract_evaluation_fields(
    d: dict[str, object],
    c: object,
    *,
    gate_contribution: int = 0,
) -> None:
    """Attach ADR-049's per-finding contract decision fields to *d*, if *c*
    carries one; always stamps ``finding_id``/``canonical_finding_id`` first.

    Shared by :func:`_change_to_dict` and :func:`_add_surface_scope` so a
    demoted finding's decision is exposed the same way a kept finding's
    already is. ``contract_relevance is None`` (the default) skips only
    the contract-specific fields below.

    *gate_contribution* completes ADR-049 D1's canonical per-finding shape.
    It defaults to ``0`` -- the true answer for every audit ledger this
    helper serializes, since none of those findings reach a gate. Only the
    ``changes`` path passes a computed value, from
    :func:`~abicheck.severity.gate_contribution_for_change`.
    """
    # The audit-ledger serializers (`_out_of_surface_entry`,
    # `_suppressed_change_entry`, `_add_reconciled`, `_filtered_internal_entry`)
    # build compact dicts that never emit `finding_id` themselves, so a
    # consumer can't join a demoted/suppressed/reconciled finding to its
    # decision. Stamped here, only when absent -- and unconditionally,
    # ahead of the contract_relevance early return below: an ordinary run
    # without `--contract` still calls this on every one of those entries and
    # still needs a joinable id (Codex review: an earlier revision stamped
    # this after the early return instead, silently skipping it on every
    # default-run audit-ledger entry).
    if "finding_id" not in d:
        from ..finding_identity import report_finding_id

        d["finding_id"] = report_finding_id(c)
    # Same reasoning, for finding_id's backend-independent sibling (2.36).
    if "canonical_finding_id" not in d:
        from ..finding_identity import report_canonical_finding_id

        d["canonical_finding_id"] = report_canonical_finding_id(c)

    contract_relevance = getattr(c, "contract_relevance", None)
    if contract_relevance is None:
        return
    d["contract_relevance"] = contract_relevance.value
    d["contract_reason_code"] = getattr(c, "contract_reason_code", None)
    contract_assurance = getattr(c, "contract_assurance", None)
    if contract_assurance is not None:
        d["contract_assurance"] = contract_assurance.value
    # ADR-049 D1's canonical trio. `compatibility_decision` is JSON `null`
    # for a NOT_EVALUATED finding and must stay that way: `null` records that
    # compatibility policy never ran, which is a different statement from any
    # verdict -- including COMPATIBLE -- that a renderer might be tempted to
    # fill in.
    from ..contract_gating import evaluation_status_of

    status = evaluation_status_of(c)
    if status is not None:
        d["compatibility_evaluation_status"] = status.value
    decision = getattr(c, "compatibility_decision", None)
    d["compatibility_decision"] = getattr(decision, "value", None)
    d["gate_contribution"] = gate_contribution
    # ADR-049 Phase 3's provider-evidence ledger. Emitted even when empty --
    # `[]` is the real answer for a non-entity finding, and omitting the key
    # would be indistinguishable from an unstamped finding.
    contract_evidence_refs = getattr(c, "contract_evidence_refs", None)
    if contract_evidence_refs is not None:
        d["contract_evidence_refs"] = list(contract_evidence_refs)
