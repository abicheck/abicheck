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

"""Hand a stored-bundle-facts comparison to the live release renderer.

A stored ``BundleFacts`` OLD_INPUT has no per-member fan-out, so it has no
``library_results`` list of its own. This adapter builds one from the stored
comparison's per-library ``DiffResult``s, with the same per-member stamps a
live member carries (counts, review summary, exit contributions), and then
renders through :func:`~abicheck.report.render_release_report.render_release_text`
-- the one release renderer. JUnit and oneline are the formats it serves
today; the stored JSON and Markdown documents keep their own shape.

Severity presets and packs are rejected for a stored operand, so a live
member's severity fold never applies here: the entries carry no
``_compatibility_contribution`` and the release ``severity_exit_code`` is
``None``, exactly as a live run without a severity setting.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ....report.comparison_scope import ComparisonScopeTerms
from ....report.release_assurance_section import (
    ReleaseAssuranceDecision,
    release_assurance_terms,
)
from ....report.release_document import ReleaseEvaluation
from ....report.release_findings import project_release_library_results
from ....report.release_member_entry import stamp_member_counts
from ....report.release_member_summary import add_member_review_summary
from ....report.release_surface_changes import attach_member_surface_changes
from ....workflows.gate import resolve_release_assurance_decision
from ....workflows.member_compare import stamp_member_exit_contributions

#: Per-member fields a stored comparison cannot compute, each an explicit
#: ``unavailable`` marker with its reason (never an empty value or a missing key).
_UNAVAILABLE_MEMBER_FIELDS = {
    "type_attribution": (
        "stored facts keep no per-member snapshot to say which type findings "
        "this member's own exports reach"
    ),
    "unreached_type_disposition": (
        "unreached-type disposition needs the sibling members' snapshots, which "
        "stored facts do not carry"
    ),
    "bundle_evidence": (
        "per-side bundle evidence is collected during a live compare and is not "
        "stored with the facts"
    ),
}


def unavailable_marker(reason: str) -> dict[str, str]:
    """An explicit ``unavailable`` state with the reason it is unavailable."""
    return {"state": "unavailable", "reason": reason}


def stored_library_entries(
    result: Any, *, require_complete_analysis: bool
) -> list[dict[str, object]]:
    """One release entry per stored per-library ``DiffResult``.

    Mirrors the stamps a live member gets from its own projection, minus the
    snapshot-dependent ones (dependency graph, pattern modulations): a stored
    comparison has no snapshots to read them from.
    """
    entries: list[dict[str, object]] = []
    for diff in result.per_library:
        entry: dict[str, object] = {
            "library": diff.library,
            "verdict": diff.verdict.value,
            "_diff_result": diff,
        }
        stamp_member_counts(entry, diff)
        if diff.coverage_warnings:
            entry["coverage_warnings"] = list(diff.coverage_warnings)
        if diff.env_matrix_source_sha256 is not None:
            entry["env_matrix_source_sha256"] = diff.env_matrix_source_sha256
        add_member_review_summary(entry, diff, None)
        stamp_member_exit_contributions(
            entry,
            diff,
            None,
            require_complete_analysis=require_complete_analysis,
            contract_evaluation=False,
        )
        for field, reason in _UNAVAILABLE_MEMBER_FIELDS.items():
            entry[field] = unavailable_marker(reason)
        entries.append(entry)
    return entries


def stored_release_evaluation(
    result: Any,
    *,
    worst_verdict: str,
    old_facts_path: Path,
    new_dir: Path,
    scope_terms: ComparisonScopeTerms,
    assurance_decision: ReleaseAssuranceDecision | None,
    require_complete_analysis: bool,
    effective_config: tuple[str, Mapping[str, str]] | None = None,
) -> ReleaseEvaluation:
    """The live :class:`ReleaseEvaluation` a stored comparison renders as.

    *worst_verdict* is the stored comparison's reported verdict. The operand
    identity fields (``old_dir``/``new_dir``) carry the stored facts path and
    the NEW side; no removed/added keys are invented for it.
    """
    assurance_terms = release_assurance_terms(
        assurance_decision
        if assurance_decision is not None
        else resolve_release_assurance_decision((), require_complete=False)
    )
    entries = stored_library_entries(
        result, require_complete_analysis=require_complete_analysis
    )
    # The per-member steps of the live projection that read each DiffResult,
    # in the live order: the surface block first, then findings and the strip
    # of private keys. The release-level public-surface fold needs the live
    # surface stage, so a stored run has none (see _stored_release_block).
    attach_member_surface_changes(entries)
    worst_verdict = project_release_library_results(
        entries, [], worst_verdict, None, needs_annotations=True
    )
    return ReleaseEvaluation(
        worst_verdict=worst_verdict,
        old_dir=old_facts_path,
        new_dir=new_dir,
        library_results=entries,
        scope_terms=scope_terms,
        assurance_terms=assurance_terms,
        diff_pairs=[(diff, None) for diff in result.per_library],
        bundle_result=result,
        require_complete_analysis=require_complete_analysis,
        effective_config=effective_config,
    )
