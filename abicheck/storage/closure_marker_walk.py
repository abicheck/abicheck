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

"""The marker-pruned snapshot walk `storage.closure_identity`'s renumbering
runs: one pass that collects every identity string *and* flags the subtrees
that may hold a closure/anonymous marker (`_collect_marking`), and the
rewrite that then visits only those flagged subtrees
(`_rewrite_marked_subtrees`).

Split out of ``closure_identity.py`` -- unchanged -- when the load path
started reusing one marking pass for both of its closure steps
(``storage.closure_marking``): the walk is its own responsibility, a leaf
over ``qualified_name_segments_walk``'s generic walk, and keeping it apart
holds ``closure_identity.py`` at the ADR-061 new-file ceiling instead of
growing it.
"""

from __future__ import annotations

import dataclasses as _dataclasses
import re as _re
from collections.abc import Callable as _Callable, Mapping as _Mapping
from enum import Enum as _Enum

from ..qualified_name_segments_walk import (
    _collect_plan as _collect_plan,
    _walk_plan as _walk_plan,
    _walk_rewrite_strings as _walk_rewrite_strings,
    collect_and_flag as _collect_and_flag,
)

#: Matches the marker prefix :func:`strip_anonymous_type_location` already produces
#: (``"(lambda:"``, ``"(unnamed struct:"``) -- NOT the raw ``at <path>:<line>:<col>``
#: form that function itself consumes. Only the fixed prefix is a regex; the
#: variable-length basename that follows is scanned manually by :func:`_scan_anon_type_marker`
#: below, since a single regex alternation (``\([^()]*\)``) can only ever balance one
#: level of nesting and fails on a basename with two, e.g. ``foo(a(b)).hpp`` (Codex review, fresh evidence).
_ANON_TYPE_MARKER_PREFIX_RE = _re.compile(r"\((lambda|unnamed\s+\w+|anonymous\s+\w+):")


def _may_hold_marker(text: str) -> bool:
    """Whether *text* holds a closure/anonymous marker -- the prefix
    :func:`_anon_type_ordinal_matches` scans for, so false means
    :func:`apply_anonymous_type_ordinals` returns *text* unchanged."""
    return "(" in text and _ANON_TYPE_MARKER_PREFIX_RE.search(text) is not None


def _handoff_dataclass(value: object) -> bool:
    """Whether the pruned walk descends *value* field by field rather than
    handing it whole to :func:`_walk_rewrite_strings`."""
    return (
        _dataclasses.is_dataclass(value)
        and not isinstance(value, type)
        and type(value).__name__ != "Fact"
    )


def _other_mapping(value: object) -> bool:
    """A mapping the walks descend entry by entry that is not a plain
    ``dict`` -- e.g. ``SemanticIR.occurrences``' ``FrozenMapping``, by far
    a snapshot's largest container. Handed off whole, one closure marker
    anywhere in it sent every occurrence through the unpruned walk (2.0M
    nodes to rewrite 120 entries on a 60-module clang dump)."""
    return (
        isinstance(value, _Mapping)
        and not isinstance(value, dict)
        and not _dataclasses.is_dataclass(value)
    )


def _descended(value: object, container: object = None) -> bool:
    """Whether the pruned walks recurse into *value* (an entry of mapping
    *container*, if given) rather than judge it as one hand-off subtree.

    A non-dict mapping's dataclass values are hand-off units: each is small
    (an occurrence's entity) and the flagger's own field loop judges it
    faster than this module's per-field recursion would.
    """
    if _handoff_dataclass(value):
        return container is None or type(container) is dict
    return isinstance(value, list) or type(value) is dict or _other_mapping(value)


