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

"""ADR-049 Phase 5's *unsuppressible* contract-coverage ledger.

The plan states the rule twice and from both directions. Section 6.1's
report shape carries a ``contract_coverage_failures`` array beside the
findings, and says of it:

    Keep proven-out-of-contract and unresolved ledgers separate.
    Provider/domain coverage failures are a **sibling canonical ledger, not
    synthetic change rows that ordinary suppression can erase.**

Section 6.2 then lists what an ordinary change suppression may not do, and
one entry is *"suppress a provider/domain coverage failure"*. Definition-of-
done item 6 names the pair: "Suppression is explicit and coverage failures
are unsuppressible."

Until now only half of that existed. `contract_evidence_collect` produces
one `EvidenceSearchRecord` per (provider, side) with its own status and
completeness -- the raw observation -- and `suppression.py` has a full audit
trail of what a run's rules silenced. What was missing is the thing in
between: a *derived* statement of which of those observations are coverage
**failures for the domain this run selected**, in a ledger that is
structurally not a change and so cannot be reached by a change suppression
at all.

**Why derivation, not a new observation.** A provider record is a fact about
what was searched; whether that fact is a *failure* depends on the selected
domain, which is a policy choice. Section 7 is explicit that the two domains
disagree here -- under `exports`, "public-header/manifest/consumer failures
are unrelated and advisory"; under `public`, "unrelated provider failures
are advisory" and "only failures needed to close this domain contribute
coverage 1". So the same record is a failure in one domain and advisory in
another, and recording it as a failure at collection time would bake one
domain's policy into a policy-independent block. :func:`coverage_failures`
takes the mode and answers for it.

**Why unsuppressibility is structural, not a flag.** A rule that says
"suppression must not touch this" and is enforced only by a check somewhere
is one refactor away from being false. The guarantee here is that a coverage
failure is not a :class:`~abicheck.checker_types.Change`: it has no
`ChangeKind`, no symbol, and never enters `DiffResult.changes`, so
`checker._filter_suppressed_changes` -- the single place suppression is
applied -- cannot see one. :func:`suppression_reaches_coverage_failures` is
the executable proof of that, and the report's own audit consumer:
it answers, for a real run's rules and this ledger, whether any rule
matched a failure. The answer must be empty, and a test asserts it stays so
for rules deliberately written to match one.

**Gating, as of ADR-049 Phase 7 -- but only through one narrow channel.**
The plan gives this ledger an independent exit `1` contribution ("The
sibling contract-coverage ledger may contribute exit `1`; it never rewrites
the null compatibility decision or the finding's zero gate contribution").
It was reported and inert through Phases 3-6, deliberately, so a consumer
could see the number before it bit; `contract_coverage_exit` now folds it
into the process exit status. This module stays the pure derivation and
gains no knowledge of exit codes, which is what keeps the number a report
states identical to the one that gated the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .contract_relevance_types import (
    ContractMode,
    EvidenceCompleteness,
    EvidenceProviderStatus,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .contract_evidence import ContractEvidenceBlock, EvidenceSearchRecord

#: Which provider a domain needs in order to *close* -- to be able to answer
#: `PROVEN_OUT_OF_CONTRACT` rather than only `UNKNOWN_UNRESOLVED`. Section 7:
#: `public`'s domain is "the selected declared-public providers/overlays" and
#: `exports`' is "only exported function/variable roots and closure computed
#: from the raw type graph". Anything not listed for a mode is advisory *in
#: that mode* -- not ignored, but not a coverage failure either.
#:
#: **The overlay providers are deliberately absent from every entry.**
#: `post_manifest` and `forced_public_symbols` are *additive* to a domain,
#: not constitutive of it: an overlay names extra entities to treat as in
#: contract, so a failed overlay leaves the domain's own roots exactly as
#: complete as they were. `public` still closes on `public_header` alone,
#: and the finding an overlay would have moved simply stays where the base
#: domain put it. Listing them here would report a coverage *failure* --
#: "this domain could not be closed" -- for a run whose domain closed fine
#: and merely applied one fewer overlay, which is a different fact and
#: belongs in the evidence ledger that already records it per provider.
#: A run that needs an overlay to have applied should assert on
#: `contract_evidence`, not on this ledger being empty.
REQUIRED_PROVIDERS: dict[ContractMode, frozenset[str]] = {
    ContractMode.PUBLIC: frozenset({"public_header"}),
    ContractMode.EXPORTS: frozenset({"export_table"}),
    # `all` requires no root or closure evidence at all ("Surface evidence is
    # unnecessary", Section 3's table), so no provider failure can prevent it
    # from closing -- which is exactly why it has no unresolved findings and
    # must have no coverage failures either.
    ContractMode.ALL: frozenset(),
}

#: A provider status that means the search did not deliver its evidence.
#: `AVAILABLE` is the only status that can close a domain; the other four
#: each describe a different way of not having the fact, and the ledger keeps
#: which one rather than collapsing them to a boolean -- "the whole purpose
#: of the ledger is to say why a domain was not closed", the same reason
#: `contract_evidence_collect` reports which guard failed.
_FAILED_STATUSES = frozenset(
    {
        EvidenceProviderStatus.UNAVAILABLE,
        EvidenceProviderStatus.FAILED,
        EvidenceProviderStatus.UNSUPPORTED,
        EvidenceProviderStatus.STALE,
    }
)


@dataclass(frozen=True)
class CoverageFailure:
    """One provider/domain coverage failure, for one side.

    Deliberately *not* a :class:`~abicheck.checker_types.Change`: it carries
    no `ChangeKind` and no symbol, so it can never enter `DiffResult.changes`
    and therefore can never be reached by `checker._filter_suppressed_changes`.
    That structural difference is the unsuppressibility guarantee; the
    ``suppressible`` field below merely *states* it for a report reader.
    """

    provider: str
    side: str
    #: The record this was derived from, so a reader can go back to the raw
    #: observation rather than trusting the derivation.
    record_id: str
    #: Why this counts as a failure: the provider's own status, or -- for an
    #: available-but-incomplete search -- which completeness facet was short.
    reason: str
    status: str
    completeness: str
    #: The mode this failure was derived for. The same record is a failure
    #: under one domain and advisory under another (Section 7), so a ledger
    #: entry that did not name its domain would be unattributable.
    mode: str
    #: Always False. Present in the serialized form because a consumer
    #: reading one entry in isolation should not have to know this module's
    #: rules to know the entry cannot be waived by a `--suppress` rule.
    suppressible: bool = field(default=False, init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "side": self.side,
            "record_id": self.record_id,
            "reason": self.reason,
            "status": self.status,
            "completeness": self.completeness,
            "mode": self.mode,
            "suppressible": self.suppressible,
        }


def _failure_reason(record: EvidenceSearchRecord) -> str | None:
    """Why *record* fails to close its domain, or ``None`` if it does close it.

    Two independent ways to fail, kept distinct because they need different
    fixes: the search never delivered (a status other than `AVAILABLE`), or
    it delivered but did not cover everything (`completeness` short of
    `COMPLETE`). An available-and-complete record is not a failure even if
    its sub-facets are `NOT_STARTED` -- `configuration_coverage` is
    `NOT_STARTED` on every record this build produces (no variant source
    exists), and treating that as a coverage failure would report every run
    as failing for a facet nothing can yet satisfy.
    """
    if record.status in _FAILED_STATUSES:
        return f"provider_{record.status.value}"
    if record.completeness is EvidenceCompleteness.PARTIAL:
        return "search_incomplete"
    if record.completeness is EvidenceCompleteness.NOT_STARTED:
        return "search_not_started"
    if record.identity_coverage is EvidenceCompleteness.PARTIAL:
        # A complete search whose *identity* coverage is partial cannot
        # support a proven exclusion for the ambiguous spellings, which is
        # exactly the closed-world requirement Section 4.2 states
        # separately from overall completeness.
        return "identity_coverage_incomplete"
    return None


def coverage_failures(
    evidence: ContractEvidenceBlock | None,
    mode: ContractMode | str,
) -> tuple[CoverageFailure, ...]:
    """The coverage failures *mode* actually has, from the persisted evidence.

    Only providers this domain needs in order to close are considered
    (:data:`REQUIRED_PROVIDERS`); a failure in any other provider is
    advisory in this mode, exactly as Section 7 says, and is deliberately
    absent rather than present-and-marked-advisory -- an absent entry is how
    this feature already encodes "not consulted", and a ledger whose entries
    are mostly non-failures would invite a reader to treat its length as the
    number.

    ``None`` evidence yields no failures: a run that computed no context has
    made no coverage claim to fail, which is not the same as a run whose
    providers failed.
    """
    if evidence is None:
        return ()
    resolved = ContractMode(mode) if not isinstance(mode, ContractMode) else mode
    required = REQUIRED_PROVIDERS.get(resolved, frozenset())
    if not required:
        return ()
    failures: list[CoverageFailure] = []
    for entry in evidence.providers:
        record = entry.record
        if record.provider not in required:
            continue
        reason = _failure_reason(record)
        if reason is None:
            continue
        failures.append(
            CoverageFailure(
                provider=record.provider,
                side=record.side,
                record_id=record.id,
                reason=reason,
                status=record.status.value,
                completeness=record.completeness.value,
                mode=resolved.value,
            )
        )
    # Sorted by the same (side, provider, id) identity `ContractEvidenceBlock`
    # canonicalizes its own providers by, so two runs that observed the same
    # thing produce the same ledger regardless of traversal order.
    return tuple(sorted(failures, key=lambda f: (f.side, f.provider, f.record_id)))


def coverage_failures_for_context(ctx: Any) -> tuple[CoverageFailure, ...]:
    """The ledger for a whole :class:`PersistedContractContext`.

    Both inputs the derivation needs are already in the context -- the
    observations in ``contract_evidence``, the selected domain in
    ``evaluation_context.resolved_config.contract.mode`` -- so the ledger is
    *derived at read time* rather than persisted alongside them. That is
    deliberate: a persisted copy is a second statement of the same fact that
    can disagree with the first after a re-evaluation under a different mode,
    which is precisely the operation
    :func:`~abicheck.contract_replay.reevaluate_from_evidence` exists to
    support. Derived, it simply answers for whatever mode is asked.
    """
    if ctx is None:
        return ()
    evidence = getattr(ctx, "contract_evidence", None)
    context = getattr(ctx, "evaluation_context", None)
    config = getattr(context, "resolved_config", None)
    contract = getattr(config, "contract", None)
    mode = getattr(contract, "mode", None)
    if evidence is None or mode is None:
        return ()
    return coverage_failures(evidence, mode)


def coverage_exit_contribution(failures: Sequence[CoverageFailure]) -> int:
    """What this ledger *would* contribute to the exit code (0 or 1).

    Section 6.1: "The sibling contract-coverage ledger may contribute exit
    `1`; it never rewrites the null compatibility decision or the finding's
    zero gate contribution." Derived here and reported; ADR-049 Phase 7
    *applies* it -- `contract_coverage_exit.fold_coverage_exit` folds it into
    the process exit status with ``max``. This function stays the pure
    derivation, so the number a report states and the number that gated the
    run cannot diverge.
    """
    return 1 if failures else 0


def suppression_reaches_coverage_failures(
    failures: Iterable[CoverageFailure],
    suppression: Any,
) -> tuple[str, ...]:
    """Which of *failures* a run's ``--suppress`` rules could reach. Always ``()``.

    The executable form of Section 6.2's "[ordinary change suppressions]
    cannot ... suppress a provider/domain coverage failure". This is not a
    filter that *enforces* the rule -- the rule is enforced structurally, by
    a coverage failure not being a `Change` and so never reaching
    `checker._filter_suppressed_changes`. This is a *witness* for it: it
    hands each failure to the real matcher and reports what matched.

    **Be honest about how strong that witness is.** Because a
    `CoverageFailure` exposes none of the attributes the matcher keys on, an
    empty answer follows from the type's shape, so a test asserting `== ()`
    would keep passing even if the structural guarantee were dismantled some
    *other* way -- by routing failures into `DiffResult.changes`, say, which
    happens nowhere near this call. What it does catch is the one mutation
    that would make a failure matchable in place: giving `CoverageFailure` a
    `kind`/`symbol` (or making it a `Change` subclass) so a wildcard rule
    starts matching. That is a narrow guarantee, not a general one; the
    general one is `contract_coverage_failures` never being built from
    `DiffResult.changes`, which the caller in `reporter.py` shows directly.

    Kept as a function rather than a comment because even the narrow
    guarantee spans two modules that are edited independently. A test calls
    it with rules deliberately written to match a failure's provider and
    side, and asserts the answer is still empty.
    """
    if suppression is None:
        return ()
    # `SuppressionList.is_suppressed` is the single predicate
    # `checker._filter_suppressed_changes` itself consults, so asking it is
    # asking the real matcher rather than a re-implementation that could
    # answer differently.
    matcher = getattr(suppression, "is_suppressed", None)
    if matcher is None:
        return ()
    reached: list[str] = []
    for failure in failures:
        # A coverage failure exposes none of the attributes the matcher
        # keys on (`kind`, `symbol`, `source_location`, ...), so this call
        # cannot succeed -- which is the point being demonstrated. Any
        # exception is that same demonstration, not an error to propagate.
        try:
            if matcher(failure):
                reached.append(f"{failure.side}:{failure.provider}")
        except (AttributeError, TypeError):
            continue
    return tuple(reached)
