# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-067 C-S2's raw-versus-effective disposition audit as ``abicheck
aggregate`` reads it: the same conserved-ledger reconciliation scalar
`compare` (C-S1) and the release/bundle fan-out (C-S2's own sibling slice)
already carry, folded across every target's already-emitted report.

A sibling leaf rather than more lines in ``load.py``/``fold.py``, both of
which sit at (or near) the 800-line production cap -- the same reason
``scope_axis.py`` exists.

Read, never recomputed: a target's report already carries its own
``disposition_audit`` block (report schema 2.51, ``compare``-only today --
a `scan` report carries none, so this axis is simply absent for a
scan-sourced target). This module only locates that block and hands it back
as a plain mapping; the actual fold across targets is
``report.disposition_audit.fold_disposition_audits``, called once by
``fold.AggregateResult``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .gate import contract_coverage_blocks


def disposition_audit_block(data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The report's own ``disposition_audit`` mapping, or ``None``.

    Reuses :func:`~abicheck.workflows.aggregate.gate.contract_coverage_blocks`'s
    shape-aware traversal (root for ``compare``/release, ``diff``/
    ``report.diff`` for a scan-shaped report) purely so a nested scan report
    is handled the same way every other per-report reader here handles it --
    in practice a scan report carries no ``disposition_audit`` at any of
    those paths, so this always resolves at the root for one that has it.
    """
    for block in contract_coverage_blocks(data):
        candidate = block.get("disposition_audit")
        if isinstance(candidate, Mapping):
            return candidate
    return None
