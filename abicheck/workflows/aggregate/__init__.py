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

"""Canonical implementation owner for multi-target report aggregation."""

from .contracts import (
    AGGREGATE_MANIFEST_VERSION,
    AGGREGATE_SCHEMA_VERSION,
    COVERAGE_INCOMPLETE_EXIT,
    DEFAULT_REPORT_PREFIX,
    CheckIdParts,
    CoverageStatus,
    GateInfo,
    ProfileMatrixEntry,
    TargetReport,
    parse_check_id,
)
from .execute import aggregate, aggregate_reports_dir, collect_reports
from .fold import AggregateResult
from .gate import (
    contract_coverage_block_paths,
    contract_coverage_blocks,
    scan_severity_gate_paths,
)
from .load import parse_report_verdict, target_id_from_path
from .matrix import (
    FindingMatrixEntry,
    ProfileContractState,
    build_finding_matrix,
    render_finding_matrix_lines,
)
from .reconcile import (
    FINDING_SCOPE_ALL_PROFILES,
    FINDING_SCOPE_PARTIAL,
    FINDING_SCOPE_PROFILE_SPECIFIC,
    FINDING_SCOPE_UNDETERMINED,
    ReportFinding,
    ReportFindings,
    comparable_mangled_symbol,
    cross_abi_declaration,
    mangling_scheme,
    parse_report_findings,
    resolve_cross_abi_identity,
    resolve_report_change_identity,
)
from .resolve import (
    AggregateError,
    ExpectedTargets,
    OnMissingRequired,
    OnUnexpectedTarget,
    resolve_gate_policy,
)

__all__ = [
    "AGGREGATE_MANIFEST_VERSION",
    "AGGREGATE_SCHEMA_VERSION",
    "COVERAGE_INCOMPLETE_EXIT",
    "DEFAULT_REPORT_PREFIX",
    "FINDING_SCOPE_ALL_PROFILES",
    "FINDING_SCOPE_PARTIAL",
    "FINDING_SCOPE_PROFILE_SPECIFIC",
    "FINDING_SCOPE_UNDETERMINED",
    "AggregateError",
    "AggregateResult",
    "CheckIdParts",
    "CoverageStatus",
    "ExpectedTargets",
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
    "comparable_mangled_symbol",
    "contract_coverage_block_paths",
    "contract_coverage_blocks",
    "cross_abi_declaration",
    "mangling_scheme",
    "parse_check_id",
    "parse_report_findings",
    "parse_report_verdict",
    "render_finding_matrix_lines",
    "resolve_cross_abi_identity",
    "resolve_gate_policy",
    "resolve_report_change_identity",
    "scan_severity_gate_paths",
    "target_id_from_path",
]
