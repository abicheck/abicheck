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

"""ADR-049 Phase 0: reserved vocabulary for contract relevance.

This module reserves the durable enums, the stable per-finding reason-code
registry, the legacy scope-flag alias table, and the snapshot/decision
schema-version constants that
:doc:`ADR-049 </contribute/adr/049-contract-relevance-and-compatibility-configuration>`
and its
:doc:`implementation plan </contribute/plans/public-contract-default>`
define. Nothing here is wired into detection, policy, the CLI, or reports
yet -- ``ContractMode``/``ContractRelevance`` are not produced or consumed by
any pipeline stage. This is intentionally a leaf module (imports nothing
from ``abicheck`` besides the standard library) so later phases -- the
effective-config resolver (Phase 1), the shadow evaluator (Phase 3), and the
snapshot schema (Phase 4) -- can depend on it without creating an import
cycle back into ``cli.py``/``checker.py``/``service.py``.

Phase 0's gate (ADR-049 plan Section 9) is that the reserved vocabulary
contains no conflation the ADR explicitly rejects:

- ``exports`` and ``all`` are distinct :class:`ContractMode` values -- an
  export root is not "every finding" (ADR-049 "Rejected alternatives").
- The machine value is ``PROVEN_OUT_OF_CONTRACT``, never a bare ``PRIVATE``
  (ADR-049 D1) -- the tool proves an entity is outside the *declared*
  contract, not that no consumer could reach it.
- There is no fourth ``public_contract`` mode/preset value anywhere in this
  module (ADR-049 D3) -- the future default is three ordinary field values
  (``contract.mode=public``, ``policy.base=strict_abi``,
  ``contract.unresolved=not_checkable``), not a hidden enum member.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType

# --------------------------------------------------------------------------
# D2: exactly three contract modes.
# --------------------------------------------------------------------------


class ContractMode(str, Enum):
    """Which evidence domain defines the declared consumer contract.

    Exactly three values (ADR-049 D2) -- resist adding a fourth: a
    persistent ``public_contract`` preset is explicitly rejected (D3), and
    collapsing ``EXPORTS`` into ``ALL`` is an explicitly rejected
    alternative (private, unreachable-type findings are not exports).
    """

    PUBLIC = "public"
    EXPORTS = "exports"
    ALL = "all"


def coerce_contract_mode(mode: ContractMode | str) -> ContractMode:
    """*mode* as a real :class:`ContractMode`, or ``ValueError`` naming the
    valid values.

    ``ContractMode`` is a ``str`` Enum, so an untyped caller passing the bare
    serialized value (``"all"`` from a config/API adapter) compares *equal*
    to a member but fails an ``is`` identity check -- coercing through the
    constructor first makes every later ``is`` comparison safe.

    One owner for the coercion *and* its error message, because there is more
    than one caller: the evaluator and ``checker.compare``'s own mode
    resolution both take an untyped value, and the bare
    ``EnumMeta.__call__`` message (``'bogus' is not a valid ContractMode``)
    tells a caller what is wrong without telling them what would be right.
    """
    try:
        return ContractMode(mode)
    except ValueError as exc:
        raise ValueError(
            f"mode must be one of {sorted(m.value for m in ContractMode)}, got {mode!r}"
        ) from exc


class LegacyScopeFlag(str, Enum):
    """The two pre-ADR-049 ``--[no-]scope-public-headers`` CLI flags."""

    NO_SCOPE_PUBLIC_HEADERS = "no_scope_public_headers"
    SCOPE_PUBLIC_HEADERS = "scope_public_headers"


LEGACY_SCOPE_FLAG_CONTRACT_MODE: MappingProxyType[LegacyScopeFlag, ContractMode] = (
    MappingProxyType(
        {
            # Exact alias: unscoped legacy behavior is precisely "all".
            LegacyScopeFlag.NO_SCOPE_PUBLIC_HEADERS: ContractMode.ALL,
            # Deliberately stricter migration alias, not an exact alias: the
            # legacy positive flag never enforced fail-closed provider
            # completeness, so mapping it onto the new `public` semantics is
            # intentionally stricter than what it used to mean (ADR-049 D2).
            LegacyScopeFlag.SCOPE_PUBLIC_HEADERS: ContractMode.PUBLIC,
        }
    )
)
"""``--no-scope-public-headers`` -> ``all`` is exact; ``--scope-public-headers``
-> ``public`` is an intentionally stricter migration alias, never ``exports``
(ADR-049 D2)."""


# --------------------------------------------------------------------------
# D1: contract relevance is an independent machine axis.
# --------------------------------------------------------------------------


class ContractRelevance(str, Enum):
    """Whether a normalized finding's entity belongs to the declared contract.

    Exactly five values (ADR-049 D1). ``PROVEN_OUT_OF_CONTRACT`` deliberately
    replaces the machine value ``PRIVATE`` -- the tool proves only that an
    entity is outside the *declared* contract, never that no unknown
    consumer reaches it via e.g. ``dlsym``.
    """

    IN_CONTRACT = "IN_CONTRACT"
    PROVEN_OUT_OF_CONTRACT = "PROVEN_OUT_OF_CONTRACT"
    UNKNOWN_UNPROVEN = "UNKNOWN_UNPROVEN"
    UNKNOWN_UNRESOLVED = "UNKNOWN_UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


NON_ENTITY_RELEVANCE: ContractRelevance = ContractRelevance.NOT_APPLICABLE
"""A finding that is not entity-surface scoped (SONAME, loader, architecture,
deployment, security state, ...) is ``NOT_APPLICABLE`` under every
:class:`ContractMode` (ADR-049 D2 mapping table, rightmost column) -- single
source of truth for that constant fact."""


class ContractAssurance(str, Enum):
    """Coverage of the evidence a contract-relevance decision rests on."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class AnalysisStatus(str, Enum):
    """Whether the run's contract-coverage evidence was sufficient to check."""

    CHECKABLE = "CHECKABLE"
    NOT_CHECKABLE = "NOT_CHECKABLE"


