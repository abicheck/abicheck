# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Pickle support for :class:`~abicheck.policy.disposition_ledger.DispositionLedger`.

The ledger is identity-keyed: ``_seen_ids`` maps ``id(change)`` to a record,
so a report can ask "which rule disposed of *this* change". A default pickle
copies those integers verbatim, and after unpickling they name the *old*
process's objects -- every lookup misses, and a member compared in a forked
child (``workflows.release_member_processes``) came back with its suppression
ledger's ``rule`` provenance silently dropped.

The reducer below pickles the key objects themselves instead (the anchored
and aliased changes the ledger already keeps alive), so pickle's memo keeps
them the *same* objects the enclosing ``DiffResult`` holds, and rebuilds the
identity map on load. Registered through :mod:`copyreg` rather than as
methods on the class, whose module is at its ``no_growth`` baseline.
"""

from __future__ import annotations

import copyreg
from typing import Any

from .disposition_ledger import DispositionLedger

__all__ = ["register"]


def _restore(
    state: dict[str, Any], keyed: list[tuple[object, int]]
) -> DispositionLedger:
    ledger = DispositionLedger.__new__(DispositionLedger)
    ledger.__dict__.update(state)
    ledger._seen_ids = {id(change): index for change, index in keyed}
    return ledger


def _reduce(ledger: DispositionLedger) -> tuple[Any, ...]:
    state = dict(ledger.__dict__)
    seen = state.pop("_seen_ids")
    holders = [*ledger._anchors, *ledger._aliases]
    keyed = [(change, seen[id(change)]) for change in holders if id(change) in seen]
    return _restore, (state, keyed)


def register() -> None:
    """Install the reducer (idempotent)."""
    copyreg.pickle(DispositionLedger, _reduce)


register()
