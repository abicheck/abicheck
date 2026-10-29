"""One public-surface resolution per side per comparison.

``ComparisonPublicSurfaces`` is created by the comparison and passed down
explicitly to every consumer (Tier-2 entity ids, ``FilterNonPublicSurface``,
contract evaluation, surface metrics, scope confidence). These tests pin:

* the call-count bound -- ``resolve_public_surface`` runs at most once per
  snapshot per comparison, at two library sizes and across every option
  combination that reaches a different consumer;
* equivalence -- the report is identical to one where every consumer
  resolves for itself (the pre-sharing behaviour);
* mutation safety -- the one shared surface object still equals a fresh
  resolution after the whole comparison ran, so no consumer mutated it;
* identity keying -- a different snapshot object never receives another
  snapshot's surface.
"""

from __future__ import annotations

import copy
import sys
from collections import Counter
from dataclasses import fields
from pathlib import Path

import pytest

import abicheck.policy.public_surface_closure as closure
from abicheck.checker import compare
from abicheck.policy.public_surface_query import ComparisonPublicSurfaces
from abicheck.report.diff_report import to_json
from abicheck.workflows.compare_policy import compare_snapshots

sys.path.insert(0, str(Path(__file__).parent))
from _compare_workloads import WORKLOADS  # noqa: E402

_WORKLOADS = (
    "signature_churn",
    "legacy_signature_churn",
    "type_churn",
    "add_remove",
    "enum_churn",
)

# Option combinations that route the surface through different consumers:
# header scoping (FilterNonPublicSurface + scope confidence), no scoping
# (contract evaluation's own fallback), and every opt-in stage at once.
_VARIANTS = {
    "tier2_default": lambda o, n: compare_snapshots(o, n),
    "tier2_unscoped_contract": lambda o, n: compare_snapshots(
        o, n, scope_to_public_surface=False, contract_mode="public"
    ),
    "tier2_allowlist": lambda o, n: compare_snapshots(
        o, n, public_surface_allowlist={"fn_0"}, contract_mode="exports"
    ),
    "core_all_stages": lambda o, n: compare(
        o, n, surface_metrics=True, pattern_verdicts=True, contract_evaluation=True
    ),
    "core_no_baseline": lambda o, n: compare(None, n, contract_evaluation=True),
}


def _count_resolutions(monkeypatch: pytest.MonkeyPatch) -> Counter[int]:
    calls: Counter[int] = Counter()
    real = closure._resolve_public_surface_from_snapshot

    def counting(snap):
        calls[id(snap)] += 1
        return real(snap)

    monkeypatch.setattr(closure, "_resolve_public_surface_from_snapshot", counting)
    return calls


@pytest.mark.parametrize("n", [20, 160])
@pytest.mark.parametrize("workload", _WORKLOADS)
@pytest.mark.parametrize("variant", sorted(_VARIANTS))
def test_resolved_at_most_once_per_side(monkeypatch, workload, variant, n):
    old, new = WORKLOADS[workload](n, f"once_{variant}_")
    calls = _count_resolutions(monkeypatch)
    _VARIANTS[variant](old, new)
    # Independent of size: the bound is per snapshot, not per declaration.
    assert calls, "the comparison resolved no public surface at all"
    assert max(calls.values()) == 1, dict(calls)
    # Two sides (the no-baseline stand-in counts as the OLD side).
    assert len(calls) <= 2


def _report(result) -> str:
    return to_json(result)


@pytest.mark.parametrize("workload", _WORKLOADS)
@pytest.mark.parametrize("variant", sorted(_VARIANTS))
def test_sharing_does_not_change_the_report(monkeypatch, workload, variant):
    old, new = WORKLOADS[workload](40, f"eq_{variant}_")
    shared = _report(_VARIANTS[variant](old, new))

    # Pre-sharing behaviour: every consumer resolves for itself.
    monkeypatch.setattr(
        ComparisonPublicSurfaces,
        "of",
        lambda self, snap: closure.resolve_public_surface(snap),
    )
    old, new = WORKLOADS[workload](40, f"eq_{variant}_")
    unshared = _report(_VARIANTS[variant](old, new))
    strip = lambda text: "\n".join(  # noqa: E731
        line for line in text.splitlines() if '"generated_at"' not in line
    )
    assert strip(shared) == strip(unshared)


def _surface_state(surface) -> dict[str, object]:
    return {f.name: copy.deepcopy(getattr(surface, f.name)) for f in fields(surface)}


@pytest.mark.parametrize("workload", _WORKLOADS)
def test_no_consumer_mutates_the_shared_surface(workload):
    old, new = WORKLOADS[workload](60, "mut_")
    surfaces = ComparisonPublicSurfaces()
    before = {
        side: _surface_state(surfaces.of(snap))
        for side, snap in (("old", old), ("new", new))
    }
    for scope in (True, False):
        compare(
            old,
            new,
            scope_to_public_surface=scope,
            surface_metrics=True,
            pattern_verdicts=True,
            contract_evaluation=True,
            public_surfaces=surfaces,
        )
    for side, snap in (("old", old), ("new", new)):
        assert _surface_state(surfaces.of(snap)) == before[side]
        assert _surface_state(surfaces.of(snap)) == _surface_state(
            closure.resolve_public_surface(snap)
        )
    assert (
        any(state["resolvable"] for state in before.values())
        or workload == "legacy_signature_churn"
    )


def test_keyed_by_snapshot_identity_not_equality():
    old, new = WORKLOADS["signature_churn"](10, "ident_")
    twin = copy.deepcopy(new)
    surfaces = ComparisonPublicSurfaces()
    assert surfaces.of(new) is surfaces.of(new)
    assert surfaces.of(twin) is not surfaces.of(new)
    assert surfaces.of(old) is not surfaces.of(new)
