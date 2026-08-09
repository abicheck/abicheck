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

"""ADR-052 D2 follow-up (G29 Phase 3 slice 10): impact_assessment caching
audit for ``buildsource/source_graph_findings.py``.

Split out of ``test_source_graph.py`` rather than appended there (it was
already at the 2000-line hard cap; see ``scripts/check_ai_readiness.py``'s
``file-size`` check) -- mirrors the existing convention of a small,
topic-scoped sibling test file rather than growing an already-large one.

``diff_source_graph_findings()``'s ten ``Change(...)`` construction sites
(across its nine per-family helpers) do **not** eagerly cache
``impact_assessment`` the way ``internal_leak.py``/``appcompat.py``'s
builders do (Slice 8/9) -- the pipeline-ordering audit in
``source_graph_findings.py``'s own comments (see ``_mapping_drift_findings``)
found ``post_processing.MarkReachability`` runs *downstream* of these
builders (their output is merged into ``checker.compare()``'s ``changes``
before ``_run_post_processing``/``DEFAULT_PIPELINE.run()``, unlike
``internal_leak.py``'s builders, which are themselves a *later* pipeline
step), so caching at construction time would freeze a stale, unset default.
``MarkReachability`` itself now caches the assessment once it finalizes
those fields (``post_processing_reachability.py``), so these findings still
end up correctly cached once they reach that step -- these tests cover both
halves of that finding.
"""

from __future__ import annotations

from abicheck.buildsource.build_evidence import BuildEvidence, Confidence, Target
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_graph import build_source_graph
from abicheck.buildsource.source_graph_findings import diff_source_graph_findings
from abicheck.checker_policy import ChangeKind, ReachabilityState
from abicheck.impact.engine import assess_change
from abicheck.model import AbiSnapshot
from abicheck.post_processing import DEFAULT_PIPELINE
from abicheck.suppression import Suppression, SuppressionList


def _surface_with(decls, mapping, *, target="target://libfoo") -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id=target)
    for qn, path in decls:
        s.reachable_declarations.append(
            SourceEntity(
                id=qn,
                kind="function",
                qualified_name=qn,
                source_location=SourceLocation(
                    path=path, line=1, origin="PUBLIC_HEADER"
                ),
                visibility="public_header",
                confidence=Confidence.HIGH,
            )
        )
    s.mappings["source_decl_to_binary_symbol"] = dict(mapping)
    return s


def _build_with_public_header(headers=("inc/foo.h",)) -> BuildEvidence:
    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=list(headers),
            confidence=Confidence.HIGH,
        )
    )
    return b


def _mapping_drift_findings():
    b = _build_with_public_header()
    old = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb"})
    )
    new = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb2"})
    )
    return diff_source_graph_findings(old, new)


def _needs_evidence_suppression() -> SuppressionList:
    return SuppressionList(
        [Suppression(namespace="__never_matches__::*", reason="evidence trigger only")]
    )


def test_findings_are_not_eagerly_cached_with_impact_assessment() -> None:
    """Right after diff_source_graph_findings() returns, none of its findings
    carry a cached impact_assessment -- confirming the audit above rather
    than assuming it."""
    findings = _mapping_drift_findings()
    assert findings
    assert all(c.impact_assessment is None for c in findings)


def test_source_graph_finding_gets_cached_assessment_once_it_reaches_mark_reachability() -> (
    None
):
    """The regression test for the pipeline-ordering safety property: once a
    source_graph_findings.py Change is merged into an ordinary compare()-style
    changes list and run through post_processing.DEFAULT_PIPELINE (with a
    suppression that needs reachability evidence, mirroring how a real
    `compare` invocation feeds `extra_changes` in before post-processing),
    MarkReachability tags it AND caches a matching impact_assessment -- and
    that cached assessment reflects the *post*-tag value
    (SOURCE_TO_BINARY_MAPPING_CHANGED is in _PUBLIC_SOURCE_ABI_KINDS, so it is
    always tagged PROVEN_REACHABLE), never a pre-tag default.
    """
    findings = _mapping_drift_findings()
    assert findings
    assert findings[0].kind == ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED

    old_snap = AbiSnapshot(library="libtest.so", version="1.0")
    new_snap = AbiSnapshot(library="libtest.so", version="2.0")
    ctx = DEFAULT_PIPELINE.run(
        list(findings), old_snap, new_snap, suppression=_needs_evidence_suppression()
    )
    found = [
        c for c in ctx.kept if c.kind == ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED
    ]
    assert len(found) == 1
    change = found[0]
    assert change.public_reachable is True
    assert change.reachability_state == ReachabilityState.PROVEN_REACHABLE
    assert change.impact_assessment is not None
    cached = change.impact_assessment
    assert cached.reachability_state == ReachabilityState.PROVEN_REACHABLE
    # Matches a genuinely uncached derivation from the same (now post-tag)
    # flat fields -- the two paths never disagree. assess_change() prefers
    # change.impact_assessment when it's non-None, so clear it first or
    # this comparison would just read the cache under test.
    change.impact_assessment = None
    fresh = assess_change(change)
    assert cached.reachability_state == fresh.reachability_state
    assert cached.public_reachable == fresh.public_reachable


def test_no_suppression_leaves_findings_uncached_same_as_before() -> None:
    """MarkReachability's own no-op gate (no suppression configured) means
    these findings stay uncached -- unchanged pre-slice-10 behavior."""
    findings = _mapping_drift_findings()
    old_snap = AbiSnapshot(library="libtest.so", version="1.0")
    new_snap = AbiSnapshot(library="libtest.so", version="2.0")
    ctx = DEFAULT_PIPELINE.run(list(findings), old_snap, new_snap, suppression=None)
    found = [
        c for c in ctx.kept if c.kind == ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED
    ]
    assert len(found) == 1
    assert found[0].impact_assessment is None
