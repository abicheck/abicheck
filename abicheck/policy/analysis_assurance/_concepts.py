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

"""Assurance gap for C++20 concepts and requires-clauses.

castxml does not model a concept definition or a ``requires`` constraint,
so a tightened concept reads ``NO_CHANGE`` there. A castxml or clang
snapshot records whether its public headers declare one
(``AbiSnapshot.cxx_concept_header_scan``); when a side does and did not model
them (``AbiSnapshot.cxx_concept_constraints is None`` -- castxml, a multi-TU
merge, no declared public set), the constraints are an unobserved dimension
and the rollup reads ``partial``. A clang side that modelled them
(``compare/concept_constraints.py`` compares the tables) names no gap.
Like ``_template_defaults``, this is a coverage gap, never a verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...model import AbiSnapshot

from ._unobserved_finding import Side, UnobservedFinding

__all__ = [
    "CONCEPT_GAP_DIMENSION",
    "concept_constraint_findings",
]

CONCEPT_GAP_DIMENSION = (
    "C++20 concepts and requires-clauses (not modelled by header backends)"
)


def _sides(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[tuple[Side, AbiSnapshot | None], ...]:
    return (("old", old), ("new", new))


def concept_constraint_findings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[UnobservedFinding]:
    """One finding per side whose public headers declare a concept or a
    requires-clause its header backend did not model, or whose headers
    could not be read to rule one out."""
    return [
        UnobservedFinding(
            CONCEPT_GAP_DIMENSION,
            label,
            f"{CONCEPT_GAP_DIMENSION}: the public headers of the {label} snapshot "
            "declare a concept or a requires-clause (or could not be read to rule "
            "one out), so a change to a template "
            "constraint is not assessed",
        )
        for label, snap in _sides(old, new)
        if snap is not None
        and snap.cxx_concept_header_scan
        and getattr(snap, "cxx_concept_constraints", None) is None
    ]
