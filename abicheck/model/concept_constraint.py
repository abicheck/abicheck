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

"""A C++20 constraint as a conjunctive set of atoms, and its direction.

Shared by the L4 source lane (``buildsource/concept_diff.py``) and the L2
clang header lane (``compare/concept_constraints.py``) so both decide a
constraint change the same way. A constraint-expression is decomposed along
its top-level conjunctions into atoms; the set is encoded as
``conj:<atom>,<atom>,...`` (sorted). A value without the prefix is an opaque
fingerprint: it proves only *that* the constraint changed.

Direction is set inclusion: the new set strictly containing the old one --
every requirement kept, at least one added -- is a tightening
(``CONCEPT_TIGHTENED``, API_BREAK). Equal sets are no change. Anything else
(relaxed, rewritten, or an opaque side) is ``CONCEPT_CONSTRAINT_CHANGED``
(RISK): a relaxed constraint can still change overload resolution through
subsumption, so it is not reported as safe either.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from .change_catalog.kinds import ChangeKind

__all__ = [
    "CONCEPT_KEY_PREFIX",
    "CONJUNCTIVE_PREFIX",
    "REF_ATOM_PREFIX",
    "TEMPLATE_KEY_PREFIX",
    "classify_constraint_change",
    "constraint_digest",
    "conjunctive_atoms",
    "encode_conjunctive",
]

CONJUNCTIVE_PREFIX = "conj:"
#: ``AbiSnapshot.cxx_concept_constraints`` key of a concept definition.
CONCEPT_KEY_PREFIX = "concept "
#: ``AbiSnapshot.cxx_concept_constraints`` key of a constrained-or-not template.
TEMPLATE_KEY_PREFIX = "template "
#: A concept-id atom of a template's constraint: ``ref:<concept>@<args digest>``.
REF_ATOM_PREFIX = "ref:"


def constraint_digest(*parts: str) -> str:
    """A short, stable digest of *parts* (one atom, or an atom's arguments)."""
    return hashlib.blake2b("\x1f".join(parts).encode(), digest_size=12).hexdigest()


def encode_conjunctive(atoms: Iterable[str]) -> str:
    """``conj:`` plus the sorted, de-duplicated *atoms*."""
    return CONJUNCTIVE_PREFIX + ",".join(sorted(set(atoms)))


def conjunctive_atoms(value: str | None) -> frozenset[str] | None:
    """The atom set a ``conj:`` value encodes; ``None`` for an opaque value."""
    if not value or not value.startswith(CONJUNCTIVE_PREFIX):
        return None
    body = value[len(CONJUNCTIVE_PREFIX) :]
    return frozenset(a for a in body.split(",") if a)


def classify_constraint_change(
    subject: str,
    old_atoms: frozenset[str] | None,
    new_atoms: frozenset[str] | None,
) -> tuple[ChangeKind, str] | None:
    """``(kind, description)`` for a changed constraint of *subject*, or
    ``None`` when the two atom sets are equal. The caller has already
    established that the two encoded values differ."""
    if old_atoms is None or new_atoms is None:
        return (
            ChangeKind.CONCEPT_CONSTRAINT_CHANGED,
            f"Concept constraint changed: {subject} (no structured constraint "
            "on one side; direction not decidable)",
        )
    if old_atoms == new_atoms:
        return None
    if old_atoms < new_atoms:
        return (
            ChangeKind.CONCEPT_TIGHTENED,
            f"Concept constraint tightened: {subject} "
            f"(+{len(new_atoms - old_atoms)} requirement(s), none removed)",
        )
    if new_atoms < old_atoms:
        return (
            ChangeKind.CONCEPT_CONSTRAINT_CHANGED,
            f"Concept constraint relaxed: {subject} "
            f"(-{len(old_atoms - new_atoms)} requirement(s)); may change "
            "overload resolution, no compile failure proven",
        )
    return (
        ChangeKind.CONCEPT_CONSTRAINT_CHANGED,
        f"Concept constraint changed: {subject} (requirements both "
        "added and removed; strictness not decidable)",
    )
