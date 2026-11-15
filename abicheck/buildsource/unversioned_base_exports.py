# SPDX-License-Identifier: Apache-2.0
"""New exports GNU ld bound to the base version: the two-sided half of
``unversioned_exported_symbol``.

The one-sided check flags only ``ElfVersionBinding.UNVERSIONED`` and skips
``BASE``, because a base-bound export is normally API that predates the
version script, and retagging it would itself break consumers. But GNU ld
binds *every* export a version script does not match to the base version,
so a symbol newly added outside every node also reads ``BASE``, and that is
exactly the hygiene problem the check names. One side cannot tell the two
apart; the comparison can: a base-bound export OLD did not export at all is
new, so it was never pre-script API.
"""

from __future__ import annotations

from ..diff_versioning import _is_unattached_private_version_node
from ..elf_symbol_filter import is_abi_relevant_elf_symbol
from ..model import AbiSnapshot
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.cxx_artifact_symbols import is_cxx_class_artifact_symbol
from ..model.elf_facts import ElfVersionBinding
from ..model.evidence_status import Confidence
from .cross_source_checks_base import _change

__all__ = ["introduced_base_bound_exports"]


def introduced_base_bound_exports(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """NEW's default-visible, base-bound exports that OLD did not export.

    Empty unless both sides carry an ELF symbol table and NEW defines a real
    version node; an OLD export of the same name under any binding keeps a
    symbol out (it predates this release, so the one-sided rule applies).
    """
    if old.elf is None or new.elf is None:
        return []
    nodes = [
        v
        for v in new.elf.versions_defined
        if not _is_unattached_private_version_node(new.elf, v)
    ]
    if not nodes:
        return []
    old_names = {s.name for s in old.elf.symbols}
    found = [
        _change(
            ChangeKind.UNVERSIONED_EXPORTED_SYMBOL,
            sym.name,
            f"Symbol {sym.name!r} is new in this release and bound to no version "
            f"node (GNU ld put it on the base version) even though the library "
            f"defines a versioning scheme ({len(nodes)} version(s)). Add it to the "
            "version script so it can be evolved compatibly, or hide it if it "
            "is not public API.",
            new_value=sym.name,
            confidence=Confidence.MEDIUM,
        )
        for sym in new.elf.symbols
        if sym.name
        and sym.is_default
        and not sym.version
        and sym.version_binding is ElfVersionBinding.BASE
        and sym.visibility in ("default", "protected")
        and sym.name not in old_names
        and is_abi_relevant_elf_symbol(sym.name)
        and not is_cxx_class_artifact_symbol(sym.name)
    ]
    return sorted(found, key=lambda c: c.symbol)
