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
    an older snapshot). Called both from the public-symbol pairing (a
    friend with an out-of-line definition, i.e. a real exported symbol)
    and from ``diff_inline_hidden_friends`` below for an inline-only
    friend that keeps the same mangled key on both sides but is HIDDEN
    on at least one — the public pairing never sees that case at all.

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
    old_public: dict[str, Function],
    new_public: dict[str, Function],
) -> list[Change]:
    """Pick up hidden-friend transitions that the public-symbol diff misses.

    Inline-defined hidden friends never appear in the .so dynsym (the
    compiler emits them as `linkonce_odr`, often inlined into callers).
    They show up in the castxml snapshot with ``visibility=HIDDEN`` and
    ``is_hidden_friend=True``, so the public-symbol diff (which only
    matches on *old_public*/*new_public*, i.e. ``_public_functions()``'s
    PUBLIC/ELF_ONLY filter) never even considers them. This pass compares
    across the full function map (*old_all*/*new_all*) instead, so it
    covers three shapes:

    * present only in *old_all* — removed together with its symbol.
    * present only in *new_all* — added together with its symbol.
    * present (same mangled key) in both — the friend keeps its symbol
      identity but may still flip ``is_hidden_friend`` with no change to
      its signature (e.g. an in-class ``friend`` declaration pulled out
      to file scope, or vice versa, which preserves the mangled name
      since a hidden friend already mangles under its enclosing
      namespace, not the class). When at least one side is HIDDEN this
      transition would otherwise never be observed — the sibling
      ``check_hidden_friend_change`` only runs on pairs matched from
      *old_public*/*new_public* — so it is checked here too, but only
      when the pair was NOT already covered by that public-symbol
      pairing (both sides public), to avoid emitting it twice (Codex
      review).
    """
    changes: list[Change] = []
    for mangled, f_old in old_all.items():
        f_new = new_all.get(mangled)
        if f_new is None:
            if f_old.is_hidden_friend:
                changes.append(
                    make_change(
                        ChangeKind.HIDDEN_FRIEND_REMOVED,
                        symbol=mangled,
                        old=f_old.name,
                        caused_by_type=f_old.hidden_friend_owner,
                    )
                )
            continue
        if mangled in old_public and mangled in new_public:
            continue
        changes.extend(check_hidden_friend_change(mangled, f_old, f_new))
    for mangled, f_new in new_all.items():
        if mangled in old_all:
            continue
        if f_new.is_hidden_friend:
            changes.append(
                make_change(
                    ChangeKind.HIDDEN_FRIEND_ADDED,
                    symbol=mangled,
                    new=f_new.name,
                    caused_by_type=f_new.hidden_friend_owner,
                )
            )
    return changes