class CompatibilityEvaluationStatus(str, Enum):
    """Whether compatibility policy ran for a finding.

    Only ``IN_CONTRACT`` and ``NOT_APPLICABLE`` findings are ``EVALUATED``;
    the other three :class:`ContractRelevance` values are ``NOT_EVALUATED``
    and carry a JSON-``null`` compatibility decision, never a new verdict
    (ADR-049 D1).
    """

    EVALUATED = "EVALUATED"
    NOT_EVALUATED = "NOT_EVALUATED"


EVALUATED_RELEVANCE: frozenset[ContractRelevance] = frozenset(
    {ContractRelevance.IN_CONTRACT, ContractRelevance.NOT_APPLICABLE}
)
"""The :class:`ContractRelevance` values for which compatibility policy runs
(``compatibility_evaluation_status=EVALUATED``); every other value is
``NOT_EVALUATED`` (ADR-049 D1)."""


def evaluation_status_for(
    relevance: ContractRelevance,
) -> CompatibilityEvaluationStatus:
    """Return the :class:`CompatibilityEvaluationStatus` *relevance* implies.

    The mapping is ADR-049 D1's, and it is a function of the relevance value
    alone -- deriving it anywhere else (a report renderer, a gate) is how the
    two axes drift apart. :data:`EVALUATED_RELEVANCE` stays the membership
    set this reads; this is the one-line total function over it, so a caller
    never has to remember which direction the set is phrased in.
    """
    return (
        CompatibilityEvaluationStatus.EVALUATED
        if relevance in EVALUATED_RELEVANCE
        else CompatibilityEvaluationStatus.NOT_EVALUATED
    )


# --------------------------------------------------------------------------
# D5 / D6: provider evidence-search records (EvidenceSearchRecord).
# --------------------------------------------------------------------------


class EvidenceProviderStatus(str, Enum):
    """Outcome of one provider's evidence search for one domain/entity scope."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    STALE = "stale"


class EvidenceCompleteness(str, Enum):
    """How much of the requested scope a provider's search actually covered."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    NOT_STARTED = "not_started"


# --------------------------------------------------------------------------
# D7: field-level provenance selector layers.
# --------------------------------------------------------------------------


class SelectorLayer(str, Enum):
    """Where an effective configuration field's value was selected from.

    Precedence (ADR-049 D7, highest first): ``EXPLICIT_CLI``/``API_REQUEST``
    > ``LEGACY_ALIAS`` (for the field it aliases) > ``RUN_RECIPE`` >
    ``RUN_PROFILE`` (execution fields only) > ``PROJECT_CONFIG`` >
    ``BUILT_IN_DEFAULT``. This set is explicitly extensible -- a new adapter
    may need a new layer -- so it is not asserted to be exhaustive by tests,
    only that these seven required members exist.
    """

    EXPLICIT_CLI = "explicit_cli"
    API_REQUEST = "api_request"
    LEGACY_ALIAS = "legacy_alias"
    RUN_RECIPE = "run_recipe"
    RUN_PROFILE = "run_profile"
    PROJECT_CONFIG = "project_config"
    BUILT_IN_DEFAULT = "built_in_default"


# --------------------------------------------------------------------------
# Stable per-finding contract_reason registry (ADR-049 Phase 0).
# --------------------------------------------------------------------------
#
# Each code is a stable string a report/consumer can match on; the mapped
# text is human-readable prose, not a format string. New codes may be added;
# an existing code's *meaning* must not be repurposed, since external
# consumers may key behavior off the string.

