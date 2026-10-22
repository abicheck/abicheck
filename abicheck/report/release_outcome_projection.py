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

"""The release report's composition from one finished outcome.

:func:`project_release_outcome` is the last step that reads each member's
live ``DiffResult``: it projects the findings every release renderer reads
(:func:`~abicheck.report.release_findings.project_release_library_results`),
folds findings several members reported identically into the release
public-surface section, and assembles the scope and assurance terms. The
CLI calls it once, then hands the result to each export; no format computes
these itself.

It strips each member's private ``_``-prefixed working keys from
``outcome.library_results`` in place, so it runs once, before any renderer
reads those results.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .comparison_scope import ComparisonScopeTerms, comparison_scope_terms
from .release_assurance_section import ReleaseAssuranceTerms, release_assurance_terms
from .release_findings import project_release_library_results
from .release_public_surface_section import (
    ReleasePublicSurfaceTerms,
    assemble_release_public_surface,
)

if TYPE_CHECKING:
    from ..workflows.release_compare import ReleaseCompareOutcome

__all__ = ["ReleaseOutcomeProjection", "project_release_outcome"]


@dataclass(frozen=True)
class ReleaseOutcomeProjection:
    """The outcome-derived values every release format reads, computed once."""

    #: The verdict after the per-library findings projection; already
    #: includes every release-global contributor and the proven-removed floor.
    worst_verdict: str
    public_surface: ReleasePublicSurfaceTerms
    scope_terms: ComparisonScopeTerms
    assurance_terms: ReleaseAssuranceTerms


def project_release_outcome(
    outcome: ReleaseCompareOutcome,
    *,
    formats: Collection[str],
    show_only: str | None,
    show_impact: bool,
) -> ReleaseOutcomeProjection:
    """Project *outcome* for every export named in *formats*.

    *formats* is the primary format plus every ``-o`` secondary format: the
    per-library findings are annotated (``needs_annotations``) only when a
    JSON export will read them. *show_only* and *show_impact* are the view
    the caller asked for; they filter what each library reports, never the
    counts or the verdict.
    """
    worst_verdict = project_release_library_results(
        outcome.library_results,
        outcome.removed_keys,
        outcome.worst_verdict,
        outcome.severity_config,
        needs_annotations="json" in formats,
        show_only=show_only,
        show_impact=show_impact,
    )
    # "One product fact, reported once": fold findings several members
    # reported identically out of the per-library tables and assemble the
    # release public-surface section every format reads.
    public_surface = assemble_release_public_surface(
        outcome.surface_stage,
        outcome.library_results,
        type_attribution=outcome.type_attribution,
    )
    return ReleaseOutcomeProjection(
        worst_verdict=worst_verdict,
        public_surface=public_surface,
        scope_terms=comparison_scope_terms(outcome.scope_decision),
        assurance_terms=release_assurance_terms(outcome.assurance_decision),
    )
