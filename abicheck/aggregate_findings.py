"""Compatibility facade for aggregate finding reconciliation.

New internal code imports :mod:`abicheck.workflows.aggregate.reconcile`.
"""

from __future__ import annotations

from .workflows.aggregate.matrix import (
    FindingMatrixEntry,
    ProfileCheckFindings,
    ProfileContractState,
    build_finding_matrix,
    render_finding_matrix_lines,
)
from .workflows.aggregate.reconcile import (
    FINDING_SCOPE_ALL_PROFILES,
    FINDING_SCOPE_PARTIAL,
    FINDING_SCOPE_PROFILE_SPECIFIC,
    FINDING_SCOPE_UNDETERMINED,
    MANGLING_ITANIUM,
    MANGLING_MSVC,
    ReportFinding,
    ReportFindings,
    comparable_mangled_symbol,
    cross_abi_declaration,
    mangling_scheme,
    parse_report_findings,
    resolve_cross_abi_identity,
    resolve_report_change_identity,
)

__all__ = [
    "FINDING_SCOPE_ALL_PROFILES",
    "FINDING_SCOPE_PARTIAL",
    "FINDING_SCOPE_PROFILE_SPECIFIC",
    "FINDING_SCOPE_UNDETERMINED",
    "FindingMatrixEntry",
    "MANGLING_ITANIUM",
    "MANGLING_MSVC",
    "ProfileContractState",
    "ProfileCheckFindings",
    "ReportFinding",
    "ReportFindings",
    "build_finding_matrix",
    "comparable_mangled_symbol",
    "cross_abi_declaration",
    "mangling_scheme",
    "parse_report_findings",
    "render_finding_matrix_lines",
    "resolve_cross_abi_identity",
    "resolve_report_change_identity",
]
