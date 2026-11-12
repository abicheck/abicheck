#!/usr/bin/env python3
"""E8 (implementation side): check ScopeCompleteness.tla's invariants on the
real ADR-065 completeness code.

For every combination of up to N release members (default 3), and every
evidence/policy setting, this drives the real functions a directory/package
``compare`` uses:

    workflows.release_scope.release_inventory_evidence
    workflows.release_scope.build_release_scope_record   (acquisition states)
    policy.scope_completeness.resolve_scope_decision      (D6/D7 under a policy)
    policy.release_exit_decision.resolve_release_exit_decision_with_assurance

and checks I1-I3 (``check_scope.invariant_failures``) on the real member
states and the real exit code. The release glue that is not a pure function
is transcribed from ``workflows/release_compare.py`` and is marked "glue:" below:
degraded matched members get a ``"failed"`` library result (line ~413), and
``--dso-only`` unclassified members get an ``ERROR`` result (line ~465).

Each member is one of the scenario kinds in ``KINDS``. ``new-only`` members
(additions) are not enumerated: the spec models the removal direction only.
``UNSUPPORTED`` is mapped to the spec's "other unchecked" label, which the
invariants do not distinguish.

    python3 formal/experiments/scope_completeness_audit.py --max-members 3
"""

from __future__ import annotations

import argparse
import itertools
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

FORMAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORMAL / "tla"))

import check_scope  # noqa: E402

from abicheck.model.scope_acquisition import (  # noqa: E402
    AcquisitionState,
    InventoryCompleteness,
)
from abicheck.policy.exit_decision import ExitReason  # noqa: E402
from abicheck.policy.release_exit_decision import (  # noqa: E402
    resolve_release_exit_decision_with_assurance,
)
from abicheck.policy.release_outcome import worse_release_verdict  # noqa: E402
from abicheck.policy.scope_completeness import resolve_scope_decision  # noqa: E402
from abicheck.workflows.release_scope import (  # noqa: E402
    build_release_scope_record,
    release_inventory_evidence,
)

#: Scenario kinds for one member, as the release fan-out can produce them.
KINDS: tuple[str, ...] = (
    "ok",  # matched, compared, NO_CHANGE
    "break",  # matched, compared, BREAKING
    "error",  # matched, compare crashed: verdict ERROR
    "unsupported",  # matched, artifact this build cannot analyze
    "degraded_matched",  # glue: matched, stored package marks it degraded -> "failed"
    "no_entry",  # matched, but no library result was recorded at all
    "degraded_old",  # OLD-only, degraded capture (old_unmatched) -> FAILED
    "unclassified_old",  # OLD-only, --dso-only could not classify -> FAILED + ERROR
    "unclassified_new",  # OLD-present, NEW side could not classify -> FAILED + ERROR
    "absent_old",  # OLD-only, nothing else: the removal candidate
    "unproduced_old",  # OLD declares it, extraction did not produce it (S3)
)
MATCHED = frozenset(
    {"ok", "break", "error", "unsupported", "degraded_matched", "no_entry"}
)

_LABEL = {
    AcquisitionState.OUT_OF_SCOPE: "unselected",
    AcquisitionState.EXPECTED_NOT_PRODUCED: "expected_not_produced",
    AcquisitionState.FAILED: "failed",
    AcquisitionState.NOT_SUPPLIED: "not_supplied",
    AcquisitionState.AVAILABLE: "compared",
    AcquisitionState.UNSUPPORTED: "other_unchecked",
    AcquisitionState.AMBIGUOUS: "other_unchecked",
    AcquisitionState.DECLARED_ABSENT: "other_unchecked",
}


@dataclass(frozen=True)
class Setting:
    old_proven: bool
    new_proven: bool
    new_single_artifact: bool
    policy: str
    fail_on_removed: bool


@dataclass
class Result:
    checks: Counter = field(default_factory=Counter)
    violations: list[tuple[str, tuple[str, ...], Setting, int, list[str]]] = field(
        default_factory=list
    )
    zero_comparison_runs: int = 0
    scenarios: int = 0


