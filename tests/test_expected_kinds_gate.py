"""The catalog lanes' strict ``expected_kinds`` gate (tests/expected_kinds_gate.py).

A lane cell that misses an ``expected_kinds`` entry, or reports an
``expected_absent_kinds`` entry, FAILs unless ``ground_truth.json`` declares
that gap for the cell's header frontend and mode; a declaration that no
longer reproduces FAILs as stale. Bug class: a verdict-only PASS hid missing
calibration kinds on the clang header lane (case17/43/74-77/80).
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import expected_kinds_gate as gate  # noqa: E402

import tests.validate_examples as ve  # noqa: E402

_HEADING = "A header-only default template-argument change"
_HEADINGS = (f"{_HEADING} reads `NO_CHANGE` at L2 on castxml (2026-10-21)",)
_KINDS = ("a_kind", "b_kind", "c_kind")
_EXPECTED = ["a_kind", "b_kind"]
_ABSENT = ["c_kind"]


def _gap(kinds, modes=None, frontend="clang"):
    obj = {"kinds": list(kinds), "reason": "r", "known_gap": _HEADING}
    if modes is not None:
        obj["modes"] = list(modes)
    return {frontend: [obj]}


def _entry(gaps=None, **extra):
    entry = {
        "expected": "BREAKING",
        "expected_kinds": list(_EXPECTED),
        "expected_absent_kinds": list(_ABSENT),
        **extra,
    }
    if gaps:
        entry["expected_kinds_gaps"] = gaps
    return entry


def _subsets(items):
    return [
        frozenset(c)
        for n in range(len(items) + 1)
        for c in itertools.combinations(items, n)
    ]


# ── the cell invariant, exhaustively over a small domain ──────────────────


@pytest.mark.parametrize("got", _subsets(_KINDS))
@pytest.mark.parametrize("declared", _subsets(_KINDS))
@pytest.mark.parametrize("mode", gate.ENFORCED_MODES)
def test_cell_passes_iff_violations_equal_the_declared_gap(got, declared, mode):
    """Oracle, stated without the gate's own helpers: a native cell passes
    exactly when the set of violated kinds equals the declared set for that
    (frontend, mode); any undeclared violation or stale declaration fails."""
    entry = _entry(_gap(sorted(declared)) if declared else None)
    violations = (set(_EXPECTED) - got) | (set(_ABSENT) & got)
    status, message, verdict = gate.judge_cell(
        entry, "PASS", "", got, mode=mode, frontend="clang"
    )
    assert (status == "PASS") is (violations == set(declared))
    if violations == set(declared):
        assert verdict.signal == ("declared-gap" if declared else "ok")
        assert message == ""
    else:
        assert "kinds gate:" in message


@pytest.mark.parametrize("got", _subsets(_KINDS))
@pytest.mark.parametrize("declared", _subsets(_KINDS)[1:])
def test_a_gap_declared_for_another_frontend_or_mode_excuses_nothing(got, declared):
    violations = (set(_EXPECTED) - got) | (set(_ABSENT) & got)
    for gaps, frontend, mode in (
        (_gap(sorted(declared), frontend="castxml"), "clang", "debug-headers"),
        (_gap(sorted(declared), modes=["build-source"]), "clang", "debug-headers"),
    ):
        status, _, _ = gate.judge_cell(
            _entry(gaps), "PASS", "", got, mode=mode, frontend=frontend
        )
        assert (status == "PASS") is (not violations)


def test_missing_kind_fails_the_cell():
    status, message, verdict = gate.judge_cell(
        _entry(), "PASS", "", {"b_kind"}, mode="debug-headers", frontend="clang"
    )
    assert status == "FAIL"
    assert verdict.signal == "mismatch"
    assert "expected_kinds missing: ['a_kind']" in message


def test_declared_gap_passes_and_names_the_gap():
    entry = _entry(_gap(["a_kind"], modes=["debug-headers"]))
    status, message, verdict = gate.judge_cell(
        entry, "PASS", "", {"b_kind"}, mode="debug-headers", frontend="clang"
    )
    assert (status, message) == ("PASS", "")
    assert verdict.signal == "declared-gap"
    assert "['a_kind']" in verdict.detail


def test_stale_declaration_fails_and_says_to_remove_it():
    entry = _entry(_gap(["a_kind"]))
    status, message, verdict = gate.judge_cell(
        entry, "PASS", "", {"a_kind", "b_kind"}, mode="build-source", frontend="clang"
    )
    assert status == "FAIL"
    assert verdict.signal == "stale-gap"
    assert "stale expected_kinds_gaps.clang for build-source: ['a_kind']" in message
    assert "remove the declaration" in message


def test_verdict_failure_keeps_its_message_and_gains_the_kinds_detail():
    status, message, _ = gate.judge_cell(
        _entry(),
        "FAIL",
        "expected='BREAKING' got='NO_CHANGE'",
        set(),
        mode="debug-headers",
        frontend="castxml",
    )
    assert status == "FAIL"
    assert message.startswith("expected='BREAKING' got='NO_CHANGE' | kinds gate:")


@pytest.mark.parametrize("status", ["XFAIL", "SKIP", "ERROR", "NOT_APPLICABLE"])
def test_cells_that_are_not_pass_or_fail_are_not_kinds_checked(status):
    """The verdict-level known_gap/XFAIL semantics stay separate."""
    assert gate.judge_cell(
        _entry(), status, "m", set(), mode="debug-headers", frontend="clang"
    ) == (status, "m", gate.NOT_CHECKED)


@pytest.mark.parametrize("mode", ["release-headers", "stripped-headers"])
def test_reduced_evidence_modes_report_but_do_not_fail(mode):
    status, _, verdict = gate.judge_cell(
        _entry(), "PASS", "", set(), mode=mode, frontend="clang"
    )
    assert status == "PASS"
    assert verdict.signal == "mismatch"


def test_detectability_none_without_violation_is_gap_observed():
    _, _, verdict = gate.judge_cell(
        _entry(detectability="none"),
        "PASS",
        "",
        {"a_kind", "b_kind"},
        mode="debug-headers",
        frontend="castxml",
    )
    assert verdict.signal == "gap-observed"


# ── run_case wiring: the gate decides the real lane cell ──────────────────


@pytest.fixture
def fake_lane(tmp_path, monkeypatch):
    """run_case with build/dump/compare replaced by a scripted emitted-kinds
    set, so the test sees the cell exactly as the lane reports it."""
    src = tmp_path / "src"
    src.mkdir()
    v1, v2 = src / "v1.c", src / "v2.c"
    v1.write_text("int f(void){return 1;}\n")
    v2.write_text("int f(void){return 2;}\n")
    emitted: dict[str, object] = {}
    calls = itertools.count()
    monkeypatch.setattr(
        ve, "_resolve_case_sources", lambda n, e: (src, (v1, v2, None, None))
    )
    monkeypatch.setattr(ve, "_run_source_smoke", lambda *a, **k: None)
    monkeypatch.setattr(
        ve, "_build_libs", lambda *a, **k: (src / "a.so", src / "b.so", None)
    )
    monkeypatch.setattr(ve, "_build_info_applies", lambda *a: False)
    monkeypatch.setattr(
        ve,
        "_dump_and_compare",
        lambda *a, **k: (emitted["verdict"], tuple(emitted["kinds"]), None),
    )

    def run(entry, kinds, *, frontend="clang", variant="debug-headers", verdict=None):
        emitted.update(verdict=verdict or entry["expected"], kinds=kinds)
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", frontend)
        work = tmp_path / f"run{next(calls)}"
        return ve.run_case("caseX", entry, work, variant=variant)

    return run


def test_run_case_fails_a_verdict_pass_that_misses_a_kind(fake_lane):
    res = fake_lane(_entry(), ["b_kind"])
    assert res.got == "BREAKING"
    assert res.status == "FAIL"
    assert res.kinds_strict == "mismatch"
    assert res.actual_kinds == ("b_kind",)


def test_run_case_passes_a_gap_declared_for_its_frontend_only(fake_lane):
    entry = _entry(_gap(["a_kind"], frontend="clang"))
    assert fake_lane(entry, ["b_kind"], frontend="clang").status == "PASS"
    assert fake_lane(entry, ["b_kind"], frontend="castxml").status == "FAIL"


def test_run_case_reports_a_stale_gap(fake_lane):
    entry = _entry(_gap(["a_kind"], frontend="castxml"))
    res = fake_lane(entry, ["a_kind", "b_kind"], frontend="castxml")
    assert res.status == "FAIL"
    assert res.kinds_strict == "stale-gap"


def test_run_case_verdict_known_gap_still_xfails_unchecked(fake_lane):
    entry = _entry(known_gap="documented wrong verdict")
    res = fake_lane(entry, [], verdict="NO_CHANGE")
    assert (res.status, res.kinds_strict) == ("XFAIL", "n/a")
    # The same gap does not excuse missing kinds once the verdict is right.
    assert fake_lane(entry, []).status == "FAIL"


# ── schema ─────────────────────────────────────────────────────────────────


def test_catalog_ground_truth_declarations_are_valid():
    verdicts = json.loads(ve.GROUND_TRUTH.read_text())["verdicts"]
    assert gate.validate_ground_truth(verdicts) == []


def test_main_refuses_an_invalid_declaration(monkeypatch, tmp_path):
    gt = tmp_path / "ground_truth.json"
    gt.write_text(
        json.dumps({"verdicts": {"caseX": _entry({"gcc": [{"kinds": ["a_kind"]}]})}})
    )
    monkeypatch.setattr(ve, "GROUND_TRUTH", gt)
    monkeypatch.setattr(ve, "_check_prerequisites", lambda: None)
    assert ve.main(["caseX"]) == 2


@pytest.mark.parametrize(
    ("gaps", "needle"),
    [
        (
            {"gcc": [{"kinds": ["a_kind"], "reason": "r", "known_gap": _HEADING}]},
            "unknown frontend",
        ),
        (
            {"clang": [{"kinds": ["z_kind"], "reason": "r", "known_gap": _HEADING}]},
            "neither expected_kinds nor expected_absent_kinds",
        ),
        (
            {"clang": [{"kinds": ["a_kind"], "known_gap": _HEADING}]},
            "missing keys ['reason']",
        ),
        (
            {"clang": [{"kinds": ["a_kind"], "reason": "r", "known_gap": "No such"}]},
            "starts no heading",
        ),
        (
            {
                "clang": [
                    {
                        "kinds": ["a_kind"],
                        "reason": "r",
                        "known_gap": _HEADING,
                        "modes": ["release-headers"],
                    }
                ]
            },
            "modes must be",
        ),
        (
            {"clang": [{"kinds": [], "reason": "r", "known_gap": _HEADING}]},
            "non-empty list",
        ),
        (
            {"clang": [{"kinds": ["a_kind"], "reason": " ", "known_gap": _HEADING}]},
            "reason must be",
        ),
        (
            {
                "clang": [
                    {
                        "kinds": ["a_kind"],
                        "reason": "r",
                        "known_gap": _HEADING,
                        "extra": 1,
                    }
                ]
            },
            "unknown keys ['extra']",
        ),
        (
            {
                "clang": [
                    {"kinds": ["a_kind"], "reason": "r", "known_gap": _HEADING},
                    {
                        "kinds": ["a_kind"],
                        "reason": "r",
                        "known_gap": _HEADING,
                        "modes": ["debug-headers"],
                    },
                ]
            },
            "declared twice for debug-headers",
        ),
    ],
)
def test_schema_rejects(gaps, needle):
    errors = gate.validate_entry("caseX", _entry(gaps), _HEADINGS)
    assert any(needle in e for e in errors), errors


def test_schema_accepts_a_single_object_and_rejects_the_retired_field():
    one = {"clang": {"kinds": ["c_kind"], "reason": "r", "known_gap": _HEADING}}
    assert gate.validate_entry("caseX", _entry(one), _HEADINGS) == []
    assert gate.declared_kinds(_entry(one), "clang", "build-source") == {"c_kind"}
    errors = gate.validate_entry("caseX", _entry(known_kind_gap="a_kind"), _HEADINGS)
    assert errors == [
        "caseX: known_kind_gap is retired; declare the gap per frontend under "
        "expected_kinds_gaps"
    ]


def test_known_gap_headings_reads_the_real_index():
    headings = gate.known_gap_headings()
    assert any(h.startswith(_HEADING) for h in headings)
