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

**Scope of the fourth slice.** Constants. Every prior slice's
``canonical_spelling`` is a declaration's own *type* spelling — but a
constant's parsed representation (``parse_constants()``) carries only its
value expression (``"42"``, ``"\"hello\""``, ...), never a captured type
string, so there is no type-spelling gap for this slice to canonicalize the
way the third slice did for functions/variables. Both backends already
attach a real ``EntityId`` to every public constant
(``parse_constant_entity_ids()``, Phase 2), so the identity half of this
slice's work was already done before it landed — what remained was wiring
the existing ``constants``/``constant_entity_ids`` maps into this
normalizer at all. **Deliberately no canonicalization is applied to the
value text itself** — ``canonical_spelling`` is the raw ``parse_constants()``
string, unchanged. This mirrors ``diff_symbols._diff_constants``'s own
``CONSTANT_CHANGED`` detector, which has always compared the two backends'
raw value strings with a plain ``!=`` and never canonicalized either side;
inventing a new value-spelling canonicalizer here without concrete evidence
of a real cross-backend spelling difference (unlike a function/variable's
type spelling, where ``"char const*"`` vs. ``"char const *"`` is a directly
observed, real disagreement) would be exactly the kind of speculative
heuristic this codebase's own bug-class discipline warns against elsewhere
(see ``_type_index_items``'s/``_diff_constants``'s own docstrings on an
identity heuristic falsified twice) — a canonicalizer with no known target
divergence to fix is a heuristic in search of a bug, not a fix for one. A
constant carries no ``cv_qualification``/``template_arguments`` either: it
has no captured type for either fact to describe.

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
(Codex review, PR #1001; unchanged by the third and fourth slices'
additions).** ``dumper_manifest.resolve_header_ast_result`` calls this
function once, on ``merge_fragments()``'s *already-merged*
``functions``/``variables``/``types``/``enums``/``typedefs_qualified``/
``typedef_entity_ids``/``constants``/``constant_entity_ids`` — and
``tu_merge.merge_fragments`` itself already
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
``parse_typedef_entity_ids()``/``parse_constants()``/
``parse_constant_entity_ids()`` surface (verified directly, not assumed), so
one function serves both — the same "converge on one shared shape" property
Phase 6's Design section wants, just realized at the already-parsed-object
layer instead of a raw-fact layer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from ..model.declarations import Function, Variable
from ..model.declarator_qualifiers import (
    _is_declarator_group,
    _split_at_trailing_param_list,
)
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

#: Whole-word ``const``/``volatile`` matcher for
#: :func:`_variable_top_level_cv_qualification`'s own depth-aware scan --
#: mirrors ``model.declarator_qualifiers._CV_WORD_RE`` (duplicated rather
#: than imported, matching that module's own reasoning for not sharing it
#: with its sibling ``signature_normalization.py``: leaf modules on two
#: different sides of the `model`/`extract` boundary, per ADR-063 D10).
#:
#: **Deliberately does NOT match ``restrict``, even though
#: ``CanonicalEntity.cv_qualification``'s own vocabulary
#: (:data:`~abicheck.model.semantic_ir.CV_QUALIFIER_ORDER`) names it
#: alongside ``const``/``volatile`` (Codex review, sixth round, fresh
#: evidence -- reverting a fifth-round addition).** clang's own variable
#: qualType spells a restrict-qualified pointer verbatim (``"int
#: *restrict"`` for ``int * restrict gp``); castxml's ``type_name_uncached``
#: never emits the word at all, by deliberate choice (see that function's
#: own ``CvQualifiedType`` branch: ``restrict`` has zero ABI/mangling effect
#: and is tracked as its own fact for *parameters*, ``Param.is_restrict`` --
#: no equivalent structural fact exists for a *variable* today). A plain
#: text scan for the word therefore reports two different things depending
#: on producer: for clang, a real, present qualifier; for castxml, an
#: absence that isn't *confirmed* -- castxml is structurally blind to it,
#: not evidence that the declaration lacks it. Recognizing it anyway (the
#: fifth round's own fix) made every castxml-produced ``CanonicalEntity``
#: silently claim a confirmed ``()`` for a qualifier its own backend cannot
#: see, which `merge_semantic_ir`'s backfill treats identically to a
#: genuine, deliberate absence: a hybrid dump then downgrades clang's real
#: `("restrict",)` to a mere disagreement against castxml's structurally-
#: unable-to-know-better ``()``, discarding it as the merged/authoritative
#: value rather than backfilling. `Fact[tuple[str, ...]]` cannot express
#: "confirmed for two of three qualifiers, blind to the third" within one
#: fact -- fixing this properly needs a per-variable structural
#: ``is_restrict`` fact populated by both backends the way ``Param.
#: is_restrict`` already is (``resolve_cv_restrict``/
#: ``clang_param_is_restrict``, both directly reusable for a variable's own
#: type id/node), almost certainly with its own reliability-tracking
#: ``AbiSnapshot`` flag mirroring ``clang_restrict_facts_reliable`` -- a
#: model-shape decision for a future slice, not a normalizer-only change,
#: the same reasoning the third slice already gave for leaving a function's
#: ``ref_qualifier``/variadic status out of this IR. Left unset (never
#: reported) here rather than reported unreliably.
_CV_KEYWORD_RE = re.compile(r"\b(?:const|volatile)\b")

