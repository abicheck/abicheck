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

import pytest

from abicheck.compare.finding_dedup import deduplicate_cross_detector
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
    )


def _replay_retarget(qualified: str) -> Change:
    return Change(
        kind=ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED,
        symbol=qualified,
        description=f"Public typedef {qualified!r} now resolves elsewhere",
        old_value="int",
        new_value="long",
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
