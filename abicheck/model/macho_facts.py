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

"""Mach-O export-trie and load-command facts as data.

The dataclasses ``abicheck.macho_metadata`` reads a Mach-O image into. Holds
no parsing logic (ADR-061 Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property

from .fact import Fact, bridge_legacy_and_fact


class MachoSymbolType(str, Enum):
    EXPORTED = "exported"  # N_EXT: externally visible
    WEAK = "weak"  # N_WEAK_DEF: weak definition
    REEXPORT = "reexport"  # re-exported from another dylib
    OTHER = "other"


@dataclass
class MachoExport:
    """A single exported symbol from a Mach-O binary."""

    name: str
    sym_type: MachoSymbolType = MachoSymbolType.EXPORTED
    is_weak: bool = False
    is_data: bool = False  # True when symbol lives in __DATA segment (global variable)


@dataclass
class MachoMetadata:
    """Mach-O metadata from a macOS dynamic library.

    NOTE: Do NOT add ``frozen=True`` — ``@cached_property`` requires a
    writable ``__dict__``.
    """

    # Binary characteristics
    cpu_type: str = ""  # selected slice, e.g. "ARM64", "X86_64"
    cpu_types: list[str] = field(
        default_factory=list
    )  # ALL slices in a fat/universal binary
    filetype: str = ""  # e.g. "MH_DYLIB", "MH_BUNDLE"
    flags: int = 0  # MH_* flags bitmask

    # Install name (equivalent of ELF SONAME)
    install_name: str = ""  # LC_ID_DYLIB install name

    # Dependencies (equivalent of ELF DT_NEEDED)
    dependent_libs: list[str] = field(default_factory=list)  # LC_LOAD_DYLIB

    # Re-exported libraries
    reexported_libs: list[str] = field(default_factory=list)  # LC_REEXPORT_DYLIB

    # Exported symbols
    exports: list[MachoExport] = field(default_factory=list)

    # Imported (undefined, N_UNDF) external symbol names — the Mach-O analogue
    # of ELF undefined imports. Needed to see a CPython extension's libpython
    # C-API import surface (G14). Leading '_' stripped, matching exports.
    imported_symbols: list[str] = field(default_factory=list)

    # Version info from LC_ID_DYLIB
    current_version: str = ""  # e.g. "1.2.3"
    compat_version: str = ""  # e.g. "1.0.0"

    # Minimum OS version
    min_os_version: str = ""  # from LC_VERSION_MIN_MACOSX or LC_BUILD_VERSION

    # Runtime search paths (LC_RPATH) — the Mach-O analogue of ELF DT_RUNPATH.
    # Tri-state: None = not captured (legacy snapshot written before this
    # field existed); [] = parsed Mach-O carrying no LC_RPATH commands.
    rpaths: list[str] | None = None

    # ADR-063 Phase 5 (seventh batch): Fact[...] sibling of rpaths -- the
    # identical schema-version-driven case-(b) shape as ElfMetadata's/
    # PeMetadata's own case-(b) fields.
    rpaths_fact: Fact[list[str] | None] | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        self.rpaths, self.rpaths_fact = bridge_legacy_and_fact(
            self.rpaths, self.rpaths_fact, None, None
        )

    @cached_property
    def export_map(self) -> dict[str, MachoExport]:
        """Name → MachoExport mapping (built once, cached on first access)."""
        return {e.name: e for e in self.exports if e.name}
