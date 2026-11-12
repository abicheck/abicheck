"""Reproducibility tests for the formal-methods experiments in ``formal/``.

These pin the *results* the experiment report (``formal/EXPERIMENTS.md``)
states, so a change that invalidates one turns a test red instead of leaving
the report silently stale. They are conformance and model-checking runs, not
proofs about the Python implementation.
"""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import sys
from pathlib import Path

import pytest

FORMAL = Path(__file__).resolve().parent.parent / "formal"


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


def test_e7_count_trigger_sound_without_same_call_additions() -> None:
    chk = _load(FORMAL / "tla" / "check_disposition.py")
    states, trace = chk.check(5, 3, adds=False)
    assert trace is None
    assert states > 1000  # the space was actually explored


def test_e7_count_trigger_misses_drop_plus_bucket_addition() -> None:
    """Shortest counterexample: a step drops finding 1 and puts a *new*
    object 2 into a bucket in the same call; the counts balance, so the
    trigger does not fire and finding 1 is neither visible, bucketed nor
    recorded."""
    chk = _load(FORMAL / "tla" / "check_disposition.py")
    _, trace = chk.check(4, 2, adds=True)
    assert trace is not None and len(trace) == 2
    (c0, b0, l0, s0, *_), (c1, b1, l1, s1, *_) = trace
    vanished = s1 - (c1 | b1 | l1)
    assert vanished and vanished <= c0
    assert len(c1) + len(b1 - b0) == len(c0)  # why the count trigger stays silent


def test_lean_layout_table_covers_declared_domain() -> None:
    rows = json.loads(
        (FORMAL / "lean" / "layout_table.json").read_text(encoding="utf-8")
    )["rows"]
    types = {"char", "short", "int", "double"}
    bases = {tuple(r["base"]) for r in rows}
    assert len(bases) == 4 + 16 + 64
    for r in rows:
        assert set(r["base"]) <= types and r["inserted"] in types
        assert 0 <= r["insert_at"] <= len(r["base"])
        assert len(r["new_offsets"]) == len(r["old_offsets"]) + 1
    assert len(rows) == sum((len(b) + 1) * 4 for b in bases)


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or platform.machine() not in ("x86_64", "aarch64")
    or sys.maxsize <= 2**32,
    reason="Linux ELF, LP64 experiment: the Lean table models x86-64/AArch64 alignment",
)
def test_e5_e6_layout_model_extraction_and_compare_agree(tmp_path: Path) -> None:
    if shutil.which("gcc") is None or shutil.which("castxml") is None:
        pytest.skip("gcc and castxml required")
    exp = _load(FORMAL / "experiments" / "layout_experiments.py")
    assert exp.main(tmp_path) == 0
    s = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    n = 2 * s["cases"]
    assert s["E6a_model_vs_gcc"] == {"agree": n}
    assert s["E6b_extraction_vs_gcc"] == {
        "castxml_headers": {"agree": n},
        "dwarf_only": {"agree": n},
    }
    mismatches = {
        k: v
        for k, v in s["E5_compare_vs_model"].items()
        if "pred=True:found=False" in k or "pred=False:found=True" in k
    }
    assert mismatches == {}
    # E4: the edit class alone does not determine the outcome
    assert s["E4_class_outcomes"]["insert_before_field:NOT moved"] > 0
    assert s["E4_class_outcomes"]["append:size SAME"] > 0


# --- E8: scope completeness (ADR-065), formal/tla/ScopeCompleteness.tla ----


def test_e8_scope_spec_invariants_hold_for_three_members() -> None:
    chk = _load(FORMAL / "tla" / "check_scope.py")
    states, found = chk.check(3)
    assert found is None
    assert states > 1000  # the space was actually explored


@pytest.mark.parametrize(
    ("mutant", "invariant"),
    [
        ("classify-unguarded", "I2 RemovalNeedsProof"),
        ("warn-clean-zero", "I1 NoCleanZeroComparison"),
        ("failed-ops-dropped", "I3 FailedNeverClean"),
    ],
)
def test_e8_checker_detects_seeded_spec_faults(mutant: str, invariant: str) -> None:
    """A checker that cannot fail proves nothing: each seeded fault must
    produce a counterexample for the invariant it breaks."""
    chk = _load(FORMAL / "tla" / "check_scope.py")
    _, found = chk.check(3, chk.Spec(mutant))
    assert found is not None
    bad, trace = found
    assert invariant in bad
    assert len(trace) >= 2  # a real path, not an initial state


def test_e8_real_completeness_code_satisfies_i1_and_i2() -> None:
    """Conformance on the real aggregation (up to 2 members, all evidence and
    policy settings): zero comparisons never exit 0, and no removal exit or
    retired member without NEW's inventory proven."""
    audit = _load(FORMAL / "experiments" / "scope_completeness_audit.py")
    res = audit.audit(2)
    assert res.zero_comparison_runs > 0
    assert [v for v in res.violations if v[0].startswith("I1")] == []
    assert [v for v in res.violations if v[0].startswith("I2")] == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "E8 finding: a FAILED member (degraded capture, ADR-065 D8) that sits "
        "beside a completed member exits 0 under the default warn policy "
        "(the operational fold reads only verdict 'ERROR'). Strict: this turns "
        "red once fixed, so the marker is then removed."
    ),
)
def test_e8_failed_member_never_reads_clean_on_real_code() -> None:
    audit = _load(FORMAL / "experiments" / "scope_completeness_audit.py")
    res = audit.audit(2)
    assert [v for v in res.violations if v[0].startswith("I3")] == []


def test_e8_known_i3_violations_are_only_the_degraded_failure_class() -> None:
    """Pins the shape of the known I3 gap: every violation is a warn-policy
    run that exits 0 and has a failed member from one of the glue paths
    (degraded matched, degraded OLD-only, no result recorded). Block is never
    affected. A new violation shape fails this test."""
    audit = _load(FORMAL / "experiments" / "scope_completeness_audit.py")
    res = audit.audit(2)
    i3 = [v for v in res.violations if v[0].startswith("I3")]
    assert i3, "expected the known warn-policy gap to reproduce"
    failing_kinds = {"degraded_matched", "degraded_old", "no_entry"}
    for _inv, combo, setting, code, _reasons in i3:
        assert setting.policy == "warn"
        assert code == 0
        assert failing_kinds & set(combo)
    assert len(i3) == len(res.violations)
