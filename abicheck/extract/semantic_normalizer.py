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
second and third slices).

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

**Scope of the second slice.** Records, enums, and typedefs — the three
entity kinds whose already-canonical spelling is directly available on the
parsed object (``RecordType.qualified_name or RecordType.name``,
``EnumType.qualified_name or EnumType.name``, and a typedef's own resolved
underlying-type string from ``parse_typedefs_qualified()``), each already
built from the identical ``ScopePath``/context walk ``entity_id_for_type``/
``entity_id_for_enum``/``entity_id_for_typedef`` use for identity.

**Scope of the third slice.** Functions and variables. Unlike records/enums/
typedefs, a function's return/parameter types and a variable's own type are
NOT already canonical the way a resolved qualified name is — castxml and
clang genuinely spell an identical type differently (``"char const*"`` vs.
``"char const *"``, an elaborated ``"struct Foo"`` vs. bare ``"Foo"``), which
is exactly the "two backends, two readings of canonical" gap the second
slice's own note named. This slice does not invent a new canonicalization
for that gap — it reuses the two primitives ``entity_id_for_function``/
``resolve_function_identity`` already apply for the identical cross-backend
problem: ``model.signature_normalization.
canonicalize_function_signature_param_type`` for each parameter type (which
also drops a top-level by-value cv-qualifier, matching the C++ standard's own
mangling rule that such a qualifier does not distinguish overloads) and
``name_classification.canonicalize_type_name`` for the return type (matching
``entity_id_for_function``'s own choice of canonicalizer for that position —
a by-value cv-qualifier on a return type is NOT dropped, since it can be a
genuine, standard-permitted discriminator for a function *template*). A
function's ``canonical_spelling`` is therefore ``"<return>(<param>, ...)"``
built from those two canonicalizations, never the raw ``return_type``/
``Param.type`` text either backend happened to print. ``is_const``/
``is_volatile`` (member-function cv-qualification) is carried separately, via
``CanonicalEntity.cv_qualification`` — the field this IR already reserves for
exactly this purpose — rather than folded into the spelling text.
*Deliberately excluded from this slice, and named here rather than silently
dropped*: a function's ``ref_qualifier`` (``"&"``/``"&&"``) and variadic
(``...``) status are not textual spelling problems at all — both backends
compute them as the same structural booleans/enums from the identical
declaration, so there is nothing for a canonicalizer to reconcile — and
``CanonicalEntity`` has no dedicated slot for either today; adding one is a
model-shape decision for a future slice, not a normalizer-only change. A
variable's ``canonical_spelling`` is ``canonicalize_type_name(variable.type)``
(the same canonicalizer the hybrid merge already applies when matching
variables across backends) with ``is_const`` carried via
``cv_qualification``, mirroring the function treatment.

