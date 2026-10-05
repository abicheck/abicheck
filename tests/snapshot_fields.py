"""Read or write an ``AbiSnapshot`` field by name (ADR-063 Phase 10).

Declaration kinds and the debug layout live in the snapshot's IR-owned store
(``snapshot.declarations``; ``dwarf``/``dwarf_advanced`` under their
backend-neutral store names); every other field stays on the snapshot. Tests
that iterate over field names use this instead of a bare ``getattr``.
"""

from __future__ import annotations

from typing import Any

from abicheck.model.declaration_store import STORE_ATTRIBUTE


def _owner(snapshot: Any, name: str) -> tuple[Any, str]:
    if name in STORE_ATTRIBUTE:
        return snapshot.declarations, STORE_ATTRIBUTE[name]
    return snapshot, name


def field_of(snapshot: Any, name: str, *default: Any) -> Any:
    owner, attr = _owner(snapshot, name)
    return getattr(owner, attr, *default)


def set_field(snapshot: Any, name: str, value: Any) -> None:
    owner, attr = _owner(snapshot, name)
    setattr(owner, attr, value)
