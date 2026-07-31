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
   is *for*, measured rather than asserted.

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
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_fp_rate import CORPUS, Case, _category_of  # noqa: E402

from abicheck.checker import compare  # noqa: E402
from abicheck.checker_policy import (  # noqa: E402
    API_BREAK_KINDS,
    BREAKING_KINDS,
)
from abicheck.checker_types import Change, DiffResult  # noqa: E402
from abicheck.contract_context import finding_key  # noqa: E402
from abicheck.contract_evidence_collect import validate_decision_evidence  # noqa: E402
from abicheck.contract_relevance_types import (  # noqa: E402
    ContractMode,
    ContractRelevance,
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

PROVEN_LOSS_BASELINE = 0
UNEVIDENCED_DELTA_BASELINE = 0
FACT_LOSS_BASELINE = 0

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


def _is_breaking_kind(change: Change) -> bool:
    """Whether *change* is a break under the default policy's kind sets.

    Deliberately kind-level rather than
    ``DiffResult._effective_verdict_for_change``: the corpus runs the default
    policy with no overrides, so the two agree, and reading the registry sets
    keeps this script from depending on a private method.
    """
    return change.kind in BREAKING_KINDS or change.kind in API_BREAK_KINDS


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
    proven_losses: list[str] = field(default_factory=list)
    fp_reductions: list[str] = field(default_factory=list)
    unevidenced_deltas: list[str] = field(default_factory=list)
    fact_losses: list[str] = field(default_factory=list)

    @property
    def unresolved_rate(self) -> float:
        return self.unresolved / self.findings if self.findings else 0.0

    @property
    def deltas(self) -> int:
        """Findings the shadow evaluator would treat differently.

        A legacy-kept finding proven out of contract, or a legacy-demoted
        finding the shadow puts back in. An unresolved decision is not a
        delta -- it changes nothing and claims nothing.
        """
        return self.delta_matrix.get(LEGACY_KEPT, {}).get(
            ContractRelevance.PROVEN_OUT_OF_CONTRACT.value, 0
        ) + self.delta_matrix.get(LEGACY_OUT_OF_SURFACE, {}).get(
            ContractRelevance.IN_CONTRACT.value, 0
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
                and relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
                and not case.internal_noise
                and _is_breaking_kind(change)
            ):
                out.proven_losses.append(key)
            if (
                case.internal_noise
                and relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
            ):
                out.fp_reductions.append(key)
    return out


def _is_delta(legacy_state: str, relevance: ContractRelevance) -> bool:
    if legacy_state == LEGACY_KEPT:
        return relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
    return relevance is ContractRelevance.IN_CONTRACT


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
    for bucket_name in ("by_provider_state", "by_platform"):
        target_bucket = getattr(into, bucket_name)
        for key, row in getattr(other, bucket_name).items():
            existing = target_bucket.setdefault(key, [0, 0])
            existing[0] += row[0]
            existing[1] += row[1]
    into.proven_losses.extend(other.proven_losses)
    into.fp_reductions.extend(other.fp_reductions)
    into.unevidenced_deltas.extend(other.unevidenced_deltas)
    into.fact_losses.extend(other.fact_losses)


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
                "proven_public_break_losses": sorted(m.proven_losses),
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
            "unevidenced_deltas": sum(
                len(m.unevidenced_deltas) for m in measurements.values()
            ),
            "fact_losses": sum(len(m.fact_losses) for m in measurements.values()),
            "proven_loss_baseline": PROVEN_LOSS_BASELINE,
            "unevidenced_delta_baseline": UNEVIDENCED_DELTA_BASELINE,
            "fact_loss_baseline": FACT_LOSS_BASELINE,
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
        f"{gate['fact_losses']} unexplained fact loss(es) "
        "(all baselines 0)",
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
    ):
        count = gate[key]
        assert isinstance(count, int)
        if count > baseline:
            print(
                f"ERROR: {count} {label} (baseline {baseline})",
                file=sys.stderr,
            )
            failed = True
    if failed:
        return 1
    print("OK: shadow contract evaluator on baseline.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
