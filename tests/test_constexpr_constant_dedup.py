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

"""One finding per namespace-scope constant value change, owned by the header tier.

``CONSTANT_CHANGED`` (header tier, ``compare.constants``) and
``CONSTEXPR_VALUE_CHANGED`` (L4 replay, ``buildsource.source_diff``) observe the
same change. The oracle for each case is written out literally: which
(tier, symbol) findings must survive. It is not the implementation's own
helper. The end-to-end cells live in ``test_constexpr_constant_source_replay_e2e.py``.
"""

from __future__ import annotations

import pytest

from abicheck.compare.finding_dedup import deduplicate_cross_detector
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind

_REPLAY = "source_only"  # the tag buildsource.evidence_report puts on L4 findings


def _header(symbol: str) -> Change:
    """A header-tier value change: the constant's qualified name, raw values."""
    return Change(
        kind=ChangeKind.CONSTANT_CHANGED,
        symbol=symbol,
        description=f"Preprocessor constant value changed: {symbol} ('10' → '20')",
        old_value="10",
        new_value="20",
    )


def _replay(symbol: str) -> Change:
    """An L4 replay value change: the same name, its own wording."""
    return Change(
        kind=ChangeKind.CONSTEXPR_VALUE_CHANGED,
        symbol=symbol,
        description=(
            f"Public constexpr {symbol!r} value changed: '10' -> '20'. "
            "Consumers that baked in the old value must be recompiled."
        ),
        old_value="10",
        new_value="20",
        evidence_category=_REPLAY,
    )


def _survivors(changes: list[Change]) -> list[tuple[str, str]]:
    """``(tier, symbol)`` for each surviving constant finding, in order."""
    return [
        ("replay" if c.evidence_category == _REPLAY else "header", c.symbol)
        for c in deduplicate_cross_detector(changes)
        if c.kind in (ChangeKind.CONSTANT_CHANGED, ChangeKind.CONSTEXPR_VALUE_CHANGED)
    ]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param(
            [_header("kLimit"), _replay("kLimit")],
            [("header", "kLimit")],
            id="c-free-constant-one-owner",
        ),
        pytest.param(
            [_header("demo::kLimit"), _replay("demo::kLimit")],
            [("header", "demo::kLimit")],
            id="cxx-namespaced-one-owner",
        ),
        pytest.param(
            [_header("outer::demo::kLimit"), _replay("outer::demo::kLimit")],
            [("header", "outer::demo::kLimit")],
            id="cxx-nested-namespace-one-owner",
        ),
        pytest.param(
            [_header("demo::kLimit"), _replay("demo::kMax")],
            [("header", "demo::kLimit"), ("replay", "demo::kMax")],
            id="different-constants-never-pair",
        ),
        pytest.param(
            [_header("demo::kLimit"), _replay("demo::kLimit"), _replay("demo::kLimit")],
            [("header", "demo::kLimit"), ("replay", "demo::kLimit")],
            id="replay-extra-copy-stays-visible",
        ),
        pytest.param(
            [_replay("demo::kLimit")],
            [("replay", "demo::kLimit")],
            id="l2-tier-not-run-replay-copy-kept",
        ),
        pytest.param(
            [_header("demo::kLimit")],
            [("header", "demo::kLimit")],
            id="header-alone-is-untouched",
        ),
    ],
)
def test_constant_value_change_keeps_one_owner(
    changes: list[Change], expected: list[tuple[str, str]]
) -> None:
    assert _survivors(changes) == expected


def test_dropped_replay_copy_is_the_only_finding_removed() -> None:
    # The surviving header finding keeps its own facts; no other kind moves.
    other = Change(
        kind=ChangeKind.VAR_VALUE_CHANGED,
        symbol="_ZN4demoL6kLimitE",
        description="Global data value changed: kLimit ('10' → '20')",
        old_value="10",
        new_value="20",
    )
    kept = deduplicate_cross_detector(
        [other, _header("demo::kLimit"), _replay("demo::kLimit")]
    )
    assert [c.kind for c in kept] == [
        ChangeKind.VAR_VALUE_CHANGED,
        ChangeKind.CONSTANT_CHANGED,
    ]
    assert kept[1].evidence_category is None
