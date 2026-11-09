# SPDX-License-Identifier: Apache-2.0
"""``noexcept`` transitions of one matched function pair.

Moved out of :mod:`.function_signature` (at its size cap) when the virtual
case was added. A side whose specification was not observed
(``FunctionSignature.is_noexcept is None``: a castxml run whose clang join
did not read it, a pre-generation-46 castxml baseline) is declined, never
read as "no ``noexcept``".

Adding ``noexcept`` to a function is binary compatible and, for a plain
function, source compatible: ``func_noexcept_added``. On a method that was
already virtual it is also a source break for every consumer that overrides
it without ``noexcept`` ([except.spec]/5), reported as
``func_virtual_noexcept_added`` beside the plain fact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ..diff_helpers import bool_transition, make_change
from ..model.change_catalog.kinds import ChangeKind
from .declined_comparisons import record_declined

if TYPE_CHECKING:
    from ..model.change import Change
    from ..model.identity import EntityId

__all__ = ["noexcept_changes"]


class _Flags(Protocol):
    """The two ``FunctionSignature`` facts this check reads (a protocol, so
    this module does not import its caller back)."""

    @property
    def is_noexcept(self) -> bool | None: ...
    @property
    def is_virtual(self) -> bool | None: ...


def noexcept_changes(
    mangled: str,
    name: str,
    old: _Flags,
    new: _Flags,
    entity_id: EntityId | None,
) -> list[Change]:
    """Emit a change if the noexcept specifier was added or removed."""
    if old.is_noexcept is None or new.is_noexcept is None:
        both = old.is_noexcept is None and new.is_noexcept is None
        record_declined(
            mangled,
            "function noexcept specifier not established on "
            f"{'both sides' if both else 'one side'}",
        )
        return []
    changes = bool_transition(
        old.is_noexcept,
        new.is_noexcept,
        mangled,
        added=(
            ChangeKind.FUNC_NOEXCEPT_ADDED,
            f"noexcept specifier added: {name}",
        ),
        removed=(
            ChangeKind.FUNC_NOEXCEPT_REMOVED,
            f"noexcept specifier removed: {name}",
        ),
        entity_id=entity_id,
    )
    if not old.is_noexcept and new.is_noexcept and old.is_virtual is True:
        changes.append(
            make_change(
                ChangeKind.FUNC_VIRTUAL_NOEXCEPT_ADDED,
                symbol=mangled,
                description=f"noexcept added to virtual method: {name} "
                "(an overrider without noexcept no longer compiles)",
                old_value="virtual",
                new_value="virtual noexcept",
                entity_id=entity_id,
            )
        )
    return changes
