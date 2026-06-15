# Copyright 2026 Nikolay Petrov
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

"""Tests for the ADR-035 D10 per-level provider protocol contract."""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.poi import build_points_of_interest
from abicheck.buildsource.providers import (
    LayerFacts,
    LayerProvider,
    ProviderCapabilities,
    ProviderCostEstimate,
    ScanContext,
)
from abicheck.buildsource.risk import RiskRules, score_changed_paths
from abicheck.model import AbiSnapshot


class _FakeProvider:
    """A minimal conforming provider — the contract a real level implements."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(method="s5", layer="L4_source_abi")

    def estimate(self, ctx: ScanContext) -> ProviderCostEstimate:
        return ProviderCostEstimate(method="s5", layer="L4_source_abi", tus=3)

    def run(self, ctx: ScanContext, poi) -> LayerFacts:
        return LayerFacts(method="s5", layer="L4_source_abi", status="present", facts=3)


def _ctx() -> ScanContext:
    return ScanContext(snapshot=AbiSnapshot(library="l", version="1"))


def test_fake_provider_satisfies_runtime_protocol():
    assert isinstance(_FakeProvider(), LayerProvider)


def test_provider_estimate_and_run_roundtrip():
    prov = _FakeProvider()
    ctx = _ctx()
    poi = build_points_of_interest(
        changed_paths=[], risk=score_changed_paths([], RiskRules.default())
    )
    est = prov.estimate(ctx)
    assert est.method == "s5" and est.tus == 3
    facts = prov.run(ctx, poi)
    assert facts.status == "present" and facts.facts == 3
    assert facts.layer == "L4_source_abi"


def test_scan_context_carries_readonly_inputs():
    ctx = ScanContext(
        snapshot=AbiSnapshot(library="l", version="1"),
        compile_db=Path("compile_commands.json"),
        changed_paths=("src/a.cc",),
        budget_seconds=30.0,
    )
    assert ctx.changed_paths == ("src/a.cc",)
    assert ctx.budget_seconds == 30.0


def test_non_conforming_object_is_not_a_provider():
    class _Missing:
        def capabilities(self) -> ProviderCapabilities:  # missing estimate/run
            return ProviderCapabilities(method=None, layer="L0_binary")

    assert not isinstance(_Missing(), LayerProvider)