def _realise(combo: tuple[str, ...]):  # type: ignore[no-untyped-def]
    """The release fan-out's inputs for one member combination."""
    old_map: dict[str, Path] = {}
    new_map: dict[str, Path] = {}
    matched: list[str] = []
    results: list[dict[str, object]] = []
    old_failed: dict[str, str] = {}
    new_failed: dict[str, str] = {}
    old_unproduced: dict[str, str] = {}
    old_unc: dict[str, str] = {}
    new_unc: dict[str, str] = {}
    unclassified_errors: list[tuple[str, str]] = []
    for i, kind in enumerate(combo):
        key = f"lib{i}"
        name = f"{key}.so"
        if kind in MATCHED:
            old_map[key] = Path("/old") / name
            new_map[key] = Path("/new") / name
            matched.append(key)
        if kind == "ok":
            results.append({"library": name, "verdict": "NO_CHANGE"})
        elif kind == "break":
            results.append({"library": name, "verdict": "BREAKING"})
        elif kind == "error":
            results.append({"library": name, "verdict": "ERROR", "error": "crash"})
        elif kind == "unsupported":
            results.append({"library": name, "verdict": "unsupported", "reason": "r"})
        elif kind == "degraded_matched":  # glue: release_compare.py
            results.append({"library": name, "verdict": "failed", "reason": "degraded"})
        elif kind == "degraded_old":
            old_map[key] = Path("/old") / name
            old_failed[key] = "degraded"
        elif kind == "unclassified_old":
            old_unc[key] = "could not classify"
            unclassified_errors.append((name, "could not classify"))
        elif kind == "unclassified_new":  # OLD present, NEW could not classify
            old_map[key] = Path("/old") / name
            new_unc[key] = "could not classify"
            unclassified_errors.append((name, "could not classify"))
        elif kind == "absent_old":
            old_map[key] = Path("/old") / name
        elif kind == "unproduced_old":
            old_unproduced[key] = "declared, not produced"
    # --dso-only unclassified members are failed on their side (glue:
    # release_compare passes them into old_failed/new_failed).
    old_failed.update(old_unc)
    new_failed.update(new_unc)
    return (
        old_map,
        new_map,
        matched,
        results,
        old_failed,
        new_failed,
        old_unproduced,
        old_unc,
        new_unc,
        unclassified_errors,
    )


def _run(combo: tuple[str, ...], s: Setting):  # type: ignore[no-untyped-def]
    (
        old_map,
        new_map,
        matched,
        results,
        old_failed,
        new_failed,
        old_unproduced,
        old_unc,
        new_unc,
        unclassified_errors,
    ) = _realise(combo)
    evidence = release_inventory_evidence(
        old_stored=s.old_proven,
        new_stored=s.new_proven,
        old_complete=s.old_proven,
        new_complete=s.new_proven,
        new_single_artifact=s.new_single_artifact,
        old_unclassified=old_unc,
        new_unclassified=new_unc,
    )
    record = build_release_scope_record(
        old_map,
        new_map,
        matched,
        results,
        evidence,
        old_failed=old_failed,
        new_failed=new_failed,
        old_unproduced=old_unproduced,
    )
    # glue: release_compare appends an ERROR result for each unclassified
    # member unless D9 narrowed it out of scope, and folds it into the
    # release worst verdict.
    states = {m.member: m.state for m in record.members}
    worst = "NO_CHANGE"
    for entry in results:
        worst = worse_release_verdict(worst, str(entry["verdict"]))
    for name, reason in unclassified_errors:
        key = next(k for k in states if f"{k}.so" == name)
        if states.get(key) is AcquisitionState.OUT_OF_SCOPE:
            continue
        results.append({"library": name, "verdict": "ERROR", "error": reason})
        worst = worse_release_verdict(worst, "ERROR")
    decision = resolve_scope_decision(record, s.policy)
    removed_keys = [m.member for m in record.proven_removed_members]
    ex = resolve_release_exit_decision_with_assurance(
        worst,
        s.fail_on_removed,
        removed_keys,
        None,
        library_results=results,
        incomplete_scope_exit_contribution=decision.incomplete_scope_exit_contribution,
        no_comparison_completed_exit_contribution=(
            decision.no_comparison_completed_exit_contribution
        ),
        failed_member_exit_contribution=decision.failed_member_exit_contribution,
    )
    return record, ex


