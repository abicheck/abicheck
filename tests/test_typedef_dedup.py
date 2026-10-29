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

"""One finding per removed or retargeted typedef, the header tier's.

The header tier (``compare.typedefs``) and the L4 source replay
(``source_diff._diff_typedefs``) both observe the same typedef change. The
kinds differ (``TYPEDEF_REMOVED`` vs ``PUBLIC_TYPEDEF_REMOVED``), so only the
qualified name pairs them. The oracle for every case is written out literally:
which finding survives. It is not the implementation's own helper.
"""

from __future__ import annotations

import itertools
from collections import Counter

import pytest

from abicheck.compare.finding_dedup import deduplicate_cross_detector
from abicheck.compare.source_replay_twins import drop_replay_twins
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.identity import EntityId, EntityKind, Namespace


def _entity(qualified: str) -> EntityId:
    """A header-tier entity id for a typedef spelled *qualified* (``a::b``)."""
    *scopes, leaf = qualified.split("::")
    scope = tuple(Namespace(name=s) for s in scopes)
    return EntityId(scope=scope, kind=EntityKind.TYPEDEF, leaf_name=leaf)


def _header_removed(bare: str, qualified: str) -> Change:
    return Change(
        kind=ChangeKind.TYPEDEF_REMOVED,
        symbol=bare,
        description=f"Typedef removed: {bare}",
        old_value="int",
        entity_id=_entity(qualified),
    )


def _header_retarget(bare: str, qualified: str) -> Change:
    return Change(
        kind=ChangeKind.TYPEDEF_BASE_CHANGED,
        symbol=bare,
        description=f"Typedef base type changed: {bare}",
        old_value="int",
        new_value="long int",
        entity_id=_entity(qualified),
    )


def _replay_removed(qualified: str) -> Change:
    return Change(
        kind=ChangeKind.PUBLIC_TYPEDEF_REMOVED,
        symbol=qualified,
        description=f"Public typedef {qualified!r} was removed",
        old_value="int",
        new_value="",
        evidence_category="source_only",
    )


def _replay_retarget(qualified: str) -> Change:
    return Change(
        kind=ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED,
        symbol=qualified,
        description=f"Public typedef {qualified!r} now resolves elsewhere",
        old_value="int",
        new_value="long",
        evidence_category="source_only",
    )


def _kept(changes: list[Change]) -> list[tuple[str, str]]:
    return [(c.kind.value, c.symbol) for c in changes]


@pytest.mark.parametrize(
    ("header", "replay", "kept"),
    [
        pytest.param(
            _header_removed("old_t", "old_t"),
            _replay_removed("old_t"),
            [("typedef_removed", "old_t")],
            id="c-removed",
        ),
        pytest.param(
            _header_retarget("count_t", "count_t"),
            _replay_retarget("count_t"),
            [("typedef_base_changed", "count_t")],
            id="c-retargeted",
        ),
        pytest.param(
            _header_removed("old_t", "demo::old_t"),
            _replay_removed("demo::old_t"),
            [("typedef_removed", "old_t")],
            id="cpp-namespaced-removed",
        ),
        pytest.param(
            _header_removed("deep_t", "demo::inner::deep_t"),
            _replay_removed("demo::inner::deep_t"),
            [("typedef_removed", "deep_t")],
            id="cpp-nested-namespace-removed",
        ),
        pytest.param(
            _header_retarget("count_t", "demo::count_t"),
            _replay_retarget("demo::count_t"),
            [("typedef_base_changed", "count_t")],
            id="cpp-namespaced-retargeted",
        ),
    ],
)
def test_header_twin_keeps_one_finding(
    header: Change, replay: Change, kept: list[tuple[str, str]]
) -> None:
    result = deduplicate_cross_detector([header, replay])
    assert _kept(result) == kept


def test_replay_without_header_twin_is_kept() -> None:
    # The header tier did not run (no --header): nothing to pair with, so the
    # replay finding is the only observation and must stay visible.
    replay = _replay_removed("demo::old_t")
    assert _kept(deduplicate_cross_detector([replay])) == [
        ("public_typedef_removed", "demo::old_t")
    ]


def test_removal_does_not_pair_with_retarget() -> None:
    # Same qualified name, different change: a removal and a retarget are two
    # observations, so neither drops the other.
    changes = [_header_removed("count_t", "count_t"), _replay_retarget("count_t")]
    assert _kept(deduplicate_cross_detector(changes)) == [
        ("typedef_removed", "count_t"),
        ("public_typedef_target_changed", "count_t"),
    ]


def test_unchanged_typedef_control_emits_nothing_to_dedup() -> None:
    # An unchanged typedef produces no finding on either tier. An unrelated
    # header-tier change must not swallow the replay finding of another name.
    changes = [
        _header_removed("old_t", "demo::old_t"),
        _replay_removed("demo::u_t"),
    ]
    assert _kept(deduplicate_cross_detector(changes)) == [
        ("typedef_removed", "old_t"),
        ("public_typedef_removed", "demo::u_t"),
    ]


