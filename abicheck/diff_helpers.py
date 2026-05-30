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

"""Reusable building blocks for diff detectors.

Detectors repeat two structural patterns:

* **Boolean attribute transitions** — "flag went from off→on / on→off"
  (e.g. ``noexcept`` added/removed, ``virtual`` added/removed). Each site
  used to hand-roll an ``if/elif`` pair around two near-identical
  ``Change`` constructions. :func:`bool_transition` collapses that into a
  single declarative call while preserving the bespoke wording and the
  tri-state (``None`` means "not recorded in this snapshot") skip rule.

* **Keyed map diffs** — "what was removed / added / present on both sides"
  over two ``{key: record}`` maps. :func:`diff_by_key` factors out the
  removed/added/common scaffold so a detector only supplies the per-bucket
  logic.

These helpers are deliberately small and behavior-preserving: they encode
the shape that was already duplicated across the ``diff_*`` modules, not
new policy.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar, cast

from .checker_policy import ChangeKind
from .checker_types import Change

K = TypeVar("K")
V = TypeVar("V")
W = TypeVar("W")

# Sentinel distinguishing "key absent" from "key present with value None".
# Typed as Any so it can stand in for a ``W`` in the get() default without
# upsetting the type checker.
_MISSING: Any = object()

# A (ChangeKind, description) pair describing one direction of a transition.
TransitionSpec = tuple[ChangeKind, str]


def bool_transition(
    old_val: bool | None,
    new_val: bool | None,
    symbol: str,
    *,
    added: TransitionSpec | None = None,
    removed: TransitionSpec | None = None,
    added_values: tuple[str | None, str | None] = (None, None),
    removed_values: tuple[str | None, str | None] = (None, None),
    skip_none: bool = False,
) -> list[Change]:
    """Emit a :class:`Change` for a boolean attribute transition.

    ``added`` fires on a ``False → True`` transition, ``removed`` on
    ``True → False``. Each is an optional ``(kind, description)`` pair; a
    direction with no spec is simply not reported.

    ``added_values`` / ``removed_values`` supply the ``(old_value,
    new_value)`` strings recorded on the emitted change for that direction
    (defaulting to ``(None, None)`` for flags whose before/after wording is
    carried entirely by the description).

    When ``skip_none`` is set, a ``None`` on *either* side suppresses
    emission. This models tri-state attributes (e.g. ``is_explicit``,
    ``is_hidden_friend``) where ``None`` means the value was not recorded in
    one snapshot — typically an older snapshot predating the field — and
    must not be mistaken for ``False``.
    """
    if skip_none and (old_val is None or new_val is None):
        return []
    if not old_val and new_val and added is not None:
        kind, description = added
        ov, nv = added_values
        return [Change(kind=kind, symbol=symbol, description=description, old_value=ov, new_value=nv)]
    if old_val and not new_val and removed is not None:
        kind, description = removed
        ov, nv = removed_values
        return [Change(kind=kind, symbol=symbol, description=description, old_value=ov, new_value=nv)]
    return []


def diff_by_key(
    old_map: Mapping[K, V],
    new_map: Mapping[K, W],
    *,
    on_removed: Callable[[K, V], Iterable[Change]] | None = None,
    on_added: Callable[[K, W], Iterable[Change]] | None = None,
    on_common: Callable[[K, V, W], Iterable[Change]] | None = None,
) -> list[Change]:
    """Diff two keyed maps, dispatching to per-bucket callbacks.

    For every key present only in ``old_map`` ``on_removed(key, old)`` is
    invoked; for keys only in ``new_map`` ``on_added(key, new)``; for keys
    in both ``on_common(key, old, new)``. Each callback returns an iterable
    of :class:`Change` (or nothing); omitted callbacks skip that bucket.

    Removed/common keys are visited in ``old_map`` iteration order and
    added keys in ``new_map`` order, matching the hand-written loops this
    replaces so change ordering is unchanged.
    """
    changes: list[Change] = []
    for key, old_val in old_map.items():
        new_val = new_map.get(key, _MISSING)
        if new_val is _MISSING:
            if on_removed is not None:
                changes.extend(on_removed(key, old_val))
        elif on_common is not None:
            changes.extend(on_common(key, old_val, cast(W, new_val)))
    for key, new_val in new_map.items():
        if key not in old_map and on_added is not None:
            changes.extend(on_added(key, new_val))
    return changes
