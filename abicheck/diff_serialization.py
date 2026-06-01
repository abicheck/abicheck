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

"""Serialization tag-id detection.

Generic detector for the failure mode where a class-identifier
constant used during persistence (the "serialization tag") changes
value between releases. Symbol table, types, and layout are all
unchanged — every conventional ABI check passes — but saved state
from the old library deserialises as the wrong class against the new.

Originally lived in :mod:`abicheck.diff_cpp_patterns` because the pattern
was first identified in a numerical library family. The detection is
naming-convention based (``*_tag_id``, ``*_serialization_tag``, …) and
applies to any library that uses the same persistence convention.

Re-exported from :mod:`abicheck.diff_cpp_patterns` for backwards
compatibility with existing tests; new code should import from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .checker_types import Change

if TYPE_CHECKING:
    from .model import AbiSnapshot


def _last_segment(qualified_name: str) -> str:
    if "::" not in qualified_name:
        return qualified_name
    return qualified_name.rsplit("::", 1)[-1]


# Naming conventions that mark a constant as a serialization tag id.
_TAG_SUFFIX_PATTERNS: tuple[str, ...] = (
    "_serialization_tag",
    "_serializationtag",
    "_tag",
    "serializationtag",
    "_tag_id",
    "_tagid",
)

_TAG_EXACT_LEAVES: frozenset[str] = frozenset({
    "tag_id",
    "tagid",
    "serializationtag",
})


def _looks_like_serialization_tag(name: str) -> bool:
    if not name:
        return False
    leaf = _last_segment(name).lower()
    if leaf in _TAG_EXACT_LEAVES:
        return True
    return any(leaf.endswith(p) for p in _TAG_SUFFIX_PATTERNS)


def _collect_tag_constants(snap: AbiSnapshot) -> dict[str, str]:
    """Return ``{constant_name: stringified_value}`` for tag-shaped constants.

    Three data sources, in order of reliability:
      1. ``snap.constants`` — ``constexpr`` / ``#define`` values.
      2. ``snap.variables`` — global ``const`` variables with values.
      3. ``snap.enums`` — enumerators whose enclosing type name or own
         name matches the tag convention.
    """
    out: dict[str, str] = {}
    for name, value in (snap.constants or {}).items():
        if _looks_like_serialization_tag(name) and value is not None:
            out.setdefault(name, str(value))
    for var in snap.variables:
        if _looks_like_serialization_tag(var.name) and var.value is not None:
            out.setdefault(var.name, str(var.value))
    for enum_t in snap.enums or []:
        enum_leaf = _last_segment(enum_t.name).lower()
        type_is_tag = enum_leaf in _TAG_EXACT_LEAVES or any(
            enum_leaf.endswith(p) for p in _TAG_SUFFIX_PATTERNS
        )
        for m in enum_t.members:
            full = f"{enum_t.name}::{m.name}"
            if type_is_tag or _looks_like_serialization_tag(m.name):
                out.setdefault(full, str(m.value))
    return out


def detect_serialization_tag_changes(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Emit ``SERIALIZATION_TAG_CHANGED`` for tag constants whose values
    changed between *old* and *new*, including swaps."""
    old_tags = _collect_tag_constants(old)
    new_tags = _collect_tag_constants(new)
    findings: list[Change] = []
    for name, old_val in old_tags.items():
        new_val = new_tags.get(name)
        if new_val is None or new_val == old_val:
            continue
        partner = next(
            (n for n, v in new_tags.items() if v == old_val and n != name),
            None,
        )
        if partner is not None:
            desc = (
                f"Serialization tag '{name}' value changed {old_val} → "
                f"{new_val}; this is the same value previously assigned to "
                f"'{partner}'. Saved data referencing the old value now "
                f"deserialises as the wrong class."
            )
        else:
            desc = (
                f"Serialization tag '{name}' value changed {old_val} → "
                f"{new_val}; persisted data using the old tag id is no "
                f"longer recognised."
            )
        findings.append(Change(
            kind=ChangeKind.SERIALIZATION_TAG_CHANGED,
            symbol=name,
            description=desc,
            old_value=old_val,
            new_value=new_val,
        ))
    return findings


__all__ = [
    "detect_serialization_tag_changes",
]
