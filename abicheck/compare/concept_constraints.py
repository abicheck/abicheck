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

"""Header-level (L2) C++20 concept and template-constraint changes.

Compares two snapshots' ``cxx_concept_constraints`` tables (recorded by the
clang header backend, ``extract/headers/clang/concept_constraints.py``) with
the same direction rule as the L4 source lane
(``model/concept_constraint.classify_constraint_change``), so a tightened
concept is ``CONCEPT_TIGHTENED`` at either depth.

* A concept present on both sides whose constraint changed is one finding.
* A template present on both sides is compared on its *symbolic* constraint
  first: when every concept-id it names is the same, any difference lies in
  a concept body and that concept's own finding already reports it. When the
  symbolic constraints differ (a requires-clause added or removed, a concept
  swapped), each concept-id is expanded through its side's concept table and
  the expanded sets are classified -- so a concept renamed with an identical
  body is no change, and a requires-clause added to an unconstrained template
  is a tightening.

A side that did not model constraints (``None``: castxml, a C parse, no
public set) yields no finding; the assurance rollup names that gap instead.
A concept or template present on one side only is not reported here: no
existing concept kind states a removal, and a removed uninstantiated
template has no binary symbol to anchor a finding on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..detector_registry import registry
from ..model.change import Change
from ..model.concept_constraint import (
    CONCEPT_KEY_PREFIX,
    REF_ATOM_PREFIX,
    TEMPLATE_KEY_PREFIX,
    classify_constraint_change,
    conjunctive_atoms,
    constraint_digest,
)

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["diff_concept_constraint_tables"]


def _expand(
    atoms: frozenset[str], concepts: dict[str, frozenset[str] | None]
) -> frozenset[str]:
    out: set[str] = set()
    for atom in atoms:
        if atom.startswith(REF_ATOM_PREFIX):
            name, _, args = atom[len(REF_ATOM_PREFIX) :].rpartition("@")
            body = concepts.get(name)
            if body is not None:
                out.update(constraint_digest(a, args) for a in body)
                continue
        out.add(atom)
    return frozenset(out)


def _concept_atoms(table: dict[str, str]) -> dict[str, frozenset[str] | None]:
    return {
        key[len(CONCEPT_KEY_PREFIX) :]: conjunctive_atoms(value)
        for key, value in table.items()
        if key.startswith(CONCEPT_KEY_PREFIX)
    }


def diff_concept_constraint_tables(
    old: dict[str, str] | None, new: dict[str, str] | None
) -> list[Change]:
    """The concept-constraint findings between two recorded tables."""
    if old is None or new is None:
        return []
    old_concepts, new_concepts = _concept_atoms(old), _concept_atoms(new)
    changes: list[Change] = []
    for key in sorted(old.keys() & new.keys()):
        before, after = old[key], new[key]
        if before == after:
            # A template naming the same concept-ids is unchanged here even
            # when a concept body changed: that concept's finding reports it.
            continue
        old_atoms, new_atoms = conjunctive_atoms(before), conjunctive_atoms(after)
        if key.startswith(TEMPLATE_KEY_PREFIX):
            subject = key[len(TEMPLATE_KEY_PREFIX) :]
            if old_atoms is not None and new_atoms is not None:
                old_atoms = _expand(old_atoms, old_concepts)
                new_atoms = _expand(new_atoms, new_concepts)
                if old_atoms == new_atoms:
                    continue
        elif key.startswith(CONCEPT_KEY_PREFIX):
            subject = key[len(CONCEPT_KEY_PREFIX) :]
        else:
            continue
        decided = classify_constraint_change(subject, old_atoms, new_atoms)
        if decided is None:
            continue
        kind, description = decided
        changes.append(
            Change(
                kind=kind,
                symbol=subject,
                description=description,
                old_value=before,
                new_value=after,
            )
        )
    return changes


@registry.detector("concept_constraints")
def _diff_concept_constraints(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """C++20 concept / requires-clause changes the clang header backend saw."""
    return diff_concept_constraint_tables(
        old.cxx_concept_constraints, new.cxx_concept_constraints
    )
