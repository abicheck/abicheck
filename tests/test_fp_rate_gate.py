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

"""Fast-lane wrapper for the ADR-024 §7 scoping FP-rate gate.

The gate logic lives in ``scripts/check_fp_rate.py`` so it is runnable
standalone in CI; this mirrors it into the pytest suite (per-case for readable
failures) so a regression is caught in the ordinary unit-test lane too.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_fp_rate.py"
_spec = importlib.util.spec_from_file_location("check_fp_rate", _GATE_PATH)
assert _spec and _spec.loader
fp_gate = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve the module's __dict__.
sys.modules["check_fp_rate"] = fp_gate
_spec.loader.exec_module(fp_gate)


@pytest.mark.parametrize("case", fp_gate.CORPUS, ids=lambda c: c.name)
def test_scoping_case_matches_ground_truth(case):
    from abicheck.checker import compare

    old, new = case.build()
    result = compare(old, new, scope_to_public_surface=True)
    is_breaking = result.verdict in fp_gate._BREAKING_VERDICTS
    if case.internal_noise:
        assert not is_breaking, (
            f"FALSE POSITIVE: internal-noise case {case.name!r} reported "
            f"breaking verdict {result.verdict.value} under scoping"
        )
    else:
        assert is_breaking, (
            f"FALSE NEGATIVE: real-break case {case.name!r} scoped away to "
            f"non-breaking verdict {result.verdict.value}"
        )


@pytest.mark.parametrize("case", fp_gate.CROSSCHECK_CORPUS, ids=lambda c: c.name)
def test_crosscheck_case_matches_ground_truth(case):
    """ADR-035 D4 promotion gate: each cross-check fires on a real hygiene issue
    and stays silent on a clean snapshot (both polarities, baseline 0/0)."""
    from abicheck.buildsource.crosscheck import run_crosschecks

    result = run_crosschecks(case.build())
    fired = any(c.kind == case.kind for c in result.findings)
    if case.should_fire:
        assert fired, (
            f"FALSE NEGATIVE: cross-check {case.kind.value!r} missed hygiene "
            f"case {case.name!r}"
        )
    else:
        assert not fired, (
            f"FALSE POSITIVE: cross-check {case.kind.value!r} flagged clean "
            f"case {case.name!r}"
        )


def test_fp_rate_within_baseline():
    outcome = fp_gate.evaluate()
    assert len(outcome.false_positives) <= fp_gate.FP_BASELINE, outcome.false_positives
    assert len(outcome.false_negatives) <= fp_gate.FN_BASELINE, outcome.false_negatives


def test_crosscheck_rate_within_baseline():
    outcome = fp_gate.evaluate_crosschecks()
    assert len(outcome.false_positives) <= fp_gate.CC_FP_BASELINE, (
        outcome.false_positives
    )
    assert len(outcome.false_negatives) <= fp_gate.CC_FN_BASELINE, (
        outcome.false_negatives
    )


def test_metrics_report_delta_vs_baseline():
    """ADR-033 D9: the gate exposes false_positive_delta_vs_baseline (0 = clean)."""
    m = fp_gate.metrics()
    assert (
        m["false_positive_delta_vs_baseline"]
        == m["false_positives"] - fp_gate.FP_BASELINE
    )
    assert (
        m["false_negative_delta_vs_baseline"]
        == m["false_negatives"] - fp_gate.FN_BASELINE
    )
    # Corpus is built for a clean sheet, so the deltas are zero.
    assert m["false_positive_delta_vs_baseline"] == 0
    assert m["false_negative_delta_vs_baseline"] == 0


def test_json_mode_emits_metrics(capsys):
    """`--json` prints the D9 metric keys for CI consumption."""
    import json

    rc = fp_gate.main(["--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "false_positive_delta_vs_baseline" in out
    # The per-axis trend breakdown rides along in the JSON for archiving.
    assert "by_category" in out
    assert out["by_category"]  # non-empty


def test_every_case_carries_a_category_tag():
    """No corpus case is silently 'uncategorized' (enforced from main() via
    uncategorized_cases(), not an import-time assert)."""
    assert fp_gate.uncategorized_cases() == []
    assert all(fp_gate._category_of(c.name) != "uncategorized" for c in fp_gate.CORPUS)


def test_category_breakdown_partitions_the_corpus():
    """Per-axis case counts sum back to the full corpus, and (on a clean build)
    every axis carries zero FP/FN so a regression is attributable to one axis."""
    breakdown = fp_gate.category_breakdown()
    assert sum(row["cases"] for row in breakdown.values()) == len(fp_gate.CORPUS)
    for axis, row in breakdown.items():
        assert row["cases"] == row["internal_noise"] + row["real_break"], axis
        assert row["false_positives"] == 0, axis
        assert row["false_negatives"] == 0, axis
    # The enum-reachability and pointer-opaque axes are represented with both
    # polarities (an internal-noise FP sentinel and a real-break FN sentinel).
    for axis in ("enum-reachability", "pointer-opaque"):
        assert breakdown[axis]["internal_noise"] >= 1, axis
        assert breakdown[axis]["real_break"] >= 1, axis


def test_markdown_mode_renders_axis_table(capsys):
    """`--markdown` renders a per-axis table for the CI step summary / trend."""
    rc = fp_gate.main(["--markdown"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "| Axis | Cases |" in out
    assert "enum-reachability" in out
    assert "pointer-opaque" in out
