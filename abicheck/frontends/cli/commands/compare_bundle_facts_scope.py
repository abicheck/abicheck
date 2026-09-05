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

"""ADR-065 S2 for the stored-baseline `compare` dispatch
(`compare_bundle_facts.py`, a `no_growth` module): the completeness axis
read off the driver's `BundleDiffResult.scope_record`.

A member a `BundleFacts` capture recorded as degraded (D8) is `failed` on
that record; the dispatcher must gate on it and say so in every view,
rather than reading a nonempty `per_library` list as a fully checked scope
(Codex review on the first cut).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ....report.comparison_scope import (
    ComparisonScopeTerms,
    comparison_scope_terms,
    render_comparison_scope_markdown,
)
from ....report.not_comparable import OperationalStatus
from ....workflows.gate import resolve_scope_decision

__all__ = [
    "json_scope_fields",
    "markdown_scope_lines",
    "scope_terms_for",
]


def scope_terms_for(result: Any, kwargs: Mapping[str, Any]) -> ComparisonScopeTerms:
    """The dispatch's resolved terms, under `compare`'s own
    `--on-incomplete-scope` value (absent -> the default `warn`): the
    decision is policy's (`resolve_scope_decision`), the projection the
    report's."""
    return comparison_scope_terms(
        resolve_scope_decision(
            getattr(result, "scope_record", None), kwargs.get("on_incomplete_scope")
        )
    )


def json_scope_fields(
    terms: ComparisonScopeTerms,
    run_outcome: dict[str, Any],
    result: Any,
) -> dict[str, Any]:
    """`run_outcome` with its `scope` axis set -- and, when the selected
    scope completed no comparison at all (D7), its `operational` axis set to
    `no_comparison_completed`, the same reading the release fan-out's own
    `run_outcome_dict_for_release` gives that contribution -- plus the
    `comparison_scope` section (absent when the driver built no record).

    *result*'s ``extraction_failures`` (ADR-065 D1, Codex review) are the
    matched members whose NEW artifact failed extraction in *this* run: an
    operational `extraction_error` whatever the completeness policy
    accepted (the native fan-out's per-library `ERROR`). It outranks D7's
    `no_comparison_completed` exactly as `run_outcome_dict_for_release`
    ranks the two contributions: when every member failed extraction the
    run exits 4 *because* extraction failed, and the operational axis must
    name that cause rather than let the exit read as a break (Codex
    review, twenty-first round); D7 stays recorded in the section.
    *not_comparable* (ADR-050 D2, thirtieth round) outranks both: the
    fan-out's own `not_comparable` rank sits above `ERROR`, and the run
    exits 16 *because* a matched pair was not comparable."""
    run_outcome["scope"] = terms.completeness.value
    if terms.decision.no_comparison_completed_exit_contribution == 1:
        run_outcome["operational"] = OperationalStatus.NO_COMPARISON_COMPLETED.value
        run_outcome["compatibility"] = None
    extraction_failures = dict(getattr(result, "extraction_failures", {}) or {})
    not_comparable = dict(getattr(result, "not_comparable_members", {}) or {})
    if extraction_failures:
        run_outcome["operational"] = OperationalStatus.EXTRACTION_ERROR.value
    if not_comparable:
        run_outcome["operational"] = OperationalStatus.NOT_COMPARABLE.value
        run_outcome["compatibility"] = None
    fields: dict[str, Any] = {
        "run_outcome": run_outcome,
        "extraction_failures": extraction_failures,
        "not_comparable_members": {
            k: {"kind": kind, "message": msg}
            for k, (kind, msg) in not_comparable.items()
        },
    }
    if terms.section is not None:
        fields["comparison_scope"] = terms.section
    return fields


def markdown_scope_lines(terms: ComparisonScopeTerms) -> list[str]:
    """The Markdown lines for the section, empty when the driver built no record."""
    return (
        [] if terms.section is None else render_comparison_scope_markdown(terms.section)
    )
