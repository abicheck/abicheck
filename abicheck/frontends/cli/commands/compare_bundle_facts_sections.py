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

"""Report sections the stored-``BundleFacts`` comparison document carries.

The stored-baseline drivers are release comparisons too, so their document
states the same release-level facts the live release renderer does. Each
function here assembles one section and hands it back; the dispatch module
only places it, which is what keeps that already-capped file from growing a
section's worth of assembly every time one is added.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....report.release_public_surface import ReleasePublicSurfaceTerms


def assurance_sections(assurance_decision: Any) -> dict[str, object]:
    """ADR-071 D9's gate-bearing scalar plus its fold section, or ``{}``.

    Present only under the setting (D4). The gate-bearing key is a plain
    scalar needing no ``exit`` block, which is why omitting it was a real
    bypass rather than a cosmetic gap (Codex review, P1). This document
    still has no ``exit`` block of its own -- a separate gap, recorded in
    known-gaps.md.
    """
    if assurance_decision is None or not assurance_decision.require_complete:
        return {}
    from ....report.release_assurance import release_assurance_terms

    return {
        "analysis_assurance_exit_contribution": assurance_decision.exit_contribution,
        "analysis_assurance": release_assurance_terms(assurance_decision).section,
    }


def public_surface_terms(result: Any) -> ReleasePublicSurfaceTerms | None:
    """The release public-surface section for a stored-baseline comparison.

    ``None`` when the comparison recorded no product contract at all (a
    single-member release, or a baseline carrying no public-header
    evidence) -- absent rather than an empty section, since an empty one
    would read as "the product promises nothing" instead of "no contract
    was recorded".

    The reconciliation itself is computed by the driver and carried on
    ``BundleDiffResult.public_surface_reconciliation``; this only projects
    it. There is no acquisition ledger on a stored path -- nothing was
    acquired -- so its counts are empty rather than fabricated, and no
    per-library fold applies, since these documents already report one
    product-level finding rather than repeating it per member.
    """
    from ....report.release_public_surface import compute_release_public_surface

    reconciliation = getattr(result, "public_surface_reconciliation", None)
    if reconciliation is None:
        return None
    return compute_release_public_surface(
        reconciliation, acquisition={}, shared_findings=()
    )


def public_surface_markdown_lines(result: Any) -> list[str]:
    """*result*'s public-surface section as Markdown lines (``[]`` if none)."""
    from ....report.release_public_surface import (
        render_release_public_surface_markdown,
    )

    terms = public_surface_terms(result)
    if terms is None:
        return []
    rendered = render_release_public_surface_markdown(terms)
    return rendered.splitlines() + [""] if rendered else []