CONTRACT_REASON_CODES: MappingProxyType[str, str] = MappingProxyType(
    {
        "public_root_membership": (
            "Entity is inside the selected public roots/closure."
        ),
        "export_root_membership": (
            "Entity is an exported function/variable root, or inside the "
            "ABI closure computed from it."
        ),
        "all_mode_normalized_entity": (
            "contract=all: every normalized entity finding is in scope, "
            "with no root/closure evidence required."
        ),
        "terminal_authoritative_exclusion": (
            "An exact declaration's contract semantics close membership for "
            "this entity, and no stronger/equal provider could override it."
        ),
        "closed_domain_no_commitment": (
            "The declared evidence domain was searched completely and no "
            "commitment or authoritative exclusion was found."
        ),
        "required_evidence_incomplete": (
            "Evidence required to close the declared domain is missing, "
            "failed, stale, partial, or contradictory."
        ),
        "identity_ambiguous": (
            "Identity coverage for the affected entity is incomplete, so "
            "root/closure membership cannot be decided."
        ),
        "non_entity_finding": (
            "The finding is not entity-surface scoped (SONAME, loader, "
            "architecture, deployment, or security state)."
        ),
        "legacy_alias_all": (
            "--no-scope-public-headers selected contract=all as its exact alias."
        ),
        "legacy_alias_public": (
            "--scope-public-headers selected contract=public as an "
            "intentionally stricter migration alias."
        ),
        "explicit_consumer_or_required_symbol_evidence": (
            "Section 4.3 item 1's strongest public evidence: the entity is "
            "an explicit required symbol/entrypoint contract, or was "
            "reached via a concrete consumer's actual import/relocation, "
            "not merely inferred from header-derived public roots."
        ),
    }
)


# --------------------------------------------------------------------------
# D6: snapshot/decision schema-version strategy.
# --------------------------------------------------------------------------
#
# Each counter below versions one independently-evolvable concern. A
# consumer that understands version N of a concern must not silently
# reinterpret data written under version N+1 (or any version it does not
# recognize) -- ADR-049 D6 requires unknown future versions to fail closed
# rather than guess. Bump the relevant counter whenever that concern's
# on-disk/wire shape changes in a way older readers cannot parse correctly;
# unrelated concerns keep their own counters unchanged.

CONTRACT_EVIDENCE_SCHEMA_VERSION: int = 1
"""Version of the persisted, policy-independent ``contract_evidence`` block
(observed provider records, declarations, manifests, raw type graph)."""

EVALUATION_CONTEXT_SCHEMA_VERSION: int = 3
"""Version of the persisted ``evaluation_context`` block (the resolved
``CompatibilityEvaluationConfig`` plus field-level provenance).

Bumped to 2 when ``GateConfig`` gained ``require_complete_analysis``/
``scope`` (dedup-and-convergence plan, Phase 2 item 1) and
``contract_context_io.py``'s ``gate`` dict grew the two matching keys: an
older reader's ``resolved_config_from_dict`` has no code path that
preserves those keys, so a context persisted under version 2 read by a
version-1 build would silently drop gate-affecting input rather than fail
closed the way ADR-049 D6 requires (Codex review, PR #817).

Bumped to 3 (Codex review, PR #1062, fresh evidence): CLI cleanup phase
two PR G2 deleted ``--exit-code-scheme``/``exit_code_scheme`` everywhere,
which removes ``field_provenance["gate.exit_code_scheme"]`` from every
context persisted from here on -- a v2 reader that expects that key
(present in every context persisted before this PR) cannot tell "removed
because the field no longer exists" apart from "removed because this
particular run stated nothing for it," which version 2 alone never
distinguished. A v3 reader knows the key is gone by construction; the scan
envelope's own, independently-versioned ``SCAN_SCHEMA_VERSION`` bump to
1.27 covers only the *scan* report's ``evidence_contract_error_message``
addition and says nothing about this block."""

DECISION_RECEIPT_SCHEMA_VERSION: int = 1
"""Version of the persisted ``decision_receipt`` block (evaluated roots,
evaluated closure, per-finding relevance).

Its own counter rather than a reuse of the evidence/context ones: the receipt
is the one block whose *keys* are a separate contract (the per-finding
identity a consumer correlates against a report's ``finding_id``), so its
shape can change without either observation or configuration changing --
exactly the "each counter versions one independently-evolvable concern" rule
above (CodeRabbit review: the block shipped with no version field at all,
while both siblings had one)."""

EVALUATOR_VERSION: int = 1
"""Version of the contract-relevance/compatibility evaluation algorithm
itself, independent of the schema versions above -- the same observed facts
can classify differently under a new evaluator."""

IDENTITY_ALGORITHM_VERSION: int = 2
"""Version of the canonical entity-identity/join algorithm (ADR-048) that
produced the persisted evidence and decision receipt.

Bumped to 2 when ``contract_evidence_collect._TypeIndex`` stopped registering
a qualified typedef's bare ``::`` tail as a spelling of that typedef: the
same ``AbiSnapshot`` now yields a *different* ``TypeGraphSnapshot`` -- a
signature spelling bare ``Alias`` no longer reaches a qualified
``ns::Alias -> Secret`` -- so a context persisted before the fix and one
persisted after it are not interchangeable evidence, even though the block's
*shape* (and therefore ``CONTRACT_EVIDENCE_SCHEMA_VERSION``) is unchanged.
That is exactly the split this counter exists for, and leaving it at 1 would
have left both formats advertising one algorithm while resolving typedefs
differently -- a consumer could neither tell them apart nor fail closed on
the newer semantics (Codex review).

The bump does not reject the older graphs: ADR-049 D6's rule is that a
version *older than or equal to* this build's is accepted (possibly
degraded) and only a *newer* one is refused, so a v1 context still replays
here while a v2 context is refused by a build that predates the fix -- the
fail-closed direction that matters."""
