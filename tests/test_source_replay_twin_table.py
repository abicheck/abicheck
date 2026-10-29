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

"""Every source-replay kind is classified: a twin, a distinct facet, or replay-only.

The replay kinds are read from the source with an AST scan of the emitter
modules, not from a hand-kept list, so a new ``ChangeKind.X`` in the replay
fails here until it is placed in ``abicheck.compare.source_replay_twins``.
The behavioural cases pin what the table does to a real pair of findings.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from abicheck.compare.source_replay_twins import (
    DISTINCT_FACETS,
    REPLAY_EMITTER_MODULES,
    REPLAY_ONLY,
    REPLAY_TWINS,
    SOURCE_REPLAY_EVIDENCE,
    drop_replay_twins,
    replay_kinds_covered,
)
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind

pytestmark = pytest.mark.repo_scan

_REPO = Path(__file__).resolve().parents[1]


def _scanned_replay_kinds() -> set[ChangeKind]:
    """Every ``ChangeKind.X`` attribute reference in the replay emitter modules."""
    found: set[ChangeKind] = set()
    for rel in REPLAY_EMITTER_MODULES:
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "ChangeKind"
            ):
                found.add(ChangeKind[node.attr])
    return found


def test_scan_reaches_the_known_replay_emitters() -> None:
    # The scan must see the emitters the table is about; an empty or truncated
    # scan would make the coverage check below vacuous.
    scanned = _scanned_replay_kinds()
    for kind in (
        ChangeKind.DEFAULT_ARGUMENT_CHANGED,
        ChangeKind.CONCEPT_TIGHTENED,
        ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
        ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
        ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
    ):
        assert kind in scanned
    assert len(scanned) >= 20


def test_every_replay_kind_is_classified_and_no_stale_entry_remains() -> None:
    scanned = _scanned_replay_kinds()
    covered = replay_kinds_covered()
    unclassified = sorted(k.name for k in scanned - covered)
    stale = sorted(k.name for k in covered - scanned)
    assert unclassified == [], (
        f"replay kinds with no twin/facet/replay-only entry: {unclassified}"
    )
    assert stale == [], f"table entries for kinds the replay no longer emits: {stale}"


def test_each_kind_has_exactly_one_role() -> None:
    twins = [t.replay for t in REPLAY_TWINS]
    facets = [f.replay for f in DISTINCT_FACETS]
    roles = twins + facets + list(REPLAY_ONLY)
    assert sorted(k.name for k in roles if roles.count(k) > 1) == []


@pytest.mark.parametrize("twin", REPLAY_TWINS, ids=lambda t: t.replay.name)
def test_twin_pairs_a_replay_kind_with_real_header_kinds(twin) -> None:
    assert twin.header, "a twin needs at least one header-tier kind"
    assert all(isinstance(k, ChangeKind) for k in twin.header)


@pytest.mark.parametrize("facet", DISTINCT_FACETS, ids=lambda f: f.replay.name)
def test_distinct_facet_says_why_it_is_not_a_duplicate(facet) -> None:
    assert facet.header
    assert len(facet.reason) > 40


@pytest.mark.parametrize(
    ("kind", "reason"), sorted(REPLAY_ONLY.items(), key=lambda kv: kv[0].name)
)
def test_replay_only_reason_is_one_line(kind: ChangeKind, reason: str) -> None:
    assert "\n" not in reason
    assert len(reason) > 15, f"{kind.name} needs a real reason"


# -- behaviour of the table on real pairs ---------------------------------------


def _header(kind: ChangeKind, symbol: str, **kw) -> Change:
    return Change(kind=kind, symbol=symbol, description=f"header {symbol}", **kw)


def _replay(kind: ChangeKind, symbol: str, **kw) -> Change:
    return Change(
        kind=kind,
        symbol=symbol,
        description=f"replay {symbol}",
        evidence_category=SOURCE_REPLAY_EVIDENCE,
        **kw,
    )


@pytest.mark.parametrize(
    ("header", "replay", "survivor"),
    [
        pytest.param(
            _header(
                ChangeKind.CONSTANT_CHANGED,
                "demo::kLimit",
                old_value="1",
                new_value="2",
            ),
            _replay(
                ChangeKind.CONSTEXPR_VALUE_CHANGED,
                "demo::kLimit",
                old_value="1",
                new_value="2",
            ),
            ChangeKind.CONSTANT_CHANGED,
            id="constexpr-value-header-owns",
        ),
        pytest.param(
            _header(ChangeKind.TYPEDEF_REMOVED, "old_t"),
            _replay(ChangeKind.PUBLIC_TYPEDEF_REMOVED, "old_t"),
            ChangeKind.TYPEDEF_REMOVED,
            id="typedef-removed-header-owns",
        ),
        pytest.param(
            _header(
                ChangeKind.PARAM_DEFAULT_VALUE_CHANGED,
                "_Z5da_fni",
                old_value="1",
                new_value="2",
            ),
            _replay(
                ChangeKind.DEFAULT_ARGUMENT_CHANGED,
                "da_fn",
                old_value="p0=1",
                new_value="p0=2",
            ),
            ChangeKind.DEFAULT_ARGUMENT_CHANGED,
            id="default-argument-replay-more-severe-survives",
        ),
        pytest.param(
            _header(ChangeKind.INLINE_FUNCTION_REMOVED, "UT_tpl"),
            _replay(ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED, "UT_tpl"),
            ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
            id="uninstantiated-template-replay-owns",
        ),
        pytest.param(
            _header(ChangeKind.FUNC_DELETED, "_Z5df_fnv"),
            _replay(ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH, "_Z5df_fnv"),
            ChangeKind.FUNC_DELETED,
            id="binary-loss-restatement-header-owns",
        ),
    ],
)
def test_matched_pair_keeps_one_survivor(
    header: Change, replay: Change, survivor: ChangeKind
) -> None:
    kept = drop_replay_twins([header, replay])
    assert [c.kind for c in kept] == [survivor]


def test_unmatched_names_never_pair() -> None:
    kept = drop_replay_twins(
        [
            _header(ChangeKind.CONSTANT_CHANGED, "demo::kLimit"),
            _replay(ChangeKind.CONSTEXPR_VALUE_CHANGED, "demo::kMax"),
        ]
    )
    assert sorted(c.symbol for c in kept) == ["demo::kLimit", "demo::kMax"]


def test_replay_copy_without_source_tag_is_not_a_replay_finding() -> None:
    untagged = Change(
        kind=ChangeKind.CONSTEXPR_VALUE_CHANGED, symbol="demo::kLimit", description="x"
    )
    kept = drop_replay_twins(
        [_header(ChangeKind.CONSTANT_CHANGED, "demo::kLimit"), untagged]
    )
    assert [c.kind for c in kept] == [
        ChangeKind.CONSTANT_CHANGED,
        ChangeKind.CONSTEXPR_VALUE_CHANGED,
    ]


def test_extra_replay_copy_stays_visible() -> None:
    kept = drop_replay_twins(
        [
            _header(ChangeKind.CONSTANT_CHANGED, "demo::kLimit"),
            _replay(ChangeKind.CONSTEXPR_VALUE_CHANGED, "demo::kLimit"),
            _replay(ChangeKind.CONSTEXPR_VALUE_CHANGED, "demo::kLimit"),
        ]
    )
    assert [c.kind for c in kept] == [
        ChangeKind.CONSTANT_CHANGED,
        ChangeKind.CONSTEXPR_VALUE_CHANGED,
    ]


def test_distinct_facet_keeps_both_findings() -> None:
    body = _replay(ChangeKind.TEMPLATE_BODY_CHANGED, "TP_def")
    default = _header(ChangeKind.TEMPLATE_PARAM_DEFAULT_CHANGED, "TP_def")
    kept = drop_replay_twins([default, body])
    assert sorted(c.kind.name for c in kept) == [
        "TEMPLATE_BODY_CHANGED",
        "TEMPLATE_PARAM_DEFAULT_CHANGED",
    ]
