#!/usr/bin/env python3
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

"""ADR-049 Phase 3's shadow-evaluator measurement and gate.

Phase 3's own work-breakdown entry ends with a four-line "Measure:" list and a
gate; this script is both. It runs the labelled corpus
``scripts/check_fp_rate.py`` already curates -- the same ``(old, new)``
snapshot pairs, with the same internal-noise / real-break ground truth --
through ``compare(..., contract_evaluation=True)`` in each contract domain,
and reports:

1. **delta by old/new decision** -- a matrix of (what the legacy gate did with
   a finding: kept vs. demoted out of surface) x (what the shadow evaluator
   decided about it). The off-diagonal cells are the deltas: findings the two
   would treat differently;
2. **unresolved rate by provider/domain** -- per contract domain and per
   provider-completeness state, the share of findings that could not be
   resolved. ("Platform", the third axis the plan names, is reported as the
   set of export tables observed: it is the only platform distinction the
   corpus's synthetic snapshots actually carry.);
3. **proven public-break losses** -- a real-break case where the shadow
   evaluator would drop a genuinely breaking, legacy-kept finding as
   ``PROVEN_OUT_OF_CONTRACT``. This is the number that must stay zero: it is
   the exact failure mode of switching the gate over in Phase 7;
4. **proven false-positive reductions** -- an internal-noise case where the
   shadow evaluator proves a finding out of contract. This is what the phase
   is *for*, measured rather than asserted;
5. **unresolved public-break losses** -- item 3's failure mode reached by the
   other door. ADR-049 D1 gives an ``UNKNOWN_*`` finding a null compatibility
   decision exactly as it does a proven-out one, so once Phase 7 made the
   decision authoritative both stop gating -- but only the proven half was
   measured, and the gap was not hypothetical: adding this metric found real
   breaks the gate had silently stopped blocking. Budgeted per domain
   (``UNRESOLVED_LOSS_BASELINE``) rather than pinned at zero, because in
   ``exports`` it reflects a corpus with no export tables to resolve against
   rather than a defect.

The gate ("every shadow delta has evidence and stable identity; zero
unexplained fact loss") is enforced as three baselines, all zero:

- ``PROVEN_LOSS_BASELINE`` -- a proven public-break loss (item 3);
- ``UNEVIDENCED_DELTA_BASELINE`` -- a delta whose decision cites no provider
  record, or cites one the persisted ``contract_evidence`` block does not
  contain (a dangling reference is not evidence);
- ``FACT_LOSS_BASELINE`` -- a finding present in the comparison but absent
  from the persisted decision receipt. That is the "zero unexplained fact
  loss" clause read literally: no finding may pass through the shadow
  evaluator without leaving a recorded decision.

Run locally with ``python scripts/measure_contract_shadow.py`` (add
``--markdown`` for a CI step summary, ``--json`` for the raw metrics).
``tests/test_contract_shadow_measurement.py`` mirrors the gate, so a
regression fails the ordinary unit lane too, not only a CI job that remembers
to run this file.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_fp_rate import CORPUS as FP_CORPUS, Case, _category_of  # noqa: E402
from contract_platform_corpus import (  # noqa: E402
    PLATFORM_CORPUS,
    UNCOVERED_LANES,
    lane_of,
)

from abicheck.checker import compare  # noqa: E402
from abicheck.checker_policy import (  # noqa: E402
    API_BREAK_KINDS,
    BREAKING_KINDS,
)
from abicheck.checker_types import Change, DiffResult  # noqa: E402
from abicheck.contract_context import finding_key, relevance_map  # noqa: E402
from abicheck.contract_evidence_collect import validate_decision_evidence  # noqa: E402
from abicheck.contract_relevance_types import (  # noqa: E402
    CompatibilityEvaluationStatus,
    ContractMode,
    ContractRelevance,
    evaluation_status_for,
)
from abicheck.export_surface import observed_exports_by_platform  # noqa: E402
from abicheck.finding_identity import report_finding_id as _finding_id  # noqa: E402

#: Every domain is measured, not only the one Phase 7 will default to: an
#: ``exports`` run on a corpus whose snapshots carry no export table is
#: exactly the "unresolved rate by domain" signal item 2 asks for, and
#: reporting it is what keeps the domain's known limits visible instead of
#: only measuring the flattering case.
MEASURED_MODES: tuple[ContractMode, ...] = (
    ContractMode.PUBLIC,
    ContractMode.EXPORTS,
    ContractMode.ALL,
)

#: Everything measured: the labelled FP-rate corpus (the ground truth for
#: losses and FP reductions) plus ADR-049 Phase 6's platform/shape corpus.
#:
#: The second was added because the first, by construction, carries no export
#: tables -- so the `exports` domain measured 100% unresolved on every case,
#: and "measured and accepted unresolved rate" was, for that domain, measuring
#: only the absence of evidence. `contract_platform_corpus` supplies the
#: ELF/PE/Mach-O, stripped, versioned, and C lanes Phase 6's Gate names, each
#: tagged so the unresolved rate can be reported *per lane* rather than as one
#: number whose composition is invisible.
CORPUS: list[Case] = [*FP_CORPUS, *PLATFORM_CORPUS]

PROVEN_LOSS_BASELINE = 0
UNEVIDENCED_DELTA_BASELINE = 0
FACT_LOSS_BASELINE = 0
#: Per-domain ceiling for :attr:`ModeMeasurement.unresolved_losses` -- a real
#: break the authoritative contract decision stops gating because it could not
#: *resolve* it. Per-domain rather than one number because the two domains'
#: numbers mean different things, and a single ceiling would have to be the
#: larger, hiding every regression in the stricter one:
#:
#: - ``public`` is the domain a future default flip targets, and every entry
#:   here is an evaluator gap rather than an evidence gap -- the header
#:   provenance needed to answer is present and the evaluator does not use it.
#:   This is the number to drive to zero.
#: - ``exports`` is an honest evidence limit, not a defect: two thirds of the
#:   corpus carries no export table at all, so the domain has nothing to
#:   resolve against. Those runs also raise the orthogonal coverage axis to
#:   ``1``, so the loss is *reported*, not silent -- which is exactly why this
#:   is a budget rather than a hard zero.
#: - ``all`` resolves everything (no header-origin demotion applies), so its
#:   zero is real and any drift is a regression.
#:
#: A budget, not an accepted loss: unlike `PROVEN_LOSS_BASELINE`, where the
#: evaluator makes a claim that turns out wrong, here it makes no claim and
#: the gate defaults open. Both directions are worth knowing about, so the
#: check reports a drop as well as a rise.
UNRESOLVED_LOSS_BASELINE: dict[str, int] = {
    "public": 3,
    "exports": 20,
    "all": 0,
}
#: The `public` entry's remaining cases, named so the budget cannot be
#: mistaken for "nothing left to do".
#:
#: `ambiguous_namespaced_leaf` is a real break on a type whose bare tail
#: collides with another type's. Confirming it needs per-identity
#: reachability the surface does not record -- an attempt to prove the
#: collision harmless from *header origin* instead was reverted for
#: confirming an unreachable sibling (see
#: `contract_evaluation._confirmed_type_matches`).
#:
#: The other two are the same gap as each other:
#: `public_stdlib_type_used_directly_layout_changed` and
#: `public_std_string_typedef_alias_layout_changed` are layout breaks on a
#: dependency type a public signature *names outright*, which is the very
#: evidence `diff_types._is_abi_surface_type` consults to keep the finding
#: rather than filter it as toolchain churn -- so the pipeline keeps the
#: finding on evidence the evaluator then declines to read, and the gate
#: loses them. Closing that needs
#: `type_reachability.directly_referenced_stdlib_types` reaching the
#: evaluator, which today receives surfaces and no snapshot; that is a
#: threaded parameter and its own change, not a drive-by here.
UNRESOLVED_LOSS_KNOWN_PUBLIC_CASES = (
    "ambiguous_namespaced_leaf",
    "public_std_string_typedef_alias_layout_changed",
    "public_stdlib_type_used_directly_layout_changed",
)
#: A replayed decision that out-claims the live one that wrote it. The
#: persisted evaluator may only ever *weaken*, so this baseline is 0 and is
#: the corpus-level counterpart of `test_contract_replay.py`'s hand-written
#: soundness cases -- the path every soundness defect in this feature has
#: been on.
REPLAY_STRENGTHENING_BASELINE = 0

_CONCLUSIVE = (
    ContractRelevance.IN_CONTRACT,
    ContractRelevance.PROVEN_OUT_OF_CONTRACT,
)
_UNRESOLVED = (
    ContractRelevance.UNKNOWN_UNRESOLVED,
    ContractRelevance.UNKNOWN_UNPROVEN,
)

LEGACY_KEPT = "kept"
LEGACY_OUT_OF_SURFACE = "out_of_surface"
#: The three remaining collections `checker._apply_contract_evaluation_shadow`
#: stamps and records in the receipt (Codex review, fresh evidence). Measuring
#: only `kept`/`out_of_surface` left the `fact_losses` gate blind to them: a
#: regression dropping a decision or a receipt entry for an audit-bucket
#: finding would still have reported zero.
#:
#: They get their own rows rather than being folded into `out_of_surface`, and
#: `_is_delta` deliberately answers `False` for them: a suppression, a display
#: dedup, and a build-context reconciliation are policy/presentation decisions
#: about an *already-decided* finding, not statements about contract
#: membership, so a shadow decision on one is never a delta against legacy
#: scoping the way a demoted-but-`IN_CONTRACT` finding is.
LEGACY_SUPPRESSED = "suppressed"
LEGACY_REDUNDANT = "redundant"
LEGACY_RECONCILED = "reconciled"


def _is_breaking_kind(change: Change) -> bool:
    """Whether *change* is a break under the default policy's kind sets.

    Deliberately kind-level rather than
    ``DiffResult._effective_verdict_for_change``: the corpus runs the default
    policy with no overrides, so the two agree, and reading the registry sets
    keeps this script from depending on a private method.
    """
    return change.kind in BREAKING_KINDS or change.kind in API_BREAK_KINDS


def _withheld_from_gate(relevance: ContractRelevance) -> bool:
    """Whether an authoritative contract decision stops *relevance* gating.

    Read off :func:`~abicheck.contract_relevance_types.evaluation_status_for`
    -- the same total function the engine's own two enforcement points derive
    the status from -- rather than re-listing the withheld relevance values
    here. A second spelling of that membership is exactly how a metric ends
    up measuring a rule the gate no longer applies: this file's whole purpose
    is to catch gate losses, so it must not be able to disagree with the gate
    about what a loss *is*.
    """
    return (
        evaluation_status_for(relevance) is CompatibilityEvaluationStatus.NOT_EVALUATED
    )


@dataclass
class ModeMeasurement:
    """One contract domain's measurement over the whole corpus."""

    mode: str
    findings: int = 0
    #: ``{legacy_state: {relevance: count}}`` -- item 1's matrix.
    delta_matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    unresolved: int = 0
    #: ``{provider_completeness_state: [resolved, unresolved]}`` -- item 2.
    by_provider_state: dict[str, list[int]] = field(default_factory=dict)
    #: ``{observed_export_tables: [resolved, unresolved]}`` -- item 2's
    #: platform axis, keyed by the tables the pair actually carried.
    by_platform: dict[str, list[int]] = field(default_factory=dict)
    #: ``{lane: [resolved, unresolved]}`` -- ADR-049 Phase 6's own axis, the
    #: Gate's corpus list (`elf`/`pe`/`macho`/`stripped`/`versioned`/`c`, plus
    #: `fp_corpus` for the labelled scoping corpus). Distinct from
    #: `by_platform`, which keys on the export tables a pair happened to
    #: carry: two lanes can share a platform key (an ELF break and a stripped
    #: ELF pair are both `elf`) while testing different evidence shapes, and
    #: it is the *shape* Phase 6 asks to have covered.
    by_lane: dict[str, list[int]] = field(default_factory=dict)
    proven_losses: list[str] = field(default_factory=list)
    #: The same failure mode as :attr:`proven_losses` reached by the other
    #: door: a genuinely breaking, legacy-kept finding the contract decision
    #: withholds from the gate because it could not *resolve* it, rather than
    #: because it proved it out. ADR-049 D1 gives an ``UNKNOWN_*`` finding a
    #: null compatibility decision exactly as it does a proven-out one, so
    #: once the decision is authoritative both stop gating -- but only the
    #: proven half had a metric, which is why this one exists. Reported and
    #: gated separately rather than summed into ``proven_losses``: a proven
    #: loss is the evaluator making a *claim* that turns out wrong, an
    #: unresolved loss is it making *no claim* and the gate defaulting open,
    #: and the two need different fixes (a wrong rule vs. missing evidence).
    unresolved_losses: list[str] = field(default_factory=list)
    fp_reductions: list[str] = field(default_factory=list)
    unevidenced_deltas: list[str] = field(default_factory=list)
    fact_losses: list[str] = field(default_factory=list)
    #: Findings whose *replay* out-claims the live decision that wrote it --
    #: `compare_decisions`'s `strengthened`/`disagreed` buckets, measured
    #: over the whole corpus. Every soundness defect this feature has had
    #: lived on that path, and until this gate existed the only signal for
    #: it was hand-written unit tests (self-review).
    replay_strengthenings: list[str] = field(default_factory=list)

    @property
    def unresolved_rate(self) -> float:
        return self.unresolved / self.findings if self.findings else 0.0

    @property
    def deltas(self) -> int:
        """Findings the shadow evaluator would treat differently.

        A legacy-kept finding proven out of contract, or a legacy-demoted
        finding the shadow puts back in. An unresolved decision is not a
        delta: it claims nothing, so there is no second opinion here to
        differ from the legacy one.

        It is emphatically not *harmless*, though, which is what changed
        when the decision became authoritative: an unresolved finding is
        withheld from the gate exactly as a proven-out one is. That effect
        is counted by :attr:`unresolved_losses` rather than here, because it
        is a property of the gate, not a disagreement between two opinions.
        """
        return self.delta_matrix.get(LEGACY_KEPT, {}).get(
            ContractRelevance.PROVEN_OUT_OF_CONTRACT.value, 0
        ) + self.delta_matrix.get(LEGACY_OUT_OF_SURFACE, {}).get(
            ContractRelevance.IN_CONTRACT.value, 0
        )