def _collect_marking(
    value: object, out: list[str], flagged: set[int], collect: bool = True
) -> None:
    """:func:`_collect_strings` over *value*, also recording in *flagged*
    the ``id`` of every hand-off subtree that may hold a marker.

    Descends exactly the way :func:`_rewrite_marked_subtrees` does -- lists,
    plain dicts, other non-dataclass mappings, non-``Fact`` dataclasses -- so every object that function
    hands to the walk has been judged here, by
    :func:`_collect_and_flag` (a superset of the walk's strings). One
    traversal answers both "which ordinals exist" and "what needs
    rewriting": the two separate walks were each millions of nodes on a
    real snapshot. An ``id`` stays valid because every flagged object is
    owned by the snapshot for the whole renumbering; a colliding id can
    only cause an unneeded walk, never a skipped one.
    """
    if isinstance(value, list):
        for item in value:
            if _collect_and_flag(item, out, _may_hold_marker, collect=collect):
                flagged.add(id(item))
        return
    if type(value) is dict or _other_mapping(value):
        for k, v in value.items():  # type: ignore[attr-defined]
            key_collected = collect and (
                (isinstance(k, str) and not isinstance(k, _Enum))
                or (_dataclasses.is_dataclass(k) and not isinstance(k, type))
            )
            if _collect_and_flag(k, out, _may_hold_marker, collect=key_collected):
                flagged.add(id(k))
            if _descended(v, value):
                _collect_marking(v, out, flagged, collect)
            elif _collect_and_flag(v, out, _may_hold_marker, collect=collect):
                flagged.add(id(v))
        return
    if _handoff_dataclass(value):
        collected = _collect_plan(type(value)) or ()
        _frozen, _fact_shape, plan = _walk_plan(type(value))
        for name, excluded, _init in plan:
            if excluded:
                continue
            _collect_marking(
                getattr(value, name), out, flagged, collect and name in collected
            )
        return
    if _collect_and_flag(value, out, _may_hold_marker, collect=collect):
        flagged.add(id(value))


def _rewrite_marked_subtrees(
    value: object,
    rewrite: _Callable[[str], str],
    flagged: set[int],
    field_name: str | None = None,
) -> object:
    """:func:`_walk_rewrite_strings`, skipping every subtree
    :func:`_collect_marking` cleared.

    A snapshot with closures in it typically has them in a small share of
    its declarations (oneDAL's live side: ~4.7k of 52k functions), yet the
    plain walk visited every node of every one -- 17.6 s of a 235 s SVS
    compare. ``rewrite`` is the identity on a string with no marker, so
    skipping a cleared subtree is exact, not a heuristic. Mirrors the
    walk's own list/dict/dataclass handling (including its frozen rebuild
    -- ``SemanticIR`` is frozen and holds the largest mapping a snapshot
    has) and hands each flagged subtree to the unchanged walk with the same
    ``field_name`` it would have seen there.
    """
    if isinstance(value, list):
        for i, item in enumerate(value):
            if id(item) in flagged:
                new_item = _walk_rewrite_strings(item, rewrite, field_name=field_name)
                if new_item is not item:
                    value[i] = new_item
        return value
    if type(value) is dict or _other_mapping(value):
        rewritten: dict[object, object] = {}
        changed = False
        for k, v in value.items():  # type: ignore[attr-defined]
            # Only the key types the walk itself rewrites (a flag is a
            # superset, so a flagged tuple key must still stay as-is).
            key_walked = (
                (isinstance(k, str) and not isinstance(k, _Enum))
                or (_dataclasses.is_dataclass(k) and not isinstance(k, type))
                # The unpruned walk rewrites every key of a non-dict mapping.
                or type(value) is not dict
            )
            new_k = (
                _walk_rewrite_strings(k, rewrite, field_name=field_name)
                if key_walked and id(k) in flagged
                else k
            )
            if _descended(v, value):
                new_v = _rewrite_marked_subtrees(v, rewrite, flagged, field_name)
            elif id(v) in flagged:
                new_v = _walk_rewrite_strings(v, rewrite, field_name=field_name)
            else:
                new_v = v
            rewritten[new_k] = new_v
            if new_k != k or new_v is not v:
                changed = True
        if not changed:
            return value
        if type(value) is not dict:
            # The unpruned walk answers a non-dict mapping with a new plain
            # dict too; the owning dataclass re-wraps it (``SemanticIR``'s
            # ``__post_init__``).
            return rewritten
        value.clear()
        value.update(rewritten)
        return value
    if (
        _dataclasses.is_dataclass(value)
        and not isinstance(value, type)
        and _handoff_dataclass(value)
    ):
        is_frozen, _fact_shape, plan = _walk_plan(type(value))
        replacements: dict[str, object] = {}
        frozen_field_updates: dict[str, object] = {}
        for name, excluded, init in plan:
            if excluded:
                continue
            old = getattr(value, name)
            new = _rewrite_marked_subtrees(old, rewrite, flagged, name)
            if new is old:
                continue
            if not is_frozen:
                setattr(value, name, new)
            elif init:
                replacements[name] = new
            else:
                frozen_field_updates[name] = new
        if replacements or frozen_field_updates:
            value = _dataclasses.replace(value, **replacements)
        for name, new in frozen_field_updates.items():
            object.__setattr__(value, name, new)
        return value
    if id(value) not in flagged:
        return value
    return _walk_rewrite_strings(value, rewrite, field_name=field_name)
