"""Read or write an ``AbiSnapshot`` field by name (ADR-063 Phase 10).

Declaration kinds live in the snapshot's IR-owned store
(``snapshot.declarations``); every other field stays on the snapshot. Tests
that iterate over field names use this instead of a bare ``getattr``.
"""

from __future__ import annotations

from typing import Any

from abicheck.model.declaration_store import DECLARATION_KINDS


def _owner(snapshot: Any, name: str) -> Any:
    return snapshot.declarations if name in DECLARATION_KINDS else snapshot


def field_of(snapshot: Any, name: str, *default: Any) -> Any:
    return getattr(_owner(snapshot, name), name, *default)


def set_field(snapshot: Any, name: str, value: Any) -> None:
    setattr(_owner(snapshot, name), name, value)
