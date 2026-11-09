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

"""Demote findings a rebuild of unchanged sources produces on what the
toolchain owns, not the library.

Rebuilding a C++ library at another optimization level changes which
vague-linkage copies of the C++ runtime's templates the linker keeps and which
runtime helpers the code calls. Two such observations are real facts with no
consumer impact, and both are demoted to ``COMPATIBLE`` through
``Change.effective_verdict`` with a disclosed rule (ADR-067: annotated, never
removed).

* **A coalesced copy of a dependency-owned entity** (rule
  :data:`COALESCED_COPY_RULE`). An export the binary marks ``WEAK`` or
  ``GNU_UNIQUE`` on every side that has it, whose owner is the C++ runtime or
  another dependency (the same origin classification the symbol-hygiene audit
  and the contract gate use), is a COMDAT copy every consumer translation unit
  that uses the entity emits as well, or resolves from the runtime itself
  (ADR-089 A3). Its appearance or disappearance is not a change to the
  library's surface: ``-O2`` inlines ``std::operator+``, ``-O0`` exports it.
  A ``GLOBAL`` export keeps its verdict, and so does a ``WEAK`` export of the
  library's own namespace (``case217``/``case218``: only a ``binding:`` policy
  demotes those).
* **An import the old binary's version requirements already cover** (rule
  :data:`COVERED_IMPORT_RULE`). A new undefined symbol bound to version node
  ``V`` of provider ``P`` adds no load-time obligation when the old binary
  already required ``P`` at ``V`` or a newer node of the same series: GNU
  version nodes are cumulative and their symbol sets frozen, so every ``P``
  the old binary loads against defines it. This is the rule
  ``symbol_version_required_added_compat`` already applies to the
  requirement itself. An unversioned import, or one under a newer or
  unparseable node, keeps ``imported_symbol_added``'s RISK.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.dotted_version import parse_dotted_numeric_version
from ..model.elf_facts import ElfMetadata, SymbolBinding

__all__ = [
    "COALESCED_COPY_REASON",
    "COALESCED_COPY_RULE",
    "COVERED_IMPORT_REASON",
    "COVERED_IMPORT_RULE",
    "demote_toolchain_rebuild_drift",
]

COALESCED_COPY_RULE = "coalesced-dependency-copy"
COALESCED_COPY_REASON = (
    "WEAK/GNU_UNIQUE copy of a dependency-owned entity (COMDAT): every consumer "
    "that uses it emits or resolves its own"
)
COVERED_IMPORT_RULE = "import-covered-by-required-version"
COVERED_IMPORT_REASON = (
    "the old binary already required this provider at this version node or a "
    "newer one, which defines the symbol"
)

#: Findings whose subject is one exported symbol's presence on one side.
_EXPORT_PRESENCE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_REMOVED_ELF_ONLY,
        ChangeKind.VAR_REMOVED,
        ChangeKind.VAR_REMOVED_ELF_ONLY,
        ChangeKind.FUNC_VISIBILITY_CHANGED,
        ChangeKind.VAR_VISIBILITY_CHANGED,
        ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
    }
)

_COALESCED: frozenset[SymbolBinding] = frozenset(
    {SymbolBinding.WEAK, SymbolBinding.UNIQUE}
)


def _bindings(elf: ElfMetadata | None) -> dict[str, set[SymbolBinding]]:
    out: dict[str, set[SymbolBinding]] = {}
    for sym in elf.symbols if elf is not None else ():
        out.setdefault(sym.name, set()).add(sym.binding)
    return out


def _is_coalesced_copy(
    symbol: str,
    sides: Iterable[dict[str, set[SymbolBinding]]],
    is_dependency_owned: Callable[[str], bool],
) -> bool:
    seen = [b for side in sides if (b := side.get(symbol))]
    return (
        bool(seen)
        and all(b <= _COALESCED for b in seen)
        and is_dependency_owned(symbol)
    )


def _tag(version: str) -> tuple[str, tuple[int, ...] | None]:
    series, _, numeric = version.rpartition("_")
    return series, parse_dotted_numeric_version(numeric) if series else None


def _import_covered(name: str, old_elf: ElfMetadata, new_elf: ElfMetadata) -> bool:
    imp = next((i for i in new_elf.imports if i.name == name), None)
    if imp is None or not imp.version:
        return False
    series, wanted = _tag(imp.version)
    if wanted is None:
        return False
    providers = (
        [imp.version_soname]
        if imp.version_soname
        else [p for p, vers in new_elf.versions_required.items() if imp.version in vers]
    )
    for provider in providers:
        for held in old_elf.versions_required.get(provider, ()):
            held_series, held_tag = _tag(held)
            if held_series == series and held_tag is not None:
                width = max(len(held_tag), len(wanted))
                pad = (0,) * width
                if (held_tag + pad)[:width] >= (wanted + pad)[:width]:
                    return True
    return False


def _demote(change: Change, rule: str, reason: str) -> None:
    change.effective_verdict = Verdict.COMPATIBLE
    change.modulation_rule = rule
    change.modulation_reason = reason


def demote_toolchain_rebuild_drift(
    changes: list[Change],
    old_elf: ElfMetadata | None,
    new_elf: ElfMetadata | None,
    is_dependency_owned: Callable[[str], bool],
) -> list[Change]:
    """Apply both rules to *changes*; mutates and returns it. A finding a
    prior rule already modulated is left alone."""
    if old_elf is None or new_elf is None:
        return changes
    sides = (_bindings(old_elf), _bindings(new_elf))
    for change in changes:
        if change.effective_verdict is not None or not change.symbol:
            continue
        if change.kind in _EXPORT_PRESENCE_KINDS:
            if _is_coalesced_copy(change.symbol, sides, is_dependency_owned):
                _demote(change, COALESCED_COPY_RULE, COALESCED_COPY_REASON)
        elif change.kind is ChangeKind.IMPORTED_SYMBOL_ADDED and _import_covered(
            change.symbol, old_elf, new_elf
        ):
            _demote(change, COVERED_IMPORT_RULE, COVERED_IMPORT_REASON)
    return changes
