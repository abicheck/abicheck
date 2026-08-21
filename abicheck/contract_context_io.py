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

"""ADR-049 Phase 4: JSON round-trip for the three persisted contract blocks.

Phase 4's gate is "byte/order-independent round-trip decisions and explicit
mixed-version failure behavior." This module is the round-trip half:
:func:`persisted_context_to_dict` writes a
:class:`~abicheck.contract_evidence.PersistedContractContext` to plain
JSON-safe data, :func:`persisted_context_from_dict` reads one back, and the
two compose to the identity function on every field that participates in a
decision.

Three properties the implementation holds deliberately:

*Order independence.* Every collection is written sorted, and every block's
own ``__post_init__`` re-canonicalizes on read, so a context built by two
different traversal orders serializes to identical bytes and decodes to an
equal object. The gate says *byte*-independent for a reason: comparing the
decoded objects is not enough if the bytes differ, since a consumer
diffing two persisted reports would see spurious churn.

*Version fields survive verbatim.* A block's version counters are written and
read as-is rather than re-stamped with this build's current constants --
re-stamping would erase exactly the mixed-version evidence D6 requires the
reader to fail closed on. Reading does **not** itself enforce the ceiling;
:func:`~abicheck.contract_evidence.check_persisted_context_versions_supported`
does, and :mod:`abicheck.contract_replay` calls it before using a decoded
context. Keeping the two separate is what lets a tool *inspect* a
newer-than-supported block (to report the version mismatch) without being
forced to first accept it as evaluable.

*No lossy defaults on read.* An absent optional key decodes to ``None``/the
empty collection, never to a plausible-looking substitute. A decoded
``suppressions: None`` means "no suppression source was selected", which is
a different fact from an empty rule list -- the same distinction
:class:`~abicheck.compatibility_evaluation_config.SuppressionConfig` exists
to preserve.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .change_registry_types import Verdict
from .compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    DigestedItems,
    EvidenceConfig,
    EvidenceProviderRequirement,
    GateConfig,
    ImmutableIdentity,
    ScopedGateSelection,
    SelectedByEntry,
    SuppressionConfig,
    SurfaceConfig,
    ValueProvenance,
)
from .contract_evidence import (
    ContractEvidenceBlock,
    DecisionReceiptBlock,
    EvaluationContextBlock,
    EvidenceSearchRecord,
    InputIdentity,
    PersistedContractContext,
    ProviderEvidenceEntry,
    TypeGraphSnapshot,
)
from .contract_relevance_types import (
    ContractMode,
    ContractRelevance,
    EvidenceCompleteness,
    EvidenceProviderStatus,
    SelectorLayer,
)
from .severity import SeverityConfig, SeverityLevel


def _require_mapping(value: object, *, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{what} must be a JSON object, not {value!r}.")
    return value


def _required(m: Mapping[str, Any], key: str, *, what: str) -> Any:
    """Read a required key, failing with the same type as :func:`_require_mapping`.

    ``persisted_context_from_dict`` is a public reader for report JSON a
    consumer may not control, so a malformed payload must fail one way rather
    than three: a missing key would otherwise raise ``KeyError`` while a
    non-object raised ``TypeError`` and a bad enum value ``ValueError``
    (CodeRabbit review).
    """
    try:
        return m[key]
    except KeyError:
        raise TypeError(f"{what} is missing the required key {key!r}.") from None


def _sequence(value: object, *, what: str) -> Sequence[Any]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{what} must be a JSON array, not {value!r}.")
    return value


def _bool(m: Mapping[str, Any], key: str, *, what: str, default: bool) -> bool:
    """Decode an optional JSON boolean field, rejecting anything merely truthy.

    A bare ``bool(value)`` accepts any JSON value at all -- including the
    string ``"false"``, a non-empty string that is truthy in Python despite
    spelling the opposite of what it decodes to -- silently coercing it to
    ``True`` rather than rejecting the malformed payload the way every other
    strict decoder in this module does (Codex review, PR #817: this is
    exactly the check ``GateConfig.__post_init__`` itself already performs
    on construction, which a loose coercion here would bypass).

    Takes the mapping and key, not a pre-fetched ``m.get(key)`` value, so it
    can tell a genuinely *absent* key (a legacy payload written before this
    field existed -- degrades to *default*) apart from a *present* JSON
    ``null`` (an explicit, malformed "no value" that must be rejected the
    same as any other wrong-typed value): a pre-fetched value collapses both
    to Python ``None``, which is exactly the gap a second round of review
    found in this decoder's first cut (Codex review, fresh evidence).
    """
    if key not in m:
        return default
    value = m[key]
    if not isinstance(value, bool):
        raise TypeError(f"{what} must be a JSON boolean, not {value!r}.")
    return value


# --------------------------------------------------------------------------
# Small value types.
# --------------------------------------------------------------------------


def _gate_scope_from_dict(d: object) -> ScopedGateSelection | None:
    """Inverse of :func:`resolved_config_to_dict`'s ``gate.scope`` encoding.

    ``None`` when the resolved config carried no ADR-043 scoped-gate
    selection at all (the common case) -- distinct from a present-but-empty
    ``targets`` list, which round-trips as a real, zero-target selection.
    """
    if d is None:
        return None
    m = _require_mapping(d, what="gate.scope")
    return ScopedGateSelection(
        kind=_required(m, "kind", what="gate.scope"),
        targets=tuple(_sequence(m.get("targets"), what="gate.scope.targets")),
    )


def _identity_to_dict(identity: ImmutableIdentity) -> dict[str, Any]:
    return {"id": identity.id, "version": identity.version, "sha256": identity.sha256}


def _identity_from_dict(d: object) -> ImmutableIdentity:
    m = _require_mapping(d, what="an immutable identity")
    what = "an immutable identity"
    return ImmutableIdentity(
        id=_required(m, "id", what=what),
        version=int(_required(m, "version", what=what)),
        sha256=_required(m, "sha256", what=what),
    )


def _digested_to_dict(items: DigestedItems) -> dict[str, Any]:
    return {"items": list(items.items), "sha256": items.sha256}


def _digested_from_dict(d: object) -> DigestedItems:
    m = _require_mapping(d, what="a digested item list")
    return DigestedItems(
        items=tuple(_sequence(m.get("items"), what="digested items")),
        sha256=_required(m, "sha256", what="a digested item list"),
    )


def _selected_by_to_dict(entry: SelectedByEntry) -> dict[str, Any]:
    out: dict[str, Any] = {"layer": entry.layer.value}
    if entry.option is not None:
        out["option"] = entry.option
    if entry.argument_index is not None:
        out["argument_index"] = entry.argument_index
    if entry.path is not None:
        out["path"] = entry.path
    if entry.identity is not None:
        out["identity"] = _identity_to_dict(entry.identity)
    if entry.sha256 is not None:
        out["sha256"] = entry.sha256
    return out


def _selected_by_from_dict(d: object) -> SelectedByEntry:
    m = _require_mapping(d, what="a selected_by hop")
    identity = m.get("identity")
    return SelectedByEntry(
        layer=SelectorLayer(_required(m, "layer", what="a selected_by hop")),
        option=m.get("option"),
        argument_index=m.get("argument_index"),
        path=m.get("path"),
        identity=_identity_from_dict(identity) if identity is not None else None,
        sha256=m.get("sha256"),
    )


def _provenance_to_dict(prov: ValueProvenance) -> dict[str, Any]:
    out: dict[str, Any] = {"layer": prov.layer.value}
    for name in (
        "source_kind",
        "reference",
        "version",
        "path",
        "sha256",
        "field_location",
    ):
        value = getattr(prov, name)
        if value is not None:
            out[name] = value
    if prov.selected_by:
        out["selected_by"] = [_selected_by_to_dict(e) for e in prov.selected_by]
    if prov.shadowed_legacy is not None:
        out["shadowed_legacy"] = _provenance_to_dict(prov.shadowed_legacy)
    return out


def _provenance_from_dict(d: object) -> ValueProvenance:
    m = _require_mapping(d, what="a field provenance entry")
    shadowed = m.get("shadowed_legacy")
    return ValueProvenance(
        layer=SelectorLayer(_required(m, "layer", what="a field provenance entry")),
        source_kind=m.get("source_kind"),
        reference=m.get("reference"),
        version=m.get("version"),
        path=m.get("path"),
        sha256=m.get("sha256"),
        field_location=m.get("field_location"),
        selected_by=tuple(
            _selected_by_from_dict(e)
            for e in _sequence(m.get("selected_by"), what="selected_by")
        ),
        shadowed_legacy=_provenance_from_dict(shadowed)
        if shadowed is not None
        else None,
    )


# --------------------------------------------------------------------------
# The resolved configuration (evaluation_context.resolved_config).
# --------------------------------------------------------------------------


def resolved_config_to_dict(config: CompatibilityEvaluationConfig) -> dict[str, Any]:
    """Serialize the whole effective configuration (plan Section 5.1).

    "``evaluation_context`` must serialize the *complete* immutable resolved
    ``CompatibilityEvaluationConfig``" -- every namespace is written, including
    the ones this build never populates yet (evidence providers/variants,
    contract overlays/packs, gate packs), so a future resolver that does
    populate them needs no schema change and an older reader sees an empty
    list rather than a missing key it must guess about.
    """
    return {
        "contract": {
            "mode": config.contract.mode.value,
            "unresolved": config.contract.unresolved,
            "overlays": list(config.contract.overlays),
            "packs": [_identity_to_dict(p) for p in config.contract.packs],
        },
        "evidence": {
            "providers": [
                {
                    "capability": p.capability,
                    "required": p.required,
                    "implementation": _identity_to_dict(p.implementation),
                }
                for p in config.evidence.providers
            ],
            "variants": (
                _digested_to_dict(config.evidence.variants)
                if config.evidence.variants is not None
                else None
            ),
        },
        "surface": {
            "explicit_scope": (
                _digested_to_dict(config.surface.explicit_scope)
                if config.surface.explicit_scope is not None
                else None
            ),
            "hints": {"internal_namespaces": list(config.surface.internal_namespaces)},
        },
        "assurance": {"require_evidence": config.assurance.require_evidence},
        "policy": {
            "base": _identity_to_dict(config.policy.base),
            "packs": [_identity_to_dict(p) for p in config.policy.packs],
            "overrides": {
                kind: verdict.value
                for kind, verdict in sorted(config.policy.overrides.items())
            },
        },
        "gate": {
            "exit_code_scheme": config.gate.exit_code_scheme,
            "preset": (
                _identity_to_dict(config.gate.preset)
                if config.gate.preset is not None
                else None
            ),
            "packs": [_identity_to_dict(p) for p in config.gate.packs],
            "severity": {
                "abi_breaking": config.gate.severity.abi_breaking.value,
                "potential_breaking": config.gate.severity.potential_breaking.value,
                "quality_issues": config.gate.severity.quality_issues.value,
                "addition": config.gate.severity.addition.value,
            },
            "require_complete_analysis": config.gate.require_complete_analysis,
            "scope": (
                {
                    "kind": config.gate.scope.kind,
                    "targets": list(config.gate.scope.targets),
                }
                if config.gate.scope is not None
                else None
            ),
        },
        "suppressions": (
            {
                "rules": list(config.suppressions.rules),
                "sha256": config.suppressions.sha256,
            }
            if config.suppressions is not None
            else None
        ),
    }


def resolved_config_from_dict(
    d: object, provenance: Mapping[str, ValueProvenance] | None = None
) -> CompatibilityEvaluationConfig:
    """Inverse of :func:`resolved_config_to_dict`.

    *provenance* is passed separately because the plan's own YAML places
    ``field_provenance`` as a sibling key of ``resolved_config``, not inside
    it (:class:`~abicheck.contract_evidence.EvaluationContextBlock` documents
    the same asymmetry) -- so the caller that decoded the outer block owns
    reassembling the two halves.
    """
    m = _require_mapping(d, what="resolved_config")
    contract = _require_mapping(
        _required(m, "contract", what="resolved_config"),
        what="resolved_config.contract",
    )
    evidence = _require_mapping(
        _required(m, "evidence", what="resolved_config"),
        what="resolved_config.evidence",
    )
    surface = _require_mapping(
        _required(m, "surface", what="resolved_config"), what="resolved_config.surface"
    )
    assurance = _require_mapping(
        _required(m, "assurance", what="resolved_config"),
        what="resolved_config.assurance",
    )
    policy = _require_mapping(
        _required(m, "policy", what="resolved_config"), what="resolved_config.policy"
    )
    gate = _require_mapping(
        _required(m, "gate", what="resolved_config"), what="resolved_config.gate"
    )
    hints = _require_mapping(surface.get("hints") or {}, what="surface.hints")
    severity = _require_mapping(gate.get("severity") or {}, what="gate.severity")
    suppressions = m.get("suppressions")
    variants = evidence.get("variants")
    explicit_scope = surface.get("explicit_scope")
    preset = gate.get("preset")
    return CompatibilityEvaluationConfig(
        contract=ContractConfig(
            mode=ContractMode(
                _required(contract, "mode", what="resolved_config.contract")
            ),
            unresolved=contract.get("unresolved", "not_checkable"),
            overlays=tuple(_sequence(contract.get("overlays"), what="overlays")),
            packs=tuple(
                _identity_from_dict(p)
                for p in _sequence(contract.get("packs"), what="contract.packs")
            ),
        ),
        evidence=EvidenceConfig(
            providers=tuple(
                EvidenceProviderRequirement(
                    capability=_required(
                        _require_mapping(p, what="a provider requirement"),
                        "capability",
                        what="a provider requirement",
                    ),
                    required=bool(
                        _required(p, "required", what="a provider requirement")
                    ),
                    implementation=_identity_from_dict(
                        _required(p, "implementation", what="a provider requirement")
                    ),
                )
                for p in _sequence(evidence.get("providers"), what="evidence.providers")
            ),
            variants=_digested_from_dict(variants) if variants is not None else None,
        ),
        surface=SurfaceConfig(
            explicit_scope=(
                _digested_from_dict(explicit_scope)
                if explicit_scope is not None
                else None
            ),
            internal_namespaces=tuple(
                _sequence(hints.get("internal_namespaces"), what="internal_namespaces")
            ),
        ),
        assurance=AssuranceConfig(
            require_evidence=bool(assurance.get("require_evidence", True))
        ),
        policy=CompatibilityPolicyConfig(
            base=_identity_from_dict(
                _required(policy, "base", what="resolved_config.policy")
            ),
            packs=tuple(
                _identity_from_dict(p)
                for p in _sequence(policy.get("packs"), what="policy.packs")
            ),
            overrides={
                kind: Verdict(verdict)
                for kind, verdict in _require_mapping(
                    policy.get("overrides") or {}, what="policy.overrides"
                ).items()
            },
        ),
        gate=GateConfig(
            exit_code_scheme=gate.get("exit_code_scheme", "severity"),
            preset=_identity_from_dict(preset) if preset is not None else None,
            packs=tuple(
                _identity_from_dict(p)
                for p in _sequence(gate.get("packs"), what="gate.packs")
            ),
            severity=SeverityConfig(
                abi_breaking=SeverityLevel(severity.get("abi_breaking", "error")),
                potential_breaking=SeverityLevel(
                    severity.get("potential_breaking", "warning")
                ),
                quality_issues=SeverityLevel(severity.get("quality_issues", "warning")),
                addition=SeverityLevel(severity.get("addition", "info")),
            ),
            require_complete_analysis=_bool(
                gate,
                "require_complete_analysis",
                what="gate.require_complete_analysis",
                default=False,
            ),
            scope=_gate_scope_from_dict(gate.get("scope")),
        ),
        suppressions=(
            SuppressionConfig(
                sha256=_required(
                    _require_mapping(suppressions, what="suppressions"),
                    "sha256",
                    what="suppressions",
                ),
                rules=tuple(
                    _sequence(
                        _require_mapping(suppressions, what="suppressions").get(
                            "rules"
                        ),
                        what="suppression rules",
                    )
                ),
            )
            if suppressions is not None
            else None
        ),
        provenance=dict(provenance or {}),
    )


# --------------------------------------------------------------------------
# The three blocks.
# --------------------------------------------------------------------------


def _record_to_dict(record: EvidenceSearchRecord) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": record.id,
        "provider": record.provider,
        "side": record.side,
        "domain_kind": record.domain_kind,
        "status": record.status.value,
        "completeness": record.completeness.value,
        "requested_scope": list(record.requested_scope),
        "searched_scope": list(record.searched_scope),
        "identity_coverage": record.identity_coverage.value,
        "configuration_coverage": record.configuration_coverage.value,
        "ambiguous_identities": list(record.ambiguous_identities),
    }
    for name in ("entity_class", "entity_scope", "domain_identity", "reason_code"):
        value = getattr(record, name)
        if value is not None:
            out[name] = value
    if record.input_identity is not None:
        identity: dict[str, Any] = {"sha256": record.input_identity.sha256}
        if record.input_identity.path is not None:
            identity["path"] = record.input_identity.path
        out["input_identity"] = identity
    return out


def _record_from_dict(d: object) -> EvidenceSearchRecord:
    m = _require_mapping(d, what="an evidence search record")
    input_identity = m.get("input_identity")
    what = "an evidence search record"
    return EvidenceSearchRecord(
        id=_required(m, "id", what=what),
        provider=_required(m, "provider", what=what),
        side=_required(m, "side", what=what),
        domain_kind=_required(m, "domain_kind", what=what),
        status=EvidenceProviderStatus(_required(m, "status", what=what)),
        completeness=EvidenceCompleteness(_required(m, "completeness", what=what)),
        entity_class=m.get("entity_class"),
        entity_scope=m.get("entity_scope"),
        domain_identity=m.get("domain_identity"),
        requested_scope=tuple(
            _sequence(m.get("requested_scope"), what="requested_scope")
        ),
        searched_scope=tuple(_sequence(m.get("searched_scope"), what="searched_scope")),
        identity_coverage=EvidenceCompleteness(
            m.get("identity_coverage", EvidenceCompleteness.NOT_STARTED.value)
        ),
        configuration_coverage=EvidenceCompleteness(
            m.get("configuration_coverage", EvidenceCompleteness.NOT_STARTED.value)
        ),
        ambiguous_identities=tuple(
            _sequence(m.get("ambiguous_identities"), what="ambiguous_identities")
        ),
        reason_code=m.get("reason_code"),
        input_identity=(
            InputIdentity(
                sha256=_require_mapping(input_identity, what="input_identity")[
                    "sha256"
                ],
                path=_require_mapping(input_identity, what="input_identity").get(
                    "path"
                ),
            )
            if input_identity is not None
            else None
        ),
    )


def contract_evidence_to_dict(block: ContractEvidenceBlock) -> dict[str, Any]:
    return {
        "schema_version": block.schema_version,
        "identity_algorithm_version": block.identity_algorithm_version,
        "providers": [
            {
                "record": _record_to_dict(entry.record),
                "declarations": list(entry.declarations),
                "manifests": list(entry.manifests),
                "type_graph": {
                    "nodes": list(entry.type_graph.nodes),
                    "edges": [list(edge) for edge in entry.type_graph.edges],
                },
            }
            for entry in block.providers
        ],
    }


def contract_evidence_from_dict(d: object) -> ContractEvidenceBlock:
    m = _require_mapping(d, what="contract_evidence")
    entries = []
    for raw in _sequence(m.get("providers"), what="contract_evidence.providers"):
        entry = _require_mapping(raw, what="a provider evidence entry")
        graph = _require_mapping(entry.get("type_graph") or {}, what="a type graph")
        entries.append(
            ProviderEvidenceEntry(
                record=_record_from_dict(
                    _required(entry, "record", what="a provider evidence entry")
                ),
                declarations=tuple(
                    _sequence(entry.get("declarations"), what="declarations")
                ),
                manifests=tuple(_sequence(entry.get("manifests"), what="manifests")),
                type_graph=TypeGraphSnapshot(
                    nodes=tuple(_sequence(graph.get("nodes"), what="graph nodes")),
                    edges=tuple(
                        tuple(edge)
                        for edge in _sequence(graph.get("edges"), what="graph edges")
                    ),
                ),
            )
        )
    return ContractEvidenceBlock(
        providers=tuple(entries),
        schema_version=int(m.get("schema_version", 1)),
        identity_algorithm_version=int(m.get("identity_algorithm_version", 1)),
    )


def evaluation_context_to_dict(block: EvaluationContextBlock) -> dict[str, Any]:
    return {
        "schema_version": block.schema_version,
        "evaluator_version": block.evaluator_version,
        "identity_algorithm_version": block.identity_algorithm_version,
        "resolved_config": resolved_config_to_dict(block.resolved_config),
        # Read through the typed source rather than the ``field_provenance``
        # alias property (which is declared ``Mapping[str, object]``, since
        # ``EvaluationContextBlock`` cannot narrow it without importing the
        # config's own value type into its signature). Same data either way:
        # the alias *is* ``resolved_config.provenance``.
        "field_provenance": {
            key: _provenance_to_dict(value)
            for key, value in sorted(block.resolved_config.provenance.items())
        },
    }


def evaluation_context_from_dict(d: object) -> EvaluationContextBlock:
    m = _require_mapping(d, what="evaluation_context")
    provenance = {
        key: _provenance_from_dict(value)
        for key, value in _require_mapping(
            m.get("field_provenance") or {}, what="field_provenance"
        ).items()
    }
    return EvaluationContextBlock(
        resolved_config=resolved_config_from_dict(
            _required(m, "resolved_config", what="evaluation_context"), provenance
        ),
        schema_version=int(m.get("schema_version", 1)),
        evaluator_version=int(m.get("evaluator_version", 1)),
        identity_algorithm_version=int(m.get("identity_algorithm_version", 1)),
    )


def decision_receipt_to_dict(block: DecisionReceiptBlock) -> dict[str, Any]:
    return {
        "schema_version": block.schema_version,
        "evaluated_contract_roots": list(block.evaluated_contract_roots),
        "evaluated_type_closure": list(block.evaluated_type_closure),
        "relevance_by_finding": {
            key: value.value
            for key, value in sorted(block.relevance_by_finding.items())
        },
    }


def decision_receipt_from_dict(d: object) -> DecisionReceiptBlock:
    m = _require_mapping(d, what="decision_receipt")
    return DecisionReceiptBlock(
        evaluated_contract_roots=tuple(
            _sequence(m.get("evaluated_contract_roots"), what="evaluated roots")
        ),
        evaluated_type_closure=tuple(
            _sequence(m.get("evaluated_type_closure"), what="evaluated closure")
        ),
        # Required, not defaulted: an absent map and an empty one would both
        # read as "this comparison recorded no decisions", so a truncated
        # receipt would replay as a real, decision-free run instead of
        # failing closed -- the same rule the whole `decision_receipt` block
        # already follows (Codex review, fresh evidence).
        relevance_by_finding={
            key: ContractRelevance(value)
            for key, value in _require_mapping(
                _required(m, "relevance_by_finding", what="a decision receipt"),
                what="relevance_by_finding",
            ).items()
        },
        # Absent only in a receipt written before this counter existed; such
        # a block is by definition version 1, and defaulting rather than
        # rejecting is what "legacy blocks remain readable" means (the
        # ceiling check lives in `contract_replay.load_replayable_context`).
        schema_version=int(m.get("schema_version", 1)),
    )


def persisted_context_to_dict(ctx: PersistedContractContext) -> dict[str, Any]:
    """The whole ``contract_evidence``/``evaluation_context``/
    ``decision_receipt`` sibling group as JSON-safe data."""
    return {
        "contract_evidence": contract_evidence_to_dict(ctx.contract_evidence),
        "evaluation_context": evaluation_context_to_dict(ctx.evaluation_context),
        "decision_receipt": decision_receipt_to_dict(ctx.decision_receipt),
    }


def persisted_context_from_dict(d: object) -> PersistedContractContext:
    """Inverse of :func:`persisted_context_to_dict`.

    Deliberately does not check version ceilings -- see the module docstring
    and :func:`~abicheck.contract_replay.load_replayable_context`, which does.
    """
    m = _require_mapping(d, what="a persisted contract context")
    return PersistedContractContext(
        contract_evidence=contract_evidence_from_dict(
            _required(m, "contract_evidence", what="a persisted contract context")
        ),
        evaluation_context=evaluation_context_from_dict(
            _required(m, "evaluation_context", what="a persisted contract context")
        ),
        # Required, like its two siblings: the report schema always writes
        # this block, so an absent one means a truncated or hand-edited
        # payload -- and defaulting it to an empty version-1 receipt made
        # `replay_original_decisions` return `{}`, which is indistinguishable
        # from a comparison that genuinely recorded no decisions (Codex
        # review). Failing closed is the same rule the version ceiling
        # follows: never reinterpret missing audit data as a real answer.
        decision_receipt=decision_receipt_from_dict(
            _required(m, "decision_receipt", what="a persisted contract context")
        ),
    )
