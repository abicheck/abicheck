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

"""Typed facts for application/plugin compatibility checking (ADR-005).

The consumer-scoping check (``compare --used-by``, ``--required-symbol``)
splits into three ADR-061 owners, and these are the values that cross
between them:

* ``extract.consumer_imports`` reads an application binary into
  :class:`ConsumerImportFacts`; ``extract.library_export_facts`` reads an
  old/new library (a real binary or a saved snapshot) into
  :class:`LibraryExportFacts`.
* ``policy.consumer_requirements`` evaluates requirements against exports
  -- pure, no I/O.
* ``workflows.consumer_scope`` wires the two and owns the result types.

A probe that could not read its input says so with
:attr:`~abicheck.model.availability.FactStatus.FAILED` and a reason; it is
never just an empty set (root ``AGENTS.md``: weaker evidence narrows
conclusions).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .availability import FactStatus


@dataclass
class AppRequirements:
    """Symbols and versions an application binary requires from a library."""

    needed_libs: list[str] = field(default_factory=list)
    undefined_symbols: set[str] = field(default_factory=set)
    required_versions: dict[str, str] = field(default_factory=dict)


#: Requirement key a PE import-by-ordinal is recorded under (``ordinal:N``):
#: such an import names no function, only an export-directory slot.
PE_ORDINAL_IMPORT_PREFIX = "ordinal:"


def pe_import_ordinal(requirement: str) -> int | None:
    """The ordinal of an ``ordinal:N`` requirement key, else ``None``."""
    if not requirement.startswith(PE_ORDINAL_IMPORT_PREFIX):
        return None
    try:
        return int(requirement[len(PE_ORDINAL_IMPORT_PREFIX) :])
    except ValueError:
        return None


@dataclass(frozen=True)
class ConsumerImportFacts:
    """What one application binary imports from one target library.

    *binary_format* is ``"elf"``, ``"pe"``, ``"macho"``, or ``None`` when the
    file is not a recognised executable. *status* is ``PRESENT`` when the
    import tables were read, ``FAILED`` when the format was unrecognised or a
    parser errored part-way (*requirements* then holds whatever was read
    before the error, and *failure_reason* says what went wrong).
    """

    path: Path
    binary_format: str | None
    target_library: str
    requirements: AppRequirements
    status: FactStatus = FactStatus.PRESENT
    failure_reason: str | None = None

    @property
    def is_readable(self) -> bool:
        """Was the file's binary format recognised at all?"""
        return self.binary_format is not None


@dataclass(frozen=True)
class LibraryExportFacts:
    """The export-side facts consumer scoping needs from one library.

    Each optional field is ``None`` when the fact does not apply to this
    library's format (e.g. ELF version definitions of a PE DLL), which is
    distinct from an empty collection the producer actually read.
    """

    #: ``str(path)`` for a binary, the snapshot's ``library`` field otherwise.
    label: str
    binary_format: str | None
    #: SONAME / install name, falling back to the file or library name.
    soname: str
    #: Every exported name, version aliases included.
    export_names: frozenset[str]
    #: ELF only: exported names with their ``@VERSION`` suffix stripped.
    unversioned_exports: frozenset[str] | None = None
    #: ELF only: version tags the library defines.
    versions_defined: frozenset[str] | None = None
    #: PE only: export ordinal -> export name (``""`` for unnamed).
    exports_by_ordinal: Mapping[int, str] | None = None
    status: FactStatus = FactStatus.PRESENT
    failure_reason: str | None = None
