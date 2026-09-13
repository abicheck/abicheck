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

"""Public-variable comparison helpers: alignment changes and top-level
const/reference-aware type-spelling normalization.

Leaf module (must not import from ``diff_symbols`` to avoid an import
cycle). ``diff_symbols._check_variable`` is the sole caller.
"""

from __future__ import annotations

import re
from typing import Any

from .checker_types import Change
from .compare import export_transition as _export_transition
from .compare.elf_only_demangle import (
    elf_only_demangled_name as _elf_only_demangled_name,
)
from .compare.fact_comparison import compare_facts
from .diff_helpers import bool_transition, make_change
from .diff_symbols_renames import _should_filter_transitive_runtime_symbols
from .elf_symbol_filter import (
    exported_symbol_names,
    is_abi_relevant_elf_symbol,
)
from .model import AbiSnapshot, AccessLevel, Variable
from .model.change_catalog.kinds import ChangeKind
from .model.surface_facts import (
    is_abi_visible,
    is_export_table_only_record,
    surface_fact_summary,
)
from .name_classification import (
    _find_matching_close,
    canonicalize_type_name,
    func_signature_cv_only_differ,
    is_local_rtti_symbol,
)


def _is_access_narrowing(old_access: Any, new_access: Any) -> bool:
    """Return True if the access level transition is narrowing (breaking).

    Narrowing = less accessible: public→protected, public→private, protected→private.
    Widening (e.g., private→public) is backward-compatible and should NOT be flagged.

    Shared by ``diff_symbols.py``'s method/field access-narrowing checks and
    :func:`var_access_changes` below — moved here (rather than staying in
    ``diff_symbols.py``) purely to make room under that file's 2000-line hard
    cap; not otherwise Variable-specific.
    """
    _RANK = {AccessLevel.PUBLIC: 0, AccessLevel.PROTECTED: 1, AccessLevel.PRIVATE: 2}  # pylint: disable=invalid-name
    return _RANK.get(new_access, 0) > _RANK.get(old_access, 0)


def var_access_changes(
    old_map: dict[str, Variable], new_map: dict[str, Variable]
) -> list[Change]:
    """``VAR_ACCESS_CHANGED``/``VAR_ACCESS_WIDENED`` for flipped variables.

    The evidence gates (header-tier, "castxml"-producer-only,
    reliability-gated) live with the registration in ``diff_symbols.py``'s
    ``_diff_var_access`` (G31 Phase C continued — see
    ``AbiSnapshot.castxml_var_access_facts_reliable``'s own docstring for
    the full reasoning); by the time this runs both sides are known-safe.

    That whole-snapshot gate only says the producer is trustworthy when it
    ran — it does not guarantee ``access_fact`` reached ``PRESENT`` for
    *this specific* variable (ADR-063 Phase 5B, the same "case-(a) field,
    one whole-snapshot reliability flag" shape ``compare.va_list_diff.
    diff_va_list_params`` already closed for ``Param.is_va_list``). Each
    pair is gated through :func:`~.compare.fact_comparison.compare_facts`
    rather than the old bare-value comparison, so a variable whose evidence
    is incomplete on either side is skipped instead of silently read as
    "confirmed public" (``AccessLevel.PUBLIC`` is both this field's normal
    resting value and a real answer — see ``Variable.access``'s own
    comment in ``model/declarations.py``).
    """
    changes: list[Change] = []
    for mangled, v_old in old_map.items():
        v_new = new_map.get(mangled)
        if v_new is None:
            continue
        access_cmp = compare_facts(
            v_old.access_fact, v_new.access_fact, AccessLevel.PUBLIC
        )
        if not access_cmp.is_comparable:
            continue
        old_access, new_access = access_cmp.old_value, access_cmp.new_value
        if old_access == new_access:
            continue
        kind = (
            ChangeKind.VAR_ACCESS_CHANGED
            if _is_access_narrowing(old_access, new_access)
            else ChangeKind.VAR_ACCESS_WIDENED
        )
        changes.append(
            make_change(
                kind,
                symbol=mangled,
                name=v_old.name,
                old=old_access.value if old_access is not None else "?",
                new=new_access.value if new_access is not None else "?",
                entity_id=v_old.entity_id or v_new.entity_id,
            )
        )
    return changes


