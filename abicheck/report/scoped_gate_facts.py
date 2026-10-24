# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Scoped-gate facts a ``--used-by``/``--required-symbol(s)`` run reports.

A scoped run reports its consumer-scoped verdict *beside* the full-library
verdict (ADR-043, workstream D-S1). These facts are computed exactly once per
render by :func:`build_scoped_gate_facts`, which
:func:`~abicheck.report.document_builder.build_report_envelope` calls above
format selection and stores on the
:class:`~abicheck.report.envelope.ReportEnvelope`. A renderer reads the stored
value and formats it; it never re-derives the counts from the ``DiffResult``.

The facts are deliberately *not* added to the shared ``ReportDocument``: the
JSON projection serializes that document wholesale, so a new root key would
change the published JSON shape. Only the envelope carries them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..checker_types import DiffResult


@dataclass(frozen=True, slots=True)
class ScopedGateFacts:
    """One scoped run's consumer-gate facts, each as the renderer will show it.

    ``None`` on an optional field means the run did not request that scope
    axis, so a renderer omits its row rather than writing an empty value.
    """

    #: The ``--used-by``/``--required-symbol(s)`` scope label, when one was set.
    gate_scope: str | None
    #: The consumer-scoped verdict value (``scoped_verdict``).
    gate_verdict: str
    #: The full-library verdict value, reported unswapped beside the gate.
    full_library_verdict: str
    #: Scoped-relevant changes, scoped-only changes and missing labels together.
    relevant_finding_count: int
    #: Changes in ``result.changes`` that fall outside the consumer's scope.
    unrelated_finding_count: int
    #: The scoped exit code, or ``None`` when no scoped exit code was resolved.
    gate_exit_code: int | None
    #: The scheme name the scoped exit code was resolved under.
    gate_exit_code_scheme: object | None
    #: How many ``--used-by`` applications were scoped, or ``None`` if none.
    used_by_app_count: int | None
    #: The ``--required-symbol(s)`` contract verdict text, or ``None`` if unset.
    required_symbol_contract_verdict: str | None


def build_scoped_gate_facts(result: DiffResult) -> ScopedGateFacts | None:
    """The scoped-gate facts for *result*, or ``None`` for an unscoped run."""
    from .view import finding_id

    scoped_verdict = getattr(result, "scoped_verdict", None)
    if scoped_verdict is None:
        return None
    relevant_ids = getattr(result, "scoped_relevant_finding_ids", None) or frozenset()
    relevant_in_changes = sum(
        1 for change in result.changes if finding_id(change) in relevant_ids
    )
    # Scoped-only changes and missing-contract members are relevant by
    # construction and never in result.changes, so they count toward
    # relevant_finding_count but not unrelated_finding_count, which only
    # counts irrelevant entries *within* result.changes (CodeRabbit review).
    scoped_only_count = len(getattr(result, "scoped_only_changes", ()) or ())
    missing_count = len(getattr(result, "scoped_missing_labels", ()) or ())
    used_by = getattr(result, "used_by", None)
    required_symbols = getattr(result, "required_symbols", None)
    return ScopedGateFacts(
        gate_scope=getattr(result, "gate_scope", None),
        gate_verdict=scoped_verdict.value,
        full_library_verdict=result.verdict.value,
        relevant_finding_count=relevant_in_changes + scoped_only_count + missing_count,
        unrelated_finding_count=len(result.changes) - relevant_in_changes,
        gate_exit_code=getattr(result, "scoped_exit_code", None),
        gate_exit_code_scheme=getattr(result, "scoped_exit_code_scheme", None),
        used_by_app_count=None if used_by is None else len(used_by),
        required_symbol_contract_verdict=(
            None
            if required_symbols is None
            else str(required_symbols.get("verdict", ""))
        ),
    )