#: castxml's own literal wrapper prefix for an ``_Atomic`` type
#: (``extract/headers/castxml/type_resolution.py``'s ``AtomicType`` branch)
#: -- see :func:`_has_unresolved_component`'s own docstring for why this
#: is treated as transparent rather than a real, depth-increasing paren.
_ATOMIC_WRAPPER_PREFIX = "_Atomic("


def _has_unresolved_component(raw_type: str) -> bool:
    """Whether *raw_type* embeds castxml's unresolved-type sentinel
    anywhere, not only as the WHOLE string (Codex review, second round,
    fresh evidence).

    castxml's own type resolver (``extract/headers/castxml/type_resolution.
    py``'s ``type_name_uncached``) composes an unresolved nested type into
    the ENCLOSING spelling rather than only ever returning the bare
    ``"?"`` itself — a pointer/reference/array wrapping an unresolvable
    pointee renders as ``"?*"``/``"?&"``/``"?[]"``, and a cv-qualified one
    as ``"const ?"`` -- so an exact-equality check (correct for the
    typedef branch's own ``underlying`` value, which is always the
    OUTERMOST ``type_name()`` call's result with nothing further wrapped
    around it) misses every one of these composite shapes for a function/
    parameter/variable type.

    **A plain substring test is NOT safe (Codex review, third round, fresh
    evidence): a real, fully-resolved type spelling CAN legally contain a
    literal ``"?"`` character** -- clang emits one verbatim for a
    dependent, unevaluated ternary expression inside a `decltype(...)` (a
    non-type template argument/parameter's own spelling, e.g.
    ``"S<decltype(flag ? A{} : B{})>"``). Distinguishing the two requires
    exactly the discriminator that makes this safe again: every
    ``"?"`` this resolver's own sentinel ever produces sits at NESTING
    DEPTH ZERO in the string -- the recursive wrapping above only ever
    prepends/appends a bare pointer/reference sigil, array brackets, or a
    cv keyword directly beside it, never inside a `(...)`/`<...>`
    grouping -- while a ternary's ``"?"`` is, by C++ grammar, only ever
    reachable inside an expression context, which for a *type* spelling
    means inside a `decltype(...)`'s parens or a template argument list's
    angle brackets (both already open by the time such a ``"?"`` is
    reached). So this function walks *raw_type* tracking depth over
    ``()``/``[]``/``<>``, and reports unresolved only for a ``"?"`` found
    at depth 0 -- never one already inside a bracketed grouping.

    **One wrapper is a deliberate, named exception (Codex review, fourth
    round, fresh evidence): castxml's own ``_Atomic(...)`` composition.**
    ``type_name_uncached``'s ``AtomicType`` branch renders an unresolved
    wrapped type as the literal ``"_Atomic(?)"`` -- genuine sentinel
    output, using a REAL parenthesis pair as part of the resolver's own
    grammar, not an expression context a real, resolved ``"?"`` could ever
    be found inside instead. Depth-tracking alone would treat that
    ``"("`` exactly like a `decltype(...)`'s, hiding the sentinel at depth
    1 and wrongly reporting the composite as resolved. ``"_Atomic("`` is
    therefore recognized as a transparent token -- skipped without
    incrementing depth -- so a sentinel directly inside it is still caught
    at its effective depth 0, the same treatment a bare `"?"` already gets.
    ``_Atomic(...)`` is also real, valid C11 syntax for an otherwise
    fully-resolved type (``"_Atomic(int)"``), which this special-casing
    does not disturb: only a literal ``"?"`` inside it is ever flagged.
    """
    depth = 0
    i = 0
    n = len(raw_type)
    while i < n:
        if raw_type.startswith(_ATOMIC_WRAPPER_PREFIX, i):
            i += len(_ATOMIC_WRAPPER_PREFIX)
            continue
        ch = raw_type[i]
        if ch in "([<":
            depth += 1
        elif ch in ")]>":
            depth = max(0, depth - 1)
        elif ch == _UNRESOLVED_TYPE_SENTINEL and depth == 0:
            return True
        i += 1
    return False