def _spec_states(record, combo):  # type: ignore[no-untyped-def]
    """The record's member states in the spec's vocabulary, in member order."""
    removed = {m.member for m in record.proven_removed_members}
    out: list[str] = []
    for m in record.members:
        if m.state is AcquisitionState.NOT_SUPPLIED and m.member in removed:
            out.append("retired")
        elif m.state is AcquisitionState.AVAILABLE:
            kind = combo[int(m.member.removeprefix("lib"))]
            out.append("compared_break" if kind == "break" else "compared_ok")
        else:
            out.append(_LABEL[m.state])
    return tuple(out)


def audit(max_members: int) -> Result:
    res = Result()
    settings = [
        Setting(op, np_, single, pol, fail)
        for op, np_, single, pol, fail in itertools.product(
            (False, True),
            (False, True),
            (False, True),
            ("warn", "block"),
            (False, True),
        )
    ]
    for n in range(0, max_members + 1):
        for combo in itertools.product(KINDS, repeat=n):
            for s in settings:
                res.scenarios += 1
                record, ex = _run(combo, s)
                states = _spec_states(record, combo)
                proven = (
                    record.new_inventory.completeness is InventoryCompleteness.PROVEN
                )
                if not record.completed_members:
                    res.zero_comparison_runs += 1
                bad = check_scope.invariant_failures(states, proven, ex.code)
                # I2, the exit side: a removal exit needs a proven retirement.
                # Only the removal reason is checked here: a legacy exit of 8
                # from anywhere else would be a different fault.
                if ExitReason.REMOVED_REQUIRED_LIBRARY in ex.reasons and not (
                    "retired" in states and proven
                ):
                    bad.append("I2 RemovalExitNeedsProof")
                for inv in (
                    "I1 NoCleanZeroComparison",
                    "I2 RemovalNeedsProof",
                    "I3 FailedNeverClean",
                ):
                    res.checks[inv] += 1
                for b in bad:
                    res.violations.append(
                        (b, combo, s, ex.code, [r.value for r in ex.reasons])
                    )
    return res


def _describe(v) -> str:  # type: ignore[no-untyped-def]
    inv, combo, s, code, reasons = v
    kinds = ", ".join(f"lib{i}={k}" for i, k in enumerate(combo)) or "(no members)"
    return (
        f"{inv}\n    members: {kinds}\n"
        f"    OLD inventory proven={s.old_proven}, NEW proven={s.new_proven}, "
        f"new_single_artifact={s.new_single_artifact}, policy={s.policy}, "
        f"fail_on_removed={s.fail_on_removed}\n"
        f"    real exit={code} reasons={reasons}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-members", type=int, default=3)
    ap.add_argument("--show", type=int, default=3, help="counterexamples to print")
    a = ap.parse_args(argv)
    res = audit(a.max_members)
    print(
        f"scenarios={res.scenarios} (members 0..{a.max_members}, "
        f"{len(KINDS)} kinds), zero-comparison runs={res.zero_comparison_runs}"
    )
    for inv in (
        "I1 NoCleanZeroComparison",
        "I2 RemovalNeedsProof",
        "I3 FailedNeverClean",
    ):
        hit = [v for v in res.violations if v[0] == inv]
        print(f"  {inv}: checked {res.checks[inv]}, violated {len(hit)}")
    # Smallest combinations first, so the printed counterexamples are minimal.
    shown = sorted(res.violations, key=lambda v: (len(v[1]), v[0]))
    seen_inv: Counter = Counter()
    for v in shown:
        if seen_inv[v[0]] >= a.show:
            continue
        seen_inv[v[0]] += 1
        print("counterexample:\n    " + _describe(v))
    return 1 if res.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
