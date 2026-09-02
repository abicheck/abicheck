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

"""``normalize_header_ast`` — projects already-parsed header-AST facts into
a real :class:`~abicheck.model.semantic_ir.SemanticIR` (ADR-063 Phase 6,
second slice).

**Second slice, not the phase's full scope.** The plan
(``docs/contribute/plans/one-semantic-pipeline.md``, "Phase 6") originally
specified this module as ``normalize(raw: RawCastXmlFacts | RawClangFacts |
...) -> SemanticIR``, receiving *raw*, pre-canonicalization facts from
parsers narrowed to stop doing their own identity resolution. That
narrowing turned out to be unnecessary for identity specifically: Phase 2's
implementation PR chose option (a) (``EntityId`` computed once, at parse
time, and carried as a field on the parsed declaration itself — see
``model/identity.py``'s own module docstring), so both header-AST backends
(``dumper_castxml.py``, ``dumper_clang.py``) already attach a real,
canonically-scoped ``entity_id`` to every ``RecordType``/``EnumType`` they
produce, and a matching ``EntityId`` sidecar to every typedef, before this
module ever runs. What is genuinely still duplicated per backend — and
still this phase's actual "happens once, not once per backend" goal — is
the *payload* canonicalization ``CanonicalEntity`` exists to hold, not a
second identity-resolution pass. This module is therefore a normalizer over
each backend's own already-parsed, already-identified output, not a
raw-fact interpreter; it computes nothing about *identity*, only reads the
``entity_id`` each backend already resolved.

**Scope of this slice.** Records, enums, and typedefs only — the three
entity kinds whose already-canonical spelling is directly available on the
parsed object (``RecordType.qualified_name or RecordType.name``,
``EnumType.qualified_name or EnumType.name``, and a typedef's own resolved
underlying-type string from ``parse_typedefs_qualified()``), each already
built from the identical ``ScopePath``/context walk ``entity_id_for_type``/
``entity_id_for_enum``/``entity_id_for_typedef`` use for identity. Functions
and variables are **not** normalized yet: a function's canonical signature
spelling and a variable's canonical type spelling are exactly the "two
backends, two readings of canonical" problem this phase exists to solve
(return/parameter type rendering, not just identity), and reusing either
backend's own current spelling here would not unify anything — it would
just carry each backend's pre-existing disagreement into ``SemanticIR``
under a name that claims otherwise. Left for a further slice, named here
rather than silently omitted. Constants are omitted for the same reason:
``CanonicalEntity.canonical_spelling`` is specified as a declaration's own
*type* spelling, and a constant's parsed representation
(``parse_constants()``) carries only its value expression, not a captured
type string, to canonicalize.

A typedef whose underlying type neither backend could resolve is stamped
``Fact.failed(...)``, not ``Fact.present("?")`` (Codex review, PR #1001):
both backends spell an unresolved chain with the identical ``"?"``
placeholder, and treating that as a confirmed spelling would permanently
block a hybrid merge's backfill the moment the *other* backend genuinely
resolves it (``extract/semantic_ir_merge.py`` only ever backfills a
non-present base fact) — recording it as a real, present spelling instead
of a failure would have also silently misrepresented the placeholder text
itself as canonical.

**Known, accepted limitation for a manifest (``--dump-manifest``) dump
(Codex review, PR #1001).** ``dumper_manifest.resolve_header_ast_result``
calls this function once, on ``merge_fragments()``'s *already-merged*
``types``/``enums``/``typedefs_qualified``/``typedef_entity_ids`` — and
``tu_merge.merge_fragments`` itself already collapses same-identity
declarations across translation units into one representative entry
before this normalizer ever sees them ("a merged entity carries exactly
one ``source_location``", that function's own docstring). So a real
ODR-duplicate/incomplete-vs-complete-declaration pair spread across two
TUs never reaches ``SemanticIR.occurrences`` as two occurrences, even
though that is exactly the shape ``OccurrenceId``-keying exists to
preserve (see ``model/semantic_ir.py``'s own docstring) — this normalizer
produces no *more* loss than the legacy ``types``/``enums`` fields already
have for a manifest dump (both read from the identical, already-merged
list), but it also does not yet realize the IR's fuller multi-occurrence
potential for that case. Closing this needs per-TU-fragment normalization
*before* ``merge_fragments`` collapses identities, threading a real
TU-context disambiguator through — materially more than this slice's
"project already-parsed output" scope. A single-header (non-manifest) dump
is unaffected: there is only one translation unit, so there is nothing for
``merge_fragments`` to collapse ahead of this function in the first place.

Backend-agnostic by construction: ``dumper_castxml.py`` and
``dumper_clang.py`` already expose the identical
``parse_types()``/``parse_enums()``/``parse_typedefs_qualified()``/
``parse_typedef_entity_ids()`` surface (verified directly, not assumed), so
one function serves both — the same "converge on one shared shape" property
Phase 6's Design section wants, just realized at the already-parsed-object
layer instead of a raw-fact layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..model.entities import EnumType, RecordType
from ..model.fact import Fact
from ..model.identity import EntityId
from ..model.occurrence import OccurrenceId
from ..model.semantic_ir import CanonicalEntity, SemanticIR

__all__ = ["normalize_header_ast"]

#: Both header-AST backends use this literal as their typedef-resolution
#: placeholder when the underlying type couldn't be followed
#: (``dumper_castxml.py``/``dumper_castxml_typedefs.py``/``dumper_clang.py``,
#: verified directly) -- never a real, structurally-fixed type spelling. See
#: its use in :func:`normalize_header_ast` below.
_UNRESOLVED_TYPE_SENTINEL = "?"


def _add_occurrence(
    occurrences: dict[OccurrenceId, CanonicalEntity],
    entity_id: EntityId | None,
    canonical_spelling: Fact[str],
    *,
    producer: str,
) -> None:
    """Record one occurrence, first-observation-wins on a key collision.

    *canonical_spelling* is a caller-supplied :class:`~abicheck.model.fact.
    Fact` rather than a bare string, so a caller can state "this backend
    tried and could not resolve this" as a non-present status instead of
    always claiming ``PRESENT`` (see the typedef branch in
    :func:`normalize_header_ast` — Codex review, PR #1001: an earlier
    revision always wrapped the raw string in ``Fact.present(...)``, which
    made an unresolved castxml typedef permanently block a resolving
    clang backfill during hybrid merge, since ``merge_semantic_ir`` only
    backfills a *non*-present base fact).

    Neither header-AST backend supplies a per-occurrence disambiguator
    today (``model/occurrence.py``'s own docstring: an empty disambiguator
    is the overwhelming common case), so two declarations that genuinely
    share one ``EntityId`` within a single backend's own output — a forward
    declaration alongside its definition, most plausibly — collide on the
    identical ``OccurrenceId`` and cannot be told apart here. Keeping the
    first is a documented limitation of this slice, not a silent
    swallow: :class:`~abicheck.model.semantic_ir.SemanticIR` itself is
    built to hold every occurrence once a real disambiguator exists (see
    that module's own docstring), so closing this gap is a matter of
    threading one through, not a shape change to this function.
    """
    if entity_id is None:
        return
    occ_id = OccurrenceId(entity_id)
    if occ_id in occurrences:
        return
    occurrences[occ_id] = CanonicalEntity(
        canonical_spelling=canonical_spelling,
        producer=producer,
    )


def normalize_header_ast(
    *,
    types: Iterable[RecordType],
    enums: Iterable[EnumType],
    typedefs_qualified: Mapping[str, str],
    typedef_entity_ids: Mapping[str, EntityId],
    producer: str,
) -> SemanticIR:
    """Build a :class:`SemanticIR` from one header-AST backend's already-
    parsed output.

    *types*/*enums* are the backend's ``parse_types()``/``parse_enums()``
    return values; *typedefs_qualified*/*typedef_entity_ids* are its
    ``parse_typedefs_qualified()``/``parse_typedef_entity_ids()`` return
    values (the same qualified-name key set by construction — both are
    built from the same element pass, see each backend's own
    ``parse_typedef_entity_ids`` docstring). *producer* is the backend name
    (``"castxml"``/``"clang"``), stamped onto every
    :class:`~abicheck.model.semantic_ir.CanonicalEntity` this call
    produces, mirroring ``AbiSnapshot.ast_producer``.

    A ``RecordType``/``EnumType`` with no ``entity_id`` (older snapshot
    reload, or a producer that has not populated it) contributes no
    occurrence — this normalizer canonicalizes evidence a backend already
    resolved identity for, it does not resolve identity itself. A typedef
    with no matching sidecar entry (should not happen — the two are built
    from one pass — but tolerated defensively rather than raising, matching
    this function's read-only, best-effort relationship to its inputs) is
    likewise skipped.
    """
    occurrences: dict[OccurrenceId, CanonicalEntity] = {}
    for rt in types:
        _add_occurrence(
            occurrences,
            rt.entity_id,
            Fact.present(rt.qualified_name or rt.name),
            producer=producer,
        )
    for et in enums:
        _add_occurrence(
            occurrences,
            et.entity_id,
            Fact.present(et.qualified_name or et.name),
            producer=producer,
        )
    for qualified_name, entity_id in typedef_entity_ids.items():
        underlying = typedefs_qualified.get(qualified_name)
        if underlying is None:
            continue
        spelling_fact = (
            Fact.failed("underlying type not resolved")
            if underlying == _UNRESOLVED_TYPE_SENTINEL
            else Fact.present(underlying)
        )
        _add_occurrence(occurrences, entity_id, spelling_fact, producer=producer)
    return SemanticIR(occurrences=occurrences)
