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

"""ADR-049 Phase 4: assembling one :class:`PersistedContractContext` from a
real comparison.

:mod:`abicheck.contract_evidence` owns the block *shapes*;
:mod:`abicheck.contract_evidence_collect` produces the observed
``contract_evidence`` half. This module produces the other two blocks and
binds all three:

- ``evaluation_context`` -- the resolved
  :class:`~abicheck.compatibility_evaluation_config.CompatibilityEvaluationConfig`
  that produced the decisions, with field-level provenance. Built from what
  ``checker.compare`` itself resolved, which is deliberately narrower than
  what a front end resolves (Phase 1's
  :mod:`abicheck.compatibility_evaluation_frontend` sees CLI/API/project
  inputs this core verb never receives): the provenance therefore records
  ``API_REQUEST`` for a value that arrived as a ``compare()`` argument,
  rather than claiming a CLI/recipe layer it cannot observe. Phase 5 is what
  replaces this with the front end's own already-resolved object;
  under-claiming until then is the honest encoding, since a wrong provenance
  layer is exactly what D7's precedence receipts exist to make impossible.
- ``decision_receipt`` -- the mode/root-dependent closure and the per-finding
  relevance map. Computed *from the evidence block's own persisted graph*
  (:func:`~abicheck.contract_evidence_collect.closure_from_graph`), not from
  the live surfaces, so the receipt is reproducible by a replay that has only
  the block (plan Section 5.1's round-trip requirement).

Nothing here is authoritative: the assembled context is an audit record of a
shadow evaluation (ADR-049 Phase 3), never consulted by verdict, policy, or
exit-code logic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from .checker_types import Change
from .compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    EvidenceConfig,
    GateConfig,
    SelectedByEntry,
    SurfaceConfig,
    ValueProvenance,
)
from .contract_evidence import (
    ContractEvidenceBlock,
    DecisionReceiptBlock,
    EvaluationContextBlock,
    PersistedContractContext,
)
from .contract_evidence_collect import (
    DOMAIN_ROOT_PROVIDER,
    PROVIDER_PUBLIC_HEADER,
    closure_from_graph,
)
from .contract_relevance_types import (
    ContractMode,
    ContractRelevance,
    SelectorLayer,
    coerce_contract_mode,
)

_API_REQUEST_REFERENCE = "checker.compare"


def _api_provenance(option: str) -> ValueProvenance:
    """Provenance for a value that reached ``compare()`` as an argument.

    ``API_REQUEST`` is D7's own layer for exactly this -- a typed caller
    stating a value -- and sits at the same precedence rank as
    ``EXPLICIT_CLI``. Claiming ``EXPLICIT_CLI`` here would assert a CLI
    option was typed, which this core verb cannot know.
    """
    return ValueProvenance(
        layer=SelectorLayer.API_REQUEST,
        source_kind="api_argument",
        reference=_API_REQUEST_REFERENCE,
        field_location=option,
        selected_by=(SelectedByEntry(layer=SelectorLayer.API_REQUEST, option=option),),
    )


def build_evaluation_context(
    *,
    mode: ContractMode,
    mode_provenance: ValueProvenance | None = None,
    policy: str = "strict_abi",
    internal_namespaces: Iterable[str] = (),
) -> EvaluationContextBlock:
    """The ``evaluation_context`` block for one comparison.

    *mode_provenance* is the receipt
    :func:`~abicheck.compatibility_evaluation_wiring.resolve_legacy_contract_mode`
    already returns for the legacy-alias path (``LEGACY_ALIAS``); pass it
    through rather than re-deriving, so the persisted receipt says which of
    D7's layers actually selected the domain. ``None`` means the caller
    stated the mode explicitly, which is recorded as ``API_REQUEST``.
    """
    mode = coerce_contract_mode(mode)
    from .compatibility_evaluation_frontend import builtin_policy_identity

    config = CompatibilityEvaluationConfig(
        contract=ContractConfig(mode=mode),
        evidence=EvidenceConfig(),
        surface=SurfaceConfig(internal_namespaces=tuple(internal_namespaces)),
        assurance=AssuranceConfig(),
        policy=CompatibilityPolicyConfig(base=builtin_policy_identity(policy)),
        gate=GateConfig(),
        provenance={
            "contract.mode": mode_provenance or _api_provenance("contract_mode"),
            "policy.base": _api_provenance("policy"),
        },
    )
    return EvaluationContextBlock(resolved_config=config)


def domain_roots(
    evidence: ContractEvidenceBlock, mode: ContractMode
) -> tuple[str, ...]:
    """The persisted root declarations of *mode*'s domain, both sides unioned.

    ``all`` has no root provider (ADR-049 D2: no root/closure evidence is
    required for it), so it has no roots here either -- an empty tuple, not
    "every declaration". Reporting the full declaration set for ``all`` would
    read as a computed closure that the mode by definition never computes.
    """
    mode = coerce_contract_mode(mode)
    provider = DOMAIN_ROOT_PROVIDER[mode]
    if provider is None:
        return ()
    roots: set[str] = set()
    for entry in evidence.providers:
        if entry.record.provider == provider:
            roots |= set(entry.declarations)
    return tuple(sorted(roots))


def build_decision_receipt(
    evidence: ContractEvidenceBlock,
    mode: ContractMode,
    relevance_by_finding: Mapping[str, ContractRelevance] | None = None,
) -> DecisionReceiptBlock:
    """The ``decision_receipt`` block: roots, closure, per-finding relevance.

    The closure is walked over each side's own persisted type graph from that
    side's own roots, then unioned -- never across sides, which would let an
    old-side root reach a new-side type that no old-side signature references.
    """
    mode = coerce_contract_mode(mode)
    provider = DOMAIN_ROOT_PROVIDER[mode]
    roots_by_side: dict[str, set[str]] = {}
    if provider is not None:
        for entry in evidence.providers:
            if entry.record.provider == provider:
                roots_by_side.setdefault(entry.record.side, set()).update(
                    entry.declarations
                )
    closure: set[str] = set()
    for entry in evidence.providers:
        if entry.record.provider != PROVIDER_PUBLIC_HEADER:
            continue
        side_roots = roots_by_side.get(entry.record.side, set())
        if side_roots:
            closure |= closure_from_graph(entry.type_graph, side_roots)
    return DecisionReceiptBlock(
        evaluated_contract_roots=domain_roots(evidence, mode),
        evaluated_type_closure=tuple(sorted(closure)),
        relevance_by_finding=dict(relevance_by_finding or {}),
    )


def relevance_map(
    changes: Sequence[Change], finding_id: Callable[[Change], object] | None = None
) -> dict[str, ContractRelevance]:
    """``{finding_id: relevance}`` for every already-stamped *change*.

    *finding_id* is injected as a callable rather than imported, for the same
    reason :func:`~abicheck.contract_evaluation.stamp_scoped_result_findings`
    injects it: importing ``reporter._finding_id`` here would close a
    ``checker -> contract_context -> reporter -> checker`` import cycle the
    ``import-cycle-growth`` AI-readiness gate rejects. With no callable given,
    findings are keyed by ``"<kind>:<symbol>"``, the same stable pair the
    report's own id is built from.
    """
    out: dict[str, ContractRelevance] = {}
    for change in changes:
        if change.contract_relevance is None:
            continue
        out[finding_key(change, finding_id)] = change.contract_relevance
    return out


def finding_key(
    change: Change, finding_id: Callable[[Change], object] | None = None
) -> str:
    """The receipt key for one finding -- see :func:`relevance_map`.

    Shared with :mod:`abicheck.contract_replay` so a re-evaluation's keys
    line up with the original receipt's without either side re-deriving the
    convention (a silent key mismatch would make every finding look newly
    added rather than re-decided).
    """
    if finding_id is not None:
        return str(finding_id(change))
    return f"{change.kind.value}:{change.symbol or ''}"


def build_persisted_context(
    evidence: ContractEvidenceBlock,
    *,
    mode: ContractMode,
    mode_provenance: ValueProvenance | None = None,
    policy: str = "strict_abi",
    internal_namespaces: Iterable[str] = (),
    changes: Sequence[Change] = (),
    finding_id: Callable[[Change], object] | None = None,
) -> PersistedContractContext:
    """Bind the three blocks ADR-049 Section 5.1 persists together."""
    return PersistedContractContext(
        contract_evidence=evidence,
        evaluation_context=build_evaluation_context(
            mode=mode,
            mode_provenance=mode_provenance,
            policy=policy,
            internal_namespaces=internal_namespaces,
        ),
        decision_receipt=build_decision_receipt(
            evidence, mode, relevance_map(changes, finding_id)
        ),
    )
