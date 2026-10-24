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

"""Per-result SARIF ``properties`` projections: change detail,
reachability/impact, and the contract decision.

Pure projections of an already-resolved :class:`Change`; they decide nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from abicheck.contract_relevance_types import CompatibilityEvaluationStatus
from abicheck.model.contract_finding_relevance import (
    evaluation_status_of,
)
from abicheck.report.change_json import resolve_demangled_symbol

if TYPE_CHECKING:
    from datetime import date

    from abicheck.checker import Change, DiffResult


def _change_detail_properties(change: Change) -> dict[str, Any]:
    """The optional per-change value/attribution entries of the properties bag.

    Each is omitted rather than emitted as ``null`` when it carries nothing, so
    a SARIF consumer can tell "not applicable" from "empty".
    """
    props: dict[str, Any] = {}
    if change.old_value is not None:
        props["oldValue"] = change.old_value
    if change.new_value is not None:
        props["newValue"] = change.new_value
    if change.affected_symbols:
        props["affectedSymbols"] = change.affected_symbols
    if change.caused_by_type:
        props["causedByType"] = change.caused_by_type
    if change.caused_count > 0:
        props["causedCount"] = change.caused_count
    if change.correlated_change_kind:
        props["correlatedChangeKind"] = change.correlated_change_kind
    # ELF symbol linkage of a removed symbol (see reporter.py's identical
    # symbol_binding property for the full rationale) -- Codex review.
    if change.symbol_binding:
        props["symbolBinding"] = change.symbol_binding
    # Plan slice 7o: both names on every machine projection, not only on an
    # ELF_ONLY finding that carries its own -- `symbol`/`old_value` stay raw
    # (see `reporter.resolve_demangled_symbol` for the full rationale).
    if demangled := resolve_demangled_symbol(change):
        props["demangledSymbol"] = demangled
    return props


def _reachability_and_impact_properties(change: Change) -> dict[str, Any]:
    """Structured reachability evidence and graph-impact data.

    ADR-044 P1 item 4 — structured reachability evidence (previously
    description-text-only, e.g. inside the suppression_would_hide_public_break
    diagnostic's prose): whether this change is public-reachable, how, and
    the shortest proof path.

    G31 Phase B B3 (ADR-048) — structured graph impact data. SARIF's own
    relatedLocations/codeFlows model source-file locations, not abstract
    graph node/edge references, so surfacing this as typed `properties`
    (matching every other graph-derived field on this object) is the
    pragmatic fit here rather than forcing an artificial codeFlow —
    documented as a deliberate scope decision in ADR-048.
    """
    props: dict[str, Any] = {}
    if change.public_reachable:
        props["publicReachable"] = True
        if change.reachability_kind:
            props["reachabilityKind"] = change.reachability_kind
        if change.reachability_proof_path:
            props["reachabilityProofPath"] = change.reachability_proof_path
    if change.affected_public_roots:
        props["affectedPublicRoots"] = change.affected_public_roots
    if change.impact_proof_path:
        props["impactProofPath"] = change.impact_proof_path
    if change.impact_is_direct is not None:
        props["impactIsDirect"] = change.impact_is_direct
    return props


def _contract_properties(
    change: Change,
    relevance: Any,
    result: DiffResult,
    severity_config: Any,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """The per-finding ADR-049 contract fields, in reporter.py's canonical shape.

    ``contract_relevance_of`` returns ``None`` for a run that never opted into
    ``--contract``, which is what keeps this inert for every
    pre-existing SARIF report. Before this shape was unified, only the
    ``NOT_EVALUATED`` case set any of these, so an ``IN_CONTRACT`` /
    ``NOT_APPLICABLE`` finding under ``--contract`` carried no
    contract properties at all even though it has a real, stamped decision
    (CLI-audit P1).
    """
    if relevance is None:
        return {}
    props: dict[str, Any] = {}
    props["contractRelevance"] = relevance.value
    if change.contract_reason_code:
        props["contractReasonCode"] = change.contract_reason_code
    if change.contract_assurance is not None:
        props["contractAssurance"] = change.contract_assurance.value
    # evaluation_status_of always resolves to a real status once
    # `relevance` is known non-None (it falls back to deriving one from
    # the relevance itself -- see its own docstring), so there is no
    # reachable `None` branch to guard here -- `cast` tells mypy that
    # without adding one.
    status = cast(CompatibilityEvaluationStatus, evaluation_status_of(change))
    props["compatibilityEvaluationStatus"] = status.value
    decision = getattr(change, "compatibility_decision", None)
    props["compatibilityDecision"] = getattr(decision, "value", None)
    from abicheck.policy.gate_contribution import gate_contribution_for_change

    props["gateContribution"] = gate_contribution_for_change(
        change,
        severity_config,
        policy=result.policy,
        policy_file=result.policy_file,
        today=today,
    )
    if change.contract_evidence_refs is not None:
        props["contractEvidenceRefs"] = list(change.contract_evidence_refs)
    return props
