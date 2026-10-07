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

"""The built-in ``contract.mode`` (ADR-049 Phase 7's default flip).

Contract evaluation runs on every comparison. When no layer states a domain
(no ``--contract``, no ``.abicheck.yml`` ``scope.public``, no API
``contract_mode``), the default is the narrowest domain the run's own
evidence closes on every compared side -- decided where that evidence is
known (``contract_pipeline.build_contract_stage``), not where front ends
resolve their configuration, which happens before any snapshot exists. This
module owns the rule and the two helpers that keep a front end's
placeholder from overwriting the decision in the persisted receipt.
"""

from __future__ import annotations

from typing import Any

from ..compatibility_evaluation_config import ValueProvenance
from ..contract_relevance_types import ContractMode, ContractRelevance, SelectorLayer

__all__ = [
    "CONTRACT_MODE_FIELD",
    "adopt_evidence_adaptive_mode",
    "evidence_adaptive_contract_mode",
    "is_evidence_adaptive",
    "build_source_fallback_applies",
    "observed_export_fallback_applies",
    "mode_is_built_in_default",
]

#: The ``CompatibilityEvaluationConfig`` field this module decides.
CONTRACT_MODE_FIELD = "contract.mode"


def mode_is_built_in_default(config: Any) -> bool:
    """Whether *config*'s ``contract.mode`` was chosen by no stated layer.

    Such a value is a placeholder until the comparison's evidence is known:
    the real built-in default is :func:`evidence_adaptive_contract_mode`.
    """
    entry = getattr(config, "provenance", {}).get(CONTRACT_MODE_FIELD)
    return entry is None or entry.layer is SelectorLayer.BUILT_IN_DEFAULT


def adopt_evidence_adaptive_mode(config: Any, observed: Any) -> Any:
    """*config* with the comparison's own ``contract.mode`` when *config*'s was
    only the built-in placeholder.

    A front end resolves its configuration before any evidence exists, so a
    mode no layer stated is a placeholder there; the comparison decided the
    real one from its evidence (:func:`evidence_adaptive_contract_mode`) and
    recorded it in *observed*. The receipt must name what ran.
    """
    from dataclasses import replace

    if not mode_is_built_in_default(config) or CONTRACT_MODE_FIELD not in (
        observed.provenance
    ):
        return config
    return replace(
        config,
        contract=replace(config.contract, mode=observed.contract.mode),
        provenance={
            **config.provenance,
            CONTRACT_MODE_FIELD: observed.provenance[CONTRACT_MODE_FIELD],
        },
    )


def evidence_adaptive_contract_mode(
    *, public_header_evidence: bool, export_evidence: bool
) -> tuple[ContractMode, ValueProvenance]:
    """The built-in ``contract.mode`` when no layer stated one (ADR-049 Phase 7).

    The narrowest contract every compared side has the evidence to close:
    ``public`` when every side was dumped with public headers, else
    ``exports`` when every side carries an observed export table, else
    ``all``. A literal ``public`` default would judge a bare
    binary-to-binary comparison against a contract nobody supplied: every
    entity finding reads ``UNKNOWN_UNRESOLVED`` and a removed export stops
    gating -- measured, ``compare old.so new.so`` with one function removed
    went from BREAKING/exit 4 to COMPATIBLE/exit 1. ``exports`` is the
    contract that evidence *does* state, and a snapshot with neither (a
    hand-built or header-less stored document) is judged on every entity,
    exactly as before the flip. Supplying headers is what narrows the
    contract to the declared surface ("optional inputs stay optional";
    "weaker evidence narrows conclusions", never the reverse).

    Provenance is ``built_in_default`` with ``source_kind`` naming the rule
    and ``reference`` the branch taken, so the receipt says why.
    """
    if public_header_evidence:
        mode, why = ContractMode.PUBLIC, "public_header_evidence"
    elif export_evidence:
        mode, why = ContractMode.EXPORTS, "export_table_evidence"
    else:
        mode, why = ContractMode.ALL, "no_contract_evidence"
    return mode, ValueProvenance(
        layer=SelectorLayer.BUILT_IN_DEFAULT,
        source_kind="evidence_adaptive",
        reference=why,
    )


def is_evidence_adaptive(provenance: ValueProvenance | None) -> bool:
    """True when the domain was chosen by :func:`evidence_adaptive_contract_mode`
    rather than stated by any layer."""
    return provenance is not None and provenance.source_kind == "evidence_adaptive"


def observed_export_fallback_applies(
    mode: ContractMode,
    provenance: ValueProvenance | None,
    reason_code: str | None,
    relevance: ContractRelevance | None = None,
) -> bool:
    """Whether an unstated ``public`` default must consult observed exports.

    A stated ``--contract public`` means "the headers are the whole promise",
    and a symbol they never commit to is ``closed_domain_no_commitment``.
    Nobody stated that for an evidence-adaptive default, and the headers'
    silence does not prove nobody binds to an exported symbol (``dlsym``, a
    leaked prototype) -- so a finding the header domain leaves uncommitted
    is judged by the export domain instead. The default contract is thereby
    public headers *plus* what the binary observably exports, never less
    than either; it can only keep a finding scored that a stated ``public``
    would not.

    The same holds where the header domain could not decide at all
    (``UNKNOWN_UNRESOLVED``/``UNKNOWN_UNPROVEN``): an undeclared ABI-support
    export (``_ZTV``/``_ZTI``/``_ZTS``) has no header declaration to place,
    yet the export table observed it. Restricting the fallback to an explicit
    non-commitment let ``-H`` turn a binary-only BREAKING into a passing run
    -- headers subtracting a break, which "weaker evidence narrows
    conclusions" forbids. An authoritative header *exclusion* (private
    header, POST manifest) is evidence, not a gap, and is not re-judged.
    """
    if mode is not ContractMode.PUBLIC or not is_evidence_adaptive(provenance):
        return False
    return reason_code == "closed_domain_no_commitment" or relevance in (
        ContractRelevance.UNKNOWN_UNRESOLVED,
        ContractRelevance.UNKNOWN_UNPROVEN,
    )


def build_source_fallback_applies(
    mode: ContractMode,
    provenance: ValueProvenance | None,
    relevance: ContractRelevance | None,
    *,
    build_source_evidence: bool,
) -> bool:
    """Whether an evidence-adaptive default must judge a finding on ``all``.

    ``--sources``/``--build-info`` evidence (L3-L5, ``AbiSnapshot.
    build_source``) describes the whole artifact: a compile-flag flip, a
    removed public macro, inline function or typedef, a new internal
    dependency. It closes no narrower domain -- neither the export table nor
    a header surface can place ``build-option:enum_size`` or a macro name --
    so a finding the selected domain (and the observed-export fallback) left
    ``UNKNOWN_*`` is judged on ``all``, the domain that evidence speaks to.
    Applies only when every compared side carries that evidence and nobody
    stated a domain; a stated ``--contract`` is never second-guessed. Like
    the export fallback it can only keep a finding scored, never drop one:
    supplying optional evidence never makes a verdict cleaner (ADR-049
    Phase 7, "never less than either").
    """
    return (
        build_source_evidence
        and is_evidence_adaptive(provenance)
        and mode is not ContractMode.ALL
        and relevance
        in (ContractRelevance.UNKNOWN_UNRESOLVED, ContractRelevance.UNKNOWN_UNPROVEN)
    )