def _function_spelling_fact(fn: Function) -> Fact[str]:
    """``"<return>(<param>, ...)"`` for *fn*, both canonicalized with the
    identical primitives ``entity_id_for_function`` already applies for the
    same cross-backend spelling problem — see this module's own docstring
    ("Scope of the third slice") for why each position uses a different one
    of the two.

    ``Fact.failed(...)`` — not ``Fact.present(...)`` — whenever the RAW
    return type or any raw parameter type embeds castxml's unresolved-type
    sentinel (:func:`_has_unresolved_component`; Codex review): treating an
    unresolved (or partially-unresolved) spelling as confirmed would both
    misrepresent the placeholder as canonical and permanently block a
    hybrid merge's backfill the moment clang resolves the same declaration
    (``merge_semantic_ir`` only ever backfills a *non*-present base fact).
    Checked on the RAW components, before canonicalization -- mirrors the
    typedef branch's own sentinel check and avoids ever asking a
    canonicalizer to interpret a placeholder as a type.
    """
    raw_components = (fn.return_type, *(p.type for p in fn.params))
    if any(_has_unresolved_component(t) for t in raw_components):
        return Fact.failed("return or parameter type not resolved")
    canonical_return = canonicalize_type_name(fn.return_type)
    canonical_params = ", ".join(
        canonicalize_function_signature_param_type(p.type) for p in fn.params
    )
    return Fact.present(f"{canonical_return}({canonical_params})")


def _variable_spelling_fact(var: Variable) -> Fact[str]:
    """``canonicalize_type_name(var.type)``, or ``Fact.failed(...)`` when the
    raw type embeds the unresolved-type sentinel — see
    :func:`_function_spelling_fact`'s own docstring for the identical
    reasoning, applied to a variable's single type instead of a function's
    return/parameter types.
    """
    if _has_unresolved_component(var.type):
        return Fact.failed("type not resolved")
    return Fact.present(canonicalize_type_name(var.type))


