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

import functools
from typing import TYPE_CHECKING, NamedTuple

from .checker_types import Change
from .compare.enum_sentinel import identifier_tokens, is_confirmed_enum_sentinel
from .diff_helpers import make_change
from .model.change_catalog.kinds import ChangeKind
from .model.name_heuristics import StructuralFact, register_severity_raising_heuristic

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


#: Token tails that mark an *enum type* as a serialization-tag registry. A bare
#: ``_tag`` suffix is deliberately absent: ``format_tag``, ``dispatch_tag`` and
#: similar layout/dispatch enums end in ``tag`` without being persisted ids,
#: and flagging their members (including end markers such as
#: ``format_tag_last``) duplicated the ordinary enum-member findings.
_TAG_TYPE_TOKEN_TAILS: tuple[tuple[str, ...], ...] = (
    ("serialization", "tag"),
    ("serializationtag",),
    ("serialization", "tags"),
    ("tag", "id"),
    ("tagid",),
    ("tag", "ids"),
)


@functools.lru_cache(maxsize=4096)
def _enum_type_is_tag_registry(enum_name: str) -> bool:
    """Whether an enum *type* name is strong evidence of a tag-id registry.

    Spelling-independent: the leaf is tokenized (snake/Camel/UPPER), and a
    trailing C typedef ``_t`` token is dropped, so ``ns::SerializationTag``,
    ``ns_serialization_tag_t`` and ``SERIALIZATION_TAG`` classify alike --
    and ``dnnl::memory::format_tag`` / ``dnnl_format_tag_t`` both do *not*.
    """
    tokens = identifier_tokens(_last_segment(enum_name))
    if len(tokens) > 1 and tokens[-1] == "t":
        tokens = tokens[:-1]
    return any(
        tuple(tokens[-len(tail) :]) == tail
        for tail in _TAG_TYPE_TOKEN_TAILS
        if len(tokens) >= len(tail)
    )


def _is_tag_candidate(name: str, *, enum_type: str | None = None) -> bool:
    """The spelling half: *name* (or its enum *type*) follows a tag-id
    naming convention. Only a nomination -- see :data:`SERIALIZATION_TAG`."""
    if enum_type is not None and _enum_type_is_tag_registry(enum_type):
        return True
    return _looks_like_serialization_tag(name)


def _value_changed(fact_input: tuple[str, str]) -> bool:
    old_val, new_val = fact_input
    return old_val != new_val


#: Registered severity-raising heuristic (design-hardening Phase 5): a
#: tag-id name raises a value change to ``serialization_tag_changed``
#: (BREAKING) only on the structural fact that the value itself changed.
SERIALIZATION_TAG = register_severity_raising_heuristic(
    "serialization_tag",
    owner=__name__,
    description=(
        "a *_tag_id/*_serialization_tag constant, or a member of a "
        "SerializationTag/TagId enum, is a persisted class id"
    ),
    matcher=_is_tag_candidate,
    helpers=(_looks_like_serialization_tag, _enum_type_is_tag_registry),
    fact=StructuralFact("compare.serialization_tag.value_changed", _value_changed),
    vocabularies=("_TAG_SUFFIX_PATTERNS", "_TAG_EXACT_LEAVES", "_TAG_TYPE_TOKEN_TAILS"),
)


class _Valued(NamedTuple):
    value: str
    entity: str
    enum_type: str | None = None
    member: str | None = None


def _collect_valued_declarations(snap: AbiSnapshot) -> dict[str, _Valued]:
    """Every declaration with a recorded value: ``{name: _Valued}``.

    Three data sources, in order of reliability:
      1. ``snap.constants`` — ``constexpr`` / ``#define`` values.
      2. ``snap.variables`` — global ``const`` variables with values.
      3. ``snap.enums`` — enumerators, keyed ``Enum::member``.

    The entity is the ``ChangeEntity`` *value* the contributing source
    resolves to, carried per entry rather than decided once for the kind. A
    single declared entity cannot be right here: the pool spans constants,
    variables and enum members, and the kind declared ``type``, which is
    wrong for all three at once -- so ``--view show=variables`` and
    ``show=enums`` both omitted real findings while the machine reports
    spelled them as types (Codex review, PR #1284). Constants share the
    ``variable`` entity with real variables, the way ``constant_changed``
    already does, so the distinction that survives is variable-vs-enum.

    ``setdefault`` keeps the *first* contributor's entity along with its
    value, so the reliability order above decides both together and the two
    can never disagree about which source a row came from. No name filter is
    applied here: the tag convention is consulted only through
    :data:`SERIALIZATION_TAG`, together with the value change it needs.
    """
    out: dict[str, _Valued] = {}
    for name, value in (snap.declarations.constants or {}).items():
        if value is not None:
            out.setdefault(name, _Valued(str(value), _ENTITY_VARIABLE))
    for var in snap.declarations.variables:
        if var.value is not None:
            out.setdefault(var.name, _Valued(str(var.value), _ENTITY_VARIABLE))
    for enum_t in snap.declarations.enums or []:
        for m in enum_t.members:
            out.setdefault(
                f"{enum_t.name}::{m.name}",
                _Valued(str(m.value), _ENTITY_ENUM, enum_t.name, m.name),
            )
    return out


def _is_tag(name: str, entry: _Valued, other_value: str) -> bool:
    return SERIALIZATION_TAG.confirmed(
        entry.member or name, (other_value, entry.value), enum_type=entry.enum_type
    )


def _confirmed_end_marker(snap: AbiSnapshot, entry: _Valued) -> bool:
    """Whether *entry* is a structurally confirmed end marker in *snap*
    (name nominates, ``holds_enum_maximum`` confirms)."""
    if entry.enum_type is None or entry.member is None:
        return False
    for enum_t in snap.declarations.enums or []:
        if enum_t.name == entry.enum_type:
            values = {x.name: x.value for x in enum_t.members}
            return is_confirmed_enum_sentinel(entry.member, values)
    return False


def _end_marker_on_both_sides(
    old: AbiSnapshot, new: AbiSnapshot, o: _Valued | None, n: _Valued
) -> bool:
    return (
        o is not None
        and _confirmed_end_marker(old, o)
        and _confirmed_end_marker(new, n)
    )


def detect_serialization_tag_changes(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Emit ``SERIALIZATION_TAG_CHANGED`` for tag constants whose values
    changed between *old* and *new*, including swaps."""
    old_vals = _collect_valued_declarations(old)
    new_vals = _collect_valued_declarations(new)
    findings: list[Change] = []
    for name, old_entry in old_vals.items():
        new_entry = new_vals.get(name)
        # Unchanged values are the common case and can never be confirmed:
        # skip them before the (costlier) spelling check runs.
        if new_entry is None or new_entry.value == old_entry.value:
            continue
        if not _is_tag(name, old_entry, new_entry.value):
            continue
        # An end-of-list marker (``*_last``, ``LastSymbol``, ``*_count``) is
        # not a persisted id: its value moves whenever a member is added, and
        # the enum-member detectors already report it. It is excluded only
        # when it is a confirmed end marker on *both* sides.
        if _end_marker_on_both_sides(old, new, old_entry, new_entry):
            continue
        old_val, new_val = old_entry.value, new_entry.value
        partner = next(
            (
                n
                for n, e in new_vals.items()
                if e.value == old_val
                and n != name
                and SERIALIZATION_TAG.matcher(e.member or n, enum_type=e.enum_type)
                and not _end_marker_on_both_sides(old, new, old_vals.get(n), e)
            ),
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
                entity_discriminator=old_entry.entity,
            )
        )
    return findings


__all__ = [
    "detect_serialization_tag_changes",
]
