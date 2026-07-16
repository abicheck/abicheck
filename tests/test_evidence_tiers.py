# SPDX-License-Identifier: Apache-2.0
"""Evidence-tier metadata gate.

Keeps ``examples/ground_truth.json``'s per-case ``min_evidence`` field in sync
with ``scripts/evidence_tiers.py`` (the single source of truth for the
five-source / L0–L4 model) and guards the tier vocabulary. Pure-Python: no
compiler, castxml, or external tool — the empirical per-tier detection is
exercised separately by ``benchmark_comparison.py --evidence-tiers``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_GT_PATH = _REPO / "examples" / "ground_truth.json"
_ET_PATH = _REPO / "scripts" / "evidence_tiers.py"

_spec = importlib.util.spec_from_file_location("evidence_tiers", _ET_PATH)
assert _spec and _spec.loader
evidence_tiers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evidence_tiers)

_VERDICTS: dict[str, dict] = json.loads(_GT_PATH.read_text())["verdicts"]


def test_every_case_has_min_evidence() -> None:
    missing = [c for c, info in _VERDICTS.items() if "min_evidence" not in info]
    assert not missing, f"cases missing min_evidence: {missing}"


@pytest.mark.parametrize("case", sorted(_VERDICTS))
def test_min_evidence_in_vocabulary(case: str) -> None:
    tier = _VERDICTS[case]["min_evidence"]
    allowed = {*evidence_tiers.TIER_ORDER, evidence_tiers.UNDETECTABLE}
    assert tier in allowed, f"{case}: min_evidence {tier!r} not in {allowed}"


@pytest.mark.parametrize("case", sorted(_VERDICTS))
def test_undetectable_min_evidence_matches_detectability_field(case: str) -> None:
    """``min_evidence: "none"`` and ``detectability: "none"`` must agree exactly."""
    info = _VERDICTS[case]
    is_undetectable_tier = info["min_evidence"] == evidence_tiers.UNDETECTABLE
    is_marked_undetectable = info.get("detectability") == "none"
    assert is_undetectable_tier == is_marked_undetectable, (
        f"{case}: min_evidence={info['min_evidence']!r} but "
        f"detectability={info.get('detectability')!r} — they must agree"
    )


@pytest.mark.parametrize("case", sorted(_VERDICTS))
def test_min_evidence_matches_tier_module(case: str) -> None:
    """ground_truth's stored value must equal the recomputed tier."""
    expected = evidence_tiers.compute_min_evidence(case, _VERDICTS[case])
    assert _VERDICTS[case]["min_evidence"] == expected, (
        f"{case}: ground_truth min_evidence={_VERDICTS[case]['min_evidence']!r} "
        f"but evidence_tiers computes {expected!r} — regenerate ground_truth or "
        f"update scripts/evidence_tiers.py"
    )


def test_every_used_changekind_is_mapped() -> None:
    """No expected kind may be missing a tier (forces a decision per new kind)."""
    used: set[str] = set()
    for info in _VERDICTS.values():
        used.update(info.get("expected_kinds", []))
    unmapped = sorted(k for k in used if k not in evidence_tiers.EVIDENCE_TIER_BY_KIND)
    assert not unmapped, f"ChangeKinds used in ground_truth but unmapped: {unmapped}"


def test_tier_rank_is_monotonic() -> None:
    ranks = [evidence_tiers.tier_rank(t) for t in evidence_tiers.TIER_ORDER]
    assert ranks == sorted(ranks) == list(range(len(ranks)))


def test_kindless_overrides_are_real_cases() -> None:
    """Every KINDLESS_CASE_TIER key must be an actual kind-less case."""
    for case in evidence_tiers.KINDLESS_CASE_TIER:
        assert case in _VERDICTS, f"KINDLESS_CASE_TIER references unknown case {case!r}"
        info = _VERDICTS[case]
        kinds = info.get("expected_kinds", [])
        assert not kinds, (
            f"{case} has kinds {kinds}; remove its KINDLESS_CASE_TIER entry"
        )


# ── detected_at crediting logic ──────────────────────────────────────────────
# A "runnable" tier map keyed L0..L3 (L4 is n/a in the benchmark harness).
def _verdicts(l0: str, l1: str, l2: str, l3: str = "n/a") -> dict[str, str]:
    return {"L0": l0, "L1": l1, "L2": l2, "L3": l3}


def test_detected_at_active_verdict_credits_on_verdict_alone() -> None:
    """A BREAKING case is credited at the first tier returning BREAKING, even
    when the emitted kind is a tier variant (L0 func_removed_elf_only)."""
    got = evidence_tiers.detected_at(
        _verdicts("BREAKING", "BREAKING", "BREAKING"),
        {
            "L0": ["func_removed_elf_only"],
            "L1": ["func_removed"],
            "L2": ["func_removed"],
        },
        expected="BREAKING",
        expected_kinds=["func_removed"],
        min_evidence="L0",
    )
    assert got == "L0"


def test_detected_at_kinded_quiet_requires_the_kind() -> None:
    """A COMPATIBLE case must emit its cataloged kind — a bare COMPATIBLE at a
    weaker tier (no kind) is not credited."""
    got = evidence_tiers.detected_at(
        _verdicts("NO_CHANGE", "COMPATIBLE", "COMPATIBLE"),
        {"L0": [], "L1": [], "L2": ["enum_member_added"]},
        expected="COMPATIBLE",
        expected_kinds=["enum_member_added"],
        min_evidence="L2",
    )
    assert got == "L2"


def test_detected_at_kindless_quiet_floored_at_min_evidence() -> None:
    """A kindless quiet baseline with a declared L4 floor is not credited at L0."""
    got = evidence_tiers.detected_at(
        _verdicts("NO_CHANGE", "NO_CHANGE", "ERROR"),
        {"L0": [], "L1": [], "L2": []},
        expected="NO_CHANGE",
        expected_kinds=[],
        min_evidence="L4",
    )
    assert got is None  # no runnable tier (L0..L3) reaches the L4 floor


def test_detected_at_kindless_quiet_at_l0_is_genuine() -> None:
    """A genuine NO_CHANGE baseline (min L0) is still credited at L0."""
    got = evidence_tiers.detected_at(
        _verdicts("NO_CHANGE", "NO_CHANGE", "NO_CHANGE"),
        {"L0": [], "L1": [], "L2": []},
        expected="NO_CHANGE",
        expected_kinds=[],
        min_evidence="L0",
    )
    assert got == "L0"


def test_detected_at_returns_none_when_no_tier_matches() -> None:
    got = evidence_tiers.detected_at(
        _verdicts("NO_CHANGE", "NO_CHANGE", "NO_CHANGE"),
        {"L0": [], "L1": [], "L2": []},
        expected="BREAKING",
        expected_kinds=["type_size_changed"],
        min_evidence="L1",
    )
    assert got is None