#: Every ``ModeMeasurement`` field that accumulates a list of finding keys,
#: derived from the dataclass so :func:`_merge` cannot fall behind it.
_LIST_ACCUMULATORS: tuple[str, ...] = tuple(
    f.name
    for f in fields(ModeMeasurement)
    if f.default_factory is list  # type: ignore[misc]
)


def _platform_key(old_tables: object, new_tables: object) -> str:
    names = sorted(set(old_tables or ()) | set(new_tables or ()))
    return "+".join(names) if names else "none"


def _provider_state(result: DiffResult) -> str:
    """A one-word summary of the run's own provider completeness.

    Taken from the persisted ledger rather than recomputed, so the axis this
    groups by is the same fact a report reader sees.
    """
    ctx = result.contract_context
    if ctx is None:
        return "no_context"
    states = {
        f"{e.record.provider}={e.record.completeness.value}"
        for e in ctx.contract_evidence.providers  # type: ignore[attr-defined]
    }
    return ",".join(sorted(states)) or "no_providers"


def _tally(bucket: dict[str, list[int]], key: str, resolved: bool) -> None:
    row = bucket.setdefault(key, [0, 0])
    row[0 if resolved else 1] += 1


def measure_case(case: Case, mode: ContractMode) -> ModeMeasurement:
    """Measure one corpus case in one domain."""
    out = ModeMeasurement(mode=mode.value)
    old, new = case.build()
    result = compare(
        old,
        new,
        scope_to_public_surface=True,
        contract_evaluation=True,
        contract_mode=mode.value,
    )
    ctx = result.contract_context
    receipt_keys = (
        set(ctx.decision_receipt.relevance_by_finding)  # type: ignore[attr-defined]
        if ctx is not None
        else set()
    )
    evidence_block = (
        ctx.contract_evidence if ctx is not None else None  # type: ignore[attr-defined]
    )
    platform = _platform_key(
        observed_exports_by_platform(old), observed_exports_by_platform(new)
    )
    provider_state = _provider_state(result)

    for legacy_state, changes in (
        (LEGACY_KEPT, result.changes),
        (LEGACY_OUT_OF_SURFACE, result.out_of_surface_changes),
        (LEGACY_SUPPRESSED, result.suppressed_changes),
        (LEGACY_REDUNDANT, result.redundant_changes),
        (LEGACY_RECONCILED, result.reconciled_changes),
    ):
        for change in changes:
            relevance = change.contract_relevance
            key = f"{case.name}:{mode.value}:{change.kind.value}:{change.symbol or ''}"
            if relevance is None:
                out.fact_losses.append(key)
                continue
            out.findings += 1
            row = out.delta_matrix.setdefault(legacy_state, {})
            row[relevance.value] = row.get(relevance.value, 0) + 1
            resolved = relevance not in _UNRESOLVED
            if not resolved:
                out.unresolved += 1
            _tally(out.by_provider_state, provider_state, resolved)
            _tally(out.by_platform, platform, resolved)
            _tally(out.by_lane, lane_of(case.name), resolved)
            # Keyed exactly as `checker` writes the receipt -- through the
            # report's own `finding_id`, not a second spelling of the
            # convention (a mismatch here would report every finding as lost).
            if finding_key(change, _finding_id) not in receipt_keys:
                out.fact_losses.append(key)
            if relevance in _CONCLUSIVE and _is_delta(legacy_state, relevance):
                refs = change.contract_evidence_refs or ()
                if not refs or not _refs_resolve(refs, evidence_block):
                    out.unevidenced_deltas.append(key)
            if (
                legacy_state == LEGACY_KEPT
                and not case.internal_noise
                and _is_breaking_kind(change)
                and _withheld_from_gate(relevance)
            ):
                # Which of the two loss metrics this is depends only on
                # whether the evaluator made a claim. `_withheld_from_gate`
                # already answered the shared question -- whether the gate
                # still sees this finding -- so neither branch restates it.
                if relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT:
                    out.proven_losses.append(key)
                else:
                    out.unresolved_losses.append(key)
            if (
                case.internal_noise
                and relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
            ):
                out.fp_reductions.append(key)
    out.replay_strengthenings.extend(_replay_strengthenings(case, mode, result, ctx))
    return out


