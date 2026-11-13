# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-065 D6/D7's scope-completeness axis as ``abicheck aggregate`` reads
it: the third orthogonal ``0``/``1`` exit floor a per-target report can
carry, the exact sibling of the contract-coverage and analysis-assurance
readers. A sibling leaf rather than more lines in ``load.py``/``gate.py``,
both of which sit at the 800-line production cap.

Read, never recomputed: a release report's ``exit`` block states
``incomplete_scope_contribution`` (``1`` only under ``--on-incomplete-scope
block``) and ``no_comparison_completed_contribution`` (``1`` whenever the
run completed no comparison, under either policy), and this aggregate holds
none of the acquisition evidence needed to answer either again. Without
this fold, a release whose ``run_outcome`` reads ``scope: incomplete`` while
``gate``/``operational`` stay ``none`` aggregated to a green target even
though the originating comparison exited ``1`` (Codex review).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .report_gate import GateInfo, _is_valid_contribution, contract_coverage_blocks

#: ADR-065 D8 / ADR-064: the exit a FAILED member (or an ``ERROR`` verdict)
#: contributes as an operational error, the same value ``compare`` reports.
OPERATIONAL_ERROR_EXIT = 4

#: The ``exit``-block keys this axis folds, in the order they are stated.
SCOPE_EXIT_KEYS: tuple[str, ...] = (
    "incomplete_scope_contribution",
    "no_comparison_completed_contribution",
)
#: The same two contributions as the ``comparison_scope`` section states
#: them -- the only place a stored/stored or stored/live comparison
#: (``compare_bundle_facts.py``) records them, since that dispatcher emits
#: no ``exit`` block at all (Codex review); ``action/run.sh`` reads the
#: same fallback.
SCOPE_SECTION_KEYS: tuple[str, ...] = (
    "incomplete_scope_exit_contribution",
    "no_comparison_completed_exit_contribution",
)


def _contribution_sources(
    data: Mapping[str, Any],
) -> list[tuple[Mapping[str, Any], tuple[str, ...]]]:
    """Every ``(mapping, keys)`` pair a contribution may be read from."""
    out: list[tuple[Mapping[str, Any], tuple[str, ...]]] = []
    for block in contract_coverage_blocks(data):
        for name, keys in (
            ("exit", SCOPE_EXIT_KEYS),
            ("comparison_scope", SCOPE_SECTION_KEYS),
        ):
            node = block.get(name)
            if isinstance(node, Mapping):
                out.append((node, keys))
    return out


def scope_completeness_exit(data: Mapping[str, Any]) -> int:
    """The report's own ADR-065 scope-completeness contribution (``0``/``1``).

    The ``max`` over :data:`SCOPE_EXIT_KEYS` in every ``exit`` block, and
    :data:`SCOPE_SECTION_KEYS` in every ``comparison_scope`` section, the
    document's shape can carry (a scan-shaped report nests both under
    ``diff``, exactly where its coverage fields live). Fails open like its
    siblings: a report stating neither, or a malformed value, contributes
    ``0``.
    """
    worst = 0
    for node, keys in _contribution_sources(data):
        for key in keys:
            raw = node.get(key)
            if _is_valid_contribution(raw):
                worst = max(worst, raw)
    return worst


def operational_error_exit(data: Mapping[str, Any]) -> int:
    """The report's own operational-error contribution: ``4`` when an
    ``exit`` block states ``operational_error_contribution == 4``, else ``0``.

    That value is decided once by the producing run's
    ``resolve_release_exit_decision`` (ADR-065 D8: a FAILED member, or an
    ``ERROR`` verdict) and persisted; this reads it, it never re-derives it
    from ``run_outcome.operational``, which folds to ``1`` and so under-reads
    the ``4`` ``compare`` exits with (known-gaps: aggregate reads a failed
    member as 1).
    """
    for block in contract_coverage_blocks(data):
        node = block.get("exit")
        if not isinstance(node, Mapping):
            continue
        raw = node.get("operational_error_contribution")
        if not isinstance(raw, bool) and raw == OPERATIONAL_ERROR_EXIT:
            return OPERATIONAL_ERROR_EXIT
    return 0


def fold_operational_error_exit(gate: GateInfo, data: Mapping[str, Any]) -> GateInfo:
    """*gate* raised to the report's own operational-error contribution
    (:func:`operational_error_exit`) by ``max``, so a target with a
    ``run_outcome.operational`` of ``extraction_error`` reads ``4`` exactly
    as the originating ``compare`` did. Never lowers a gate."""
    op_exit = operational_error_exit(data)
    if op_exit <= gate.exit_code:
        return gate
    return replace(gate, exit_code=op_exit, blocking=True)


def scope_completeness_incomplete(data: Mapping[str, Any]) -> bool:
    """Whether the report *recorded* an incomplete scope at all, whatever
    it contributed: ``run_outcome.scope`` or ``comparison_scope.
    completeness`` reading ``incomplete``. Tracked separately from
    :func:`scope_completeness_exit` for the reason contract coverage
    tracks ``contract_coverage_incomplete``: the default
    ``--on-incomplete-scope warn`` zeroes the contribution and changes
    nothing else, and an aggregate that then omits the target hides an
    accepted evidence gap the source report stated (Codex review).
    """
    for block in contract_coverage_blocks(data):
        for name, key in (
            ("run_outcome", "scope"),
            ("comparison_scope", "completeness"),
        ):
            node = block.get(name)
            if isinstance(node, Mapping) and node.get(key) == "incomplete":
                return True
    return False


def declares_null_compatibility(data: Mapping[str, Any]) -> bool:
    """Whether a schema-valid ``run_outcome`` block states ``compatibility:
    null`` -- a release that completed no comparison (ADR-065 D7) keeps a
    legacy root ``verdict: "NO_CHANGE"`` for older readers, and the aggregate
    must not manufacture a clean verdict from it (Codex review)."""
    from .report_gate import _has_valid_run_outcome_block

    return (
        _has_valid_run_outcome_block(data)
        and data["run_outcome"].get("compatibility") is None
    )
