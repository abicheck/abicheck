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

"""Match old and new ELF symbols across symbol-versioned aliases.

``ElfMetadata.symbol_map`` keeps one entry per bare name, so an alias such as
``foo@VER1`` that is not the last ``.dynsym`` entry for ``foo`` was never
compared. :func:`pair_elf_aliases` matches every alias that exists on both
sides, which is the old/new matching the symbol diff needs.
"""

from __future__ import annotations

from collections.abc import Callable

from ..model.elf_facts import ElfMetadata, ElfSymbol
from .declined_comparisons import record_declined


def pair_elf_aliases(
    old_elf: ElfMetadata,
    new_elf: ElfMetadata,
    keep: Callable[[str], bool] = lambda _name: True,
) -> list[tuple[str, ElfSymbol, ElfSymbol]]:
    """Pair every symbol-versioned alias that exists on both sides.

    A bare name with one entry per side pairs as before. When a name has several
    aliases, they pair by version tag, so each alias is compared with its own old
    counterpart and the order of ``.dynsym`` cannot change the result.

    A version tag present on one side only pairs when exactly one alias is left
    unpaired on each side and both share ``is_default``, which reads as a version
    rename. Any other leftover is ambiguous: it is skipped rather than matched to
    an arbitrary alias, and recorded as a declined comparison so the report shows
    it instead of dropping it silently.

    *keep* selects the bare names this caller judges. Other names are neither
    paired nor declined, so a detector never reports a decline for a name it
    would not have examined.
    """
    new_aliases = new_elf.symbol_aliases
    pairs: list[tuple[str, ElfSymbol, ElfSymbol]] = []
    for name, old_list in old_elf.symbol_aliases.items():
        new_list = new_aliases.get(name)
        if not new_list or not keep(name):
            continue
        if len(old_list) == 1 and len(new_list) == 1:
            pairs.append((name, old_list[0], new_list[0]))
            continue
        old_by_ver = _group_by_version(old_list)
        new_by_ver = _group_by_version(new_list)
        old_left: list[ElfSymbol] = []
        new_left: list[ElfSymbol] = []
        for ver, old_group in old_by_ver.items():
            new_group = new_by_ver.get(ver, [])
            if len(old_group) == 1 and len(new_group) == 1:
                pairs.append((name, old_group[0], new_group[0]))
            else:
                old_left.extend(old_group)
                new_left.extend(new_group)
        for ver, new_group in new_by_ver.items():
            if ver not in old_by_ver:
                new_left.extend(new_group)
        if len(old_left) == 1 and len(new_left) == 1:
            old_alias, new_alias = old_left[0], new_left[0]
            if old_alias.is_default == new_alias.is_default:
                pairs.append((name, old_alias, new_alias))
                continue
        if old_left or new_left:
            record_declined(
                name,
                "symbol-versioned aliases could not be paired by version tag "
                f"(old: {len(old_left)} unmatched, new: {len(new_left)} unmatched)",
            )
    return pairs


def _group_by_version(aliases: list[ElfSymbol]) -> dict[str, list[ElfSymbol]]:
    groups: dict[str, list[ElfSymbol]] = {}
    for alias in aliases:
        groups.setdefault(alias.version, []).append(alias)
    return groups
