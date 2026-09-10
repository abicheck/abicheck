# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""JUnit's ADR-067 disposition-audit testsuite properties.

A sibling of :mod:`abicheck.report.junit_scope`: the JUnit-shaped projection
of one already-computed report block, owned by ``report`` rather than by
``junit_report.py``'s legacy monolith (ADR-061 Phase 2's per-format
compute/render split, and the shrink-by-moving-responsibility rule root
``AGENTS.md`` states for an ``architecture/debt.yaml`` ``no_growth`` entry).

``junit_report.py`` re-exports :func:`add_disposition_audit_properties` under
its historical private name, so every existing call site and test resolves
unchanged.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from .document import ReportDocument


def add_disposition_audit_properties(
    props: ET.Element,
    result: DiffResult,
    severity_config: object | None = None,
    report_document: ReportDocument | None = None,
) -> None:
    """Append ADR-067 D3's raw-versus-effective counts as testsuite properties.

    A JUnit consumer reads ``tests``/``failures``, which are the *effective*
    numbers by construction -- a fully suppressed comparison reports zero
    failures and would otherwise carry no trace that anything was detected at
    all. These properties are that trace, in the one mechanism JUnit gives for
    suite-level metadata; the per-disposition counts are emitted individually
    so a dashboard can chart one without parsing a blob.

    Unconditional, unlike the sibling scoped block, which is emitted only
    under ``--used-by``/``--required-symbol(s)``: a raw-versus-effective
    count a view can drop is not the invariant D3 states.

    *report_document* (ADR-061 gap C), when given, is reused via
    ``disposition_audit_dict_reusing_document`` instead of a second
    ``compute_disposition_audit`` call -- mirrors ``sarif.to_sarif``.
    """
    from . import disposition_audit as _da

    audit = _da.DispositionAudit.from_dict(
        _da.disposition_audit_dict_reusing_document(
            result, severity_config, report_document
        )
    )

    def _prop(name: str, value: str) -> None:
        p = ET.SubElement(props, "property")
        p.set("name", name)
        p.set("value", value)

    _prop("abicheck.detected_total", str(audit.detected_total))
    _prop("abicheck.effective_total", str(audit.effective_total))
    for name, count in audit.counts:
        _prop(f"abicheck.disposition.{name}", str(count))
    for rule, count in audit.rules:
        _prop(
            f"abicheck.disposition_rule.{rule.rule_id or 'rule'}",
            f"{count} finding(s); reason={rule.reason or 'none'}; "
            f"intent={rule.intent}; source={rule.source_file or 'inline'}",
        )
    if audit.policy_overlays:  # findings the gate scores that are not detections
        _prop("abicheck.policy_overlays", str(audit.policy_overlays))
    if audit.not_evaluated_detectors:
        names = ",".join(d.name for d in audit.not_evaluated_detectors)
        _prop("abicheck.not_evaluated_detectors", names)
