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
"""The overall ``AnalysisAssurance.status`` ladder, as a pure function.

Rungs in rank order: ``failed``, ``partial``, ``not_requested``,
``complete``. Each sub-status maps to a rung through a table keyed by its
closed vocabulary (``policy.assurance_status_vocabulary``), so a value that
is not in a table is a ``KeyError`` here and a test failure, never a silent
``complete`` (the if-chain this replaces listed only the values it knew).
"""

from __future__ import annotations

from collections.abc import Mapping

from ..assurance_status_vocabulary import (
    ContextStatus,
    FactSetComparability,
    GraphCompleteness,
    HeaderContextStatus,
    SchemaStalenessStatus,
)
from ._result import AssuranceStatus

#: ``True`` = this value is a shortfall that makes the run ``partial``.
CONTEXT_SHORTFALL: Mapping[ContextStatus, bool] = {
    "clean": False,
    "asymmetric": True,
    "not_evaluated": False,
}
HEADER_SHORTFALL: Mapping[HeaderContextStatus, bool] = {
    "clean": False,
    "drift_detected": True,
    "asymmetric": True,
    "not_evaluated": False,
}
SCHEMA_STALENESS_SHORTFALL: Mapping[SchemaStalenessStatus, bool] = {
    "clean": False,
    "degraded": True,
    "not_evaluated": False,
}
GRAPH_SHORTFALL: Mapping[GraphCompleteness, bool] = {
    "complete": False,
    "degraded": True,
    "narrowed": True,
    "unknown": True,
    "not_collected": False,
}
#: Fact-set comparability is the one axis with a ``failed`` rung.
FACT_SET_RUNG: Mapping[FactSetComparability, AssuranceStatus | None] = {
    "comparable": None,
    "inconsistent": "failed",
    "unknown": "partial",
    "not_applicable": None,
}


def overall_status(
    *,
    fact_set_comparability: FactSetComparability,
    depth_unsatisfied: bool,
    l0_context_status: ContextStatus,
    header_context_status: HeaderContextStatus,
    dwarf_context_status: ContextStatus,
    l3_context_status: ContextStatus,
    schema_staleness_status: SchemaStalenessStatus,
    graph_completeness: GraphCompleteness,
    other_shortfall: bool,
    nothing_requested: bool,
) -> AssuranceStatus:
    fact_set_rung = FACT_SET_RUNG[fact_set_comparability]
    if fact_set_rung == "failed" or depth_unsatisfied:
        return "failed"
    if (
        fact_set_rung == "partial"
        or CONTEXT_SHORTFALL[l0_context_status]
        or HEADER_SHORTFALL[header_context_status]
        or CONTEXT_SHORTFALL[dwarf_context_status]
        or CONTEXT_SHORTFALL[l3_context_status]
        or SCHEMA_STALENESS_SHORTFALL[schema_staleness_status]
        or GRAPH_SHORTFALL[graph_completeness]
        or other_shortfall
    ):
        return "partial"
    return "not_requested" if nothing_requested else "complete"
