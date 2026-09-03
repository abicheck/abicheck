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

"""ADR-063 Phase 0: the DWARF backend states ``Param.is_va_list_fact`` as
``Fact.unsupported()`` explicitly -- DWARF debug info carries no
va_list-ness signal for any parameter, on any run, the same stance
``dumper_castxml.py`` already takes (see that module's own test file).

``_process_param``'s "no DW_AT_type" branch (a parameter DWARF recorded
with no resolvable type) needs no real DIE/CU machinery beyond a bare
object exposing ``.attributes`` -- built via ``object.__new__`` to skip
``_DwarfSnapshotBuilder.__init__``'s real-ELF-file requirement, the same
bypass pattern ``test_change_registry_pickle.py`` already uses for a
different class. The typed-parameter branch shares the identical
``Fact.unsupported()`` literal and is exercised end-to-end by the
`integration`-marked DWARF test suite against real compiled binaries.
"""
from __future__ import annotations

from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder
from abicheck.model.fact import FactStatus


class _FakeDie:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}


def test_untyped_param_is_va_list_fact_is_unsupported() -> None:
    builder = object.__new__(_DwarfSnapshotBuilder)
    param = builder._process_param(_FakeDie(), CU=None)
    assert param is not None
    assert param.type == "?"
    assert param.is_va_list is False
    assert param.is_va_list_fact.status is FactStatus.UNSUPPORTED
