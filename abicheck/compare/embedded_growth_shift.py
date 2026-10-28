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

"""Why a record member moved: growth of an earlier member embedded by value.

An aggregate that embeds other records by value (Level Zero's
``ze_dditable_t`` holds every per-object dispatch table) shifts every later
member when one embedded record grows. :func:`stamp_embedded_growth_shifts`
records that cause on each ``type_field_offset_changed`` finding it fully
explains, as ``review_evidence["offset_shift_cause"]``; the finding itself is
unchanged and keeps its verdict. Policy decides what the cause is worth
(``SelectorSet.shifted_by_type_pattern``, issue #1759).

The explanation is accepted only when it is exact: every member before the
shifted one keeps its name, type spelling and order, the shifted member keeps
its type, and the shift equals the summed size growth of the preceding
by-value record members. A reorder, insertion, removal, retype, padding
change or shrink leaves the finding unstamped.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

from ..model import RecordType, TypeField
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.snapshot import AbiSnapshot
from ..model.snapshot_index import record_lookup_index
from .typedef_spelling_keys import (
    TypedefSpellingIndex,
    typedef_spelling_index,
    typedef_target,
)

#: ``review_evidence`` key this module writes and the policy selector reads.
OFFSET_SHIFT_CAUSE_KEY = "offset_shift_cause"

_TAG_OR_CV_WORDS = frozenset({"struct", "class", "const", "volatile"})
_NOT_BY_VALUE = frozenset("*&[(")
_MAX_TYPEDEF_HOPS = 16


class _Side(NamedTuple):
    """One snapshot's records and typedefs, each looked up the way the closure spells them."""

    records: dict[str, RecordType]
    snap: AbiSnapshot
    typedefs: TypedefSpellingIndex


def _side(snap: AbiSnapshot) -> _Side:
    """Index one side's records by qualified identity and its typedefs by the
    closure's resolver.

    ``RecordType.name`` is the castxml leaf, so keying on it merged ``a::Box``
    with ``b::Box`` (last one wins) and never matched a qualified spelling.
    The qualified-identity index keeps both, and gives a bare alias only to a
    leaf that names exactly one record.
    """
    records = record_lookup_index(
        t for t in snap.declarations.types if t.kind != "union"
    )
    return _Side(records, snap, typedef_spelling_index(snap))


def _resolve_by_value_record(spelling: str, side: _Side) -> RecordType | None:
    """The record a by-value member of type *spelling* embeds, else ``None``.

    A spelling that names several things resolves to nothing: a bare name two
    scopes declare, or a typedef key shared by colliding member aliases. No
    size is claimed for a record that may not be the embedded one.
    """
    name = spelling
    for _ in range(_MAX_TYPEDEF_HOPS):
        if any(ch in _NOT_BY_VALUE for ch in name):
            return None
        name = " ".join(w for w in name.split() if w not in _TAG_OR_CV_WORDS)
        if name in side.records:
            return side.records[name]
        keys = side.typedefs.keys_for(name)
        if len(keys) != 1:
            return None
        name = typedef_target(side.snap, keys[0])
    return None


def _explained_shift(
    old_rec: RecordType,
    new_rec: RecordType,
    field_name: str,
    old_side: _Side,
    new_side: _Side,
) -> list[dict[str, object]] | None:
    """Grown members that exactly explain *field_name*'s shift, else ``None``."""
    old_fields, new_fields = old_rec.fields, new_rec.fields
    idx = next((i for i, f in enumerate(old_fields) if f.name == field_name), None)
    if idx is None or idx >= len(new_fields):
        return None
    shifted_old, shifted_new = old_fields[idx], new_fields[idx]
    if not _same_member(shifted_old, shifted_new):
        return None
    if shifted_old.offset_bits is None or shifted_new.offset_bits is None:
        return None
    grown: list[dict[str, object]] = []
    total = 0
    for f_old, f_new in zip(old_fields[:idx], new_fields[:idx], strict=True):
        if not _same_member(f_old, f_new) or f_old.is_bitfield:
            return None
        rec_old = _resolve_by_value_record(f_old.type, old_side)
        rec_new = _resolve_by_value_record(f_new.type, new_side)
        if rec_old is None or rec_new is None:
            continue
        if rec_old.size_bits is None or rec_new.size_bits is None:
            return None
        delta = rec_new.size_bits - rec_old.size_bits
        if delta < 0:
            return None
        if delta:
            total += delta
            grown.append({"member": f_old.name, "type": rec_new.name, "bits": delta})
    if not grown or shifted_new.offset_bits - shifted_old.offset_bits != total:
        return None
    return grown


def _same_member(a: TypeField, b: TypeField) -> bool:
    return a.name == b.name and a.type == b.type


def stamp_embedded_growth_shifts(
    changes: Iterable[Change], old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """Stamp each offset shift fully explained by embedded growth; return *changes*."""
    out = list(changes)
    shifts = [
        c
        for c in out
        if c.kind == ChangeKind.TYPE_FIELD_OFFSET_CHANGED and c.field_name
    ]
    if not shifts:
        return out
    old_side, new_side = _side(old), _side(new)
    for c in shifts:
        old_rec = old_side.records.get(c.symbol)
        new_rec = new_side.records.get(c.symbol)
        if old_rec is None or new_rec is None or c.field_name is None:
            continue
        grown = _explained_shift(old_rec, new_rec, c.field_name, old_side, new_side)
        if grown is None:
            continue
        evidence = dict(c.review_evidence or {})
        evidence[OFFSET_SHIFT_CAUSE_KEY] = {
            "cause": "embedded_member_growth",
            "grown_members": grown,
        }
        c.review_evidence = evidence
    return out
