# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The one shared report-document build entry point (ADR-061 Phase 2 gap C).

Before this module, every output format (JSON, Markdown, HTML, SARIF,
JUnit) independently walked a completed :class:`~abicheck.checker_types.
DiffResult` through its own ``_add_*``/``compute_*`` pipeline before
freezing a :class:`~abicheck.report.document.ReportDocument` -- six
separately-decided projections of the same evaluation, rather than one
shared decided document each format purely projects.

:func:`build_report_document` is the single choke point: called once per
top-level render (see ``abicheck.service_render.render_output``), it
performs every fact/decision computation exactly once and returns the frozen
document every enabled format then reads from. A format needing data this
document does not yet carry should get that data added *here*, never
recomputed locally in the format's own renderer.

This is the ``report_mode="full"`` build formerly inlined in
``abicheck.reporter.to_json`` -- moved here (rather than kept as a sibling
function in ``reporter.py``) purely to stay clear of that file's ADR-061
``no_growth`` architecture-debt baseline (``architecture/debt.yaml``); the
logic is otherwise unchanged from what ``to_json`` used to run inline. This
module imports ``reporter.py``'s private ``_add_*``/``_build_*`` helpers
statically (function-local, to avoid a module-level import-time ordering
issue, but a real, named import all the same); ``reporter.to_json`` reaches
back into *this* module through ``importlib`` instead of a static
``from .report.build import ...``, so the pair never forms a real import
cycle -- only one direction is a static edge.

Scope note (2026-09-07): JSON's own ``report_mode="full"`` build is the
first, and so far only, pipeline routed through this choke point. Markdown,
HTML, SARIF, and JUnit still each build (and freeze) their own document
independently -- see ``docs/contribute/adr/
061-responsibility-package-architecture.md``'s Gap C status note and
``docs/contribute/plans/duplication-and-convergence-assessment.md``'s
Phase 4 status note for the precise, current per-format state and the
remaining work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .document import ReportDocument

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..workflows.gate import SeverityConfig


def build_report_document(
    result: DiffResult,
    *,
    show_only: str | None = None,
    show_impact: bool = False,
    severity_config: SeverityConfig | None = None,
    require_complete_analysis: bool = False,
    include_exit_decision: bool = True,
    contract_evaluation: bool = False,
) -> ReportDocument:
    """Build the one canonical, format-neutral ``report_mode="full"`` document.

    Every keyword mirrors ``abicheck.service_render.render_output``'s own
    format-neutral options (``show_only``/``show_impact``/
    ``severity_config``/``require_complete_analysis``/
    ``include_exit_decision``/``contract_evaluation``); a genuinely
    format-specific presentation flag (HTML's ``demangle``, Markdown's
    ``show_recommendation``) is not a parameter here -- it belongs to the
    format's own render step, applied to a *projection* of this document,
    never to a second, differently-decided build.
    """
    # Static import of `reporter.py`'s own privately-defined helpers --
    # this is the one direction of the report.build <-> reporter dependency
    # that stays a real import; `reporter.to_json` reaches back into this
    # module through `importlib` instead, so the pair never forms an
    # actual import cycle (see `reporter.to_json`'s own comment).
    from ..policy.gate_decision import gate_decision_for_result
    from ..reporter import (
        _SCOPED_GATE_HELPERS,
        _add_abi_surface_breakdown,
        _add_changes_block,
        _add_confidence_evidence,
        _add_detectors,
        _add_evidence_fields,
        _add_policy_overrides,
        _add_reconciled,
        _add_show_only_filter,
        _add_suppression,
        _add_surface_scope,
        _add_trailing_fields,
        _build_json_base,
        _build_severity_json,
        _displayed_with_scoped_only,
        _suppress_dangling_correlation_notes,
        apply_show_only,
    )
    from ..reporter_contract_blocks import (
        add_contract_context as _add_contract_context,
        build_report_document_with_side_facts,
    )
    from .disposition_audit import add_disposition_audit as _add_disposition_audit
    from .finding_evolution import add_finding_evolution as _add_finding_evolution

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        changes = _suppress_dangling_correlation_notes(changes)

    d = _build_json_base(result)
    _add_abi_surface_breakdown(d, result)
    _add_evidence_fields(d, result)
    effective_policy = result.policy or "strict_abi"
    d["policy"] = effective_policy
    eff_sets = result._effective_kind_sets()

    if show_only:
        _add_show_only_filter(d, result, changes, show_only)

    # Severity-categorized summary when severity config is provided
    gate = gate_decision_for_result(result, severity_config)
    if gate is not None:
        assert severity_config is not None  # gate is None otherwise
        d["severity"] = _build_severity_json(
            changes,
            severity_config,
            gate=gate,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )

    _add_changes_block(
        d,
        result,
        changes,
        effective_policy,
        eff_sets,
        show_only,
        severity_config=severity_config,
    )
    _add_suppression(d, result)
    _add_disposition_audit(d, result, severity_config)
    _add_finding_evolution(d, result)
    _add_surface_scope(d, result)
    _add_reconciled(d, result)
    _add_contract_context(
        d,
        result,
        _displayed_with_scoped_only(result, changes, show_only),
        require_complete_analysis=require_complete_analysis,
        severity_config=severity_config,
        include_exit_decision=include_exit_decision,
    )
    _add_detectors(d, result)
    _add_confidence_evidence(d, result)
    _add_policy_overrides(d, result)
    _add_trailing_fields(d, result, show_impact, show_only)
    return build_report_document_with_side_facts(
        d,
        result,
        helpers=_SCOPED_GATE_HELPERS,
        severity_config=severity_config,
        gate=gate,
        show_only=show_only,
        contract_evaluation=contract_evaluation,
    )
