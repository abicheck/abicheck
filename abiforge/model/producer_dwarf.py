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

"""Which producers record a deleted member in DWARF (``DW_AT_deleted``).

A ``= delete`` member is recorded in DWARF only by a producer that emits
``DW_AT_deleted``. The producer's DWARF decides what an edit's DWARF
evidence can show, so an operator's expectation for that evidence depends
on the producer. The table is measured, not inferred from a family:

    printf 'struct Calc { int m(int); int n(int) = delete; };\\n' > d.cpp
    <producer> -std=c++17 -g -O0 -c d.cpp -o d.o
    readelf --debug-dump=info d.o | grep -c DW_AT_deleted

A producer missing from the table has no measurement; the rules read it as
"unknown" and keep the operator's lenient base expectation rather than guess.
``tests/test_producer_dw_at_deleted.py`` re-measures every installed producer
and fails when the table disagrees, so a compiler change shows up as a failing
test, not as silent drift.
"""

from __future__ import annotations

from .config import Toolchain

__all__ = ["DW_AT_DELETED_BY_PRODUCER", "records_deleted_member"]

#: ``(family, major)`` -> whether that producer's DWARF records ``DW_AT_deleted``.
DW_AT_DELETED_BY_PRODUCER: dict[tuple[str, int], bool] = {
    ("gcc", 12): True,
    ("gcc", 13): True,
    ("gcc", 14): True,
    ("clang", 16): False,
    ("clang", 17): True,
    ("clang", 18): True,
    ("clang", 19): True,
}


def records_deleted_member(producer: Toolchain) -> bool | None:
    """Whether *producer*'s DWARF records a deleted member; ``None`` when unmeasured."""
    return DW_AT_DELETED_BY_PRODUCER.get((producer.family, producer.version))
