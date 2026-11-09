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

"""The stack-protector setting each compilation unit was built with.

``__stack_chk_fail`` disappearing from a binary has two causes: the hardening
flag was weakened, or no frame needs a canary any more (``-O2`` keeps a
fixed-size buffer in registers, an edit removed the array). The binary alone
cannot tell them apart. A compiler that records its command line in
``DW_AT_producer`` (gcc's default ``-grecord-gcc-switches``; clang only with
``-grecord-command-line``) states the setting per unit, so a census of every
unit's setting can.

The census is ``{level: number of units}`` over :class:`StackProtectorLevel`
values. ``unrecorded`` counts a unit whose producer records no switches, or
records switches but none that selects the protector: its setting is
compiler-default and unknown (``--enable-default-ssp`` toolchains add the
flag without spelling it). An assembler unit holds no compiled frame and is
left out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum

__all__ = [
    "PROTECTED_LEVELS",
    "StackProtectorLevel",
    "StackProtectorTransition",
    "compare_stack_protector",
    "level_from_producer",
    "stack_protector_census",
]


class StackProtectorLevel(StrEnum):
    """One unit's stack-protector setting, weakest first."""

    NONE = "none"  # -fno-stack-protector
    EXPLICIT = "explicit"  # -fstack-protector-explicit: attributed functions only
    BASIC = "basic"  # -fstack-protector
    STRONG = "strong"  # -fstack-protector-strong
    ALL = "all"  # -fstack-protector-all
    UNRECORDED = "unrecorded"


_SWITCHES: Mapping[str, StackProtectorLevel] = {
    "-fno-stack-protector": StackProtectorLevel.NONE,
    "-fstack-protector-explicit": StackProtectorLevel.EXPLICIT,
    "-fstack-protector": StackProtectorLevel.BASIC,
    "-fstack-protector-strong": StackProtectorLevel.STRONG,
    "-fstack-protector-all": StackProtectorLevel.ALL,
}

_RANK: Mapping[StackProtectorLevel, int] = {
    StackProtectorLevel.NONE: 0,
    StackProtectorLevel.EXPLICIT: 1,
    StackProtectorLevel.BASIC: 2,
    StackProtectorLevel.STRONG: 3,
    StackProtectorLevel.ALL: 4,
}

#: Levels under which every function with a vulnerable frame gets a canary.
PROTECTED_LEVELS = frozenset(
    {StackProtectorLevel.BASIC, StackProtectorLevel.STRONG, StackProtectorLevel.ALL}
)

#: ``DW_LANG_Mips_Assembler``: what gcc and clang give an assembler unit.
_DW_LANG_ASSEMBLER = 0x8001


def _is_assembler_unit(producer: str | None, language: int | None) -> bool:
    if language == _DW_LANG_ASSEMBLER:
        return True
    return producer is not None and producer.startswith("GNU AS ")


def level_from_producer(producer: str | None) -> StackProtectorLevel:
    """The setting one ``DW_AT_producer`` records: the last protector switch
    on its command line (the driver's last one wins), else ``unrecorded``."""
    if not producer:
        return StackProtectorLevel.UNRECORDED
    level = StackProtectorLevel.UNRECORDED
    for token in producer.split():
        level = _SWITCHES.get(token, level)
    return level


def stack_protector_census(
    units: Iterable[tuple[str | None, int | None]],
) -> dict[str, int] | None:
    """``{level: units}`` over *units* (``(DW_AT_producer, DW_AT_language)``
    per CU); ``None`` when no compiled unit is present (nothing to say)."""
    census: dict[str, int] = {}
    for producer, language in units:
        if producer is None and language is None:
            continue
        if _is_assembler_unit(producer, language):
            continue
        level = level_from_producer(producer).value
        census[level] = census.get(level, 0) + 1
    return dict(sorted(census.items())) or None


class StackProtectorTransition(StrEnum):
    """What two censuses establish about the protector between two builds."""

    #: Every unit on both sides records its setting, every new unit is
    #: protected, and the weakest setting did not drop.
    UNCHANGED = "unchanged"
    #: Every old unit records its setting and a recorded new unit is weaker
    #: than the weakest of them.
    WEAKENED = "weakened"
    #: Not weakened, but a recorded new unit is built without a protector
    #: (``none``/``explicit``), so the flag is not on in every unit.
    PARTIAL = "partial"
    #: A side has no census, or a unit whose setting is not recorded.
    UNKNOWN = "unknown"


def _weakest(levels: set[StackProtectorLevel]) -> int:
    return min(_RANK[level] for level in levels)


def compare_stack_protector(
    old: Mapping[str, int] | None, new: Mapping[str, int] | None
) -> StackProtectorTransition:
    """Classify the protector between *old* and *new* censuses."""
    if not old or not new:
        return StackProtectorTransition.UNKNOWN
    old_levels = {StackProtectorLevel(k) for k, n in old.items() if n > 0}
    new_levels = {StackProtectorLevel(k) for k, n in new.items() if n > 0}
    old_known = old_levels - {StackProtectorLevel.UNRECORDED}
    new_known = new_levels - {StackProtectorLevel.UNRECORDED}
    # Proven only when no old unit is unrecorded (one could have been the
    # weakest); an unrecorded new unit cannot lower the new side's weakest.
    if (
        StackProtectorLevel.UNRECORDED not in old_levels
        and new_known
        and _weakest(new_known) < _weakest(old_known)
    ):
        return StackProtectorTransition.WEAKENED
    if new_known - PROTECTED_LEVELS:
        return StackProtectorTransition.PARTIAL
    if StackProtectorLevel.UNRECORDED in old_levels | new_levels:
        return StackProtectorTransition.UNKNOWN
    return StackProtectorTransition.UNCHANGED