def _replay_strengthenings(
    case: Case, mode: ContractMode, result: object, ctx: object
) -> list[str]:
    """Findings a re-evaluation decides more strongly than the live run did.

    The persisted evaluator is deliberately narrower than the live one, so a
    *weakening* is an expected coverage limit; a strengthening -- or a flip
    between the two equally-strong-but-opposite conclusions -- means the
    replay out-claimed the evidence that produced it, which is the one
    direction :func:`~abicheck.contract_replay.compare_decisions` treats as
    a defect.

    Run through the real wire format (dict round-trip), because that is what
    a consumer replays from: an object that only survives in-process proves
    nothing about a persisted report.
    """
    if ctx is None:
        return []
    from abicheck.contract_context_io import (
        persisted_context_from_dict,
        persisted_context_to_dict,
    )
    from abicheck.contract_replay import compare_decisions, reevaluate_from_evidence

    changes = [
        *result.changes,  # type: ignore[attr-defined]
        *result.out_of_surface_changes,  # type: ignore[attr-defined]
    ]
    if not changes:
        return []
    reloaded = persisted_context_from_dict(persisted_context_to_dict(ctx))  # type: ignore[arg-type]
    replayed = reevaluate_from_evidence(
        reloaded, changes, mode=mode, finding_id=_finding_id
    )
    comparison = compare_decisions(relevance_map(changes, _finding_id), replayed)
    return [
        f"{case.name}:{mode.value}:{key}"
        for key in (*comparison.strengthened, *comparison.disagreed)
    ]


