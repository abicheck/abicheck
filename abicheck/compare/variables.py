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

"""The variable type/const detector family, reading through ``SemanticIRIndex``
(ADR-063 6B, variable cohort: ``VAR_TYPE_CHANGED``/``VAR_BECAME_CONST``/
``VAR_LOST_CONST``).

**Why this cohort.** A variable is the one declaration family whose whole
comparison payload the IR already carries: ``CanonicalEntity.
canonical_spelling`` is ``canonicalize_type_name(Variable.type)`` (or a
non-present fact for an unresolved or opaque spelling) and
``cv_qualification`` is the declaration's own *top-level* qualification.
Those are exactly the two values this family decides on, so migrating it is
a real read-path change, not new normalizer output.

**Authority, per side** (the T3 rule the record-layout cohort follows, not a
fidelity gate): a variable is read from its side's ``SemanticIR`` when that
IR carries an occurrence for its ``entity_id``. A variable the IR cannot
name -- no ``entity_id`` (an export-table-only data symbol), no occurrence
for it, or a side with no variable occurrences at all (a pre-v38 document)
-- is projected through the legacy adapter
(``semantic_ir_legacy_adapter.legacy_variable_occurrences``) with the
normalizer's own formula. Nothing re-reads ``Variable.type`` to check the IR.

**Two deliberate differences from the pre-cutover reads**, both because the
IR's facts are the more precise ones:

* *Const is top-level.* ``Variable.is_const`` is a word search over the
  whole spelling, so ``const int * const g`` -> ``const int * g`` read as
  const on both sides and was reported as ``VAR_TYPE_CHANGED``. The IR's
  ``cv_qualification`` answers for the variable itself, so the same pair is
  the ``VAR_LOST_CONST`` it is.
* *Unknown is any unresolved component.* The legacy check skipped only a
  whole-string ``"?"``; the IR's spelling is non-present for any
  depth-zero sentinel (``"?*"``, ``"const ?"``) and for castxml's opaque
  ``FunctionType`` tag, neither of which is a comparable spelling.

**This module may not read a variable's type facts off ``Variable``**, nor
a snapshot's variables: ``scripts/semantic_ir_cutover.py`` forbids
``type``/``is_const``/``variables``/``variable_map`` reads here. Pairing
stays with the caller's ``SymbolIdentityIndex``; the displayed ``old``/
``new`` values are the caller's raw spellings, so report text is unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

from ..diff_helpers import bool_transition, make_change
from ..model.castxml_spelling_artifacts import has_unresolved_component
from ..model.change_catalog.kinds import ChangeKind
from ..model.identity import EntityId, EntityKind
from ..model.semantic_ir import CanonicalEntity, SemanticIR
from ..model.semantic_ir_function_signature import overlay_established_facts
from ..model.semantic_ir_index import SemanticIRIndex
from ..model.semantic_ir_legacy_adapter import (
    semantic_ir_covers_kind,
)
from ..model.type_indirection import indirection_provably_differs
from ..name_classification import _find_matching_close, func_signature_cv_only_differ
from .declined_comparisons import record_declined
from .detection_memo import memoized

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model.declarations import Variable
    from ..model.identity import EntityId

__all__ = [
    "VariableTypeIndex",
    "variable_type_changes",
    "variable_type_facts",
    "variable_type_index",
]


@dataclass(frozen=True)
class VariableTypeIndex:
    """One side's variable view: its ``SemanticIR`` index, plus the adapter
    entity of each variable the IR cannot name (keyed by the variable
    object, since several such variables may share one spelling)."""

    index: SemanticIRIndex
    projected: dict[int, CanonicalEntity] = field(default_factory=dict)

    def entity_for(self, var: Variable) -> CanonicalEntity | None:
        entity = self.projected.get(id(var))
        if entity is not None or var.entity_id is None:
            return entity
        return self.index.entity(var.entity_id)


#: One shared empty IR, so a side without variable occurrences memoizes
#: under one key rather than a fresh object per index.
_EMPTY_IR = SemanticIR()


def _named_variable_entities(ir: SemanticIR) -> dict[EntityId, CanonicalEntity]:
    return dict(SemanticIRIndex(ir).entities_of_kind(EntityKind.VARIABLE))


def _project_variable(
    var: Variable,
    named: Mapping[EntityId, CanonicalEntity],
    project: Callable[[Variable], CanonicalEntity],
) -> CanonicalEntity:
    """*var*'s entity: its named occurrence with the declaration re-projected
    over it (a named occurrence's facts are a boundary copy of its
    declaration's, so an edit of a loaded snapshot's ``Variable`` would
    otherwise be masked), or the projection itself when the IR cannot name
    it."""
    fresh = project(var)
    entity = named.get(var.entity_id) if var.entity_id is not None else None
    if entity is None:
        return fresh
    return overlay_established_facts(entity, dict(fresh.fact_items()))


def variable_type_index(
    semantic_ir: SemanticIR | None,
    variables: Iterable[Variable],
    project: Callable[[Variable], CanonicalEntity],
    *,
    projection_key: Hashable = None,
) -> VariableTypeIndex:
    """One side's index -- see the module docstring for the rule.

    *variables* are the objects the caller will pair (the adapter half is
    keyed by object); *project* is the normalizer's payload formula
    (``extract.semantic_normalizer.variable_canonical_entity`` bound to the
    side's producer). Inside a ``detection_memo_scope`` each variable's
    entity is projected once per (IR, *projection_key*); *projection_key*
    must identify *project* (e.g. its producer), and ``None`` disables the
    memo.
    """
    covered = semantic_ir is not None and semantic_ir_covers_kind(
        semantic_ir, EntityKind.VARIABLE
    )
    ir_used = semantic_ir if covered and semantic_ir is not None else _EMPTY_IR
    named = memoized(
        "variable_type_named", ir_used, None, partial(_named_variable_entities, ir_used)
    )
    seen: dict[int, tuple[Variable, CanonicalEntity]] = (
        {}
        if projection_key is None
        else memoized("variable_type_entities", ir_used, projection_key, dict)
    )
    projected: dict[int, CanonicalEntity] = {}
    for v in variables:
        hit = seen.get(id(v))
        if hit is None or hit[0] is not v:
            hit = (v, _project_variable(v, named, project))
            seen[id(v)] = hit
        projected[id(v)] = hit[1]
    ir_index = memoized(
        "variable_type_ir_index", ir_used, None, partial(SemanticIRIndex, ir_used)
    )
    return VariableTypeIndex(index=ir_index, projected=projected)


def variable_type_facts(
    entity: CanonicalEntity | None,
) -> tuple[str | None, bool | None]:
    """``(canonical type spelling, is top-level const)`` off *entity*, each
    ``None`` when the entity does not establish it."""
    if entity is None:
        return None, None
    spelling = entity.canonical_spelling
    cv = entity.cv_qualification
    return (
        spelling.value if spelling.is_present else None,
        ("const" in cv.value) if cv.is_present and cv.value is not None else None,
    )


def _unresolved_structure_differs(
    canon_old: str | None, canon_new: str | None, displays: tuple[str, str]
) -> bool:
    """Whether a pair with an unestablished spelling is still a provable
    type change: each side is either established or a castxml-unresolved
    composite (``"?*"``, ``"const ?"``), and the resolved declarator
    structure alone differs (``int`` -> ``"?*"``). Two compatible shapes
    (``"?*"``/``"?*"``, ``int **``/``"?*"``) stay a decline."""
    sides = []
    for canon, raw in zip((canon_old, canon_new), displays):
        if canon is not None:
            sides.append(canon)
        elif raw and has_unresolved_component(raw):
            sides.append(raw)
        else:
            return False
    return indirection_provably_differs(sides[0], sides[1])


def variable_type_changes(
    mangled: str,
    name: str,
    displays: tuple[str, str],
    old_entity: CanonicalEntity | None,
    new_entity: CanonicalEntity | None,
    *,
    entity_id: EntityId | None,
    cv_facts_reliable: bool = True,
) -> list[Change]:
    """``VAR_TYPE_CHANGED``/``VAR_BECAME_CONST``/``VAR_LOST_CONST`` for one
    matched variable pair, from each side's IR entity.

    *displays* is the caller's ``(old, new)`` raw spelling, used only as the
    finding's displayed values. *cv_facts_reliable* is ``False`` when either
    side is a document whose writer dropped ``volatile`` from a variable's
    spelling (pre-v9 castxml), where a cv-only spelling difference is noise
    rather than evidence -- and so is the const fact derived from the same
    spelling (Codex review, PR #589).
    """
    canon_old, const_old = variable_type_facts(old_entity)
    canon_new, const_new = variable_type_facts(new_entity)
    # RD2-5: an unresolved spelling on either side is not a type change --
    # but it is not a confirmed "unchanged" either, so the declined
    # comparison is recorded (T9 accounting) rather than passing silently.
    if canon_old is None or canon_new is None:
        if _unresolved_structure_differs(canon_old, canon_new, displays):
            return [
                make_change(
                    ChangeKind.VAR_TYPE_CHANGED,
                    symbol=mangled,
                    name=name,
                    old=displays[0],
                    new=displays[1],
                    entity_id=entity_id,
                )
            ]
        record_declined(
            mangled,
            "variable type spelling not established on "
            + ("both sides" if canon_old is None and canon_new is None else "one side"),
        )
        return []
    if canon_old != canon_new:
        # A pure TOP-LEVEL const flip is a const transition (below), not a
        # base-type change; a pointee-level const (`int *` -> `const int *`)
        # still falls through to VAR_TYPE_CHANGED, since the pointer itself
        # did not become const.
        is_pure_const_flip = (
            const_old is not None
            and const_new is not None
            and const_old != const_new
            and _without_top_level_const(canon_old)
            == _without_top_level_const(canon_new)
        )
        if not is_pure_const_flip:
            if not cv_facts_reliable and func_signature_cv_only_differ(
                canon_old, canon_new
            ):
                return []
            return [
                make_change(
                    ChangeKind.VAR_TYPE_CHANGED,
                    symbol=mangled,
                    name=name,
                    old=displays[0],
                    new=displays[1],
                    entity_id=entity_id,
                )
            ]
    # const-qualification transitions only matter when the type is unchanged.
    return bool_transition(
        const_old,
        const_new,
        mangled,
        added=(
            ChangeKind.VAR_BECAME_CONST,
            f"Variable became const-qualified: {name} (writes now → SIGSEGV)",
        ),
        added_values=("non-const", "const"),
        removed=(
            ChangeKind.VAR_LOST_CONST,
            f"Variable lost const qualifier: {name} (ODR / inlining break)",
        ),
        removed_values=("const", "non-const"),
        skip_none=True,
        entity_id=entity_id,
    )


_TRAILING_CONST_RE = re.compile(r"\s*\bconst\b\s*$")
_LEADING_CONST_TOKEN_RE = re.compile(r"^\s*\bconst\b\s*")
_CV_TOKEN_RE = re.compile(r"\b(?:const|volatile)\b")


def _has_top_level_pointer_or_ref(canonical_type: str) -> bool:
    """True if *canonical_type* has a ``*``/``&`` outside any ``<...>``
    template-argument bracket.

    A plain substring search for ``*``/``&`` would also match one nested
    *inside* a template argument (e.g. ``std::vector<int *>`` — a by-value
    vector of pointers, not itself a pointer), wrongly routing a pure
    top-level const flip on that by-value variable into the
    pointer/reference branch below, which only strips a *trailing* const —
    but the top-level const here is leading (Codex review).
    """
    depth = 0
    for ch in canonical_type:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch in "*&" and depth == 0:
            return True
    return False


def _last_sigil_in_range(canonical_type: str, start: int, end: int) -> int | None:
    """Index of the last ``*``/``&`` in ``canonical_type[start:end]`` outside
    any ``<...>`` bracket, or None. Same "top-level" definition as
    ``_has_top_level_pointer_or_ref``, just reporting a position within a
    sub-range instead of a whole-string boolean.
    """
    depth = 0
    pos = None
    for i in range(start, end):
        ch = canonical_type[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch in "*&" and depth == 0:
            pos = i
    return pos


def _first_top_level_paren_span(canonical_type: str) -> tuple[int, int] | None:
    """``(open_idx, close_idx)`` of the first top-level (non-``<...>``-
    nested) ``(...)`` group, or None if there is none."""
    depth = 0
    for i, ch in enumerate(canonical_type):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            close = _find_matching_close(canonical_type, i)
            return i, min(close, len(canonical_type) - 1)
    return None


def _declarator_sigil_pos(canonical_type: str) -> int | None:
    """Index of the variable's OWN declarator ``*``/``&``, as opposed to one
    belonging to a parameter or array size nested further in the spelling.

    A function-/array-pointer variable's canonical spelling is always
    ``BaseType ( *quals)(...)``/``BaseType ( *quals)[...]`` — the FIRST
    top-level ``(...)`` group is structurally this declarator (the actual
    parameter list or array dimensions, if present, always comes after it).
    Searching the whole string for the LAST top-level sigil instead (as an
    earlier version of this function did) picks up a parameter's own
    pointer sigil for a callback/function-pointer parameter (``"void (
    *const)(int *)"`` — the last ``*`` is the parameter's, not the outer
    declarator's), so its trailing ``const`` never gets recognized as the
    variable's own (Codex review, PR #589). Falls back to the whole
    string's last top-level sigil for a bare pointer with no parens at all
    (``"int * const"``).
    """
    span = _first_top_level_paren_span(canonical_type)
    if span is not None:
        open_idx, close_idx = span
        pos = _last_sigil_in_range(canonical_type, open_idx + 1, close_idx)
        if pos is not None:
            return pos
    return _last_sigil_in_range(canonical_type, 0, len(canonical_type))


def _has_real_trailing_group(canonical_type: str, after: int) -> bool:
    """True if a top-level ``(...)``/``[...]`` group (skipping only
    whitespace) starts at or after index *after*.

    Used to recognize a member-function-POINTER's own trailing cv (``"void
    (C:: *)(int) const"`` — the pointer points to a const member function):
    once a REAL parameter list or array-dimension group follows the
    declarator, anything trailing after IT is that group's own business
    (member-function cv-qualification, a genuinely different, non-
    interchangeable type — confirmed against real g++ mangling), never the
    bare-pointer "trailing own const" case the end-of-string fallback below
    exists for (CodeRabbit review, PR #589).
    """
    k = after
    while k < len(canonical_type) and canonical_type[k].isspace():
        k += 1
    return k < len(canonical_type) and canonical_type[k] in "(["


def _strip_trailing_declarator_const(canonical_type: str) -> str:
    """Strip a top-level pointer/reference declarator's own trailing
    ``const`` — whether at the absolute end of the string (a bare pointer,
    ``"int * const"``) or immediately before the closing paren/bracket of a
    function- or array-pointer declarator (``"void ( *const)()"``, ``"int
    ( *const)[5]"`` — a variable whose type itself is a function or array
    pointer, canonicalized with the qualifier directly after the ``*``, not
    at the string's end). Only a run of pure cv tokens between the sigil and
    that close counts; anything else there (a real parameter/element type)
    means this isn't the simple ``"(*quals)"`` declarator shape, so fall
    back to the plain end-of-string case — UNLESS a real parameter list or
    array-dimension group follows the declarator (see
    :func:`_has_real_trailing_group`), in which case a trailing end-of-string
    ``const``/``volatile`` belongs to THAT group, not this declarator, and
    must be left untouched entirely rather than risk stripping something
    that isn't actually this declarator's own qualifier (CodeRabbit review,
    PR #589, x2).
    """
    pos = _declarator_sigil_pos(canonical_type)
    if pos is not None:
        span_end = len(canonical_type)
        for k in range(pos + 1, len(canonical_type)):
            if canonical_type[k] in ")]":
                span_end = k
                break
        span = canonical_type[pos + 1 : span_end]
        if (
            span_end < len(canonical_type)
            and re.fullmatch(r"(?:\s|const|volatile)*", span)
            and _CV_TOKEN_RE.search(span)
        ):
            # .strip(): canonicalize_type_name never puts whitespace directly
            # after the sigil or directly before the declarator's closing
            # bracket (e.g. an unchanged volatile-only declarator canonicalizes
            # to "( *volatile)", not "( * volatile)") — removing "const" from
            # a combined "const volatile"/"volatile const" span leaves a
            # separator space stranded at whichever end "const" vacated,
            # which must be trimmed to match that convention or an unrelated,
            # unchanged volatile qualifier makes the two sides spuriously
            # compare unequal (Codex review, PR #589).
            new_span = re.sub(r"\bconst\b", "", span).strip()
            return canonical_type[: pos + 1] + new_span + canonical_type[span_end:]
        if span_end < len(canonical_type) and _has_real_trailing_group(
            canonical_type, span_end + 1
        ):
            return canonical_type
    return _TRAILING_CONST_RE.sub("", canonical_type)


def _without_top_level_const(canonical_type: str) -> str:
    """Strip the *top-level* ``const`` from an already-canonicalized type name.

    ``canonicalize_type_name`` normalizes a leading ``const T`` to ``T
    const`` (moving the qualifier immediately after what it qualifies) —
    but only when the base type has no template args (``"<...>"``); for a
    templated base it deliberately leaves the spelling untouched, so a
    top-level const on e.g. ``std::vector<int>`` stays leading
    (``"const std::vector<int>"``), not trailing.

    So which end is "top-level" depends on whether a pointer/reference
    sigil is present, not on template-ness:

    - No ``*``/``&`` at all: the *whole object* is what's qualified, so a
      const at *either* end (leading, for a templated base; trailing, the
      east-const form for a non-template base) is the top-level qualifier
      — strip whichever is present.
    - A ``*``/``&`` present: the top-level (pointer-itself) qualifier is
      always the trailing token (``"int * const"``, or ``"std::vector<int>
      * const"``) regardless of template-ness — see
      ``_strip_trailing_declarator_const`` for the function-/array-pointer
      declarator's own variant of "trailing". A *leading* const there
      (``"int const *"``, ``"const std::vector<int> *"``) qualifies the
      pointee, not the pointer, and must NOT be stripped — collapsing it
      would hide a real type change (the pointer itself is still writable;
      only what it points to changed) behind a misleading "variable became
      const" (Codex review, x2: the original non-template pointee-const
      case, and the templated-base variant of the same issue).
    """
    if _has_top_level_pointer_or_ref(canonical_type):
        return _strip_trailing_declarator_const(canonical_type)
    stripped = _LEADING_CONST_TOKEN_RE.sub("", canonical_type)
    return _TRAILING_CONST_RE.sub("", stripped)
