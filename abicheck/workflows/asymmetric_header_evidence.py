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

"""Record that a pair was judged at its operands' common evidence rung.

``policy.common_evidence_depth`` decides the rung; this workflow step runs
the joint projection and records the consequence on the run: the
header-built side's evidence gap (so assurance reads ``partial``) and the
coverage warning the report shows. Weaker evidence narrows the conclusion
and stays visible; it never reads as a full comparison.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..policy.common_evidence_depth import effective_compare_depth
from ..policy.depth_projection import project_pair_to_depth

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "ASYMMETRIC_HEADER_GAP",
    "asymmetric_header_evidence_warning",
    "mark_asymmetric_header_gap",
    "project_pair_for_compare",
]


def project_pair_for_compare(
    old: AbiSnapshot, new: AbiSnapshot, requested: str | None
) -> tuple[AbiSnapshot, AbiSnapshot, str | None, str | None]:
    """``(old, new, depth, warning)``: the pair projected to the rung it is
    classified at, that rung, and the coverage warning to report when the
    rung was capped on evidence rather than requested (else ``None``)."""
    depth = effective_compare_depth(requested, old, new)
    p_old, p_new = project_pair_to_depth(old, new, depth)
    if requested is not None or depth is None:
        return p_old, p_new, depth, None
    mark_asymmetric_header_gap(old, p_old, p_new)
    return p_old, p_new, depth, asymmetric_header_evidence_warning(old)


def asymmetric_header_evidence_warning(old: AbiSnapshot) -> str:
    """The coverage warning a pair judged at its common rung carries."""
    side, other = ("old", "new") if old.from_headers else ("new", "old")
    return (
        f"headers were given for the {side} side only: the {other} side has no "
        "header-level evidence, so the pair was compared at depth binary "
        "(exported symbols) and header-level changes were not assessed; give "
        "headers for both sides to compare declarations"
    )


#: The evidence a common-rung comparison sets aside on the header side.
ASYMMETRIC_HEADER_GAP = (
    "the header-built side's declarations (headers were given for one side "
    "only, so the pair was compared at binary depth)"
)


def mark_asymmetric_header_gap(
    original_old: AbiSnapshot, projected_old: AbiSnapshot, projected_new: AbiSnapshot
) -> None:
    """Record on the projected header-built side that its header evidence was
    set aside, so the run's assurance reads ``partial`` rather than
    ``complete`` (weaker evidence narrows the conclusion; it never reads as a
    full comparison)."""
    side = projected_old if original_old.from_headers else projected_new
    if ASYMMETRIC_HEADER_GAP not in side.projection_evidence_gaps:
        side.projection_evidence_gaps = (
            *side.projection_evidence_gaps,
            ASYMMETRIC_HEADER_GAP,
        )
