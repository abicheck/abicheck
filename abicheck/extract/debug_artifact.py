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

"""The debug-artifact value and the build-id primitives, as a leaf (ADR-021a).

A deliberate leaf both :mod:`abicheck.debug_resolver` (the resolver chain)
and :mod:`abicheck.extract.detached_debug` (plan Phase 7n's
named-artifact transport) depend on, rather than one of them depending on
the other. The alternative -- `detached_debug` importing `DebugArtifact`
back from `debug_resolver` while `debug_resolver` imports the resolver --
is a real import cycle, and this repo's AI-readiness gate rejects a new one
outright rather than taking an allowlist entry for it (AGENTS.md, "What NOT
to do"): the fix for a cycle is the shared leaf, not the exception.

``debug_resolver`` re-exports every name here, so
``from abicheck.debug_resolver import DebugArtifact`` (the usage its own
module docstring documents) and every internal call site are unaffected.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)

# Strict hex pattern for build-id validation
_BUILD_ID_RE = re.compile(r"^[0-9a-f]+$")


@dataclass
class DebugArtifact:
    """Resolved debug artifact location (ADR-021a)."""

    dwarf_path: Path | None = None
    dwp_path: Path | None = None
    dwo_dir: Path | None = None
    pdb_path: Path | None = None
    dsym_path: Path | None = None
    source: str = ""

    @property
    def has_dwarf(self) -> bool:
        return self.dwarf_path is not None

    @property
    def has_pdb(self) -> bool:
        return self.pdb_path is not None

    @property
    def has_dsym(self) -> bool:
        return self.dsym_path is not None

    @property
    def has_split_dwarf(self) -> bool:
        return self.dwp_path is not None or self.dwo_dir is not None

    @property
    def description(self) -> str:
        """Human-readable summary of what was found."""
        parts: list[str] = []
        if self.dwarf_path:
            parts.append(f"DWARF from {self.dwarf_path}")
        if self.dwp_path:
            parts.append(f"DWP from {self.dwp_path}")
        if self.dwo_dir:
            parts.append(f"DWO files in {self.dwo_dir}")
        if self.pdb_path:
            parts.append(f"PDB from {self.pdb_path}")
        if self.dsym_path:
            parts.append(f"dSYM from {self.dsym_path}")
        if not parts:
            return "no debug info found"
        return "; ".join(parts)


def _is_valid_build_id(build_id: str | None) -> bool:
    """Validate that a build-id is a strict lowercase hex string."""
    return build_id is not None and bool(_BUILD_ID_RE.fullmatch(build_id))


# ---------------------------------------------------------------------------
# Build-id extraction
# ---------------------------------------------------------------------------


def extract_build_id(binary_path: Path) -> str | None:
    """Extract the build-id from an ELF binary's .note.gnu.build-id section.

    Returns the build-id as a lowercase hex string, or None if not found.
    """
    try:
        from elftools.common.exceptions import ELFError
        from elftools.elf.elffile import ELFFile
        from elftools.elf.sections import NoteSection
    except ImportError:
        _logger.debug("pyelftools not available; cannot extract build-id")
        return None

    try:
        with open(binary_path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            for section in elf.iter_sections():  # type: ignore[no-untyped-call]
                if not isinstance(section, NoteSection):
                    continue
                for note in section.iter_notes():  # type: ignore[no-untyped-call]
                    if note["n_type"] == "NT_GNU_BUILD_ID":
                        desc = note["n_desc"]
                        if isinstance(desc, str):
                            return desc.lower()
                        if isinstance(desc, bytes):
                            return desc.hex().lower()
                        return str(desc).lower()
    except (OSError, ValueError, KeyError, ELFError) as exc:
        _logger.debug("Failed to extract build-id from %s: %s", binary_path, exc)

    return None