def _check_variable_alignment(
    mangled: str, v_old: Variable, v_new: Variable
) -> list[Change]:
    """Emit a change when a variable's declared alignment changed.

    Tri-state: None = not captured (older snapshots / dumpers without
    alignment support) — skip rather than compare.
    """
    if v_old.alignment_bits is None or v_new.alignment_bits is None:
        return []
    if v_old.alignment_bits == v_new.alignment_bits:
        return []
    return [
        make_change(
            ChangeKind.VAR_ALIGNMENT_CHANGED,
            symbol=mangled,
            name=v_old.name,
            old=str(v_old.alignment_bits),
            new=str(v_new.alignment_bits),
            entity_id=v_old.entity_id or v_new.entity_id,
        )
    ]


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


_UNKNOWN_TYPE = "?"


def _type_unknown(type_name: str | None) -> bool:
    """An unresolved type spelling -- a stripped side's placeholder, not a
    real type. Mirrors ``diff_symbols``' own predicate of the same name; the
    two are independent one-liners over the same sentinel rather than a
    cross-module import between two modules that already import one way."""
    return type_name is None or type_name.strip() == _UNKNOWN_TYPE


def _var_removed(
    mangled: str,
    v_old: Variable,
    new_all: dict[str, Variable] | None = None,
    old_exported_symbols: frozenset[str] = frozenset(),
    cv_facts_reliable: bool = True,
) -> list[Change]:
    """A public variable with no peer in the NEW side's public surface.

    *new_all* is the NEW side's FULL variable map (not just its public
    surface): a variable still declared there, which left the compared
    surface only because this run established less contract evidence for
    that side, is an evidence gap rather than a removal -- see
    ``export_transition.surface_exit_is_evidence_gap``. Defaulted so a
    caller with no full map behaves exactly as before.

    Such a surviving declaration is then *compared* (:func:`_check_variable`)
    rather than reported as removed: a real type change on it must still be
    reported (Codex review, P1).
    """
    v_new = None if new_all is None else new_all.get(mangled)
    if v_new is not None and _export_transition.surface_exit_is_evidence_gap(
        v_old,
        v_new,
        old_exported_symbols=old_exported_symbols,
        key=mangled,
    ):
        # Matched, not discarded (Codex review, P1) -- see the identical
        # reasoning at `diff_symbols._match_old_function`'s own call: the
        # declaration is on both sides, so a real type or const-qualification
        # change on it is still comparable and must still be reported.
        return _check_variable(
            mangled, v_old, v_new, cv_facts_reliable=cv_facts_reliable
        )
    return [
        make_change(
            ChangeKind.VAR_REMOVED,
            symbol=mangled,
            name=v_old.name,
            # See Change.symbol_binding's docstring — None when not captured.
            symbol_binding=v_old.elf_binding.value if v_old.elf_binding else None,
            entity_id=v_old.entity_id,
            demangled_symbol=_elf_only_demangled_name(mangled, v_old.visibility),
            # See _check_removed_function's identical stamp.
            surface_facts=surface_fact_summary(v_old),
        )
    ]


def _var_added(
    mangled: str,
    v_new: Variable,
    old_all: dict[str, Variable] | None = None,
    new_exported_symbols: frozenset[str] = frozenset(),
    cv_facts_reliable: bool = True,
) -> list[Change]:
    """A public variable with no peer in the OLD side's public surface.

    The mirror of :func:`_var_removed`'s own guard (Codex review, P2): when
    it is the OLD side that lacks contract evidence, the same unchanged
    declaration *enters* the compared surface and reads as an addition. Same
    predicate with the sides swapped, same outcome -- the surviving pair is
    compared, not reported as new.
    """
    v_old = None if old_all is None else old_all.get(mangled)
    if v_old is not None and _export_transition.surface_exit_is_evidence_gap(
        v_new,
        v_old,
        old_exported_symbols=new_exported_symbols,
        key=mangled,
    ):
        return _check_variable(
            mangled, v_old, v_new, cv_facts_reliable=cv_facts_reliable
        )
    return [
        make_change(
            ChangeKind.VAR_ADDED,
            symbol=mangled,
            name=v_new.name,
            entity_id=v_new.entity_id,
        )
    ]


