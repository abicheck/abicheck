"""ADR-067 D3's conservation invariant, as the disposition tests assert it.

Every per-disposition count sums to the detected total. This was
``policy.disposition_close.conservation_holds`` until production stopped
having a caller for it (dead-code plan, Stage D); the tests that state the
invariant over a ledger are its consumers.
"""

from __future__ import annotations

from abicheck.policy.disposition_ledger import DispositionLedger


def conservation_holds(ledger: DispositionLedger) -> bool:
    """Whether *ledger*'s per-disposition counts sum to its detected total."""
    return sum(ledger.counts().values()) == ledger.detected_total
