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

Neither header backend models a concept definition or a ``requires``
constraint, so a tightened concept reads ``NO_CHANGE``. A castxml or clang
snapshot records whether its public headers declare one
(``AbiSnapshot.cxx_concept_header_scan``); when either side does, the
constraints are an unobserved dimension and the rollup reads ``partial``.
Like ``_template_defaults``, this is a coverage gap, never a verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...model import AbiSnapshot

__all__ = ["CONCEPT_GAP_DIMENSION", "concept_constraint_gap"]

CONCEPT_GAP_DIMENSION = (
    "C++20 concepts and requires-clauses (not modelled by header backends)"
)


def concept_constraint_gap(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[bool, list[str]]:
    """``(gap, notes)``: one note per side whose public headers declare a
    concept or a requires-clause the header backend does not model."""
    notes = [
        f"{CONCEPT_GAP_DIMENSION}: the public headers of the {label} snapshot "
        "declare a concept or a requires-clause, so a change to a template "
        "constraint is not assessed"
        for label, snap in (("old", old), ("new", new))
        if snap is not None and snap.cxx_concept_header_scan
    ]
    return bool(notes), notes