def _observed_exports(snap: AbiSnapshot, types: frozenset[str]) -> frozenset[str]:
    """The names *snap*'s own export table carries (empty when it has none).

    The removal paths cross-check against this rather than trusting a
    declaration's own, possibly legacy-bridged, ``binary_exported`` fact --
    see ``export_transition.surface_exit_is_evidence_gap``.
    """
    return frozenset(
        exported_symbol_names(
            getattr(snap, "elf", None),
            types,
            abi_relevant_only=True,
            filter_transitive_runtime_symbols=(
                _should_filter_transitive_runtime_symbols(snap)
            ),
        )
    )


def _public_variables(snap: AbiSnapshot) -> dict[str, Variable]:
    """Return public/ELF-only variables from *snap*.

    Excludes RTTI/vtable symbols of function-local types (lambda closures and
    other in-function types): they are not nameable public ABI and only churn
    across builds (RD2-4).
    """
    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(snap)
    return {
        k: v
        for k, v in snap.variable_map.items()
        if (
            is_abi_visible(v)
            and (
                not is_export_table_only_record(v)
                or is_abi_relevant_elf_symbol(
                    k,
                    filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
                )
            )
            and not is_local_rtti_symbol(k)
        )
    }


def _check_variable(
    mangled: str, v_old: Variable, v_new: Variable, *, cv_facts_reliable: bool = True
) -> list[Change]:
    """Compare a matched pair of public variables.

    *cv_facts_reliable* mirrors ``diff_types._field_type_genuinely_changed``:
    a pre-v9 CastXML snapshot silently dropped ``volatile`` from a variable's
    type spelling (no dedicated ``is_volatile`` fact to fall back on, unlike
    ``TypeField``), so an unchanged legacy-vs-fresh pair would otherwise
    misreport a breaking ``VAR_TYPE_CHANGED`` (Codex review, PR #582).
    """
    changes = _check_variable_alignment(mangled, v_old, v_new)
    # The export axis is independent of every type/qualifier comparison below
    # and must survive their early returns -- an unknown "?" type on a
    # stripped side says nothing about whether the symbol is still exported --
    # so it is folded in first (compare/export_transition.py).
    changes += _export_transition.check_variable(mangled, v_old, v_new)
    # RD2-5: a stripped side reports type "?"; unknown is not a type change.
    if _type_unknown(v_old.type) or _type_unknown(v_new.type):
        return changes
    canon_old = canonicalize_type_name(v_old.type)
    canon_new = canonicalize_type_name(v_new.type)
    if canon_old != canon_new:
        # A pure TOP-LEVEL const-qualifier flip is a real, common case where
        # the type strings differ (the dumper bakes "const" into the type
        # text) but the base type is otherwise identical — that's a const
        # transition (below), not a base-type change. Only the trailing
        # (top-level) const is stripped for this comparison — a pointee-level
        # const (e.g. `int *` -> `const int *`) must still fall through to
        # VAR_TYPE_CHANGED, since the pointer itself didn't become const.
        is_pure_const_flip = (
            v_old.is_const != v_new.is_const
            and _without_top_level_const(canon_old)
            == _without_top_level_const(canon_new)
        )
        if not is_pure_const_flip:
            if not cv_facts_reliable and func_signature_cv_only_differ(
                canon_old, canon_new
            ):
                # Legacy-snapshot cv noise: the type-string difference itself
                # is untrustworthy (see this function's docstring), so don't
                # fall through to the const-transition check below either —
                # is_const may be equally unreliable for the same reason,
                # and falling through would just resurface the same false
                # positive as VAR_BECAME_CONST/VAR_LOST_CONST instead of
                # VAR_TYPE_CHANGED (Codex review, PR #589).
                return changes
            return changes + [
                make_change(
                    ChangeKind.VAR_TYPE_CHANGED,
                    symbol=mangled,
                    name=v_old.name,
                    old=v_old.type,
                    new=v_new.type,
                    entity_id=v_old.entity_id or v_new.entity_id,
                )
            ]
    # const-qualification transitions only matter when the type is unchanged.
    return changes + bool_transition(
        v_old.is_const,
        v_new.is_const,
        mangled,
        added=(
            ChangeKind.VAR_BECAME_CONST,
            f"Variable became const-qualified: {v_old.name} (writes now → SIGSEGV)",
        ),
        added_values=("non-const", "const"),
        removed=(
            ChangeKind.VAR_LOST_CONST,
            f"Variable lost const qualifier: {v_old.name} (ODR / inlining break)",
        ),
        removed_values=("const", "non-const"),
        entity_id=v_old.entity_id or v_new.entity_id,
    )
