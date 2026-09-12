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

"""A debug artifact the caller named **directly**, and what its content says.

One-comparison-product plan Phase 7n merged ``--debug-root`` into
``--debug-info``: a debug package, a directory of debug files, and a detached
debug file are three *transports* of one evidence role, so the CLI takes one
input and decides which transport each operand is from its content. Two of
the three were already resolvable -- a package through the release
extractor, a directory through :mod:`abicheck.debug_resolver`'s
build-id-tree/path-mirror backends, which only ever search *inside* a root.
This module is the third: the classifier that reads a file's own sections and
magic, and the resolver backend that uses a named artifact as the artifact.

A sibling of :mod:`abicheck.debug_resolver` rather than more of it (that
file sits within a few lines of ADR-061's 800-line production cap), sharing
its ``DebugArtifact``/build-id primitives through the leaf they both import
(:mod:`abicheck.extract.debug_artifact`) rather than importing each other.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .debug_artifact import DebugArtifact, _is_valid_build_id, extract_build_id

_logger = logging.getLogger(__name__)

#: PDB container magic (MSF 7.00 superblock signature).
_PDB_MAGIC = b"Microsoft C/C++ MSF 7.00"


def _elf_section_names(path: Path) -> frozenset[str] | None:
    """Every section name in an ELF file, or ``None`` if it is not one."""
    try:
        from elftools.common.exceptions import ELFError
        from elftools.elf.elffile import ELFFile
    except ImportError:  # pragma: no cover - pyelftools is a hard dependency
        return None
    try:
        with open(path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            return frozenset(
                s.name for s in elf.iter_sections()  # type: ignore[no-untyped-call]
            )
    except (OSError, ValueError, KeyError, ELFError) as exc:
        _logger.debug("Cannot read ELF sections from %s: %s", path, exc)
        return None


def classify_detached_debug_file(path: Path) -> str | None:
    """Which kind of detached debug artifact *path*'s **content** is.

    ``"dwarf"`` for a separate debug ELF (a ``.debug`` sidecar, or any
    stripped-to-debug-only object), ``"dwp"`` for a DWARF package file,
    ``"pdb"`` for a PDB, and ``None`` for a file that carries no debug
    evidence at all.

    Deliberately reads sections and magic rather than suffixes: plan Phase
    7n routes ``--debug-info`` on what each operand *is*, so a sidecar a
    release job staged as ``libfoo-dbg`` is the same evidence as one named
    ``libfoo.so.debug``.
    """
    if path.is_dir() or not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            head = f.read(len(_PDB_MAGIC))
    except OSError:
        return None
    if head == _PDB_MAGIC:
        return "pdb"
    names = _elf_section_names(path)
    if names is None:
        return None
    if any(n.endswith(".dwo") for n in names if n.startswith((".debug", ".zdebug"))):
        return "dwp"
    if any(n.startswith((".debug_", ".zdebug_")) for n in names):
        return "dwarf"
    return None


class DetachedDebugFileResolver:
    """Use a debug artifact the caller named **directly** (ADR-021a, 7n).

    ``--debug-info`` carries one evidence *role* over three transports (plan
    Phase 7n): a debug package, a directory of debug files, and a detached
    debug file. The first two were always resolvable -- a package through
    the release extractor, a directory through the build-id-tree/path-mirror
    resolvers, which only ever search *inside* a root. This resolver is the
    third: an entry of ``debug_roots`` that is a *file* is the artifact
    itself, not somewhere to look for one.

    First in the chain, because an explicitly named artifact outranks
    whatever the binary happens to carry or whatever a distro tree happens
    to hold -- that is what naming it means.

    **DWARF only, deliberately.** A named ``.pdb`` or ``.dwp`` is refused at
    the front end (``options/evidence_roles.unsupported_detached_debug``)
    rather than resolved here, because no extraction path consumes one: the
    ELF dump reads ``DebugArtifact.dwarf_path`` and nothing else, and the PE
    dump never consults ``debug_roots`` at all. Returning one would have
    been worse than ignoring it -- this resolver runs first, so a named PDB
    would have ended the chain before ``EmbeddedDwarfResolver`` ran, and a
    binary carrying perfectly good DWARF would have been compared with no
    debug evidence and reported clean (Codex review, PR #1253). The
    directory form (``--debug-info old=<dir>``) still reaches ``PDBResolver``
    exactly as it always did; wiring the named-file form into the PE and
    split-DWARF extraction paths is a real capability, and a separate one.

    **Identity is checked, not assumed.** A sidecar whose GNU build-id
    contradicts the binary's is not this binary's debug info, so it is
    rejected (logged, and skipped) rather than used to describe a different
    build. A build-id missing on either side proves nothing either way, so
    it is accepted: this validates what it can observe and never invents a
    mismatch from absent evidence.
    """

    @staticmethod
    def _build_id_conflict(binary_path: Path, debug_path: Path, build_id: str | None) -> bool:
        if not _is_valid_build_id(build_id):
            return False
        debug_build_id = extract_build_id(debug_path)
        if not _is_valid_build_id(debug_build_id):
            return False
        if debug_build_id == build_id:
            return False
        _logger.warning(
            "Ignoring %s: its build-id (%s) does not match %s (%s)",
            debug_path, debug_build_id, binary_path.name, build_id,
        )
        return True

    def resolve(
        self,
        binary_path: Path,
        build_id: str | None = None,
        debug_roots: list[Path] | None = None,
    ) -> DebugArtifact | None:
        for candidate in debug_roots or []:
            if not candidate.is_file():
                continue
            if classify_detached_debug_file(candidate) != "dwarf":
                # Not DWARF (or not a debug artifact at all): leave the rest
                # of the chain to run rather than ending it with something
                # no extraction path reads. See the class docstring.
                _logger.debug("No usable detached DWARF in %s", candidate)
                continue
            if self._build_id_conflict(binary_path, candidate, build_id):
                continue
            return DebugArtifact(
                dwarf_path=candidate, source=f"detached debug file ({candidate})"
            )
        return None
