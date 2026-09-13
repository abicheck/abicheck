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

from .checker_types import Change
from .diff_helpers import make_change
from .model.change_catalog.kinds import ChangeKind

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

#: ``ChangeEntity`` values this detector attributes a tag to, spelled as the
#: plain strings ``Change.entity_discriminator`` carries. Named once rather
#: than repeated as a bare literal at each of the three pools below, so the
#: three cannot drift apart; spelled out rather than imported from
#: ``ChangeEntity`` because that enum lives in `model`, and the resolver
#: (`reporter_markdown.entity_for_change`) validates the string against it
#: anyway -- an unrecognized spelling falls back to the declared entity.
_ENTITY_VARIABLE = "variable"
_ENTITY_ENUM = "enum"

_TAG_EXACT_LEAVES: frozenset[str] = frozenset(
    {
        "tag_id",
        "tagid",
        "serializationtag",
    }
)


def _looks_like_serialization_tag(name: str) -> bool:
    if not name:
        return False
    leaf = _last_segment(name).lower()
    if leaf in _TAG_EXACT_LEAVES:
        return True
    return any(leaf.endswith(p) for p in _TAG_SUFFIX_PATTERNS)


def _collect_tag_constants(snap: AbiSnapshot) -> dict[str, tuple[str, str]]:
    """Return ``{tag_name: (stringified_value, display_entity)}``.

    Three data sources, in order of reliability:
      1. ``snap.constants`` — ``constexpr`` / ``#define`` values.
      2. ``snap.variables`` — global ``const`` variables with values.
      3. ``snap.enums`` — enumerators whose enclosing type name or own
         name matches the tag convention.

    The second element is the ``ChangeEntity`` *value* the contributing
    source resolves to, carried per entry rather than decided once for the
    kind. A single declared entity cannot be right here: the pool spans
    constants, variables and enum members, and the kind declared ``type``,
    which is wrong for all three at once -- so ``--view show=variables`` and
    ``show=enums`` both omitted real findings while the machine reports
    spelled them as types (Codex review, PR #1284). Constants share the
    ``variable`` entity with real variables, the way ``constant_changed``
    already does, so the distinction that survives is variable-vs-enum.

    ``setdefault`` keeps the *first* contributor's entity along with its
    value, so the reliability order above decides both together and the two
    can never disagree about which source a row came from.
    """
    out: dict[str, tuple[str, str]] = {}
    for name, value in (snap.constants or {}).items():
        if _looks_like_serialization_tag(name) and value is not None:
            out.setdefault(name, (str(value), _ENTITY_VARIABLE))
    for var in snap.variables:
        if _looks_like_serialization_tag(var.name) and var.value is not None:
            out.setdefault(var.name, (str(var.value), _ENTITY_VARIABLE))
    for enum_t in snap.enums or []:
        enum_leaf = _last_segment(enum_t.name).lower()
        type_is_tag = enum_leaf in _TAG_EXACT_LEAVES or any(
            enum_leaf.endswith(p) for p in _TAG_SUFFIX_PATTERNS
        )
        for m in enum_t.members:
            full = f"{enum_t.name}::{m.name}"
            if type_is_tag or _looks_like_serialization_tag(m.name):
                out.setdefault(full, (str(m.value), _ENTITY_ENUM))
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
    for name, (old_val, entity) in old_tags.items():
        new_entry = new_tags.get(name)
        if new_entry is None or new_entry[0] == old_val:
            continue
        new_val = new_entry[0]
        partner = next(
            (n for n, (v, _e) in new_tags.items() if v == old_val and n != name),
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
        findings.append(
            make_change(
                ChangeKind.SERIALIZATION_TAG_CHANGED,
                symbol=name,
                description=desc,
                old_value=old_val,
                new_value=new_val,
                # Per finding, since one kind covers three producers' pools.
                entity_discriminator=entity,
            )
        )
    return findings


__all__ = [
    "detect_serialization_tag_changes",
]
