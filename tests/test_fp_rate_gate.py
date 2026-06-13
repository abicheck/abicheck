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


def test_fp_rate_within_baseline():
    outcome = fp_gate.evaluate()
    assert len(outcome.false_positives) <= fp_gate.FP_BASELINE, outcome.false_positives
    assert len(outcome.false_negatives) <= fp_gate.FN_BASELINE, outcome.false_negatives


def test_metrics_report_delta_vs_baseline():
    """ADR-033 D9: the gate exposes false_positive_delta_vs_baseline (0 = clean)."""
    m = fp_gate.metrics()
    assert m["false_positive_delta_vs_baseline"] == m["false_positives"] - fp_gate.FP_BASELINE
    assert m["false_negative_delta_vs_baseline"] == m["false_negatives"] - fp_gate.FN_BASELINE
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
