# Copyright 2026 Nikolay Petrov
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

"""Schema v54's resolved type identities, as public-surface evidence.

A type slot's spelling is the bare source text, so ``Cache *`` cannot say
whether it means ``ns1::Cache`` or ``ns2::Cache``. A header backend that
records what the compiler resolved each slot to (``Param.type_identities_fact``,
``Function.return_type_identities_fact``, ``Variable.type_identities_fact``,
``TypeField.type_identities_fact``; castxml today) has already answered that.
This module turns those answers into entry points and edges for
:func:`~abicheck.policy.public_surface_closure._walk_exact_type_closure` --
the ambiguity-vetoing walk behind ``PublicSurface.exact_type_identities``.
Kept separate from that module, which is at its size limit, and owning
nothing but this one question.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..model import AbiSnapshot, Fact, FactStatus, RecordType
from ..model.surface_facts import in_public_surface


def captured_identity_seeds(snap: AbiSnapshot) -> set[str]:
    """The exact record/enum identities public signatures were *resolved* to.

    A signature's spelling is the bare source text, so ``Cache *`` names
    neither ``ns1::Cache`` nor ``ns2::Cache`` and :func:`_walk_exact_type_closure`
    rightly stops at it. A header backend that recorded what the compiler
    resolved the slot to (``Param.type_identities_fact``/``Function.
    return_type_identities_fact``/``Variable.type_identities_fact``, schema
    v54) has
    already answered that question, so its identities are exact entry
    points by construction -- extraction evidence, not a guess from header
    origin or spelling. Only a ``PRESENT`` fact contributes (see
    :func:`present_identities`); anything else leaves the walk exactly where
    it was.

    Fed to the exact walk only: the ordinary closure's ambiguity-tolerant
    fan-out already reaches every same-leaf candidate, so it has nothing to
    learn from a narrower answer.

    A compiler-generated member is skipped. castxml synthesizes every
    implicit constructor/assignment/destructor of every record it parses --
    private-header ones included -- and those reach ``in_public_surface``
    only through its keep-it-when-unknown export fallback (no mangled name to
    look up). Their slots name their own class, so they promise nothing a
    user wrote; seeding the *exact* walk from them would turn that
    anti-hiding fallback into confirmation, which is precisely what the
    exact walk exists to refuse. Verified against a real castxml dump where
    a private-header ``ns2::Cache`` became "exact" through its implicit copy
    constructor alone.
    """
    seeds: set[str] = set()
    for fn in snap.declarations.functions:
        if not in_public_surface(fn) or fn.is_compiler_generated:
            continue
        seeds.update(present_identities(fn.return_type_identities_fact))
        for p in fn.params:
            seeds.update(present_identities(p.type_identities_fact))
    for var in snap.declarations.variables:
        if in_public_surface(var):
            seeds.update(present_identities(var.type_identities_fact))
    return seeds


def field_identities(rec: RecordType) -> Iterator[str]:
    """Each field's captured identity: where a field's bare spelling would
    fork the exact walk, its resolved identity continues the chain. A field
    without a ``PRESENT`` fact yields nothing, leaving the walk as before."""
    for fld in rec.fields:
        yield from present_identities(fld.type_identities_fact)


def present_identities(fact: Fact[tuple[str, ...]] | None) -> tuple[str, ...]:
    """The identities a slot fact *established*, else nothing.

    ``NOT_COLLECTED``/``UNSUPPORTED`` (clang JSON, DWARF, a pre-v54 snapshot)
    and ``PRESENT(())`` (a slot naming no record) are different facts, and
    the distinction is kept in the model for any consumer that needs it --
    but for seeding, both mean "no entry point", so neither contributes.
    Status is read first, never the bare value (the ``fact-detector-misuse``
    discipline).
    """
    if fact is None or fact.status is not FactStatus.PRESENT:
        return ()
    return fact.value or ()
