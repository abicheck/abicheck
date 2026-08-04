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

from abicheck.contract_relevance_types import (
    CompatibilityEvaluationStatus,
    ContractRelevance,
    evaluation_status_for,
)

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("check_fp_rate")  # measure_contract_shadow imports the corpus from it
platform_corpus = _load("contract_platform_corpus")
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


@pytest.mark.parametrize("case,mode", _CASES)
def test_replay_never_out_claims_the_live_decision(case, mode) -> None:
    """The corpus-level counterpart of the replay soundness unit tests.

    Every soundness defect this feature has had was a *replay* that decided
    more strongly than the live run that wrote it, and until this gate the
    only signal was hand-written cases -- so a regression was found by
    reviewers rather than by CI. Verified non-vacuous by deliberately
    regressing the ambiguity gate, which makes it fire (self-review).
    """
    measurement = shadow.measure_case(case, mode)
    assert measurement.replay_strengthenings == [], (
        f"replay out-claimed the live decision in {case.name!r} under "
        f"contract={mode.value}: {measurement.replay_strengthenings}"
    )


def test_the_corpus_contains_an_ambiguous_identity_case() -> None:
    """The soundness gate above needs a pair whose identity is ambiguous.

    Without one it passes for every implementation, correct or not: the
    other 32 cases resolve unambiguously, so no replay decision can differ.
    Pinned as its own assertion because "the gate is green" and "the gate
    can fail" are different claims (self-review).
    """
    names = {case.name for case in shadow.CORPUS}
    assert "ambiguous_namespaced_leaf" in names


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
        assert "unresolved_by_lane" in row
        assert "proven_public_break_losses" in row
        assert "proven_false_positive_reductions" in row


class TestUnresolvedLossMetric:
    """A real break withheld from the gate because the decision could not
    *resolve* it -- the same failure mode ``proven_public_break_losses``
    covers, reached through ``UNKNOWN_*`` instead of
    ``PROVEN_OUT_OF_CONTRACT``."""

    def test_every_domain_is_within_its_own_budget(self) -> None:
        gate = shadow.metrics()["gate"]
        counts = gate["unresolved_public_break_losses"]
        assert set(counts) == set(shadow.UNRESOLVED_LOSS_BASELINE)
        for domain, count in counts.items():
            assert count <= shadow.UNRESOLVED_LOSS_BASELINE[domain], domain

    def test_the_metric_is_not_vacuous(self) -> None:
        # A loss metric that cannot fire reads exactly like "no losses". The
        # `exports` domain has a standing non-zero budget precisely because
        # that corpus carries no export tables, so it is the executable proof
        # that the classification path is reachable at all.
        counts = shadow.metrics()["gate"]["unresolved_public_break_losses"]
        assert counts["exports"] > 0

    def test_withheld_matches_the_engine_rather_than_a_second_list(self) -> None:
        # The one property that makes this metric trustworthy: it must call
        # exactly the relevance values withheld a loss, so it cannot measure
        # a rule the gate stopped applying.
        for relevance in ContractRelevance:
            assert shadow._withheld_from_gate(relevance) is (
                evaluation_status_for(relevance)
                is CompatibilityEvaluationStatus.NOT_EVALUATED
            )

    def test_public_losses_are_pinned_by_identity_not_only_count(self) -> None:
        # A budget alone cannot tell "the accepted gaps are still the
        # accepted gaps" from "one was fixed and a different case regressed"
        # -- the total is the same either way (Codex review).
        measured = shadow.metrics()["gate"]["unresolved_public_break_cases"]
        assert sorted(measured["public"]) == sorted(
            shadow.UNRESOLVED_LOSS_KNOWN_PUBLIC_CASES
        )

    def test_the_known_list_is_consistent_with_the_budget(self) -> None:
        # Two statements of the same fact; if they drift, one of them is
        # lying about what is accepted.
        assert len(shadow.UNRESOLVED_LOSS_KNOWN_PUBLIC_CASES) == (
            shadow.UNRESOLVED_LOSS_BASELINE["public"]
        )

    def test_merge_carries_every_list_accumulator(self) -> None:
        # `_merge` used to name each accumulator by hand and silently dropped
        # `unresolved_losses` when it was added: `measure_case` classified
        # correctly and `measure` still reported zero. Deriving the set from
        # the dataclass is the fix; this pins it, so a future accumulator
        # cannot regress the same way.
        assert "unresolved_losses" in shadow._LIST_ACCUMULATORS
        total = shadow.ModeMeasurement(mode="public")
        one = shadow.ModeMeasurement(mode="public")
        for name in shadow._LIST_ACCUMULATORS:
            getattr(one, name).append(f"sentinel:{name}")
        shadow._merge(total, one)
        for name in shadow._LIST_ACCUMULATORS:
            assert getattr(total, name) == [f"sentinel:{name}"], name


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


