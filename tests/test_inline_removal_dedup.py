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

"""One ``INLINE_FUNCTION_REMOVED`` per removed header-only inline.

The header tier (``diff_symbols._check_removed_function``) and the L4 source
replay (``source_diff._diff_inline_bodies``) both observe the same removal and
both emit the kind. The oracle for every case is written out literally: which
(tier, function) findings must survive. It is not the implementation's own
helper.
"""

from __future__ import annotations

import pytest

from abicheck.compare.finding_dedup import deduplicate_cross_detector
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind

_HEADER = None  # evidence_category left unset: the header tier's own findings
_REPLAY = "source_only"  # the tag buildsource.evidence_report puts on L4 findings
_FACTS = {"declared_in_headers": "true", "binary_exported": "false"}


def _header(name: str, mangled: str | None = None) -> Change:
    """A header-tier removal: a mangled symbol, a demangled name, surface facts."""
    return Change(
        kind=ChangeKind.INLINE_FUNCTION_REMOVED,
        symbol=mangled or name,
        description=f"Hidden function removed: {name}",
        old_value=name,
        demangled_symbol=name if mangled else None,
        surface_facts=dict(_FACTS),
    )


def _replay(qualified: str) -> Change:
    """An L4 source-replay removal: qualified name, body hash, its own wording."""
    return Change(
        kind=ChangeKind.INLINE_FUNCTION_REMOVED,
        symbol=qualified,
        description=(
            f"Public inline function {qualified!r} was removed. It had no "
            "exported binary symbol, so only source replay sees the loss."
        ),
        old_value="sha256:0",
        new_value="",
        evidence_category=_REPLAY,
    )


def _survivors(changes: list[Change]) -> list[tuple[str, str]]:
    """``(tier, symbol)`` for each surviving inline removal, in order."""
    return [
        ("replay" if c.evidence_category == _REPLAY else "header", c.symbol)
        for c in deduplicate_cross_detector(changes)
        if c.kind is ChangeKind.INLINE_FUNCTION_REMOVED
    ]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param(
            [_header("clamp_it"), _replay("clamp_it")],
            [("header", "clamp_it")],
            id="c-free-inline-one-owner",
        ),
        pytest.param(
            [
                _header("demo::clamp_it(int)", "_ZN4demo8clamp_itEi"),
                _replay("demo::clamp_it"),
            ],
            [("header", "_ZN4demo8clamp_itEi")],
            id="cxx-mangled-header-vs-qualified-replay",
        ),
        pytest.param(
            [
                _header("demo::f(int)", "_ZN4demo1fEi"),
                _header("demo::f(double)", "_ZN4demo1fEd"),
                _replay("demo::f"),
                _replay("demo::f"),
            ],
            [("header", "_ZN4demo1fEi"), ("header", "_ZN4demo1fEd")],
            id="overloads-both-owned-by-header",
        ),
        pytest.param(
            [_header("f"), _replay("f"), _replay("f")],
            [("header", "f"), ("replay", "f")],
            id="replay-extra-removal-stays-visible",
        ),
        pytest.param(
            [_replay("g")],
            [("replay", "g")],
            id="replay-alone-is-not-a-duplicate",
        ),
        pytest.param(
            [_header("h")],
            [("header", "h")],
            id="header-alone-is-untouched",
        ),
        pytest.param(
            [_header("a"), _replay("b")],
            [("header", "a"), ("replay", "b")],
            id="different-functions-never-pair",
        ),
    ],
)
def test_inline_removal_keeps_one_owner_per_function(
    changes: list[Change], expected: list[tuple[str, str]]
) -> None:
    assert _survivors(changes) == expected


def test_kept_header_finding_is_the_artifact_tier_one() -> None:
    # The header tier carries the declaration's surface facts; the replay copy
    # does not. Keeping the replay copy would lose them.
    kept = deduplicate_cross_detector([_replay("clamp_it"), _header("clamp_it")])
    inline = [c for c in kept if c.kind is ChangeKind.INLINE_FUNCTION_REMOVED]
    assert len(inline) == 1
    assert inline[0].surface_facts == _FACTS
    assert inline[0].evidence_category is _HEADER


def test_non_inline_kinds_are_not_touched() -> None:
    func_removed = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="_Z1fv",
        description="Function removed: f",
    )
    replay = _replay("f")
    kept = deduplicate_cross_detector([func_removed, _header("f"), replay])
    assert [c.kind for c in kept] == [
        ChangeKind.FUNC_REMOVED,
        ChangeKind.INLINE_FUNCTION_REMOVED,
    ]
