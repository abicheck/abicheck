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

"""A complete :class:`~abicheck.model.AbiSnapshot` from DWARF alone.

The DIE walk is :func:`abicheck.dwarf_snapshot.extract_dwarf_declarations`
(``extract``); this module only assembles its result into a snapshot,
through :func:`~abicheck.workflows.snapshot_factory.new_snapshot`. The
assembly lived in ``dwarf_snapshot.py`` until design-hardening Phase 2 made
the factory the one production constructor, which an ``extract`` module
cannot import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..dwarf_snapshot import extract_dwarf_declarations
from .snapshot_factory import new_snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from ..dwarf_advanced import AdvancedDwarfMetadata
    from ..dwarf_metadata import DwarfMetadata
    from ..dwarf_unified import DwarfSession
    from ..elf_metadata import ElfMetadata
    from ..model import AbiSnapshot

__all__ = ["build_snapshot_from_dwarf"]


def build_snapshot_from_dwarf(
    elf_path: Path,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    *,
    version: str = "unknown",
    language_profile: str | None = None,
    session: DwarfSession | None = None,
) -> AbiSnapshot:
    """Build a complete AbiSnapshot from DWARF, no headers required.

    *elf_meta* supplies the exported symbol set; *dwarf_meta*/*dwarf_adv* are
    the pre-parsed DWARF metadata stored on the snapshot. *session* is
    forwarded to the DIE walk (see
    :func:`~abicheck.dwarf_snapshot.extract_dwarf_declarations`). The result
    has functions, variables, types, enums and typedefs populated from DWARF
    and ``elf_only_mode=False``.
    """
    decls = extract_dwarf_declarations(elf_path, elf_meta, session=session)
    return new_snapshot(
        library=elf_path.name,
        version=version,
        # The image this surface was read from -- the same identity the
        # header-AST and symbols-only ELF builders record; dump-time
        # build-mode capture (extract.build_mode_capture) reads it back.
        source_path=str(elf_path.resolve()),
        functions=decls.functions,
        variables=decls.variables,
        types=decls.types,
        enums=decls.enums,
        typedefs=decls.typedefs,
        typedef_entity_ids=decls.typedef_entity_ids,
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        elf_only_mode=False,
        platform="elf",
        language_profile=language_profile,
    )
