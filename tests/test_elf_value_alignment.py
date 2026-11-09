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

"""An exported symbol's address is evidence of alignment only up to its
section's ``sh_addralign`` and the page size (``extract/elf_value_alignment``).

Bug class: placement read as a fact. An optimization rebuild moved ``int
g_counter`` from ``0x4010`` to ``0x4008`` in a 4-aligned ``.bss`` and read as
``exported_object_alignment_reduced`` 16 -> 8. Checked exhaustively over
addresses and section alignments against the oracle "the largest power of two
dividing the address, no larger than the section's alignment (0/1 meaning 1)
or the page", computed by trial division rather than the bit trick.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.extract.elf_value_alignment import symbol_value_alignment


class _Elf:
    def __init__(self, aligns: dict[int, int]) -> None:
        self.aligns = aligns
        self.reads = 0

    def get_section(self, index: int) -> dict[str, int]:
        self.reads += 1
        if index not in self.aligns:
            raise IndexError(index)
        return {"sh_addralign": self.aligns[index]}


def _oracle(address: int, section_align: int | None) -> int:
    if address <= 0:
        return 0
    align = 1
    while align < 4096 and address % (align * 2) == 0:
        align *= 2
    if section_align is not None:
        align = min(align, max(section_align, 1))
    return align


_ADDRESSES = (0, 1, 3, 4, 6, 8, 0x4008, 0x4010, 0x4020, 0x10000, 0x12345)
_SECTION_ALIGNS = (0, 1, 2, 4, 8, 16, 32, 64, 8192)


@pytest.mark.parametrize(
    ("address", "section_align"), list(itertools.product(_ADDRESSES, _SECTION_ALIGNS))
)
def test_alignment_is_bounded_by_the_section(address, section_align):
    elf = _Elf({7: section_align})
    assert symbol_value_alignment(elf, address, 7, {}) == _oracle(
        address, section_align
    )


@pytest.mark.parametrize("address", _ADDRESSES)
@pytest.mark.parametrize("shndx", ["SHN_UNDEF", "SHN_COMMON", 99])
def test_without_a_readable_section_only_the_address_and_page_bound(address, shndx):
    assert symbol_value_alignment(_Elf({}), address, shndx, {}) == _oracle(
        address, None
    )


def test_each_section_header_is_read_once_per_walk():
    elf, cache = _Elf({3: 4}), {}
    results = [
        symbol_value_alignment(elf, a, 3, cache) for a in (0x4010, 0x4008, 0x400C)
    ]
    assert results == [4, 4, 4]
    assert elf.reads == 1
