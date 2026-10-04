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

"""A variable's SemanticIR payload -- its canonical type spelling and
top-level cv_qualification -- computed from a parsed Variable.

The one formula both extract.semantic_normalizer.normalize_header_ast and
the variable cohort's legacy projection (compare/variables.py, via
semantic_ir_legacy_adapter.legacy_variable_occurrences) apply, so a side
read through a real IR and a side read through the adapter cannot disagree
about the same declaration's facts. Lives in model so both layers can
reach it (ADR-061: compare may import only model).
"""

from __future__ import annotations

import re

from ..name_classification import canonicalize_type_name
from .castxml_spelling_artifacts import (
    has_unresolved_component,
    is_castxml_opaque_function_type,
)
from .declarations import Variable
from .declarator_qualifiers import _is_declarator_group, _split_at_trailing_param_list
from .fact import Fact
from .semantic_ir import CanonicalEntity, canonical_cv_qualification

__all__ = ["DWARF_PRODUCER", "variable_canonical_entity"]

#: The producer string DWARF-derived IR carries.
DWARF_PRODUCER = "dwarf"

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


def _variable_spelling_fact(var: Variable, producer: str) -> Fact[str]:
    """``canonicalize_type_name(var.type)``, or a non-present ``Fact``
    when the raw type embeds either castxml artifact -- see
    :func:`_function_spelling_fact`'s own docstring for the identical
    reasoning (unresolved-type sentinel vs. opaque ``FunctionType`` tag),
    applied to a variable's single type instead of a function's
    return/parameter types.
    """
    if has_unresolved_component(var.type):
        return Fact.failed("type not resolved")
    if is_castxml_opaque_function_type(var.type, producer):
        return Fact.unsupported(
            "castxml's opaque FunctionType tag is not a comparable spelling"
        )
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

    **A ``"<"``/``">"`` used as a real comparison operator inside a
    parenthesized non-type template argument does not throw off the sigil
    search either (Codex review, twelfth round, fresh evidence) -- the
    identical bracket-KIND-aware discipline :func:`~abicheck.extract.
    semantic_normalizer_artifacts.has_unresolved_component` already applies,
    reused here for the same reason.** For clang's own spelling of
    ``S<(N < 0)> * const gp`` (``"S<(N < 0)> *const"``), a flat depth
    counter treats the comparison ``<`` as another template opener, so
    after the real ``)``/``>`` closers the running depth never returns to
    zero -- the sigil search then never finds the real top-level ``*`` at
    all (everything after the false-elevated depth looks "still nested"),
    silently reporting no qualification for a genuinely const pointer. A
    ``"<"`` only legitimately opens when what follows is NOT immediately
    inside an unmatched ``(``/``[`` (the same "is this really a template
    bracket or a real operator" question ``>``'s own popping rule already
    answers, applied symmetrically to ``<``'s own PUSH): a bracket kind
    stack records each opaque ``(``/``[``/``<`` as it opens, and a ``">"``
    only pops when the innermost still-open entry is itself a ``"<"`` --
    a real ``<``/``>`` comparison/shift pair sitting inside an already-open
    ``(``/``[`` neither pushes nor pops that stack, so the running depth is
    never falsely elevated by them in the first place.

    **Known, accepted limitation: a top-level qualifier hidden behind a
    typedef is not detected (Codex review, eighth round, fresh evidence).**
    For ``typedef int * const ConstPtr; extern ConstPtr p;``, both backends
    pass this function the ALIAS spelling (``"ConstPtr"``) as *type_str* --
    neither castxml's ``type_name()`` nor clang's plain (sugared) ``qualType``
    resolves through a typedef the way this function's own text scan would
    need to see the real ``int * const`` underneath. This function then
    finds no sigil and no keyword in ``"ConstPtr"`` and reports ``()``, a
    CONFIRMED absence, even though the variable's real top-level
    qualification is ``("const",)``. A real fix needs new structural
    evidence this function does not have access to today: clang exposes a
    separate ``desugaredQualType`` string precisely for this
    (``dumper_clang_qualifiers.desugared_qualtype``, already used elsewhere
    for the identical const/volatile-behind-a-typedef problem on a FIELD),
    and castxml already has a structured, typedef-following resolver
    (``resolve_cv_restrict``, which already walks through ``Typedef``/
    ``ElaboratedType`` nodes) -- but neither is currently threaded onto
    ``Variable`` for this function to read, and adding either is a new,
    per-backend model field (mirroring ``Param.is_restrict``'s own
    structural-fact treatment), not a normalizer-only change. Left
    undetected (an honest, if incomplete, ``()``) rather than guessed at
    from text that cannot see through the alias -- the same "state a real
    structural gap rather than attempt a speculative heuristic" choice this
    function's own `restrict` non-recognition already makes for a closely
    related reason (see `_CV_KEYWORD_RE`'s own comment).
    """
    # Bracket-KIND-aware stack, not a flat depth counter (Codex review,
    # twelfth round, fresh evidence) -- mirrors `semantic_normalizer_
    # artifacts.has_unresolved_component`'s identical discipline (see this
    # function's own docstring, the paragraph just above, for the full
    # reasoning). Contains only "(" (opaque, non-declarator-group parens),
    # "[", and "<" -- "at depth 0" is exactly `not stack`.
    stack: list[str] = []
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
                stack.append(ch)
            i += 1
            continue
        if ch == ")":
            was_transparent = transparent_parens.pop() if transparent_parens else False
            if not was_transparent and stack:
                stack.pop()
            i += 1
            continue
        if ch == "[":
            stack.append(ch)
        elif ch == "]":
            if stack:
                stack.pop()
        elif ch == "<":
            if not stack or stack[-1] not in "([":
                stack.append(ch)
            # else: a real comparison operator character sitting inside a
            # paren/bracket expression context, not a template opener --
            # leave the stack untouched (symmetric with the ">" rule below).
        elif ch == ">":
            if stack and stack[-1] == "<":
                stack.pop()
            # else: a real comparison/shift-operator character sitting
            # inside a paren/bracket expression context, not a template
            # closer -- leave the stack untouched.
        elif ch in "*&" and not stack:
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


def _variable_cv_qualification_fact(
    var: Variable, producer: str
) -> Fact[tuple[str, ...]]:
    """A variable's ``cv_qualification`` fact, producer-aware: castxml/clang
    get :func:`_variable_top_level_cv_qualification`'s text scan over
    ``var.type``; DWARF reads its structural ``is_const`` (see
    :mod:`~abicheck.extract.semantic_normalizer_dwarf`); any other producer
    is scanned only when its spelling names a qualifier, and otherwise reads
    ``is_const`` the DWARF way."""
    if producer == DWARF_PRODUCER:
        return _dwarf_variable_cv_qualification(var.is_const)
    if producer in _HEADER_AST_PRODUCERS or _CV_KEYWORD_RE.search(var.type):
        return Fact.present(_variable_top_level_cv_qualification(var.type))
    # Any other producer (PDB, BTF/CTF, a hand-built or pre-producer-stamp
    # snapshot) whose spelling names no qualifier at all: the spelling is not
    # evidence the variable is unqualified, but ``is_const`` is the one
    # structural fact such a producer recorded -- read it the way DWARF's is.
    return _dwarf_variable_cv_qualification(var.is_const)


#: The two header-AST backends, whose variable spelling always carries the
#: declaration's own qualifiers verbatim.
_HEADER_AST_PRODUCERS = frozenset({"castxml", "clang"})


def variable_canonical_entity(var: Variable, producer: str) -> CanonicalEntity:
    """The payload :func:`normalize_header_ast` records for *var* -- its
    canonical type spelling and top-level ``cv_qualification`` -- as one
    :class:`CanonicalEntity`, with no identity attached.

    The one formula both a real normalizer run and the legacy adapter's
    variable projection (``model.semantic_ir_legacy_adapter.
    legacy_variable_occurrences``, ADR-063 6B variable cohort) apply, so a
    side read through the adapter and a side read through a real IR cannot
    disagree about what the same declaration's facts are.
    """
    return CanonicalEntity(
        canonical_spelling=_variable_spelling_fact(var, producer),
        producer=producer,
        cv_qualification=_variable_cv_qualification_fact(var, producer),
    )


def _dwarf_variable_cv_qualification(is_const: bool) -> Fact[tuple[str, ...]]:
    """A DWARF-sourced variable's ``cv_qualification``, built from its
    already-extracted, structurally-sound ``Variable.is_const`` -- see this
    module's own docstring for why this differs from the castxml/clang text
    scan (and why it can only ever report ``const``, never ``volatile``).

    Always ``Fact.partial(...)``, never ``Fact.present(...)`` -- including
    when *is_const* is ``False`` (Codex review, fresh evidence): DWARF's own
    DIE walk never extracts a volatile-qualifier fact for a variable at all
    (no backend has an ``is_volatile`` field on ``Variable``), so even a
    confirmed-non-const result is only ever confirmed for the "const" half
    of this tuple's vocabulary -- volatile stays genuinely uncollected
    regardless of what *is_const* says. ``Fact.present(())`` would
    misrepresent that gap as "confirmed: neither qualifier applies", the
    identical "PRESENT denotes a complete, confirmed value" mistake this
    module's own function cv_qualification carve-out already avoids for a
    different reason.
    """
    return Fact.partial(
        canonical_cv_qualification(("const",) if is_const else ()),
        "DWARF does not extract a volatile-qualifier fact for a variable",
    )
