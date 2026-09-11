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

**What this buys, given that.** Three things, none of which is "the
matching outcome changes for a compliant producer" (it provably does not,
for the reasons above): (1) it satisfies the architectural invariant that a
migrated module never reads the legacy flat collection directly, so a
future change to how function identity is represented has to go through
:mod:`abicheck.model.semantic_ir_legacy_adapter` instead of being bolted
onto an ad hoc flat-field read here; (2) it exercises
:class:`~abicheck.model.semantic_ir_index.SemanticIRIndex` against a third
entity kind whose identity lives in ``EntityId.extra`` rather than in a
payload ``Fact`` (typedefs/constants both keyed their comparison on a
rendered *display name* string; functions key on a raw ``EntityId``
lookup), proving the shared abstraction generalizes past the "one payload
fact" shape; and (3) it is the scaffold a future, better-scoped slice can
build on once ``CanonicalEntity`` grows the per-position facts (a
separately-addressable return-type spelling, ``ref_qualifier``, variadic
status -- see ``extract/semantic_normalizer.py``'s own "Deliberately
excluded from this slice" list) that would let a signature-level comparison
move here safely. Migrating the richer per-parameter/return-type/cv/
virtual-method/ctor-dtor/hidden-friend detectors is explicitly **not**
attempted in this PR: ``CanonicalEntity.canonical_spelling`` combines a
function's return type and every parameter into one opaque
``"<return>(<param>, ...)"`` string with no way to recover which position
changed, and the third slice's own normalizer does not yet carry
``ref_qualifier``/variadic status at all -- rebuilding those detectors on
top of that payload today would either lose per-position detail the
existing findings carry or require inventing new normalizer output this PR
was told not to invent speculatively.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..finding_identity import (
    FindingIdentity,
    SymbolIdentityIndex,
    resolve_function_identity,
)
from ..model.identity import EntityKind
from ..model.semantic_ir_index import SemanticIRIndex
from ..model.semantic_ir_legacy_adapter import (
    legacy_function_ir,
    semantic_ir_covers_kind,
)

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function

__all__ = ["function_identity_index"]


def function_identity_index(
    functions: Mapping[str, Function], snapshot: AbiSnapshot
) -> SymbolIdentityIndex[Function]:
    """Cohort 3's replacement for ``SymbolIdentityIndex.for_functions``.

    *functions* is the comparison's own already ELF/API-surface-selected map
    (``diff_symbols._public_functions``'s result) for *snapshot* -- the same
    input ``SymbolIdentityIndex.for_functions`` takes today. *snapshot* is
    used only to decide which occurrence set to read through
    (:func:`~abicheck.model.semantic_ir_legacy_adapter.semantic_ir_covers_kind`
    gates on a real, ``FUNCTION``-covering ``SemanticIR`` exactly the way
    ``compare.typedefs``/``compare.constants``'s own per-side selectors do),
    never to re-derive *functions* itself.

    The resolved :class:`~abicheck.finding_identity.FindingIdentity` for
    each function is unchanged --
    :func:`~abicheck.finding_identity.resolve_function_identity` still reads
    the flat ``Function`` object directly, for the reasons this module's own
    docstring documents at length (there is no second identity
    representation for a function to prefer instead). What is real about
    this cutover is the occurrence set the lookup runs against: a real
    ``SemanticIR`` when *snapshot* carries one covering ``FUNCTION``
    entities, or :func:`~abicheck.model.semantic_ir_legacy_adapter.
    legacy_function_ir`'s projection of *functions* itself otherwise --
    never a direct read of ``AbiSnapshot.functions``/``.function_map``.
    """
    if snapshot.semantic_ir is not None and semantic_ir_covers_kind(
        snapshot.semantic_ir, EntityKind.FUNCTION
    ):
        ir_index = SemanticIRIndex(snapshot.semantic_ir)
    else:
        ir_index = SemanticIRIndex(legacy_function_ir(functions))

    def _resolve(func: Function) -> FindingIdentity:
        # The presence check itself is this cutover's whole contribution
        # (see module docstring): a real ``SemanticIR`` covering FUNCTION
        # entities in general can still legitimately miss one particular
        # function's own occurrence (e.g. an ELF-exported function an
        # ELF-fallback exporter, not a header-AST backend, identified --
        # mirrors the same v38-v41-style gap
        # ``compare.typedefs``/``compare.constants`` already accept via
        # ``semantic_ir_covers_kind``'s own per-kind, not per-entity, gate).
        # Absent or present, the identity computation itself is unchanged --
        # see this module's own docstring for why recomputing it from
        # ``EntityId.extra`` would add risk, not evidence.
        if func.entity_id is not None:
            ir_index.entity(func.entity_id)
        return resolve_function_identity(func)

    return SymbolIdentityIndex(functions, _resolve)
