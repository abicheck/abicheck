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

"""Fast-lane mirror of ADR-049 Phase 3's shadow-evaluator gate.

The gate logic lives in ``scripts/measure_contract_shadow.py`` so CI can run
it standalone and archive the measurement; this mirrors it into the pytest
suite -- per case and per domain, so a failure names the case rather than a
total -- following the same pattern ``test_fp_rate_gate.py`` uses for the
FP-rate gate.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("check_fp_rate")  # measure_contract_shadow imports the corpus from it
shadow = _load("measure_contract_shadow")


_CASES = [
    pytest.param(case, mode, id=f"{case.name}-{mode.value}")
    for case in shadow.CORPUS
    for mode in shadow.MEASURED_MODES
]


@pytest.mark.parametrize("case,mode", _CASES)
def test_no_proven_public_break_loss(case, mode) -> None:
    """The number that must stay zero before Phase 7 can flip the default.

    A real-break case whose genuinely breaking, legacy-kept finding the
    shadow evaluator proves out of contract is a break the new gate would
    drop.
    """
    measurement = shadow.measure_case(case, mode)
    assert measurement.proven_losses == [], (
        f"shadow evaluator would lose a real public break in {case.name!r} "
        f"under contract={mode.value}: {measurement.proven_losses}"
    )


@pytest.mark.parametrize("case,mode", _CASES)
def test_every_delta_carries_resolvable_evidence(case, mode) -> None:
    """Phase 3's gate: "every shadow delta has evidence and stable identity".

    A delta is a finding the shadow evaluator would treat differently from
    the legacy gate. Each must cite at least one provider record, and every
    cited id must exist in the persisted ``contract_evidence`` block -- a
    dangling reference is indistinguishable from a record that failed to
    serialize, so it does not count as evidence.
    """
    measurement = shadow.measure_case(case, mode)
    assert measurement.unevidenced_deltas == [], (
        f"unevidenced shadow delta(s) in {case.name!r} under "
        f"contract={mode.value}: {measurement.unevidenced_deltas}"
    )


@pytest.mark.parametrize("case,mode", _CASES)
def test_no_unexplained_fact_loss(case, mode) -> None:
    """Phase 3's gate: "zero unexplained fact loss".

    Every finding the comparison produced -- kept or demoted out of surface
    -- must carry a stamped decision *and* appear in the persisted decision
    receipt. A finding that passes through the shadow evaluator without
    leaving a record is exactly the silent gap the receipt exists to close.
    """
    measurement = shadow.measure_case(case, mode)
    assert measurement.fact_losses == [], (
        f"finding(s) with no recorded contract decision in {case.name!r} "
        f"under contract={mode.value}: {measurement.fact_losses}"
    )


def test_metrics_report_the_four_measured_quantities() -> None:
    """The measurement itself is part of the deliverable, not just the gate.

    Phase 3's "Measure:" list names four quantities; this asserts the script
    actually reports all four (delta matrix, unresolved rate by
    provider/domain/platform, proven losses, proven FP reductions) rather
    than only the pass/fail counters.
    """
    modes = shadow.metrics()["modes"]
    for mode, row in modes.items():
        assert row["delta_matrix"] is not None, mode
        assert "unresolved_rate" in row
        assert "unresolved_by_provider_state" in row
        assert "unresolved_by_platform" in row
        assert "proven_public_break_losses" in row
        assert "proven_false_positive_reductions" in row


def test_public_domain_proves_some_internal_noise_out_of_contract() -> None:
    """The phase's *purpose*, measured rather than asserted.

    If the shadow evaluator proved nothing out of contract on a corpus built
    around internal noise, the gate above would pass vacuously -- zero losses
    is trivial for an evaluator that never concludes anything.
    """
    public = shadow.metrics()["modes"]["public"]
    assert public["proven_false_positive_reductions"], (
        "no internal-noise finding was proven out of contract under "
        "contract=public -- the loss gate would be vacuous"
    )


def test_audit_bucket_findings_are_measured_too(monkeypatch) -> None:
    """``checker`` stamps and records five collections, not two.

    ``measure_case`` walked only ``changes``/``out_of_surface_changes``, so a
    regression dropping a decision (or a receipt entry) for a *suppressed*,
    *redundant*, or *reconciled* finding still reported zero fact losses --
    the gate was blind to three of the five buckets it claims to cover
    (Codex review, fresh evidence). The corpus supplies no case that
    populates them, so this drives the buckets directly.
    """
    from abicheck.contract_relevance_types import ContractRelevance

    case = next(c for c in shadow.CORPUS if not c.internal_noise)
    mode = shadow.MEASURED_MODES[0]
    real = shadow.measure_case(case, mode)
    assert real.findings, "fixture must produce at least one decided finding"

    for bucket in ("suppressed_changes", "redundant_changes", "reconciled_changes"):
        captured: list = []

        def _relocating_compare(*args, _bucket=bucket, _seen=captured, **kwargs):
            from abicheck.checker import compare as _compare

            result = _compare(*args, **kwargs)
            source = result.changes or result.out_of_surface_changes
            moved = source.pop()
            _seen.append(moved)
            getattr(result, _bucket).append(moved)
            return result

        monkeypatch.setattr(shadow, "compare", _relocating_compare)
        measurement = shadow.measure_case(case, mode)
        assert captured, bucket
        # Relocating a finding must not lose it: same total, still no fact
        # loss, and its decision now counted under the bucket's own row.
        assert measurement.findings == real.findings, bucket
        assert measurement.fact_losses == [], bucket
        relocated = captured[0]
        assert relocated.contract_relevance is not None, bucket
        state = {
            "suppressed_changes": shadow.LEGACY_SUPPRESSED,
            "redundant_changes": shadow.LEGACY_REDUNDANT,
            "reconciled_changes": shadow.LEGACY_RECONCILED,
        }[bucket]
        assert measurement.delta_matrix.get(state), bucket
        # An audit bucket carries no legacy contract claim, so a decision
        # there is never a delta -- not even a conclusive one.
        assert not shadow._is_delta(state, ContractRelevance.IN_CONTRACT)
        assert not shadow._is_delta(state, ContractRelevance.PROVEN_OUT_OF_CONTRACT)


def test_render_markdown_covers_every_domain() -> None:
    text = shadow.render_markdown(shadow.metrics())
    for mode in shadow.MEASURED_MODES:
        assert f"| {mode.value} |" in text