def test_extra_replay_copy_beyond_header_twins_stays_visible() -> None:
    # One header-tier finding absorbs one replay copy. A second replay finding
    # for the same name has no twin left and must survive.
    changes = [
        _header_removed("old_t", "old_t"),
        _replay_removed("old_t"),
        _replay_removed("old_t"),
    ]
    result = deduplicate_cross_detector(changes)
    assert _kept(result) == [
        ("typedef_removed", "old_t"),
        ("public_typedef_removed", "old_t"),
    ]


def test_header_finding_without_entity_id_pairs_by_symbol() -> None:
    header = Change(
        kind=ChangeKind.TYPEDEF_REMOVED,
        symbol="old_t",
        description="Typedef removed: old_t",
        old_value="int",
    )
    assert _kept(deduplicate_cross_detector([header, _replay_removed("old_t")])) == [
        ("typedef_removed", "old_t")
    ]


_NAMES = ("old_t", "demo::old_t", "demo::inner::deep_t")
_PAIRS = (
    (_header_removed, _replay_removed, "typedef_removed", "public_typedef_removed"),
    (
        _header_retarget,
        _replay_retarget,
        "typedef_base_changed",
        "public_typedef_target_changed",
    ),
)
_SLOTS = [(i, name) for i in range(len(_PAIRS)) for name in _NAMES]


def _domain() -> list[
    tuple[list[Change], dict[tuple[str, str], int], dict[tuple[str, str], int]]
]:
    """Every combination of header and replay copies over a small domain.

    Each (kind pair, name) slot takes 0 or 1 header copies and 0 or 1 replay
    copies: 4^6 = 4096 cases, exhaustive rather than sampled. Each case carries
    the copy counts it was built from, so the oracle does not read the
    implementation's own name rendering.
    """
    cases = []
    for counts in itertools.product(range(2), repeat=2 * len(_SLOTS)):
        header_counts = counts[: len(_SLOTS)]
        replay_counts = counts[len(_SLOTS) :]
        changes: list[Change] = []
        expected_header: dict[tuple[str, str], int] = {}
        expected_replay: dict[tuple[str, str], int] = {}
        for (i, name), h, r in zip(_SLOTS, header_counts, replay_counts, strict=True):
            build_header, build_replay, header_kind, replay_kind = _PAIRS[i]
            bare = name.rsplit("::", 1)[-1]
            changes += [build_header(bare, name)] * h
            changes += [build_replay(name)] * r
            if h:
                expected_header[(header_kind, name)] = h
            if r:
                expected_replay[(replay_kind, name)] = r
        cases.append((changes, expected_header, expected_replay))
    return cases


_DOMAIN = _domain()
_REPLAY_KINDS = {replay for _, _, _, replay in _PAIRS}


def test_typedef_pairing_oracle_over_exhaustive_domain() -> None:
    """Over every case: all header copies survive, each surviving replay copy
    count is exactly ``max(0, replay - header)`` for its qualified name, only
    replay-kind findings are ever dropped, and nothing is lost in total.
    """
    for changes, expected_header, expected_replay in _DOMAIN:
        result = drop_replay_twins(changes)
        kept = Counter((c.kind.value, c.symbol) for c in result)
        for header_kind, replay_kind in ((h, r) for _, _, h, r in _PAIRS):
            # A header-tier finding's symbol is the bare alias, so names that
            # share one bare alias ("old_t", "demo::old_t") share one count.
            for bare in {n.rsplit("::", 1)[-1] for n in _NAMES}:
                header_bare = sum(
                    expected_header.get((header_kind, n), 0)
                    for n in _NAMES
                    if n.rsplit("::", 1)[-1] == bare
                )
                assert kept[(header_kind, bare)] == header_bare, (header_kind, bare)
            for name in _NAMES:
                replay_n = expected_replay.get((replay_kind, name), 0)
                header_n = expected_header.get((header_kind, name), 0)
                assert kept[(replay_kind, name)] == max(0, replay_n - header_n), (
                    replay_kind,
                    name,
                )
        dropped = Counter(c.kind.value for c in changes) - Counter(
            c.kind.value for c in result
        )
        assert set(dropped) <= _REPLAY_KINDS
        assert sum(dropped.values()) + len(result) == len(changes)


def test_typedef_pairing_is_input_order_invariant() -> None:
    for changes, _, _ in _DOMAIN:
        forward = Counter((c.kind.value, c.symbol) for c in drop_replay_twins(changes))
        backward = Counter(
            (c.kind.value, c.symbol) for c in drop_replay_twins(list(reversed(changes)))
        )
        assert forward == backward
