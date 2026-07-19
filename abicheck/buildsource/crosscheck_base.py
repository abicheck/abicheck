# Copyright 2026 Nikolay Petrov
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

"""Shared plumbing for the cross-check engine and its check modules.

A **leaf** module: it holds the finding/coverage primitives that both
``crosscheck.py`` (the engine) and the split-out check modules
(``crosscheck_coherence.py``) need, so those two never have to import each
other's internals. Keeping this dependency-free of ``crosscheck`` is what lets
a check live in its own file without forming a ``crosscheck`` ↔ check import
cycle (CLAUDE.md "M1-3": move the shared logic to a leaf module both sides can
depend on). ``crosscheck.py`` re-exports every name here for back-compat, so
existing ``from .crosscheck import _change`` call sites and tests keep working.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..checker_policy import ChangeKind, Confidence
from ..checker_types import Change
from ..model import AbiSnapshot

# The §6.8 provider-agreement vocabulary (ADR-035 D4) — which evidence source
# corroborates a finding, driving its confidence tag.
PROVIDER_BINARY_EXPORTS = "binary_exports"
PROVIDER_PUBLIC_HEADER_AST = "public_header_ast"
PROVIDER_DEBUG_INFO = "debug_info"
PROVIDER_BUILD_CONFIG = "build_config"
PROVIDER_SOURCE_INDEX = "source_index"


@dataclass(frozen=True)
class _CheckOutput:
    """One check's result: findings, its coverage row, and the providers used."""

    findings: list[Change]
    status: str  # "present" | "skipped"
    detail: str
    providers: list[str]
    #: Optional ADR-035 D4 source-surface boundary integrity numbers (e.g.
    #: exported/matched/unmatched symbols) carried onto the coverage row so a
    #: degraded link is named, never folded in as clean. ``facts`` anchors the row.
    facts: int = 0
    counters: dict[str, int] = field(default_factory=dict)


def _change(
    kind: ChangeKind,
    symbol: str,
    description: str,
    *,
    old_value: str | None = None,
    new_value: str | None = None,
    source_location: str | None = None,
    confidence: Confidence = Confidence.MEDIUM,
    caused_by_type: str | None = None,
    evidence_category: str = "source_only",
) -> Change:
    """Build a cross-check :class:`Change` with the shared metadata defaults."""
    return Change(
        kind=kind,
        symbol=symbol,
        description=description,
        old_value=old_value,
        new_value=new_value,
        source_location=source_location,
        confidence=confidence,
        caused_by_type=caused_by_type,
        evidence_category=evidence_category,
    )


def _exported_symbol_names(snapshot: AbiSnapshot) -> set[str] | None:
    """The binary's exported symbol names, or ``None`` if no export table exists.

    Only **default/unversioned** ELF exports count toward the obligation set: a
    symbol that exists *only* as a non-default version alias (``foo@LIB_1``,
    ``is_default == False``) does not satisfy an unversioned consumer link
    (which needs ``foo@@…``), so including it would mask the exact
    missing-export case this set feeds (Codex review).

    Mach-O names are normalized the same way the dumper normalizes
    ``Function.mangled`` (strip the platform's single leading underscore:
    ``_foo`` → ``foo``, ``__Z...`` → ``_Z...``) so the comparison set matches the
    header-side mangled spelling instead of flagging every C/C++ symbol as
    missing (Codex review).
    """
    if snapshot.elf is not None:
        return {s.name for s in snapshot.elf.symbols if s.name and s.is_default}
    if snapshot.pe is not None:
        return {e.name for e in snapshot.pe.exports if e.name}
    if snapshot.macho is not None:
        return {
            e.name[1:] if e.name.startswith("_") else e.name
            for e in snapshot.macho.exports
            if e.name
        }
    return None


def _linked_export_symbols(snapshot: AbiSnapshot) -> set[str] | None:
    """Exported symbol names in the **L4 source-linker's** keyspace.

    Matches ``cli_buildsource_merge._exported_symbols_from_snapshot`` — the set
    that seeds ``source_link`` — so a comparison against the surface's
    ``source_decl_to_binary_symbol`` mappings uses the *same spelling*. It reads
    the raw platform dynamic-symbol-table names and, unlike
    :func:`_exported_symbol_names`, does **not** apply the dumper's *second*
    Mach-O underscore strip (``_Z…`` → ``Z…``). ``macho_metadata`` already strips
    the one platform underscore, so a C++ export is stored as ``_Z…`` and the L4
    linker keeps that form; stripping again (as ``_exported_symbol_names`` does,
    to match the double-stripped ``Function.mangled`` the dumper produces) would
    make a correctly relinked macOS C++ surface intersect nothing and falsely
    trip ``source_surface_dso_mismatch`` (Codex review). ELF is still limited to
    default-versioned exports — a non-default alias can't satisfy an unversioned
    consumer link, mirroring both peers.
    """
    if snapshot.elf is not None:
        return {s.name for s in snapshot.elf.symbols if s.name and s.is_default}
    if snapshot.pe is not None:
        return {e.name for e in snapshot.pe.exports if e.name}
    if snapshot.macho is not None:
        return {e.name for e in snapshot.macho.exports if e.name}
    return None
