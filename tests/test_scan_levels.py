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

"""Tests for the ADR-035 D1/D3 deterministic level resolution (G19.3).

Pins the precedence rule (--source-method wins over --depth wins over --mode
preset), the lossy depth→S map, the AUTO opt-in path, and the S→collect-mode
mapping. Pure-Python, default lane.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.scan_levels import (
    EvidenceDepth,
    ScanMode,
    SourceMethod,
    depth_to_method,
    level_to_collect_mode,
    method_to_collect_mode,
    method_to_depth,
    mode_preset,
    resolve_level,
    resolve_source_method,
)


def test_pr_and_pr_deep_resolve_to_distinct_levels():
    # pr-deep must not collapse to the same level as pr (Codex review).
    pr_method, pr_depth = resolve_level(mode=ScanMode.PR)
    deep_method, deep_depth = resolve_level(mode=ScanMode.PR_DEEP)
    assert (pr_method, pr_depth) == (SourceMethod.S5, EvidenceDepth.SOURCE)
    assert (deep_method, deep_depth) == (SourceMethod.S5, EvidenceDepth.GRAPH)
    pr_mode = level_to_collect_mode(pr_method, pr_depth)
    deep_mode = level_to_collect_mode(deep_method, deep_depth)
    assert pr_mode == "source-changed"
    assert deep_mode == "graph-full"
    assert pr_mode != deep_mode


def test_resolve_level_explicit_source_method_reports_its_depth():
    method, depth = resolve_level(mode=ScanMode.PR, source_method=SourceMethod.S6)
    assert method is SourceMethod.S6
    assert depth is EvidenceDepth.FULL


def test_resolve_level_explicit_depth_is_verbatim():
    method, depth = resolve_level(mode=ScanMode.PR, depth=EvidenceDepth.GRAPH)
    assert method is SourceMethod.S4
    assert depth is EvidenceDepth.GRAPH
    # S4 is graph-only — graph-build (L3+L5), NOT the L4-replaying graph-summary.
    assert level_to_collect_mode(method, depth) == "graph-build"


def test_s4_graph_only_avoids_l4_replay():
    assert method_to_collect_mode(SourceMethod.S4) == "graph-build"


@pytest.mark.parametrize(
    ("mode", "expected_method"),
    [
        (ScanMode.PR, SourceMethod.S5),
        (ScanMode.PR_DEEP, SourceMethod.S5),
        (ScanMode.BASELINE, SourceMethod.S6),
        (ScanMode.AUDIT, SourceMethod.S5),
    ],
)
def test_mode_presets_are_fixed(mode, expected_method):
    method, _depth = mode_preset(mode)
    assert method is expected_method
    # The preset is also what resolve returns with no explicit overrides.
    assert resolve_source_method(mode=mode) is expected_method


def test_source_method_wins_over_depth_and_mode():
    resolved = resolve_source_method(
        mode=ScanMode.BASELINE,
        source_method=SourceMethod.S1,
        depth=EvidenceDepth.FULL,
    )
    assert resolved is SourceMethod.S1


def test_depth_wins_over_mode_when_no_source_method():
    resolved = resolve_source_method(mode=ScanMode.PR, depth=EvidenceDepth.BUILD)
    assert resolved is SourceMethod.S1


def test_depth_headers_resolves_to_s0():
    # --depth headers reaches no S-method (L2 is intrinsic); only S0/S3 always-on.
    assert depth_to_method(EvidenceDepth.HEADERS) is None
    assert (
        resolve_source_method(mode=ScanMode.PR, depth=EvidenceDepth.HEADERS)
        is SourceMethod.S0
    )


def test_depth_map_is_lossy_cannot_reach_s2_or_s3():
    reachable = {depth_to_method(d) for d in EvidenceDepth}
    assert SourceMethod.S2 not in reachable
    assert SourceMethod.S3 not in reachable


def test_auto_uses_supplied_risk_method():
    resolved = resolve_source_method(
        mode=ScanMode.PR,
        source_method=SourceMethod.AUTO,
        auto_method="s3",
    )
    assert resolved is SourceMethod.S3


def test_auto_without_risk_method_falls_back_to_mode_preset():
    resolved = resolve_source_method(
        mode=ScanMode.BASELINE,
        source_method=SourceMethod.AUTO,
    )
    assert resolved is SourceMethod.S6


def test_method_to_collect_mode_maps_every_concrete_method():
    for method in SourceMethod:
        if method is SourceMethod.AUTO:
            continue
        # Each concrete method maps to a known CI evidence mode string.
        assert method_to_collect_mode(method)


def test_method_to_collect_mode_rejects_auto():
    with pytest.raises(ValueError):
        method_to_collect_mode(SourceMethod.AUTO)


def test_method_to_depth_reports_resolved_depth_not_request():
    # An explicit S-method must report ITS depth, not the mode preset (Codex).
    assert method_to_depth(SourceMethod.S1) is EvidenceDepth.BUILD
    assert method_to_depth(SourceMethod.S6) is EvidenceDepth.FULL
    assert method_to_depth(SourceMethod.S0) is EvidenceDepth.HEADERS


def test_method_to_depth_maps_every_concrete_method():
    for method in SourceMethod:
        if method is SourceMethod.AUTO:
            continue
        assert isinstance(method_to_depth(method), EvidenceDepth)


def test_method_to_depth_rejects_auto():
    with pytest.raises(ValueError):
        method_to_depth(SourceMethod.AUTO)


def test_lexical_methods_collect_no_inline_pack():
    # S0/S3 are covered by the always-on pattern scan; no inline L3-L5 collection.
    assert method_to_collect_mode(SourceMethod.S0) == "off"
    assert method_to_collect_mode(SourceMethod.S3) == "off"


def test_semantic_methods_select_replay_scopes():
    assert method_to_collect_mode(SourceMethod.S5) == "source-changed"
    assert method_to_collect_mode(SourceMethod.S6) == "graph-full"
