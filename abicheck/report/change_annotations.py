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

"""The optional per-change annotation fields a JSON finding may carry.

Moved out of ``reporter.py`` (ADR-061's ``report/`` ownership rule, and
AGENTS.md's "the way to shrink a debt entry is to move responsibility out
to a properly-owned module") when ADR-068 Phase 2d needed one more field
here and ``reporter.py`` was already at the 2000-line hard cap. Pure
projection: it reads a finding and returns plain values, deciding nothing.
``reporter.py`` re-exports it under its original private name, so every
existing call site and test import keeps working.
"""

from __future__ import annotations

from typing import Any

from ..checker_policy import Verdict
from .cross_source_evolution import change_cross_source_evolution_field as _cse


def change_annotation_fields(c: Any) -> dict[str, Any]:
    """Optional per-change attribution/annotation fields for the JSON report.

    Each is omitted rather than emitted as ``null`` when it carries nothing, so
    a consumer can tell "not applicable" from "empty". The reasoning behind the
    individual entries is kept with them below.
    """
    out: dict[str, Any] = {}
    # Source location
    loc = getattr(c, "source_location", None)
    if loc:
        out["source_location"] = loc
    # Affected symbols
    affected = getattr(c, "affected_symbols", None)
    if affected:
        out["affected_symbols"] = affected
    # Redundancy annotation
    caused_by = getattr(c, "caused_by_type", None)
    if caused_by:
        out["caused_by_type"] = caused_by
    caused_count = getattr(c, "caused_count", 0)
    if caused_count > 0:
        out["caused_count"] = caused_count
    # ADR-027 A4 — disclose a pattern-aware modulation on the finding itself.
    mod_reason = getattr(c, "modulation_reason", None)
    if mod_reason:
        out["modulation_reason"] = mod_reason
        out["modulation_rule"] = getattr(c, "modulation_rule", None)
        eff = getattr(c, "effective_verdict", None)
        if isinstance(eff, Verdict):
            out["effective_verdict"] = eff.value
    # ADR-041 P0 roadmap item 2 — this finding correlates with another
    # finding (currently: PUBLIC_API_INTERNAL_DEPENDENCY_ADDED correlating
    # with the same entry's own body/type-hash change), named by ChangeKind
    # value so a machine consumer can act on it without parsing description.
    correlated = getattr(c, "correlated_change_kind", None)
    if correlated:
        out["correlated_change_kind"] = correlated
    if getattr(c, "symbol_binding", None):
        out["symbol_binding"] = c.symbol_binding
    # ADR-068 D3 / plan P2 — this finding's OLD->NEW evolution *within this
    # one comparison* (a one-sided cross-source check re-run against the
    # baseline), distinct from the cross-comparison-chain `evolution` field.
    if (cse := _cse(c)) is not None:
        out["cross_source_evolution"] = cse
    # ADR-068 D3 — a candidate-only check's finding (the --abi3 audit) rides
    # this comparison's own result document, marked rather than split out.
    if getattr(c, "candidate_side_enrichment", False):
        out["candidate_side_enrichment"] = True
    return out