class TestPhase6CorpusCoverage:
    """ADR-049 Phase 6's Gate names a corpus, not just a number.

    The measurement originally ran one corpus -- the FP-rate corpus, which
    by construction carries no export tables -- so the `exports` domain was
    100% unresolved on every case and its "measured and accepted unresolved
    rate" measured only the absence of evidence.
    `scripts/contract_platform_corpus.py` supplies the ELF/PE/Mach-O,
    stripped, versioned, and C lanes the Gate names; these tests assert the
    lanes are actually reached and actually change the answer, so a future
    refactor cannot quietly drop them back to the single corpus.
    """

    def test_every_declared_lane_is_actually_measured(self) -> None:
        modes = shadow.metrics()["modes"]
        measured = {
            lane for row in modes.values() for lane in row["unresolved_by_lane"]
        }
        declared = set(platform_corpus.CASE_LANE.values())
        assert declared <= measured, sorted(declared - measured)
        # The FP-rate corpus keeps its own row rather than being folded in:
        # the two answer different questions and their rates must stay
        # separable.
        assert "fp_corpus" in measured

    def test_the_exports_domain_resolves_where_an_export_table_exists(self) -> None:
        """The reason this corpus exists. Every lane that carries a real
        export table must resolve under `exports`; the FP-rate corpus, which
        carries none, is the one that does not -- and that contrast is the
        honest signal, not a number to be improved away."""
        lanes = shadow.metrics()["modes"]["exports"]["unresolved_by_lane"]
        # Derived from `CASE_LANE`, not hardcoded: a lane added there would
        # otherwise keep passing this invariant without ever being checked
        # against it (CodeRabbit review) -- the same source of truth the
        # neighbouring declared-lane test already uses.
        for lane in sorted(set(platform_corpus.CASE_LANE.values())):
            counts = lanes[lane]
            assert counts["resolved"], f"{lane} resolved nothing under exports"
            # And nothing *un*resolved: a lane carrying a complete export
            # table has no excuse for an unresolved finding, and one
            # appearing means the fixture invented a spelling no real
            # snapshot can hold (Codex review found exactly that on macho).
            assert counts["unresolved"] == 0, f"{lane} left findings unresolved"
        assert lanes["fp_corpus"]["resolved"] == 0

    def test_the_exports_domain_proves_something_out_of_contract(self) -> None:
        """Without this the `exports` domain could pass every gate while
        concluding nothing -- the same vacuity guard the `public` domain
        already has, for the domain the platform corpus was added for."""
        exports = shadow.metrics()["modes"]["exports"]
        assert exports["proven_false_positive_reductions"], (
            "no finding was proven out of contract under contract=exports -- "
            "the domain's own gate would be vacuous"
        )

    def test_a_lane_the_two_domains_disagree_on_exists(self) -> None:
        """A corpus where every case answers identically in every domain
        would measure the plumbing, not the domains. At least one case must
        be `IN_CONTRACT` under `public` and proven out under `exports`."""
        modes = shadow.metrics()["modes"]
        assert modes["public"]["deltas"] == 0  # header scoping agrees with itself
        assert modes["exports"]["deltas"] > 0

    def test_uncovered_lanes_are_named_with_a_reason(self) -> None:
        """Phase 6's Gate lists lanes this measurement cannot reach. Naming
        them (with why) is what keeps the coverage claim bounded by what was
        run rather than by what the list mentions."""
        uncovered = shadow.metrics()["uncovered_lanes"]
        assert uncovered, "the coverage claim must state its own limits"
        assert all(reason.strip() for reason in uncovered.values())
        # Neither may silently become a lane name the corpus also claims to
        # cover -- that would be two contradictory statements about one lane.
        assert not (set(uncovered) & set(platform_corpus.CASE_LANE.values()))

    def test_the_markdown_summary_renders_the_lane_table(self) -> None:
        rendered = shadow.render_markdown(shadow.metrics())
        assert "Unresolved rate by lane" in rendered
        assert "Lanes not covered by this measurement" in rendered
        for lane in sorted(set(platform_corpus.CASE_LANE.values())):
            assert f"| {lane} " in rendered or f" {lane} |" in rendered