def _variable_top_level_cv_qualification(type_str: str) -> tuple[str, ...]:
    """The declaration's OWN cv-qualification -- the one that applies to
    *var* itself, e.g. ``"const"`` for ``const int g`` or a const pointer
    ``int * const g``, but NOT for a mutable pointer to const data
    (``const int *g`` — the pointee is const, the pointer/variable itself
    is not) (Codex review, fresh evidence).

    Deliberately does NOT read ``Variable.is_const``: both header-AST
    backends compute that legacy field via a bare word-boundary search
    over the WHOLE type spelling (``dumper_castxml.py``'s/``dumper_clang.
    py``'s ``parse_variables()``), which is exactly the pointee-vs-value
    conflation above -- correct enough for that field's own existing,
    narrower "would writing through this pointer/reference SIGSEGV"
    question, but the wrong shape for this IR's own top-level
    ``cv_qualification``, which this codebase's other structural CV
    primitives (``model.signature_normalization``'s "outermost vs. pointee
    position" discipline; ``extract/headers/castxml/type_resolution.
    cv_qualifies_pointer_value``) already treat as a load-bearing
    distinction.

    Finds the last top-level (nesting-depth-0) pointer/reference sigil
    (``*``/``&``/``&&``) in *type_str*; the qualification is read only from
    the text AFTER it (the sigil's own qualification -- ``int * const``),
    or from the WHOLE string when there is no top-level sigil at all (a
    plain by-value declaration -- ``const int``/``int const``). Both the
    sigil search and the keyword search are depth-aware (outside any
    ``<...>``/``(...)``/``[...]``), mirroring ``model.declarator_qualifiers.
    _extract_top_level_cv``'s identical discipline, so a `const` inside a
    template argument (``vector<const int> *g`` -- the vector's own element
    type, not this pointer's qualification) is never mistaken for this
    declaration's own.

    **A declarator-grouping paren is transparent to the sigil search, not
    depth-increasing (Codex review, fifth round, fresh evidence).** A const
    function-pointer/pointer-to-array/pointer-to-member-function variable
    wraps its own sigil in a real, syntactic ``(...)`` group -- clang spells
    ``int (* const fp)(int)`` for ``int (* const)(int) fp``'s declaration --
    which an ordinary opaque-paren depth count would treat exactly like a
    parameter list or a `decltype(...)`'s parens, hiding the sigil at depth
    1 and reporting no qualification at all.
    ``model.signature_normalization.canonicalize_function_signature_param_type``
    already solved this identical shape for a parameter's own type; this
    reuses its ``_is_declarator_group`` classifier (same "one open paren
    lookahead, is what follows a bare/qualified sigil rather than a type"
    test) so a genuine parameter-list/template/`decltype` paren still counts
    normally, and only the declarator's own grouping paren is skipped.

    **A pointer-to-member-function's own trailing parameter list ends the
    region this function reads qualifiers from (Codex review, sixth round,
    fresh evidence).** For ``void (C::*pmf)(int) const``, the ``const``
    after the parameter list qualifies the POINTED-TO member function
    itself, not the ``pmf`` pointer variable -- the identical "member
    qualifier vs. pointer's own qualifier" distinction
    ``model.declarator_qualifiers._canonicalize_member_qualifiers`` already
    draws for a parameter's own type. An earlier revision scanned the
    entire text after the sigil (correct for a bare pointer, e.g. ``int *
    const``, which has no trailing parameter list at all) and wrongly
    attributed the member function's own ``const`` to the pointer variable
    too, reporting an identical ``("const",)`` for both a mutable and a
    genuinely const member-function pointer. This reuses
    ``_split_at_trailing_param_list`` (the same primitive
    ``canonicalize_function_signature_param_type`` already uses for the
    identical split) to find the declarator's own trailing parameter list,
    if any, and reads qualifiers only from the text BEFORE it -- the
    pointer's own by-value qualifier region (``void (C::* const)(int)``'s
    `` const`` sits there, correctly attributed) -- never from the text
    after the parameter list closes.
    """
    depth = 0
    last_sigil = -1
    # True for a paren currently open on this stack that groups a
    # declarator's own sigil -- popped, not depth-counted, so a sigil (or a
    # trailing cv-qualifier) inside one is still found at depth 0. Mirrors
    # `signature_normalization.canonicalize_function_signature_param_type`'s
    # identical `transparent_parens` stack.
    transparent_parens: list[bool] = []
    i = 0
    n = len(type_str)
    while i < n:
        ch = type_str[i]
        if ch == "(":
            transparent = _is_declarator_group(type_str, i + 1)
            transparent_parens.append(transparent)
            if not transparent:
                depth += 1
            i += 1
            continue
        if ch == ")":
            was_transparent = transparent_parens.pop() if transparent_parens else False
            if not was_transparent:
                depth = max(0, depth - 1)
            i += 1
            continue
        if ch in "[<":
            depth += 1
        elif ch in "]>":
            depth = max(0, depth - 1)
        elif ch in "*&" and depth == 0:
            last_sigil = i
        i += 1
    raw_suffix = type_str[last_sigil + 1 :] if last_sigil != -1 else type_str
    split = _split_at_trailing_param_list(raw_suffix)
    # A trailing parameter list means *raw_suffix* is a declarator (a
    # callback or pointer-to-member-function): only the text BEFORE it is
    # the pointer's own by-value qualifier -- text after belongs to the
    # pointed-to function itself, never this variable's own qualification
    # (see this function's own docstring, "A pointer-to-member-function's
    # own trailing parameter list..."). No split at all (a bare pointer, or
    # the by-value case with no sigil) reads the whole region, unchanged.
    region = split[0] if split is not None else raw_suffix
    depth = 0
    found: list[str] = []
    i = 0
    n = len(region)
    while i < n:
        ch = region[i]
        if ch in "([<":
            depth += 1
            i += 1
        elif ch in ")]>":
            depth = max(0, depth - 1)
            i += 1
        elif depth == 0 and (m := _CV_KEYWORD_RE.match(region, i)):
            found.append(m.group())
            i = m.end()
        else:
            i += 1
    return canonical_cv_qualification(found)


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
    constants: Mapping[str, str] = {},
    constant_entity_ids: Mapping[str, EntityId] = {},
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
    producer) needs no change. *constants*/*constant_entity_ids* are its
    ``parse_constants()``/``parse_constant_entity_ids()`` return values (the
    same qualified-name key set by construction, mirroring
    *typedefs_qualified*/*typedef_entity_ids*), also optional (default
    ``{}``) for the identical reason. *producer* is the backend name
    (``"castxml"``/``"clang"``), stamped onto every
    :class:`~abicheck.model.semantic_ir.CanonicalEntity` this call
    produces, mirroring ``AbiSnapshot.ast_producer``.

    A ``RecordType``/``EnumType``/``Function``/``Variable`` with no
    ``entity_id`` (older snapshot reload, or a producer that has not
    populated it) contributes no occurrence — this normalizer canonicalizes
    evidence a backend already resolved identity for, it does not resolve
    identity itself. A typedef/constant with no matching sidecar entry
    (should not happen — the two maps of each pair are built from one pass —
    but tolerated defensively rather than raising, matching this function's
    read-only, best-effort relationship to its inputs) is likewise skipped.
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
            if _has_unresolved_component(underlying)
            else Fact.present(underlying)
        )
        _add_occurrence(occurrences, entity_id, spelling_fact, producer=producer)
    for fn in functions:
        # Every function is normalized unconditionally, synthetic-ctor/dtor-
        # keyed ones (`model.synthetic_key`) and compiler-generated ones
        # included (Codex review, third round, fresh evidence: an earlier
        # revision of this slice excluded synthetic-keyed functions here to
        # dodge a real hazard in `dumper_hybrid.merge_snapshots()` -- that
        # hazard is now closed AT THE SOURCE instead: `_merge_functions`'s
        # own ctor/dtor identity rewrite is propagated into the `semantic_ir`
        # merge too, via `_rewrite_semantic_ir_entity_ids`, so this
        # per-backend normalizer no longer needs to guess which functions a
        # LATER hybrid step might rewrite. A single-backend (non-hybrid)
        # dump has no such rewrite step at all, so excluding them there was
        # always unnecessary lost evidence for no safety benefit).
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
                _variable_top_level_cv_qualification(var.type)
            ),
        )
    for qualified_name, entity_id in constant_entity_ids.items():
        value = constants.get(qualified_name)
        if value is None:
            continue
        # No unresolved-sentinel check and no `cv_qualification` -- unlike a
        # typedef/function/variable's type spelling, a constant's value text
        # never comes from `type_name_uncached`'s resolver at all (see this
        # module's own docstring, "Scope of the fourth slice"), so there is
        # no "?" placeholder to guard against and no captured type for
        # `cv_qualification` to describe.
        _add_occurrence(occurrences, entity_id, Fact.present(value), producer=producer)
    return SemanticIR(occurrences=occurrences)