def _is_delta(legacy_state: str, relevance: ContractRelevance) -> bool:
    if legacy_state == LEGACY_KEPT:
        return relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
    if legacy_state == LEGACY_OUT_OF_SURFACE:
        return relevance is ContractRelevance.IN_CONTRACT
    # An audit-bucket finding was moved there by policy or presentation, not
    # by contract scoping -- there is no legacy contract claim for a shadow
    # decision to differ from. Measured (so a lost decision still fails the
    # `fact_losses` gate), never counted as a delta.
    return False


def _refs_resolve(refs: Sequence[str], block: object) -> bool:
    try:
        validate_decision_evidence(refs, block)  # type: ignore[arg-type]
    except ValueError:
        return False
    return True


def _merge(into: ModeMeasurement, other: ModeMeasurement) -> None:
    into.findings += other.findings
    for state, row in other.delta_matrix.items():
        target = into.delta_matrix.setdefault(state, {})
        for relevance, count in row.items():
            target[relevance] = target.get(relevance, 0) + count
    into.unresolved += other.unresolved
    for bucket_name in ("by_provider_state", "by_platform", "by_lane"):
        target_bucket = getattr(into, bucket_name)
        for key, row in getattr(other, bucket_name).items():
            existing = target_bucket.setdefault(key, [0, 0])
            existing[0] += row[0]
            existing[1] += row[1]
    # Every list accumulator, found from the dataclass rather than named one
    # by one. The hand-written version silently dropped `unresolved_losses`
    # when it was added: `measure_case` classified it correctly and `measure`
    # still reported zero, which for a gate metric is the worst possible
    # failure -- it reads exactly like "no losses". Deriving the set from the
    # fields means a future accumulator is merged the moment it is declared.
    for name in _LIST_ACCUMULATORS:
        getattr(into, name).extend(getattr(other, name))


