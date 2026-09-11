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

"""Groundwork for a future function-family checker cutover (ADR-063
Phase 6B) -- see ``compare/typedefs.py``'s and ``compare/constants.py``'s
own docstrings for the two cohorts that *are* closed and the shared
background.

**This module is NOT a closed ``MIGRATED_COHORTS`` entry.**
``scripts/semantic_ir_cutover.py`` does not list ``functions`` -- unlike the
typedef/constant cohorts, :func:`function_identity_index` reads no
``SemanticIR``/adapter content at all today. See "What was investigated and
why it doesn't close yet" below for the full account; the short version is
that an earlier draft built a ``SemanticIRIndex`` and looked up each
function's identity in it, but discarded the result, which a review
correctly identified as indistinguishable from not reading it at all (Codex
review, PR #1224) -- registering that as a closed cohort would have made
the cutover gate pass while giving future work false assurance that this
path was migrated.

**Why functions are riskier than typedefs/constants, and why a full cutover
isn't attempted here regardless.** Both closed cohorts migrated a family
the IR already covers *completely*: one identity plus exactly one payload
fact, with no other consumer of the legacy collection left in the picture.
A function is not that. ``diff_symbols._diff_functions`` compares parameter
signatures, calling-convention-adjacent facts (``ref_qualifier``, variadic
status), member cv-qualification, virtual-method/vtable layout, ctor/dtor
reconciliation, and inline/hidden-friend transitions -- an entire family of
detectors this module does **not** touch, and ADR-063 Phase 6B's own
charter explicitly excludes trying to validate "is the IR correct" and
"does every detector still behave identically" in one unreviewable pass.

**What was investigated and why it doesn't close yet.**
``diff_symbols._diff_functions``'s old/new *matching* -- which function
pairs with which, before any comparison of what changed between them --
runs through :class:`~abicheck.finding_identity.SymbolIdentityIndex`
(``SymbolIdentityIndex.for_functions``), an exact-mangled-name join plus an
ambiguity-checked ``extern "C"`` name-alias fallback. That looked like the
one piece narrow enough to migrate without inventing new normalizer output.
But investigating whether :func:`~abicheck.finding_identity.
resolve_function_identity`'s own mangled/``extern "C"``/normalized-signature
tiers could be informed by a real ``SemanticIR`` occurrence instead of only
the flat ``Function`` object found no genuine second source of evidence to
cut over to. Unlike a typedef's alias map plus a *separately* resolved
``typedef_entity_ids`` sidecar -- two independently-computed representations
of the same fact, which really can disagree -- a function's identity has
exactly one computation site: ``Function.entity_id`` is resolved once, at
parse time, via ``entity_id_for_function`` (every producer does this:
DWARF's ``extract/dwarf_scope.py``, both header-AST backends, and the
ELF-fallback exporter's ``extract/export_symbol_identity.py`` alike), and
``extract/semantic_normalizer.py``'s own third-slice docstring is explicit
that it "computes nothing about identity, only reads the ``entity_id`` each
backend already resolved" when it builds a real ``SemanticIR`` occurrence
for a header-AST-derived function. A real ``SemanticIR``'s function
occurrence key is therefore *the same object*, copied, not a second
resolution -- there is nothing for a matching-time ``SemanticIR`` lookup to
disagree with, confirm, or otherwise meaningfully consume, so any such
lookup is either a no-op (what the earlier draft did) or would have to
*recompute* identity from ``EntityId.extra`` and risk a subtly wrong
reimplementation of logic that already exists, tested, in
``finding_identity.py``, for zero evidentiary gain.

**What did land, and why it's still worth keeping.**
:func:`~abicheck.model.semantic_ir_legacy_adapter.legacy_function_ir` -- a
real ``SemanticIR`` projection of a snapshot's flat function map, shaped
like ``legacy_typedef_ir``/``legacy_constant_ir``, tested directly by its
own unit tests in ``tests/test_function_cutover.py`` -- and this module's
own boundary as the place a future consumer belongs. This mirrors how
:class:`~abicheck.model.semantic_ir_index.SemanticIRIndex` itself was
accepted with no live caller at all ("landed and proven correct in
isolation first," per that class's own docstring): infrastructure is
allowed to land ahead of a consumer, but a ``MIGRATED_COHORTS`` entry is
not, because that entry's whole meaning is "this module's own read path was
verified never to regress to the legacy collection" -- a claim that
requires something to actually be reading the IR-backed path in the first
place.

**What a real cohort 3 would still need.** ``CanonicalEntity`` growing
per-position payload facts a signature-level comparison could genuinely
read -- a separately-addressable return-type spelling, ``ref_qualifier``,
variadic status (``canonical_spelling`` today combines a function's return
type and every parameter into one opaque ``"<return>(<param>, ...)"``
string with no way to recover which position changed, and the third
slice's own normalizer does not yet carry ``ref_qualifier``/variadic status
at all -- see ``extract/semantic_normalizer.py``'s own "Deliberately
excluded from this slice" list). Only once such a fact exists does a
consumer have something real to read through the IR instead of the flat
``Function`` object; inventing that normalizer output speculatively, with
no detector ready to consume it, is exactly what this phase's own non-goal
warns against.
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
    """``diff_symbols._diff_functions``'s entry point for the function-family
    matching index -- not a closed ``MIGRATED_COHORTS`` entry; see the module
    docstring.

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
