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

"""Confidence-qualified evidence-coverage rows for the header (L2) layer.

A header-derived layer is *present* whenever a snapshot came from headers, but
a header set that parsed and declared nothing is not high-confidence
public-surface evidence: every declaration the other side has then reads as
added or removed against it. The coverage table states that with the existing
:class:`~abicheck.buildsource.model.LayerConfidence` vocabulary (``reduced``),
never with a new tier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.empty_header_surface import header_surface_declares_nothing
from .model import CoverageStatus, LayerConfidence, LayerCoverage

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["header_layer_coverage", "side_status_label"]


def header_layer_coverage(snap: AbiSnapshot) -> LayerCoverage:
    """The L2 coverage row for one side's snapshot (``from_headers`` provenance)."""
    has_headers = bool(snap.from_headers and not snap.from_headers_inferred)
    if not has_headers:
        return LayerCoverage(
            layer="L2",
            status=CoverageStatus.NOT_COLLECTED,
            confidence=LayerConfidence.UNKNOWN,
        )
    if header_surface_declares_nothing(snap):
        return LayerCoverage(
            layer="L2",
            status=CoverageStatus.PRESENT,
            confidence=LayerConfidence.REDUCED,
            detail="header-scoped, zero public declarations",
        )
    return LayerCoverage(
        layer="L2",
        status=CoverageStatus.PRESENT,
        confidence=LayerConfidence.HIGH,
        detail="header-scoped",
    )


def side_status_label(cov: LayerCoverage | None) -> str:
    """One side's status word, with a reduced-confidence layer named as such."""
    if cov is None:
        return "not_collected"
    if cov.confidence == LayerConfidence.REDUCED:
        return f"{cov.status.value} (reduced)"
    return cov.status.value