def measure(
    corpus: Iterable[Case] = CORPUS, modes: Iterable[ContractMode] = MEASURED_MODES
) -> dict[str, ModeMeasurement]:
    """Measure the whole corpus in every domain, keyed by mode value."""
    out: dict[str, ModeMeasurement] = {}
    for mode in modes:
        total = ModeMeasurement(mode=mode.value)
        for case in corpus:
            _merge(total, measure_case(case, mode))
        out[mode.value] = total
    return out


def metrics(
    measurements: dict[str, ModeMeasurement] | None = None,
) -> dict[str, object]:
    """The four measured quantities plus the three gate counters, as data."""
    measurements = measurements or measure()
    return {
        "cases": len(CORPUS),
        # Bounded honestly: the coverage claim is what was actually run, not
        # what Phase 6's Gate list mentions. A lane this measurement cannot
        # reach is named with its reason rather than silently absent.
        "uncovered_lanes": dict(sorted(UNCOVERED_LANES.items())),
        "modes": {
            mode: {
                "findings": m.findings,
                "delta_matrix": {
                    state: dict(sorted(row.items()))
                    for state, row in sorted(m.delta_matrix.items())
                },
                "deltas": m.deltas,
                "unresolved": m.unresolved,
                "unresolved_rate": round(m.unresolved_rate, 4),
                "unresolved_by_provider_state": {
                    key: {"resolved": row[0], "unresolved": row[1]}
                    for key, row in sorted(m.by_provider_state.items())
                },
                "unresolved_by_platform": {
                    key: {"resolved": row[0], "unresolved": row[1]}
                    for key, row in sorted(m.by_platform.items())
                },
                "unresolved_by_lane": {
                    key: {"resolved": row[0], "unresolved": row[1]}
                    for key, row in sorted(m.by_lane.items())
                },
                "proven_public_break_losses": sorted(m.proven_losses),
                "unresolved_public_break_losses": sorted(m.unresolved_losses),
                "proven_false_positive_reductions": sorted(m.fp_reductions),
                "unevidenced_deltas": sorted(m.unevidenced_deltas),
                "fact_losses": sorted(m.fact_losses),
            }
            for mode, m in sorted(measurements.items())
        },
        "gate": {
            "proven_public_break_losses": sum(
                len(m.proven_losses) for m in measurements.values()
            ),
            "unresolved_public_break_losses": {
                mode: len(m.unresolved_losses)
                for mode, m in sorted(measurements.items())
            },
            # The *identities*, not only the count. A budget alone cannot
            # tell "the accepted gaps are still the accepted gaps" from "one
            # was fixed and a different case regressed to UNKNOWN_*" -- the
            # total is 2 either way, so a count-only gate would swap an
            # accepted gap for a fresh false negative silently (Codex
            # review).
            "unresolved_public_break_cases": {
                mode: sorted({key.split(":", 1)[0] for key in m.unresolved_losses})
                for mode, m in sorted(measurements.items())
            },
            "unevidenced_deltas": sum(
                len(m.unevidenced_deltas) for m in measurements.values()
            ),
            "fact_losses": sum(len(m.fact_losses) for m in measurements.values()),
            "replay_strengthenings": sum(
                len(m.replay_strengthenings) for m in measurements.values()
            ),
            "unresolved_loss_baseline": dict(sorted(UNRESOLVED_LOSS_BASELINE.items())),
            "proven_loss_baseline": PROVEN_LOSS_BASELINE,
            "unevidenced_delta_baseline": UNEVIDENCED_DELTA_BASELINE,
            "fact_loss_baseline": FACT_LOSS_BASELINE,
            "replay_strengthening_baseline": REPLAY_STRENGTHENING_BASELINE,
        },
    }


