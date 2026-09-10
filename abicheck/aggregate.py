# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Compatibility facade for multi-target aggregation.

The supported ``abicheck.aggregate`` import path remains available. New
internal code must import the canonical implementation owner at
:mod:`abicheck.workflows.aggregate` instead.
"""

from __future__ import annotations

from .workflows.aggregate import (
    AGGREGATE_MANIFEST_VERSION,
    AGGREGATE_SCHEMA_VERSION,
    COVERAGE_INCOMPLETE_EXIT,
    DEFAULT_REPORT_PREFIX,
    FINDING_SCOPE_ALL_PROFILES,
    FINDING_SCOPE_PARTIAL,
    FINDING_SCOPE_PROFILE_SPECIFIC,
    FINDING_SCOPE_UNDETERMINED,
    AggregateError,
    AggregateResult,
    CheckIdParts,
    CoverageStatus,
    ExpectedTargets,
    FindingMatrixEntry,
    GateInfo,
    OnMissingRequired,
    OnUnexpectedTarget,
    ProfileContractState,
    ProfileMatrixEntry,
    ReportFinding,
    ReportFindings,
    TargetReport,
    aggregate,
    aggregate_reports_dir,
    build_finding_matrix,
    collect_reports,
    contract_coverage_block_paths,
    contract_coverage_blocks,
    parse_check_id,
    parse_report_findings,
    parse_report_verdict,
    render_finding_matrix_lines,
    resolve_gate_policy,
    scan_severity_gate_paths,
    target_id_from_path,
)

__all__ = [
    "AGGREGATE_MANIFEST_VERSION",
    "AGGREGATE_SCHEMA_VERSION",
    "COVERAGE_INCOMPLETE_EXIT",
    "DEFAULT_REPORT_PREFIX",
    "AggregateError",
    "AggregateResult",
    "CheckIdParts",
    "CoverageStatus",
    "ExpectedTargets",
    "FINDING_SCOPE_ALL_PROFILES",
    "FINDING_SCOPE_PARTIAL",
    "FINDING_SCOPE_PROFILE_SPECIFIC",
    "FINDING_SCOPE_UNDETERMINED",
    "FindingMatrixEntry",
    "GateInfo",
    "OnMissingRequired",
    "OnUnexpectedTarget",
    "ProfileContractState",
    "ProfileMatrixEntry",
    "ReportFinding",
    "ReportFindings",
    "TargetReport",
    "aggregate",
    "aggregate_reports_dir",
    "build_finding_matrix",
    "collect_reports",
    "contract_coverage_block_paths",
    "contract_coverage_blocks",
    "parse_check_id",
    "parse_report_findings",
    "parse_report_verdict",
    "render_finding_matrix_lines",
    "resolve_gate_policy",
    "scan_severity_gate_paths",
    "target_id_from_path",
]
