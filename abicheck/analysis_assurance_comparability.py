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

"""What a *bounded* (non-fatal) comparability mismatch contributes to the
``AnalysisAssurance`` rollup.

Split out of ``analysis_assurance.py`` (at its ``architecture/debt.yaml``
no-growth baseline) rather than added there, the same way
``analysis_assurance_layout.py`` and
``analysis_assurance_schema_staleness.py`` already own their own axis. This
module is a real leaf: it reads one mapping off an already-computed result
and depends on nothing.

**The gap this closes** (Codex review, PR #1274): ``compute_analysis_
assurance`` special-cased only ``result.assurance == "none"`` -- the
``--diagnostic-comparison`` escape hatch -- and read
``comparability_assurance`` nowhere. A run bounded by a non-fatal
``ComparabilityMismatch`` (``comparability.ComparabilityMismatch.fatal ==
False``) leaves ``assurance`` at ``None``, because nothing was forced
through a refusal, so such a run reported ``status="complete"`` while the
very same report said its ``declaration`` and ``layout`` dimensions were
unverified. Two assurance fields of one document contradicting each other,
and ``assurance.require_complete`` declining to gate a run that is by its
own account not complete.
"""

from __future__ import annotations

from collections.abc import Mapping


def unverified_dimensions(
    comparability_assurance: Mapping[str, str] | None,
) -> list[str]:
    """The sorted dimension names a bounded mismatch left unverified.

    Empty for ``None`` (no mismatch at all) and for an all-``"trusted"``
    mapping -- both mean this axis contributes nothing.
    """
    return sorted(
        dimension
        for dimension, state in (comparability_assurance or {}).items()
        if state == "unverified"
    )


def bounded_comparability_notes(
    comparability_assurance: Mapping[str, str] | None,
) -> list[str]:
    """The rollup notes this axis contributes -- empty when it contributes
    nothing, so the caller can `notes.extend(...)` unconditionally.

    Phrased as reduced assurance on named dimensions rather than as a
    refusal: the comparison ran, produced a verdict, and every other axis
    was genuinely computed. That is also why the caller folds this into
    `partial` rather than `not_comparable` -- collapsing it to the latter
    would discard the axes the run did establish.
    """
    unverified = unverified_dimensions(comparability_assurance)
    if not unverified:
        return []
    return [
        "extraction contexts were not provably identical; conclusions on "
        f"the {', '.join(unverified)} dimension(s) carry reduced assurance"
    ]


def is_comparability_bounded(
    comparability_assurance: Mapping[str, str] | None,
) -> bool:
    """Whether any dimension reads ``"unverified"`` -- the predicate the
    rollup's own ``partial`` branch tests."""
    return bool(unverified_dimensions(comparability_assurance))
