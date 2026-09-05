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

from ....model.scope_acquisition import UNCHECKED_STATES
from ....report.comparison_scope import (
    ComparisonScopeTerms,
    comparison_scope_terms,
    render_comparison_scope_markdown,
)
from ....report.not_comparable import OperationalStatus

__all__ = [
    "has_unchecked_matched_members",
    "json_scope_fields",
    "markdown_scope_lines",
    "scope_terms_for",
]


def scope_terms_for(result: Any, kwargs: Mapping[str, Any]) -> ComparisonScopeTerms:
    """The dispatch's resolved terms, under `compare`'s own
    `--on-incomplete-scope` value (absent -> the default `warn`)."""
    return comparison_scope_terms(
        getattr(result, "scope_record", None), kwargs.get("on_incomplete_scope")
    )


def has_unchecked_matched_members(result: Any) -> bool:
    """Whether the record names a *matched* member that never reached a
    completed comparison -- `failed` (a degraded capture) or `unsupported`
    (an artifact this build cannot analyze; Codex review) -- the case where
    an empty `per_library` is D7's "no comparison completed", not the
    "nothing matched" usage error."""
    record = getattr(result, "scope_record", None)
    if record is None:
        return False
    return any(
        m.old_present and m.new_present and m.state in UNCHECKED_STATES
        for m in record.members
    )


def json_scope_fields(
    terms: ComparisonScopeTerms, run_outcome: dict[str, Any]
) -> dict[str, Any]:
    """`run_outcome` with its `scope` axis set -- and, when the selected
    scope completed no comparison at all (D7), its `operational` axis set to
    `no_comparison_completed`, the same reading the release fan-out's own
    `run_outcome_dict_for_release` gives that contribution -- plus the
    `comparison_scope` section (absent when the driver built no record)."""
    run_outcome["scope"] = terms.completeness.value
    if terms.no_comparison_completed_exit_contribution == 1:
        run_outcome["operational"] = OperationalStatus.NO_COMPARISON_COMPLETED.value
        run_outcome["compatibility"] = None
    fields: dict[str, Any] = {"run_outcome": run_outcome}
    if terms.section is not None:
        fields["comparison_scope"] = terms.section
    return fields


def markdown_scope_lines(terms: ComparisonScopeTerms) -> list[str]:
    return (
        [] if terms.section is None else render_comparison_scope_markdown(terms.section)
    )
