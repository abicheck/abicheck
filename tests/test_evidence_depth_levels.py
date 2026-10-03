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

"""Tests for the ADR-035 depth/method vocabulary (G19.3).

Pins the lossy depth→S map, the S→collect-mode and S→depth maps, and the
one owner of "explicit ``--depth`` → collect mode"
(:func:`collect_mode_for_depth`), which ``dump``, ``compare``'s typed
pipeline and the planner each used to compute with their own copy.
Pure-Python, default lane.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model.evidence_depth_levels import (
    EvidenceDepth,
    SourceMethod,
    collect_mode_for_depth,
    depth_to_method,
    level_to_collect_mode,
    method_to_collect_mode,
    method_to_depth,
)

#: The oracle, written from ADR-033 D2 / ADR-043 D3 rather than derived from
#: the module's own tables: no source method below ``build``; ``source``
#: replays the target's units; ``graph`` is S4's graph-only build, ``full``
#: S6's full graph collection.
_EXPECTED_COLLECT_MODE: dict[str, str] = {
    "binary": "off",
    "headers": "off",
    "build": "build",
    "source": "source-target",
    "graph": "graph-build",
    "full": "graph-full",
}


def _spellings(value: str) -> set[str]:
    """Every upper/lower-case spelling of *value* (small exhaustive domain)."""
    return {
        "".join(chars)
        for chars in itertools.product(*((c.lower(), c.upper()) for c in value))
    }


def test_oracle_covers_every_depth() -> None:
    assert set(_EXPECTED_COLLECT_MODE) == {d.value for d in EvidenceDepth}


@pytest.mark.parametrize("depth", sorted(_EXPECTED_COLLECT_MODE))
def test_collect_mode_for_depth_matches_the_documented_mapping(depth: str) -> None:
    expected = _EXPECTED_COLLECT_MODE[depth]
    for spelling in _spellings(depth):
        assert collect_mode_for_depth(spelling) == expected, spelling


@pytest.mark.parametrize("depth", sorted(_EXPECTED_COLLECT_MODE))
def test_every_explicit_depth_resolver_agrees_with_the_owner(depth: str) -> None:
    """``dump``, ``compare``'s typed pipeline and the planner answer the same
    question; each must give the owner's answer for every explicit depth."""
    from abicheck.cli_dump_depth import resolve_dump_depth
    from abicheck.service_compare_evidence import _resolve_depth_collect_mode
    from abicheck.workflows.plan import _depth_implied_collect_mode

    expected = _EXPECTED_COLLECT_MODE[depth]
    for spelling in _spellings(depth):
        assert resolve_dump_depth(spelling, "source-target") == expected
        assert _resolve_depth_collect_mode(spelling, "off") == expected
        assert _depth_implied_collect_mode(spelling) == expected


def test_omitted_depth_keeps_each_command_default() -> None:
    from abicheck.cli_dump_depth import resolve_dump_depth
    from abicheck.service_compare_evidence import _resolve_depth_collect_mode

    assert resolve_dump_depth(None, "source-target") == "source-target"
    assert _resolve_depth_collect_mode(None, "off") == "off"


def test_unknown_depth_is_rejected() -> None:
    with pytest.raises(ValueError):
        collect_mode_for_depth("symbols")


def test_s4_graph_only_avoids_l4_replay():
    assert method_to_collect_mode(SourceMethod.S4) == "graph-build"
    assert level_to_collect_mode(SourceMethod.S4, EvidenceDepth.GRAPH) == "graph-build"


def test_depth_headers_reaches_no_method():
    # --depth headers reaches no S-method (L2 is intrinsic); only S0/S3 always-on.
    assert depth_to_method(EvidenceDepth.HEADERS) is None


def test_depth_map_is_lossy_cannot_reach_s2_or_s3():
    reachable = {depth_to_method(d) for d in EvidenceDepth}
    assert SourceMethod.S2 not in reachable
    assert SourceMethod.S3 not in reachable


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
