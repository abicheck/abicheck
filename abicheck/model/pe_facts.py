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

"""PE/COFF export-table and header facts as data.

The dataclasses ``abicheck.pe_metadata`` reads a Windows PE/COFF image into.
Holds no parsing logic (ADR-061 Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property

from .fact import Fact, bridge_legacy_and_fact


class PeSymbolType(str, Enum):
    EXPORTED = "exported"  # ordinal / name in export table
    FORWARDED = "forwarded"  # forwarded to another DLL
    OTHER = "other"


@dataclass
class PeExport:
    """A single exported symbol from a PE export directory."""

    name: str
    ordinal: int = 0
    sym_type: PeSymbolType = PeSymbolType.EXPORTED
    forwarder: str = ""  # e.g. "NTDLL.RtlAllocateHeap" for forwarded exports


@dataclass
class PeMetadata:
    """PE metadata from a Windows DLL.

    NOTE: Do NOT add ``frozen=True`` — ``@cached_property`` requires a
    writable ``__dict__``.
    """

    # DLL characteristics
    machine: str = ""  # e.g. "IMAGE_FILE_MACHINE_AMD64"
    characteristics: int = 0  # IMAGE_FILE_HEADER.Characteristics
    dll_characteristics: int = 0  # IMAGE_OPTIONAL_HEADER.DllCharacteristics

    # Imports and exports
    exports: list[PeExport] = field(default_factory=list)
    imports: dict[str, list[str]] = field(
        default_factory=dict
    )  # dll_name → [func_names]
    # Delay-loaded imports (IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT): resolved on
    # first call rather than at load time, so a missing DLL fails late.
    # Tri-state: None = not captured (legacy snapshot written before this
    # field existed); {} = parsed PE with no delay-load directory.
    delay_imports: dict[str, list[str]] | None = None

    # Version resource (VS_FIXEDFILEINFO)
    file_version: str = ""  # e.g. "10.0.19041.1"
    product_version: str = ""  # e.g. "10.0.19041.1"

    # Minimum OS floor: OPTIONAL_HEADER.MajorSubsystemVersion.MinorSubsystemVersion
    # (e.g. "6.1" = Windows 7). "" = not captured (legacy snapshot).
    subsystem_version: str = ""

    # ADR-063 Phase 5 (seventh batch): Fact[...] sibling of delay_imports --
    # the identical schema-version-driven case-(b) shape as ElfMetadata's
    # own three case-(b) fields.
    delay_imports_fact: Fact[dict[str, list[str]] | None] | None = field(
        default=None, kw_only=True
    )

    def __post_init__(self) -> None:
        self.delay_imports, self.delay_imports_fact = bridge_legacy_and_fact(
            self.delay_imports, self.delay_imports_fact, None, None
        )

    @cached_property
    def export_map(self) -> dict[str, PeExport]:
        """Name → PeExport mapping (built once, cached on first access)."""
        return {e.name: e for e in self.exports if e.name}
