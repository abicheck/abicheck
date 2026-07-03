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

"""Fast-lane mirror for the per-evidence-tier accuracy gate.

The gate logic lives in ``scripts/check_tier_accuracy.py`` (runnable standalone
in CI); this mirrors it into the unit suite so a regression in *what each
evidence level buys* is caught in the ordinary lane too.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parent.parent / "scripts" / "check_tier_accuracy.py"
_spec = importlib.util.spec_from_file_location("check_tier_accuracy", _GATE)
assert _spec and _spec.loader
tier_gate = importlib.util.module_from_spec(_spec)
sys.modules["check_tier_accuracy"] = tier_gate
_spec.loader.exec_module(tier_gate)

Tier = tier_gate.Tier


@pytest.fixture(scope="module")
def trajectories():
    return tier_gate.evaluate()


@pytest.mark.parametrize("case", tier_gate.CORPUS, ids=lambda c: c.name)
def test_case_is_correct_at_its_top_tier(case):
    """With full (top-tier) evidence the tool reaches the ground-truth band."""
    old, new = case.build()
    assert tier_gate.band_at(old, new, case.top_tier) == case.expected_band, (
        f"{case.name}: wrong verdict band with full evidence"
    )


def test_no_top_tier_mismatches(trajectories):
    assert tier_gate.top_tier_mismatches(trajectories) == []


def test_under_call_monotonicity(trajectories):
    """More evidence never *hides* a real break a lower tier already caught
    (authority rule, ADR-028 D3)."""
    assert tier_gate.under_call_monotonicity_violations(trajectories) == []


def test_lower_levels_are_demonstrably_insufficient(trajectories):
    """The representative-example guarantee: some real breaks are invisible to a
    stripped binary (L0 under-calls at least one true break)."""
    l0 = tier_gate.per_tier_counts(trajectories)["L0"]
    assert l0["under"] >= 1, "corpus must contain L0-insufficiency (FN) examples"
    # ...and at least one break needs more than debug info — only headers or
    # build context resolve it (an under-call surviving past L1).
    assert any(
        t.outcome(Tier.L1) == "under" for t in trajectories
    ), "corpus must contain a break L1 still cannot see"


def test_each_higher_level_reduces_false_positives(trajectories):
    """The headline: the scoping layer (L1->L2) removes over-calls (FPs) that the
    layout-only layer raises, and no transition *introduces* an unresolved FP at
    the top tier."""
    resolved = tier_gate.resolved_by_transition(trajectories)
    assert "L1->L2" in resolved, "L2 scoping must resolve at least one L1 over-call"
    assert len(resolved["L1->L2"]["fp_removed"]) >= 1
    # The scoping tier itself must not over-call anything.
    counts = tier_gate.per_tier_counts(trajectories)
    assert counts["L2"]["over"] == 0


def test_build_context_catches_what_artifacts_cannot(trajectories):
    """L2->L3: a risk only the build context reveals (cross-impl same-size)."""
    resolved = tier_gate.resolved_by_transition(trajectories)
    assert "L2->L3" in resolved
    assert "cross_stdlib_same_size" in resolved["L2->L3"]["fn_removed"]


# --- projection faithfulness (the model each tier observes) -------------------


def test_l0_projection_strips_types_and_signatures():
    old, _ = tier_gate.CORPUS[0].build()
    p = tier_gate.project(old, Tier.L0)
    assert p.types == [] and p.enums == []
    assert p.elf_only_mode is True and p.from_headers is False
    assert all(f.return_type == "?" and not f.params for f in p.functions)


def test_l1_projection_keeps_layout_but_drops_header_scope():
    old, _ = tier_gate.CORPUS[0].build()
    p = tier_gate.project(old, Tier.L1)
    assert p.types, "L1 must retain type layout"
    assert p.from_headers is False
    from abicheck.model import ScopeOrigin, Visibility

    assert all(f.visibility == Visibility.ELF_ONLY for f in p.functions)
    assert all(t.origin == ScopeOrigin.UNKNOWN for t in p.types)


def test_l3_projection_retains_build_mode():
    old, _ = tier_gate.CORPUS[-1].build()  # cross_stdlib case carries build_mode
    assert tier_gate.project(old, Tier.L2).build_mode is None
    assert tier_gate.project(old, Tier.L3).build_mode is not None


def test_markdown_and_json_render(trajectories):
    md = tier_gate.render_markdown(trajectories)
    assert "Per-tier accuracy" in md and "FP" in md and "FN" in md
    m = tier_gate.metrics(trajectories)
    assert m["top_tier_mismatches"] == []
    assert "resolved_by_transition" in m


def test_gate_main_exits_zero():
    assert tier_gate.main([]) == 0
    assert tier_gate.main(["--json"]) == 0
    assert tier_gate.main(["--markdown"]) == 0
