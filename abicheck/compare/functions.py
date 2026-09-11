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

"""The function detector family's cohort-3 read path (ADR-063 Phase 6B) --
see ``compare/typedefs.py``'s and ``compare/constants.py``'s own docstrings
for cohorts 1/2 and the shared background.

**Why functions are riskier, and why this cohort's scope is much narrower
than typedefs'/constants'.** Both prior cohorts migrated a family the IR
already covers *completely*: one identity plus exactly one payload fact,
with no other consumer of the legacy collection left in the picture. A
function is not that. ``diff_symbols._diff_functions`` compares parameter
signatures, calling-convention-adjacent facts (``ref_qualifier``,
variadic status), member cv-qualification, virtual-method/vtable layout,
ctor/dtor reconciliation, and inline/hidden-friend transitions -- an
entire family of detectors this PR does **not** touch. ADR-063 Phase 6B's
own charter explicitly excludes trying to validate "is the IR correct" and
"does every detector still behave identically" in one unreviewable pass, so
this module deliberately migrates a much smaller slice than the family's
full comparison surface, and documents the rest as future work rather than
silently declaring the whole family "done".

**What this slice actually is, and why.** ``diff_symbols._diff_functions``'s
old/new *matching* -- which function pairs with which, before any
comparison of what changed between them -- runs through
:class:`~abicheck.finding_identity.SymbolIdentityIndex`
(``SymbolIdentityIndex.for_functions``), an exact-mangled-name join plus an
ambiguity-checked ``extern "C"`` name-alias fallback. That is the piece this
module migrates: :func:`function_identity_index` is a behavior-preserving
replacement for ``SymbolIdentityIndex.for_functions`` that reads only
through :class:`~abicheck.model.semantic_ir_index.SemanticIRIndex` and
:mod:`abicheck.model.semantic_ir_legacy_adapter`, never
``AbiSnapshot.functions``/``.function_map`` directly (the invariant
``scripts/semantic_ir_cutover.py`` enforces for every migrated cohort).

**Why the resolved identity itself is deliberately left unchanged.**
Investigating whether ``resolve_function_identity``'s own mangled/
``extern "C"``/normalized-signature tiers could be *recomputed* from a real
``SemanticIR`` occurrence instead of the flat ``Function`` object found no
genuine second source of evidence to cut over to. Unlike a typedef's alias
map plus a *separately* resolved ``typedef_entity_ids`` sidecar --  two
independently-computed representations of the same fact, which really can
disagree -- a function's identity has exactly one computation site:
``Function.entity_id`` is resolved once, at parse time, via
``entity_id_for_function`` (every producer does this: DWARF's
``extract/dwarf_scope.py``, both header-AST backends, and the ELF-fallback
exporter's ``extract/export_symbol_identity.py`` alike), and
``extract/semantic_normalizer.py``'s own third-slice docstring is explicit
that it "computes nothing about identity, only reads the ``entity_id`` each
backend already resolved" when it builds a real ``SemanticIR`` occurrence
for a header-AST-derived function. A real ``SemanticIR``'s function
occurrence key is therefore *the same object*, copied, not a second
resolution -- recomputing ``resolve_function_identity``'s tiers from
``EntityId.extra`` instead of the flat ``Function`` fields it already reads
would not add evidence, only risk (a subtly wrong reimplementation of
mangled/``extern "C"``/normalized-signature tier logic that already exists,
tested, in ``finding_identity.py``). So :func:`function_identity_index`
still calls :func:`~abicheck.finding_identity.resolve_function_identity`
for the actual identity computation, on both the real-``SemanticIR`` and
the legacy-adapter-projected path alike -- what changes is only *which
occurrence set* the caller-supplied function map is checked against on the
way there, not how a function's own identity tiers are derived.

**What this buys, given that.** Two things, neither of which is "the
matching outcome changes for a compliant producer" (it provably does not,
for the reasons above): (1) it satisfies the architectural invariant that a
migrated module never reads the legacy flat collection directly, so a
future change to how function identity is represented has to go through
:mod:`abicheck.model.semantic_ir_legacy_adapter` instead of being bolted
onto an ad hoc flat-field read here; and (2) it is the scaffold a future,
better-scoped slice can build on once ``CanonicalEntity`` grows the
per-position facts (a separately-addressable return-type spelling,
``ref_qualifier``, variadic status -- see ``extract/semantic_normalizer.py``'s
own "Deliberately excluded from this slice" list) that would let a
signature-level comparison move here safely. Migrating the richer
per-parameter/return-type/cv/virtual-method/ctor-dtor/hidden-friend
detectors is explicitly **not** attempted in this PR: ``CanonicalEntity.
canonical_spelling`` combines a function's return type and every parameter
into one opaque ``"<return>(<param>, ...)"`` string with no way to recover
which position changed, and the third slice's own normalizer does not yet
carry ``ref_qualifier``/variadic status at all -- rebuilding those detectors
on top of that payload today would either lose per-position detail the
existing findings carry or require inventing new normalizer output this PR
was told not to invent speculatively.

**Deliberately not a per-function ``SemanticIRIndex`` lookup.** An earlier
version of this module called ``ir_index.entity(func.entity_id)`` per
function and discarded the result, on the theory that "the presence check
is the contribution". It is not: since :func:`resolve_function_identity`
reads only the flat ``Function`` object (see above -- there is no second
identity computation for a function to prefer), a lookup whose result
influences nothing is dead code, not a migration -- indistinguishable, to
every test and to a mutation run alike, from deleting it. This module's
actual, honest contribution is exactly the two points above: an
architectural boundary and a scaffold, not a runtime behavioral dependency
on IR content. :func:`function_identity_index` therefore only *selects*
which occurrence set the cohort is nominally reading through
(``semantic_ir_covers_kind`` gating real IR vs.
:func:`~abicheck.model.semantic_ir_legacy_adapter.legacy_function_ir`'s
projection), the same per-side selection ``compare.typedefs``/
``compare.constants`` make, without threading a no-op lookup through every
resolved identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..finding_identity import SymbolIdentityIndex, resolve_function_identity

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function

__all__ = ["function_identity_index"]


def function_identity_index(
    functions: Mapping[str, Function], snapshot: AbiSnapshot
) -> SymbolIdentityIndex[Function]:
    """Cohort 3's entry point for the function-family matching index.

    *functions* is the comparison's own already ELF/API-surface-selected map
    (``diff_symbols._public_functions``'s result) for *snapshot* -- the same
    input ``SymbolIdentityIndex.for_functions`` takes today.
    :func:`~abicheck.finding_identity.resolve_function_identity` still reads
    the flat ``Function`` object directly to compute each identity, for the
    reasons this module's own docstring documents at length: a function's
    ``entity_id`` is resolved exactly once, by the producer, and copied
    -- not recomputed -- into ``SemanticIR``, so there is no second,
    independently-derived identity representation here for a
    ``SemanticIR``-backed lookup to prefer. *snapshot* is accepted (rather
    than this function taking only *functions*) to keep the same call shape
    ``compare.typedefs``/``compare.constants``'s own per-side selectors use,
    and so a future slice that gives ``CanonicalEntity`` real per-function
    payload facts (see module docstring) can extend this signature's
    existing caller instead of every call site needing to change again.

    This function is currently a thin, intentionally inert wrapper around
    ``SymbolIdentityIndex(functions, resolve_function_identity)`` --
    identical to ``SymbolIdentityIndex.for_functions(functions)`` in every
    observable way. See the module docstring's "Deliberately not a
    per-function ``SemanticIRIndex`` lookup" section for why: this cohort's
    real, tested infrastructure
    (:func:`~abicheck.model.semantic_ir_legacy_adapter.legacy_function_ir`,
    :func:`~abicheck.model.semantic_ir_legacy_adapter.semantic_ir_covers_kind`)
    lives in the adapter module, exercised directly by its own tests, ready
    for a future slice to wire in -- landed and proven correct in isolation
    first, the same pattern ``SemanticIRIndex`` itself was landed with no
    live caller (see that class's own module docstring). Wiring it into
    *this* function today, with no consequence to gate on, would be
    indistinguishable from dead code to both a reader and a mutation run.
    """
    return SymbolIdentityIndex(functions, resolve_function_identity)
