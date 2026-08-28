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

"""Kernel ABI (``Module.symvers``) facts as data.

The dataclasses ``abicheck.symvers_metadata`` reads a kernel symbol-version
table into. Holds no parsing logic (ADR-061 Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KabiEntry:
    """One ``Module.symvers`` record."""

    crc: str  # e.g. "0x12345678"
    symbol: str
    module: str  # "vmlinux" or a module path
    export_type: str  # EXPORT_SYMBOL / EXPORT_SYMBOL_GPL / EXPORT_SYMBOL_NS[_GPL]
    namespace: str = ""  # 5th column (kernel ≥ 5.4); "" when absent


@dataclass
class KabiMetadata:
    """Parsed ``Module.symvers`` — symbol → entry."""

    entries: dict[str, KabiEntry] = field(default_factory=dict)