Constants are still omitted, for a different, independent reason:
``CanonicalEntity.canonical_spelling`` is specified as a declaration's own
*type* spelling, and a constant's parsed representation
(``parse_constants()``) carries only its value expression, not a captured
type string, to canonicalize. Left for a further slice, named here rather
than silently dropped.

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
(Codex review, PR #1001; unchanged by the third slice's functions/variables
addition).** ``dumper_manifest.resolve_header_ast_result`` calls this
function once, on ``merge_fragments()``'s *already-merged*
``functions``/``variables``/``types``/``enums``/``typedefs_qualified``/
``typedef_entity_ids`` — and ``tu_merge.merge_fragments`` itself already
collapses same-identity declarations across translation units into one
representative entry before this normalizer ever sees them ("a merged
entity carries exactly one ``source_location``", that function's own
docstring). So a real ODR-duplicate/incomplete-vs-complete-declaration
pair spread across two TUs never reaches ``SemanticIR.occurrences`` as two
occurrences, even though that is exactly the shape ``OccurrenceId``-keying
exists to preserve (see ``model/semantic_ir.py``'s own docstring) — this
normalizer produces no *more* loss than the legacy
``functions``/``variables``/``types``/``enums`` fields already have for a
manifest dump (all read from the identical, already-merged lists), but it
also does not yet realize the IR's fuller multi-occurrence potential for
that case. Closing this needs per-TU-fragment normalization *before*
``merge_fragments`` collapses identities, threading a real TU-context
disambiguator through — materially more than either slice's "project
already-parsed output" scope. A single-header (non-manifest) dump is
unaffected: there is only one translation unit, so there is nothing for
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

from ..model.declarations import Function, Variable
from ..model.entities import EnumType, RecordType
from ..model.fact import Fact
from ..model.identity import EntityId
from ..model.occurrence import OccurrenceId
from ..model.semantic_ir import CanonicalEntity, SemanticIR, canonical_cv_qualification
from ..model.signature_normalization import canonicalize_function_signature_param_type
from ..name_classification import canonicalize_type_name

__all__ = ["normalize_header_ast"]

#: Both header-AST backends use this literal as their type-resolution
#: placeholder when a type couldn't be followed -- a typedef's underlying
#: type, a function's return/parameter type, or a variable's own type
#: (``dumper_castxml.py``/``dumper_castxml_typedefs.py``/``dumper_clang.py``,
#: verified directly) -- never a real, structurally-fixed type spelling. See
#: its use in :func:`normalize_header_ast`, :func:`_function_spelling_fact`,
#: and :func:`_variable_spelling_fact` below.
_UNRESOLVED_TYPE_SENTINEL = "?"


def _function_spelling_fact(fn: Function) -> Fact[str]:
    """``"<return>(<param>, ...)"`` for *fn*, both canonicalized with the
    identical primitives ``entity_id_for_function`` already applies for the
    same cross-backend spelling problem — see this module's own docstring
    ("Scope of the third slice") for why each position uses a different one
    of the two.

    ``Fact.failed(...)`` — not ``Fact.present(...)`` — whenever the RAW
    return type or any raw parameter type is exactly the
    ``_UNRESOLVED_TYPE_SENTINEL`` placeholder (Codex review): castxml emits
    the identical ``"?"`` sentinel for a function/parameter type it could
    not resolve the same way it does for a typedef's underlying type (see
    the typedef branch in :func:`normalize_header_ast`), and treating that
    as a confirmed spelling would both misrepresent the placeholder as
    canonical and permanently block a hybrid merge's backfill the moment
    clang resolves the same declaration (``merge_semantic_ir`` only ever
    backfills a *non*-present base fact). Checked on the RAW components,
    before canonicalization -- canonicalizing ``"?"`` first would not
    change it (no known type spells literally ``"?"``), but checking raw
    input mirrors the typedef branch's own exact-sentinel check and avoids
    ever asking a canonicalizer to interpret a placeholder as a type.
    """
    raw_components = (fn.return_type, *(p.type for p in fn.params))
    if any(t == _UNRESOLVED_TYPE_SENTINEL for t in raw_components):
        return Fact.failed("return or parameter type not resolved")
    canonical_return = canonicalize_type_name(fn.return_type)
    canonical_params = ", ".join(
        canonicalize_function_signature_param_type(p.type) for p in fn.params
    )
    return Fact.present(f"{canonical_return}({canonical_params})")


def _variable_spelling_fact(var: Variable) -> Fact[str]:
    """``canonicalize_type_name(var.type)``, or ``Fact.failed(...)`` when the
    raw type is exactly the unresolved-type sentinel — see
    :func:`_function_spelling_fact`'s own docstring for the identical
    reasoning, applied to a variable's single type instead of a function's
    return/parameter types.
    """
    if var.type == _UNRESOLVED_TYPE_SENTINEL:
        return Fact.failed("type not resolved")
    return Fact.present(canonicalize_type_name(var.type))


def _add_occurrence(
    occurrences: dict[OccurrenceId, CanonicalEntity],
    entity_id: EntityId | None,
    canonical_spelling: Fact[str],
    *,
    producer: str,
    cv_qualification: Fact[tuple[str, ...]] | None = None,
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
        **(
            {"cv_qualification": cv_qualification}
            if cv_qualification is not None
            else {}
        ),
    )


def normalize_header_ast(
    *,
    types: Iterable[RecordType],
    enums: Iterable[EnumType],
    typedefs_qualified: Mapping[str, str],
    typedef_entity_ids: Mapping[str, EntityId],
    producer: str,
    functions: Iterable[Function] = (),
    variables: Iterable[Variable] = (),
) -> SemanticIR:
    """Build a :class:`SemanticIR` from one header-AST backend's already-
    parsed output.

    *types*/*enums* are the backend's ``parse_types()``/``parse_enums()``
    return values; *typedefs_qualified*/*typedef_entity_ids* are its
    ``parse_typedefs_qualified()``/``parse_typedef_entity_ids()`` return
    values (the same qualified-name key set by construction — both are
    built from the same element pass, see each backend's own
    ``parse_typedef_entity_ids`` docstring). *functions*/*variables* are its
    ``parse_functions()``/``parse_variables()`` return values, both optional
    (default ``()``) so a caller that has not migrated to the third slice's
    scope yet (or a backend that produces neither, e.g. a future non-header
    producer) needs no change. *producer* is the backend name
    (``"castxml"``/``"clang"``), stamped onto every
    :class:`~abicheck.model.semantic_ir.CanonicalEntity` this call
    produces, mirroring ``AbiSnapshot.ast_producer``.

    A ``RecordType``/``EnumType``/``Function``/``Variable`` with no
    ``entity_id`` (older snapshot reload, or a producer that has not
    populated it) contributes no occurrence — this normalizer canonicalizes
    evidence a backend already resolved identity for, it does not resolve
    identity itself. A typedef with no matching sidecar entry (should not
    happen — the two are built from one pass — but tolerated defensively
    rather than raising, matching this function's read-only, best-effort
    relationship to its inputs) is likewise skipped.
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
    for fn in functions:
        if fn.is_compiler_generated:
            # A compiler-synthesized implicit special member (default/copy/
            # move constructor, copy/move assignment, destructor) was never
            # written in the header a user reads -- clang's own AST walk
            # skips such a node entirely before it ever becomes a `Function`
            # at all (`Function.is_compiler_generated`'s own docstring), so
            # normalizing castxml's side (which DOES emit one, `artificial=
            # "1"`) would add a phantom occurrence with no clang counterpart
            # and no real declared spelling to canonicalize -- the identical
            # "leaking into the reachable surface as if genuine public API"
            # bug this same field exists to guard against elsewhere (see
            # `buildsource.source_extractors.base.entity_from_function`'s own
            # `api_relevant` computation, and AGENTS.md's "PR C" entry).
            continue
        _add_occurrence(
            occurrences,
            fn.entity_id,
            _function_spelling_fact(fn),
            producer=producer,
            cv_qualification=Fact.present(
                canonical_cv_qualification(
                    (
                        *(("const",) if fn.is_const else ()),
                        *(("volatile",) if fn.is_volatile else ()),
                    )
                )
            ),
        )
    for var in variables:
        _add_occurrence(
            occurrences,
            var.entity_id,
            _variable_spelling_fact(var),
            producer=producer,
            cv_qualification=Fact.present(
                canonical_cv_qualification(("const",) if var.is_const else ())
            ),
        )
    return SemanticIR(occurrences=occurrences)
