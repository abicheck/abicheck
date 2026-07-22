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

"""Hidden-friend (in-class ``friend`` declaration) diff detectors.

Split out of ``diff_symbols.py`` (which sits at the AI-readiness file-size
hard cap) rather than grown in place — see ``AGENTS.md`` "Files that are
large".
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import bool_transition, make_change
from .model import Function


def check_hidden_friend_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change if the hidden-friend status transitioned.

    Hidden-friend transitions: an in-class ``friend`` declaration was
    added or removed across versions. Tri-state — skip when either
    side's snapshot did not record the flag (e.g. DWARF-only path or
    an older snapshot). The matched-mangled iteration here handles
    the case where the friend has an out-of-line definition (i.e.
    a real symbol). Inline-only hidden friends never appear here
    because they have no symbol on either side; those transitions
    are picked up by ``diff_inline_hidden_friends`` below by matching
    on (name, params) rather than mangled name.

    ``caused_by_type`` carries the befriending class's qualified name (the
    side that is/was actually a hidden friend) so surface classification can
    key demotion off the *owner's* header origin rather than unconditionally
    retaining every hidden-friend finding (``surface.py``).
    """
    owner = (
        f_new.hidden_friend_owner
        if f_new.is_hidden_friend
        else f_old.hidden_friend_owner
    )
    return bool_transition(
        f_old.is_hidden_friend,
        f_new.is_hidden_friend,
        mangled,
        skip_none=True,
        added=(
            ChangeKind.HIDDEN_FRIEND_ADDED,
            f"Function became an in-class friend declaration: {f_old.name}",
        ),
        added_values=("non-friend", "hidden friend"),
        removed=(
            ChangeKind.HIDDEN_FRIEND_REMOVED,
            f"Function is no longer an in-class friend declaration: {f_old.name}",
        ),
        removed_values=("hidden friend", "non-friend"),
        caused_by_type=owner,
    )


def diff_inline_hidden_friends(
    old_all: dict[str, Function],
    new_all: dict[str, Function],
) -> list[Change]:
    """Pick up hidden-friend additions/removals that have no public symbol.

    Inline-defined hidden friends never appear in the .so dynsym (the
    compiler emits them as `linkonce_odr`, often inlined into callers).
    They show up in the castxml snapshot with ``visibility=HIDDEN`` and
    ``is_hidden_friend=True``. The public-symbol diff above skips them.
    This pass compares across the full function map and only fires for
    functions that are flagged as hidden friends on one side.
    """
    changes: list[Change] = []
    for mangled, f_old in old_all.items():
        if not f_old.is_hidden_friend:
            continue
        if mangled in new_all:
            continue
        changes.append(
            make_change(
                ChangeKind.HIDDEN_FRIEND_REMOVED,
                symbol=mangled,
                old=f_old.name,
                caused_by_type=f_old.hidden_friend_owner,
            )
        )
    for mangled, f_new in new_all.items():
        if not f_new.is_hidden_friend:
            continue
        if mangled in old_all:
            continue
        changes.append(
            make_change(
                ChangeKind.HIDDEN_FRIEND_ADDED,
                symbol=mangled,
                new=f_new.name,
                caused_by_type=f_new.hidden_friend_owner,
            )
        )
    return changes