def fp_reduction_by_axis(
    measurements: dict[str, ModeMeasurement] | None = None,
) -> dict[str, int]:
    """Item 4 broken down by the FP-rate corpus's own scoping axis.

    Reuses ``check_fp_rate.CASE_CATEGORY`` rather than inventing a second
    taxonomy, so a reader can line this table up directly against the FP-rate
    gate's own per-axis breakdown.
    """
    measurements = measurements or measure()
    out: dict[str, int] = {}
    for m in measurements.values():
        for key in m.fp_reductions:
            axis = _category_of(key.split(":", 1)[0])
            out[axis] = out.get(axis, 0) + 1
    return dict(sorted(out.items()))


def render_markdown(m: dict[str, object]) -> str:
    modes = m["modes"]
    gate = m["gate"]
    assert isinstance(modes, dict) and isinstance(gate, dict)
    lines = [
        f"### ADR-049 shadow contract evaluator — {m['cases']} corpus cases",
        "",
        f"Gate: {gate['proven_public_break_losses']} proven public-break loss(es), "
        f"{gate['unevidenced_deltas']} unevidenced delta(s), "
        f"{gate['fact_losses']} unexplained fact loss(es), "
        f"{gate['replay_strengthenings']} replay strengthening(s) "
        "(these four baselines are 0; the unresolved-loss budgets below are "
        "per-domain and not all zero)",
        "",
        "Unresolved public-break losses (a real break withheld from the gate "
        "because the decision could not resolve it), per domain against its "
        "own budget: "
        + ", ".join(
            f"`{domain}` {count}/{UNRESOLVED_LOSS_BASELINE.get(domain, 0)}"
            for domain, count in sorted(
                gate["unresolved_public_break_losses"].items()  # type: ignore[union-attr]
            )
        ),
        "",
        "| Domain | Findings | Deltas | Unresolved | Unresolved rate | FP reductions |",
        "|--------|---------:|-------:|-----------:|----------------:|--------------:|",
    ]
    for mode, row in modes.items():
        lines.append(
            f"| {mode} | {row['findings']} | {row['deltas']} | {row['unresolved']} | "
            f"{row['unresolved_rate']:.2%} | "
            f"{len(row['proven_false_positive_reductions'])} |"
        )

    # ADR-049 Phase 6's own axis. The aggregate unresolved rate above says
    # *how much* is unresolved; only this says *where*, which is what makes
    # the number acceptable-or-not rather than merely reported: a domain
    # unresolved exactly on the lanes carrying no evidence for it is working
    # as designed, and one unresolved on a lane that does carry evidence is
    # a defect the aggregate would hide.
    lanes = sorted({lane for row in modes.values() for lane in row["unresolved_by_lane"]})
    if lanes:
        lines += [
            "",
            "Unresolved rate by lane (Phase 6's corpus axis):",
            "",
            "| Domain | " + " | ".join(lanes) + " |",
            "|--------|" + "|".join("---:" for _ in lanes) + "|",
        ]
        for mode, row in modes.items():
            cells = []
            for lane in lanes:
                counts = row["unresolved_by_lane"].get(lane)
                if not counts:
                    cells.append("—")
                    continue
                total = counts["resolved"] + counts["unresolved"]
                cells.append(f"{counts['unresolved']}/{total}")
            lines.append(f"| {mode} | " + " | ".join(cells) + " |")

    uncovered = m.get("uncovered_lanes") or {}
    if uncovered:
        lines += ["", "Lanes not covered by this measurement:", ""]
        lines += [f"- **{lane}** — {why}" for lane, why in uncovered.items()]  # type: ignore[union-attr]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="ADR-049 Phase 3 shadow-evaluator measurement and gate."
    )
    parser.add_argument("--json", metavar="PATH", help="write raw metrics JSON here")
    parser.add_argument(
        "--markdown", action="store_true", help="print a Markdown summary table"
    )
    args = parser.parse_args(argv)

    m = metrics()
    gate = m["gate"]
    assert isinstance(gate, dict)
    if args.json:
        Path(args.json).write_text(json.dumps(m, indent=2), encoding="utf-8")
    if args.markdown:
        print(render_markdown(m))
    else:
        print(json.dumps(m, indent=2))

    failed = False
    for label, key, baseline in (
        (
            "proven public-break losses",
            "proven_public_break_losses",
            PROVEN_LOSS_BASELINE,
        ),
        ("unevidenced deltas", "unevidenced_deltas", UNEVIDENCED_DELTA_BASELINE),
        ("unexplained fact losses", "fact_losses", FACT_LOSS_BASELINE),
        (
            "replay strengthenings",
            "replay_strengthenings",
            REPLAY_STRENGTHENING_BASELINE,
        ),
    ):
        count = gate[key]
        assert isinstance(count, int)
        if count > baseline:
            print(
                f"ERROR: {count} {label} (baseline {baseline})",
                file=sys.stderr,
            )
            failed = True
    # Per-domain, and reported in both directions. A rise is a regression;
    # a drop means an evaluator fix landed and the budget it was measured
    # against is now stale -- leaving that unsaid is how a ceiling silently
    # stops being a ceiling.
    # `public` is additionally pinned by identity: it is the domain a future
    # default flip targets and the one whose budget is an evaluator gap
    # rather than an evidence limit, so a *new* case appearing there is a
    # regression even when an old one left at the same time. The other
    # domains stay count-only -- `exports`' 20 tracks which corpus pairs
    # happen to carry export tables, which is not a claim worth pinning.
    cases = gate["unresolved_public_break_cases"]
    assert isinstance(cases, dict)
    unexpected = sorted(set(cases.get("public", ())) - set(UNRESOLVED_LOSS_KNOWN_PUBLIC_CASES))
    if unexpected:
        print(
            "ERROR: unresolved public-break loss in case(s) not on the known "
            f"list: {', '.join(unexpected)} (known: "
            f"{', '.join(UNRESOLVED_LOSS_KNOWN_PUBLIC_CASES)})",
            file=sys.stderr,
        )
        failed = True
    fixed = sorted(set(UNRESOLVED_LOSS_KNOWN_PUBLIC_CASES) - set(cases.get("public", ())))
    if fixed:
        print(
            f"NOTE: known unresolved public-break case(s) no longer losing: "
            f"{', '.join(fixed)} -- drop them from the known list.",
            file=sys.stderr,
        )

    unresolved = gate["unresolved_public_break_losses"]
    assert isinstance(unresolved, dict)
    for domain, count in sorted(unresolved.items()):
        budget = UNRESOLVED_LOSS_BASELINE.get(domain, 0)
        if count > budget:
            print(
                f"ERROR: {count} unresolved public-break loss(es) in "
                f"contract={domain} (baseline {budget})",
                file=sys.stderr,
            )
            failed = True
        elif count < budget:
            print(
                f"NOTE: only {count} unresolved public-break loss(es) in "
                f"contract={domain} (baseline {budget}) -- lower the baseline.",
                file=sys.stderr,
            )
    if failed:
        return 1
    print("OK: shadow contract evaluator on baseline.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
