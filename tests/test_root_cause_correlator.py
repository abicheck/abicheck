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

"""Tests for ``RootCauseCorrelator`` (G29 Phase 6)."""

from __future__ import annotations

import hashlib

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.impact.correlation import (
    EVIDENCE_LEVEL_BY_KIND,
    RootCauseGroup,
    correlate_root_causes,
)


def _change(**overrides: object) -> Change:
    base: dict[str, object] = {
        "kind": ChangeKind.FUNC_REMOVED,
        "symbol": "ns::internal::helper",
        "description": "helper removed",
    }
    base.update(overrides)
    return Change(**base)  # type: ignore[arg-type]


class TestCorrelateRootCauses:
    def test_empty_input(self) -> None:
        assert correlate_root_causes([]) == []

    def test_uncorrelated_kinds_are_ignored(self) -> None:
        changes = [
            _change(kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="foo"),
            _change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="foo"),
        ]
        assert correlate_root_causes(changes) == []

    def test_singleton_family_member_is_not_a_group(self) -> None:
        # Only one of the four correlated kinds present, with no sibling to
        # join -- a lone piece is not "root-cause information", it's just
        # the finding itself (mirrors reporter_markdown's own singleton
        # exclusion for root_cause_for_change).
        changes = [_change(kind=ChangeKind.FUNC_REMOVED, symbol="internal_helper")]
        assert correlate_root_causes(changes) == []

    def test_two_piece_correlation_via_caused_by_type(self) -> None:
        removed = _change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="internal_helper",
            description="internal_helper removed",
        )
        leaked = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="internal_helper",
            description="internal_helper required by public entry",
            caused_by_type="internal_helper",
        )
        groups = correlate_root_causes([removed, leaked])
        assert len(groups) == 1
        group = groups[0]
        assert isinstance(group, RootCauseGroup)
        assert group.root_display == "internal_helper"
        assert (
            group.root_cause_id == hashlib.sha256(b"internal_helper").hexdigest()[:16]
        )
        assert [c.kind for c, _ in group.members] == [removed.kind, leaked.kind]
        assert group.evidence_levels == ("artifact_proven", "call_graph_proven")
        assert group.strongest_evidence_level == "call_graph_proven"

    def test_three_piece_correlation_ranks_consumer_proven_above_call_graph(
        self,
    ) -> None:
        removed = _change(kind=ChangeKind.FUNC_REMOVED, symbol="internal_helper")
        leaked = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="internal_helper",
            caused_by_type="internal_helper",
        )
        overlay = _change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="internal_helper",
        )
        groups = correlate_root_causes([removed, leaked, overlay])
        assert len(groups) == 1
        group = groups[0]
        assert len(group.members) == 3
        assert group.strongest_evidence_level == "consumer_proven"

    def test_consumer_proven_reachability_kind_promotes_a_shared_func_removed(
        self,
    ) -> None:
        # appcompat._attach_consumer_impact enriches an *existing* FUNC_REMOVED
        # in place with reachability_kind="consumer_proven" instead of
        # emitting a separate CONSUMER_REQUIRED_SYMBOL_REMOVED overlay,
        # whenever uncovered_missing_symbols already finds the symbol
        # covered. The correlator must read that promotion off the change,
        # not assume artifact_proven purely from FUNC_REMOVED's kind
        # (Codex review, fresh evidence).
        removed = _change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="internal_helper",
            reachability_kind="consumer_proven",
        )
        leaked = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="internal_helper",
            caused_by_type="internal_helper",
        )
        groups = correlate_root_causes([removed, leaked])
        assert len(groups) == 1
        group = groups[0]
        levels_by_kind = {c.kind: level for c, level in group.members}
        assert levels_by_kind[ChangeKind.FUNC_REMOVED] == "consumer_proven"
        assert group.strongest_evidence_level == "consumer_proven"

    def test_overapprox_call_graph_path_is_downgraded(self) -> None:
        # internal_leak.compute_call_graph_leak_paths prefixes a proof path
        # crossing a virtual/function-pointer dispatch with "overapprox: "
        # (ADR-046 D5 effect_transitions) -- it proves *a* possible dispatch
        # target reaches the internal symbol, not that this exact chain
        # does, so it must not be read back as the same call_graph_proven
        # tier an exact chain gets (Codex review, fresh evidence).
        removed = _change(kind=ChangeKind.FUNC_REMOVED, symbol="internal_helper")
        leaked = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="internal_helper",
            caused_by_type="internal_helper",
            reachability_proof_path="overapprox: pub() --[DECL_CALLS_DECL]--> internal_helper()",
        )
        groups = correlate_root_causes([removed, leaked])
        assert len(groups) == 1
        group = groups[0]
        levels_by_kind = {c.kind: level for c, level in group.members}
        assert (
            levels_by_kind[ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API]
            == "call_graph_overapprox"
        )
        assert group.strongest_evidence_level == "call_graph_overapprox"

    def test_exact_call_graph_path_is_not_downgraded(self) -> None:
        removed = _change(kind=ChangeKind.FUNC_REMOVED, symbol="internal_helper")
        leaked = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="internal_helper",
            caused_by_type="internal_helper",
            reachability_proof_path="pub() --[DECL_CALLS_DECL]--> internal_helper()",
        )
        groups = correlate_root_causes([removed, leaked])
        levels_by_kind = {c.kind: level for c, level in groups[0].members}
        assert (
            levels_by_kind[ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API]
            == "call_graph_proven"
        )

    def test_overapprox_call_graph_path_still_yields_to_consumer_proof(self) -> None:
        removed = _change(kind=ChangeKind.FUNC_REMOVED, symbol="internal_helper")
        leaked = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="internal_helper",
            caused_by_type="internal_helper",
            reachability_proof_path="overapprox: pub() --[DECL_CALLS_DECL]--> internal_helper()",
        )
        overlay = _change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="internal_helper",
        )
        groups = correlate_root_causes([removed, leaked, overlay])
        assert groups[0].strongest_evidence_level == "consumer_proven"

    def test_all_kinds_rank_consumer_proven_strongest(self) -> None:
        pieces = [
            _change(kind=ChangeKind.FUNC_REMOVED, symbol="s"),
            _change(
                kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
                symbol="s",
                caused_by_type="s",
            ),
            _change(kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, symbol="s"),
        ]
        groups = correlate_root_causes(pieces)
        assert len(groups) == 1
        group = groups[0]
        assert len(group.members) == 3
        assert group.strongest_evidence_level == "consumer_proven"
        assert group.evidence_levels == (
            "artifact_proven",
            "call_graph_proven",
            "consumer_proven",
        )

    def test_distinct_symbols_produce_distinct_groups_in_first_seen_order(self) -> None:
        a1 = _change(kind=ChangeKind.FUNC_REMOVED, symbol="a")
        a2 = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="a",
            caused_by_type="a",
        )
        b1 = _change(kind=ChangeKind.FUNC_REMOVED, symbol="b")
        b2 = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="b",
            caused_by_type="b",
        )
        # b's pieces appear first in the input; group order must follow that,
        # not symbol sort order.
        groups = correlate_root_causes([b1, a1, b2, a2])
        assert [g.root_display for g in groups] == ["b", "a"]

    def test_unrelated_same_symbol_findings_outside_the_family_do_not_join(
        self,
    ) -> None:
        # A FUNC_PARAMS_CHANGED sharing a symbol with a correlated finding
        # must not silently join the group -- only the four registered
        # kinds participate at all.
        removed = _change(kind=ChangeKind.FUNC_REMOVED, symbol="s")
        leaked = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="s",
            caused_by_type="s",
        )
        unrelated = _change(kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="s")
        groups = correlate_root_causes([removed, leaked, unrelated])
        assert len(groups) == 1
        assert len(groups[0].members) == 2

    def test_no_symbol_or_caused_by_type_is_skipped(self) -> None:
        orphan = _change(kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, symbol="")
        sibling = _change(kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, symbol="")
        assert correlate_root_causes([orphan, sibling]) == []

    def test_to_dict_shape(self) -> None:
        removed = _change(kind=ChangeKind.FUNC_REMOVED, symbol="s")
        leaked = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="s",
            caused_by_type="s",
        )
        group = correlate_root_causes([removed, leaked])[0]
        d = group.to_dict()
        assert d["root_display"] == "s"
        assert d["strongest_evidence_level"] == "call_graph_proven"
        assert d["evidence_levels"] == ["artifact_proven", "call_graph_proven"]
        assert d["members"] == [
            {
                "finding_symbol": "s",
                "kind": "func_removed",
                "evidence_level": "artifact_proven",
            },
            {
                "finding_symbol": "s",
                "kind": "internal_symbol_required_by_public_api",
                "evidence_level": "call_graph_proven",
            },
        ]


class TestRootCauseGroupEmptyMembers:
    def test_strongest_evidence_level_is_none_for_an_empty_group(self) -> None:
        # correlate_root_causes never itself produces a group with 0
        # members, but RootCauseGroup is a plain public dataclass -- pin
        # its own defensive guard directly rather than only through the
        # (unreachable-via-correlate_root_causes) caller path.
        group = RootCauseGroup(root_cause_id="deadbeef", root_display="s", members=())
        assert group.strongest_evidence_level is None
        assert group.evidence_levels == ()


class TestEvidenceLevelVocabulary:
    def test_every_correlated_kind_has_a_distinct_level(self) -> None:
        levels = list(EVIDENCE_LEVEL_BY_KIND.values())
        assert len(levels) == len(set(levels))

    def test_exactly_the_kinds_named_by_the_plan(self) -> None:
        assert set(EVIDENCE_LEVEL_BY_KIND) == {
            ChangeKind.FUNC_REMOVED,
            ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
        }
