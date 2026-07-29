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

"""ADR-049 Phase 4: the persisted ``contract_evidence`` / ``evaluation_context``
/ ``decision_receipt`` snapshot blocks (plan Section 5.1).

This module implements the *shape* of Phase 4's three-block split -- nothing
here is wired into ``dumper.py``/``serialization.py``/``checker.py`` yet, the
same "land the typed shape first" pattern Phase 1
(``compatibility_evaluation_config.py``) and Phase 0
(``contract_relevance_types.py``) already used. No snapshot is actually
persisted with these blocks, and no evaluator reads them back; that is later,
still-unwired composition work (see
``docs/contribute/plans/public-contract-default.md``'s Phase 4 section).

Three blocks, matching the plan's own split:

- :class:`ContractEvidenceBlock` -- policy-independent observed provider
  evidence (:class:`ProviderEvidenceEntry`, one per provider consulted).
  Never depends on which contract mode/policy was in effect: the same
  evidence block is valid input to a later re-evaluation under a different
  effective configuration (plan Section 5.1: "re-evaluation uses old
  observations with a newly resolved context").
- :class:`EvaluationContextBlock` -- the resolved
  :class:`~abicheck.compatibility_evaluation_config.CompatibilityEvaluationConfig`
  (Phase 1) that produced one decision, plus the evaluator/identity-algorithm
  versions active when it did. Deliberately does **not** duplicate
  ``field_provenance`` as a second mapping: ``CompatibilityEvaluationConfig``
  already carries its own ``provenance`` field for exactly this (the plan's
  illustrative YAML shows ``field_provenance`` as a YAML-level sibling key,
  but that is a serialization-layer choice for a future writer, not a reason
  to duplicate the data in this Python-level shape).
- :class:`DecisionReceiptBlock` -- the small, mode/root-dependent closure
  result computed *from* the evidence block by a specific evaluation context
  (plan Section 5.1: "the mode/root-dependent closure is computed by the
  evaluator and stored in the decision receipt, not as observed evidence").

:class:`PersistedContractContext` binds the three as the sibling group the
plan persists together on one snapshot side.

Every counter (``schema_version``/``evaluator_version``/
``identity_algorithm_version``) is one of the four already-reserved
constants in ``contract_relevance_types.py``
(``CONTRACT_EVIDENCE_SCHEMA_VERSION``/``EVALUATION_CONTEXT_SCHEMA_VERSION``/
``EVALUATOR_VERSION``/``IDENTITY_ALGORITHM_VERSION``) -- this module supplies
the shape those constants describe, not new version concerns.
:func:`check_persisted_context_versions_supported` implements ADR-049 D6's
fail-closed rule: a version *older than or equal to* what this build
understands is accepted (a legacy snapshot stays readable, possibly
degraded); a version *newer* than what this build understands raises
:class:`UnsupportedSchemaVersionError` rather than silently reinterpreting
data it cannot correctly parse. All five version fields (backed by the four
reserved constants above -- ``IDENTITY_ALGORITHM_VERSION`` supplies two of
the five) are checked independently -- a mixed-version
``PersistedContractContext`` (older evidence paired with a newer evaluation
context, the documented re-evaluation case) is not itself an error; only
each individual field exceeding its current ceiling is.

Every dataclass here is frozen, following the same normalize-in-``__post_init__``
convention as ``compatibility_evaluation_config.py``: a mapping/sequence field
is copied into an immutable ``MappingProxyType``/``tuple`` so a caller's later
mutation of the collection it passed in cannot silently change an
already-constructed, supposedly-immutable block.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeVar

from .compatibility_evaluation_config import CompatibilityEvaluationConfig
from .contract_relevance_types import (
    CONTRACT_EVIDENCE_SCHEMA_VERSION,
    EVALUATION_CONTEXT_SCHEMA_VERSION,
    EVALUATOR_VERSION,
    IDENTITY_ALGORITHM_VERSION,
    ContractRelevance,
    EvidenceCompleteness,
    EvidenceProviderStatus,
)

_T = TypeVar("_T")


def _frozen_tuple(s: Sequence[_T], *, element_type: type[_T]) -> tuple[_T, ...]:
    """Freeze *s* into an order-preserving tuple, validating element types.

    Rejects a bare ``str``/``bytes`` (iterating one yields characters, not
    the intended single element) and materializes before validating so a
    one-shot iterable isn't silently consumed by the check alone -- mirrors
    ``compatibility_evaluation_config._frozen_tuple``.
    """
    if isinstance(s, (str, bytes)):
        raise TypeError(
            f"Expected a sequence of items, not a bare {type(s).__name__} "
            f"{s!r} -- iterating a string/bytes value yields individual "
            "characters, not the intended elements; wrap a single value in "
            "a list/tuple explicitly."
        )
    materialized = tuple(s)
    invalid = [item for item in materialized if not isinstance(item, element_type)]
    if invalid:
        raise TypeError(
            f"Every element must be a {element_type.__name__}, not: {invalid!r}"
        )
    return materialized


def _frozen_mapping(m: Mapping[str, object]) -> MappingProxyType[str, object]:
    return MappingProxyType(dict(m))


def _require_version_int(value: object, *, owner: str, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{owner}.{field_name} must be an int, not {value!r}.")
    if value < 1:
        raise ValueError(
            f"{owner}.{field_name} must be >= 1, not {value!r} -- version "
            "counters start at 1 (ADR-049 D6); 0/negative has no defined meaning."
        )


_VALID_SIDES: frozenset[str] = frozenset({"old", "new"})


# --------------------------------------------------------------------------
# Shared small value types.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InputIdentity:
    """Content identity of one observed input (plan Section 5.1:
    ``input_identity: {sha256: "..."}``).

    Deliberately narrower than
    :class:`~abicheck.compatibility_evaluation_config.ImmutableIdentity`
    (which additionally requires ``id``/``version`` naming a versioned,
    named resource like a policy base or pack) and
    :class:`~abicheck.compatibility_evaluation_config.DigestedItems` (a
    digested *list* of item names): this identifies a single observed
    input file/artifact by content digest alone, optionally with a path for
    human-readable context.
    """

    sha256: str
    path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, str):
            raise TypeError(f"InputIdentity.sha256 must be a str, not {self.sha256!r}.")
        if not self.sha256:
            raise ValueError(
                "InputIdentity.sha256 must be a non-empty digest -- an empty "
                "string cannot detect content drift on replay, the exact "
                "guarantee this field exists for (ADR-049 D6)."
            )
        if self.path is not None and not isinstance(self.path, str):
            raise TypeError(
                f"InputIdentity.path must be a str or None, not {self.path!r}."
            )


@dataclass(frozen=True)
class TypeGraphSnapshot:
    """Raw, policy-independent type nodes/edges observed by one provider
    (plan Section 5.1: ``type_graph: {nodes: [], edges: []}``).

    Deliberately minimal: *what* a node/edge actually represents (a real
    type identity, a reachability relationship, ...) is real evidence-
    collection wiring this pass does not attempt -- only the versioned
    container shape a future writer/reader agrees on. ``nodes`` are opaque
    string identifiers; ``edges`` are ``(from_node, to_node)`` pairs, both
    referencing entries in ``nodes`` by convention only (not enforced here,
    since this leaf module has no access to a real type-graph algorithm to
    validate referential integrity against).
    """

    nodes: tuple[str, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", _frozen_tuple(self.nodes, element_type=str))
        if isinstance(self.edges, (str, bytes)):
            raise TypeError(
                f"TypeGraphSnapshot.edges must be a sequence of (str, str) "
                f"pairs, not a bare {type(self.edges).__name__} {self.edges!r}."
            )
        edges: list[tuple[str, str]] = []
        for edge in self.edges:
            if (
                isinstance(edge, (str, bytes))
                or not isinstance(edge, Sequence)
                or len(edge) != 2
                or not isinstance(edge[0], str)
                or not isinstance(edge[1], str)
            ):
                raise TypeError(
                    "TypeGraphSnapshot.edges elements must each be a "
                    f"(str, str) pair, not {edge!r}."
                )
            # Accept any non-string two-element sequence (a plain ``tuple``
            # from in-process construction, or a ``list`` from a JSON/YAML
            # decoder -- ``json.loads`` has no tuple type, so a real
            # persisted-then-decoded edge is always a list) and normalize to
            # a tuple here, rather than rejecting the decoder's native shape
            # and pushing a bespoke inner-list conversion onto every future
            # reader (Codex review, fresh evidence).
            edges.append((edge[0], edge[1]))
        object.__setattr__(self, "edges", tuple(edges))


# --------------------------------------------------------------------------
# contract_evidence block.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceSearchRecord:
    """One provider's observation-status record (ADR-049 Section 4.1's
    ``EvidenceSearchRecord``).

    ``status``/``completeness`` describe the search as a whole;
    ``identity_coverage``/``configuration_coverage`` are the two
    completeness sub-facets Section 4.2's closed-world rule for
    ``UNKNOWN_UNPROVEN`` separately requires ("affected entity identity
    coverage is complete" and "every declared compile/generated-header
    variant completed") -- kept as their own fields rather than folded into
    the single ``completeness`` value so a consumer can tell *which* facet
    was incomplete, not just that something was.
    """

    id: str
    provider: str
    side: str
    domain_kind: str
    status: EvidenceProviderStatus
    completeness: EvidenceCompleteness
    entity_class: str | None = None
    entity_scope: str | None = None
    domain_identity: str | None = None
    requested_scope: tuple[str, ...] = ()
    searched_scope: tuple[str, ...] = ()
    identity_coverage: EvidenceCompleteness = EvidenceCompleteness.NOT_STARTED
    configuration_coverage: EvidenceCompleteness = EvidenceCompleteness.NOT_STARTED
    reason_code: str | None = None
    input_identity: InputIdentity | None = None

    def __post_init__(self) -> None:
        for str_field in ("id", "provider", "side", "domain_kind"):
            value = getattr(self, str_field)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"EvidenceSearchRecord.{str_field} must be a non-empty "
                    f"str, not {value!r}."
                )
        if self.side not in _VALID_SIDES:
            raise ValueError(
                f"EvidenceSearchRecord.side must be one of "
                f"{sorted(_VALID_SIDES)}, not {self.side!r}."
            )
        for optional_str_field in (
            "entity_class",
            "entity_scope",
            "domain_identity",
            "reason_code",
        ):
            value = getattr(self, optional_str_field)
            if value is not None and not isinstance(value, str):
                raise TypeError(
                    f"EvidenceSearchRecord.{optional_str_field} must be a "
                    f"str or None, not {value!r}."
                )
        try:
            object.__setattr__(self, "status", EvidenceProviderStatus(self.status))
        except ValueError as exc:
            raise ValueError(
                f"EvidenceSearchRecord.status must be a valid "
                f"EvidenceProviderStatus, not {self.status!r}."
            ) from exc
        for completeness_field in (
            "completeness",
            "identity_coverage",
            "configuration_coverage",
        ):
            value = getattr(self, completeness_field)
            try:
                object.__setattr__(
                    self, completeness_field, EvidenceCompleteness(value)
                )
            except ValueError as exc:
                raise ValueError(
                    f"EvidenceSearchRecord.{completeness_field} must be a "
                    f"valid EvidenceCompleteness, not {value!r}."
                ) from exc
        object.__setattr__(
            self,
            "requested_scope",
            _frozen_tuple(self.requested_scope, element_type=str),
        )
        object.__setattr__(
            self, "searched_scope", _frozen_tuple(self.searched_scope, element_type=str)
        )
        if self.input_identity is not None and not isinstance(
            self.input_identity, InputIdentity
        ):
            raise TypeError(
                "EvidenceSearchRecord.input_identity must be an "
                f"InputIdentity or None, not {self.input_identity!r}."
            )


@dataclass(frozen=True)
class ProviderEvidenceEntry:
    """One provider's full contribution to ``contract_evidence.providers``:
    its :class:`EvidenceSearchRecord` plus the raw observed content."""

    record: EvidenceSearchRecord
    declarations: tuple[str, ...] = ()
    manifests: tuple[str, ...] = ()
    type_graph: TypeGraphSnapshot = field(default_factory=TypeGraphSnapshot)

    def __post_init__(self) -> None:
        if not isinstance(self.record, EvidenceSearchRecord):
            raise TypeError(
                "ProviderEvidenceEntry.record must be an EvidenceSearchRecord, "
                f"not {self.record!r}."
            )
        object.__setattr__(
            self, "declarations", _frozen_tuple(self.declarations, element_type=str)
        )
        object.__setattr__(
            self, "manifests", _frozen_tuple(self.manifests, element_type=str)
        )
        if not isinstance(self.type_graph, TypeGraphSnapshot):
            raise TypeError(
                "ProviderEvidenceEntry.type_graph must be a TypeGraphSnapshot, "
                f"not {self.type_graph!r}."
            )


@dataclass(frozen=True)
class ContractEvidenceBlock:
    """The persisted, policy-independent ``contract_evidence`` block (plan
    Section 5.1)."""

    providers: tuple[ProviderEvidenceEntry, ...] = ()
    schema_version: int = CONTRACT_EVIDENCE_SCHEMA_VERSION
    identity_algorithm_version: int = IDENTITY_ALGORITHM_VERSION

    def __post_init__(self) -> None:
        providers = _frozen_tuple(self.providers, element_type=ProviderEvidenceEntry)
        # Canonicalize by each provider's own stable identity (side, id) --
        # not the order two independent collectors happened to visit
        # providers in. Provider traversal order has no defined semantics,
        # so leaving it order-preserving made two otherwise-identical
        # blocks compare unequal and serialize differently, contradicting
        # this phase's own byte/order-independent round-trip gate (Codex
        # review, fresh evidence).
        providers = tuple(
            sorted(providers, key=lambda entry: (entry.record.side, entry.record.id))
        )
        object.__setattr__(self, "providers", providers)
        _require_version_int(
            self.schema_version,
            owner="ContractEvidenceBlock",
            field_name="schema_version",
        )
        _require_version_int(
            self.identity_algorithm_version,
            owner="ContractEvidenceBlock",
            field_name="identity_algorithm_version",
        )


# --------------------------------------------------------------------------
# evaluation_context block.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationContextBlock:
    """The persisted ``evaluation_context`` block (plan Section 5.1): the
    resolved effective configuration plus the evaluator/identity-algorithm
    versions active when a decision was made."""

    resolved_config: CompatibilityEvaluationConfig
    schema_version: int = EVALUATION_CONTEXT_SCHEMA_VERSION
    evaluator_version: int = EVALUATOR_VERSION
    identity_algorithm_version: int = IDENTITY_ALGORITHM_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.resolved_config, CompatibilityEvaluationConfig):
            raise TypeError(
                "EvaluationContextBlock.resolved_config must be a "
                f"CompatibilityEvaluationConfig, not {self.resolved_config!r}."
            )
        _require_version_int(
            self.schema_version,
            owner="EvaluationContextBlock",
            field_name="schema_version",
        )
        _require_version_int(
            self.evaluator_version,
            owner="EvaluationContextBlock",
            field_name="evaluator_version",
        )
        _require_version_int(
            self.identity_algorithm_version,
            owner="EvaluationContextBlock",
            field_name="identity_algorithm_version",
        )

    @property
    def field_provenance(self) -> Mapping[str, object]:
        """Alias for ``resolved_config.provenance`` -- the plan's
        illustrative YAML shows ``field_provenance`` as a sibling key of
        ``resolved_config``, but this Python shape does not duplicate the
        data; this property is the one place that YAML-level naming maps
        onto the actual field."""
        return self.resolved_config.provenance


# --------------------------------------------------------------------------
# decision_receipt block.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionReceiptBlock:
    """The persisted ``decision_receipt`` block (plan Section 5.1): the
    mode/root-dependent closure computed *from* the evidence block by a
    specific evaluation context -- itself not observed evidence."""

    evaluated_contract_roots: tuple[str, ...] = ()
    evaluated_type_closure: tuple[str, ...] = ()
    relevance_by_finding: Mapping[str, ContractRelevance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evaluated_contract_roots",
            _frozen_tuple(self.evaluated_contract_roots, element_type=str),
        )
        object.__setattr__(
            self,
            "evaluated_type_closure",
            _frozen_tuple(self.evaluated_type_closure, element_type=str),
        )
        if not isinstance(self.relevance_by_finding, Mapping):
            raise TypeError(
                "DecisionReceiptBlock.relevance_by_finding must be a "
                f"Mapping[str, ContractRelevance], not {self.relevance_by_finding!r}."
            )
        normalized: dict[str, ContractRelevance] = {}
        for key, value in self.relevance_by_finding.items():
            if not isinstance(key, str):
                raise TypeError(
                    "DecisionReceiptBlock.relevance_by_finding keys must be "
                    f"str, not {key!r}."
                )
            try:
                normalized[key] = ContractRelevance(value)
            except ValueError as exc:
                raise ValueError(
                    "DecisionReceiptBlock.relevance_by_finding values must "
                    f"be a valid ContractRelevance, not {value!r} (for {key!r})."
                ) from exc
        object.__setattr__(self, "relevance_by_finding", _frozen_mapping(normalized))


# --------------------------------------------------------------------------
# The sibling group persisted together on one snapshot side.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistedContractContext:
    """The three sibling blocks ADR-049 plan Section 5.1 persists together:
    ``contract_evidence``, ``evaluation_context``, ``decision_receipt``.

    Not wired into any real snapshot yet -- see module docstring.
    """

    contract_evidence: ContractEvidenceBlock
    evaluation_context: EvaluationContextBlock
    decision_receipt: DecisionReceiptBlock = field(default_factory=DecisionReceiptBlock)

    def __post_init__(self) -> None:
        if not isinstance(self.contract_evidence, ContractEvidenceBlock):
            raise TypeError(
                "PersistedContractContext.contract_evidence must be a "
                f"ContractEvidenceBlock, not {self.contract_evidence!r}."
            )
        if not isinstance(self.evaluation_context, EvaluationContextBlock):
            raise TypeError(
                "PersistedContractContext.evaluation_context must be an "
                f"EvaluationContextBlock, not {self.evaluation_context!r}."
            )
        if not isinstance(self.decision_receipt, DecisionReceiptBlock):
            raise TypeError(
                "PersistedContractContext.decision_receipt must be a "
                f"DecisionReceiptBlock, not {self.decision_receipt!r}."
            )


# --------------------------------------------------------------------------
# ADR-049 D6: unknown future versions fail closed.
# --------------------------------------------------------------------------


class UnsupportedSchemaVersionError(ValueError):
    """A persisted block's version counter is newer than this build
    understands (ADR-049 D6: "unknown future versions fail closed").

    Never raised for a version older than or equal to what this build
    supports -- an older legacy snapshot stays readable, only possibly
    degraded (plan Section 5.1: "legacy snapshots remain readable but
    become unresolved where old-side facts needed by `public` are absent").
    """

    def __init__(
        self, *, block_name: str, observed_version: int, supported_version: int
    ) -> None:
        self.block_name = block_name
        self.observed_version = observed_version
        self.supported_version = supported_version
        super().__init__(
            f"{block_name}: persisted version {observed_version} is newer "
            f"than this build's supported version {supported_version} -- "
            "refusing to reinterpret data written by a newer, unrecognized "
            "schema (ADR-049 D6)."
        )


def check_schema_version_supported(
    *, block_name: str, observed_version: int, supported_version: int
) -> None:
    """Raise :class:`UnsupportedSchemaVersionError` if *observed_version*
    exceeds *supported_version*; a no-op for an older-or-equal version."""
    if not isinstance(observed_version, int) or isinstance(observed_version, bool):
        raise TypeError(
            f"{block_name}: observed_version must be an int, not {observed_version!r}."
        )
    if not isinstance(supported_version, int) or isinstance(supported_version, bool):
        raise TypeError(
            f"{block_name}: supported_version must be an int, not {supported_version!r}."
        )
    if observed_version > supported_version:
        raise UnsupportedSchemaVersionError(
            block_name=block_name,
            observed_version=observed_version,
            supported_version=supported_version,
        )


def check_persisted_context_versions_supported(ctx: PersistedContractContext) -> None:
    """Check every version counter in *ctx* against this build's currently
    supported ceiling, independently (ADR-049 D6).

    Five fields are checked (``contract_evidence.schema_version``,
    ``contract_evidence.identity_algorithm_version``,
    ``evaluation_context.schema_version``,
    ``evaluation_context.evaluator_version``,
    ``evaluation_context.identity_algorithm_version``) -- backed by four
    reserved constants, since ``IDENTITY_ALGORITHM_VERSION`` supplies both
    of the ``identity_algorithm_version`` fields. Each is checked one at a
    time, not required to agree with each other: a ``PersistedContractContext``
    combining older
    ``contract_evidence`` with a newer ``evaluation_context`` is the
    documented re-evaluation case (plan Section 5.1: "re-evaluation uses old
    observations with a newly resolved context"), not itself an error --
    only an individual counter exceeding *this build's* current ceiling is.
    Raises :class:`UnsupportedSchemaVersionError` on the first counter found
    to exceed its ceiling (checked in a fixed, deterministic order).
    """
    if not isinstance(ctx, PersistedContractContext):
        raise TypeError(
            f"check_persisted_context_versions_supported: ctx must be a "
            f"PersistedContractContext, not {ctx!r}."
        )
    check_schema_version_supported(
        block_name="contract_evidence.schema_version",
        observed_version=ctx.contract_evidence.schema_version,
        supported_version=CONTRACT_EVIDENCE_SCHEMA_VERSION,
    )
    check_schema_version_supported(
        block_name="contract_evidence.identity_algorithm_version",
        observed_version=ctx.contract_evidence.identity_algorithm_version,
        supported_version=IDENTITY_ALGORITHM_VERSION,
    )
    check_schema_version_supported(
        block_name="evaluation_context.schema_version",
        observed_version=ctx.evaluation_context.schema_version,
        supported_version=EVALUATION_CONTEXT_SCHEMA_VERSION,
    )
    check_schema_version_supported(
        block_name="evaluation_context.evaluator_version",
        observed_version=ctx.evaluation_context.evaluator_version,
        supported_version=EVALUATOR_VERSION,
    )
    check_schema_version_supported(
        block_name="evaluation_context.identity_algorithm_version",
        observed_version=ctx.evaluation_context.identity_algorithm_version,
        supported_version=IDENTITY_ALGORITHM_VERSION,
    )
