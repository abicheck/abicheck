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

"""The function-signature detector family, reading through ``SemanticIRIndex``
(ADR-063 6B, function-signature cohort: ``FUNC_RETURN_CHANGED``/
``FUNC_PARAMS_CHANGED``/``FUNC_REF_QUAL_CHANGED``/``FUNC_VARIADIC_ADDED``/
``FUNC_VARIADIC_REMOVED``).

**What the IR carries.** ``CanonicalEntity.return_type_spelling``/
``parameter_type_spellings``/``parameter_kinds``/``ref_qualifier``/
``is_variadic`` (semantic_ir document version 3, snapshot schema v57). The
spellings are the producer's own rather than canonicalized, because the cv-
and scalar-equivalence predicates below decide on raw spellings; storing a
canonical form would change what they decide. The facts are filled at the
snapshot boundary from the snapshot's final functions
(``model/semantic_ir_function_signature.py``), so a hybrid merge, a layout
backfill or a pre-v57 document all reach this module with the same facts.

**Authority, per side** (the T3 rule the record-layout and variable cohorts
follow): a function is read from its side's ``SemanticIR`` when that IR has
an occurrence for its ``entity_id`` carrying a signature. A function the IR
cannot speak for -- no ``entity_id``, no occurrence, an occurrence the fill
declined (two different signatures under one identity), or a side with no
function occurrences at all -- is projected through the legacy adapter
(``semantic_ir_legacy_adapter.legacy_function_signature_occurrences``) with
the same formula. Nothing re-reads a ``Function``'s signature fields here.

**This module may not read a function's signature off ``Function``**, nor a
snapshot's functions: ``scripts/semantic_ir_cutover.py`` forbids
``return_type``/``params``/``functions``/``function_map`` reads here. The
gate is name-based, and ``ref_qualifier``/``is_variadic`` are also the IR
facts' own names, so those two are held by review rather than the scan. Pairing stays with the caller's
``SymbolIdentityIndex``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from ..diff_helpers import bool_transition, make_change
from ..diff_symbols_scalar import _abi_equivalent_scalar
from ..model.cc_attributes import is_cc_attribute
from ..model.change_catalog.kinds import ChangeKind
from ..model.fact import Fact
from ..model.identity import EntityKind
from ..model.semantic_ir import CanonicalEntity, SemanticIR
from ..model.semantic_ir_function_signature import with_declaration_signature
from ..model.semantic_ir_index import SemanticIRIndex
from ..model.semantic_ir_legacy_adapter import (
    legacy_function_signature_entity,
    semantic_ir_covers_kind,
)
from ..name_classification import (
    canonicalize_type_name,
    cv_qualifiers_only_differ,
    func_signature_cv_only_differ,
)
from .declined_comparisons import record_declined
from .detection_memo import memoized

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model.declarations import Function
    from ..model.identity import EntityId

__all__ = [
    "FunctionSignature",
    "FunctionSignatureIndex",
    "function_signature_changes",
    "function_signature_index",
    "hidden_friend_changes",
    "signature_of",
]

_UNKNOWN_TYPE = "?"


def _type_unknown(type_name: str | None) -> bool:
    """An unresolved spelling -- a stripped side's placeholder (RD2-5)."""
    return type_name is None or type_name.strip() == _UNKNOWN_TYPE


@dataclass(frozen=True)
class FunctionSignature:
    """One side's signature facts, read off its IR entity; ``None`` where the
    entity does not establish a value."""

    return_spelling: str | None
    param_types: tuple[str, ...] | None
    param_kinds: tuple[str, ...] | None
    ref_qualifier: str | None
    is_variadic: bool | None
    is_extern_c: bool | None = None
    is_noexcept: bool | None = None
    is_virtual: bool | None = None
    is_explicit: bool | None = None
    is_hidden_friend: bool | None = None
    hidden_friend_owner: str | None = None
    contract_attributes: tuple[str, ...] | None = None
    exception_spec: str | None = None
    vtable_index: int | None = None


def signature_of(entity: CanonicalEntity | None) -> FunctionSignature:
    """The signature *entity* records (all ``None`` for no entity)."""
    if entity is None:
        return FunctionSignature(None, None, None, None, None)

    def value(fact: Fact[Any]) -> Any:
        return fact.value if fact.is_present else None

    return FunctionSignature(
        return_spelling=value(entity.return_type_spelling),
        param_types=value(entity.parameter_type_spellings),
        param_kinds=value(entity.parameter_kinds),
        ref_qualifier=value(entity.ref_qualifier),
        is_variadic=value(entity.is_variadic),
        is_extern_c=value(entity.is_extern_c),
        is_noexcept=value(entity.is_noexcept),
        is_virtual=value(entity.is_virtual),
        is_explicit=value(entity.is_explicit),
        is_hidden_friend=value(entity.is_hidden_friend),
        hidden_friend_owner=value(entity.hidden_friend_owner),
        contract_attributes=value(entity.contract_attributes),
        exception_spec=value(entity.exception_spec),
        vtable_index=value(entity.vtable_index),
    )


@dataclass(frozen=True)
class FunctionSignatureIndex:
    """One side's signature view: its ``SemanticIR`` index, plus the adapter
    entity of each function the IR cannot speak for (keyed by object)."""

    index: SemanticIRIndex
    projected: dict[int, CanonicalEntity] = field(default_factory=dict)

    def entity_for(self, fn: Function) -> CanonicalEntity | None:
        entity = self.projected.get(id(fn))
        if entity is not None or fn.entity_id is None:
            return entity
        return self.index.entity(fn.entity_id)


#: One shared empty IR, so a side without function occurrences memoizes
#: under one key rather than a fresh object per index.
_EMPTY_IR = SemanticIR()


def _named_signature_entities(ir: SemanticIR) -> dict[EntityId, CanonicalEntity]:
    return {
        eid: entity
        for eid, entity in SemanticIRIndex(ir)
        .entities_of_kind(EntityKind.FUNCTION)
        .items()
        if entity.return_type_spelling.is_present
    }


def _project(
    functions: list[Function], named: dict[EntityId, CanonicalEntity]
) -> list[CanonicalEntity]:
    """Each function's entity: its named occurrence with the declaration
    re-projected over it, or the legacy adapter's projection when the IR
    cannot name it.

    A named occurrence's signature facts are a copy of its declaration's,
    written by the same formula at the snapshot boundary. A caller that edits
    a loaded snapshot's ``Function`` leaves that copy stale, so the
    declaration this comparison pairs wins where it establishes a fact; the
    IR still owns identity and every fact the formula does not produce."""
    out: list[CanonicalEntity] = []
    for fn in functions:
        entity = named.get(fn.entity_id) if fn.entity_id is not None else None
        out.append(
            legacy_function_signature_entity(fn)
            if entity is None
            else with_declaration_signature(entity, fn)
        )
    return out


def function_signature_index(
    semantic_ir: SemanticIR | None, functions: Iterable[Function]
) -> FunctionSignatureIndex:
    """One side's index -- see the module docstring for the rule.

    *functions* are the objects the caller will pair; the adapter half is
    keyed by object. Inside a ``detection_memo_scope`` (one ``compare()``,
    which builds ~20 indexes over the same functions) each function's entity
    is projected once per IR; declarations must not be edited inside it.
    """
    covered = semantic_ir is not None and semantic_ir_covers_kind(
        semantic_ir, EntityKind.FUNCTION
    )
    ir_used = semantic_ir if covered and semantic_ir is not None else _EMPTY_IR
    # id(fn) -> (fn, entity); the function is held so its id stays unique.
    seen: dict[int, tuple[Function, CanonicalEntity]] = memoized(
        "function_signature_entities", ir_used, None, dict
    )
    functions = list(functions)
    missing = [
        f for f in functions if (hit := seen.get(id(f))) is None or hit[0] is not f
    ]
    if missing:
        named = memoized(
            "function_signature_named",
            ir_used,
            None,
            partial(_named_signature_entities, ir_used),
        )
        for f, entity in zip(missing, _project(missing, named), strict=True):
            seen[id(f)] = (f, entity)
    projected = {id(f): seen[id(f)][1] for f in functions}
    return FunctionSignatureIndex(index=SemanticIRIndex(ir_used), projected=projected)


def _decline(mangled: str, what: str, both: bool) -> None:
    record_declined(
        mangled,
        f"function {what} not established on {'both sides' if both else 'one side'}",
    )


def _decline_flag(mangled: str, what: str, old: object, new: object) -> None:
    """A flag every producer records (linkage, noexcept, virtual) that is not
    established on a side: declined, never read as ``False``."""
    _decline(mangled, what, old is None and new is None)


def _format_params(types: tuple[str, ...]) -> str:
    """A parameter list as display text -- each spelling already carries its
    own pointer/reference sigils."""
    return ", ".join(types) if types else "(none)"


def _return_changes(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
    is_llp64: bool,
) -> list[Change]:
    r_old, r_new = old.return_spelling, new.return_spelling
    if r_old is None or r_new is None:
        # Not established on a side: declined, not a confirmed "unchanged".
        _decline(mangled, "return type", r_old is None and r_new is None)
        return []
    # RD2-5: a stripped side reports "?"; that is unknown, not a change.
    if _type_unknown(r_old) or _type_unknown(r_new):
        return []
    if canonicalize_type_name(r_old) == canonicalize_type_name(r_new):
        return []
    # A pointee/by-value cv change (``char *`` -> ``const char *``) keeps the
    # return register and calling convention (ISSUE-29/52).
    if cv_qualifiers_only_differ(r_old, r_new):
        return []
    # A top-level by-value cv change is absent from the mangled name entirely
    # (Codex review, PR #582).
    if func_signature_cv_only_differ(r_old, r_new):
        return []
    # ABI-equivalent integer spellings (long -> long long on LP64).
    if _abi_equivalent_scalar(r_old, r_new, is_llp64):
        return []
    return [
        make_change(
            ChangeKind.FUNC_RETURN_CHANGED,
            symbol=mangled,
            name=name,
            old=r_old,
            new=r_new,
            entity_id=entity_id,
        )
    ]


def _param_differs(
    t_old: str, t_new: str, k_old: str, k_new: str, is_llp64: bool
) -> bool:
    """Whether two positionally-matched parameters differ in an ABI-relevant
    way. A kind is compared only when both producers established it (``""``
    is "not established", see ``semantic_ir_function_signature``); the
    spelling comparison still catches a real kind change otherwise, since a
    pointer/reference is rendered in the spelling itself."""
    if _type_unknown(t_old) or _type_unknown(t_new):
        return False
    if k_old and k_new and k_old != k_new:
        return True
    if canonicalize_type_name(t_old) == canonicalize_type_name(t_new):
        return False
    if cv_qualifiers_only_differ(t_old, t_new):
        return False
    if func_signature_cv_only_differ(t_old, t_new):
        return False
    return not _abi_equivalent_scalar(t_old, t_new, is_llp64)


def _params_changes(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
    params_unconfirmed: bool,
    is_llp64: bool,
) -> list[Change]:
    # RD2-5: a stripped symbols-only side's empty list is "unknown", not
    # "zero args". Otherwise a count change is always real, and an individual
    # "?" parameter never masks a real change on a known one.
    if params_unconfirmed:
        return []
    if old.param_types is None or new.param_types is None:
        _decline(
            mangled,
            "parameter list",
            old.param_types is None and new.param_types is None,
        )
        return []
    o_types, n_types = old.param_types, new.param_types
    o_kinds = old.param_kinds or ("",) * len(o_types)
    n_kinds = new.param_kinds or ("",) * len(n_types)
    if len(o_types) != len(n_types):
        changed = True
    else:
        changed = any(
            _param_differs(a, b, ka, kb, is_llp64)
            for a, b, ka, kb in zip(o_types, n_types, o_kinds, n_kinds)
        )
    if not changed:
        return []
    return [
        make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol=mangled,
            name=name,
            old=_format_params(o_types),
            new=_format_params(n_types),
            entity_id=entity_id,
        )
    ]


def _ref_qualifier_changes(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    if old.ref_qualifier is None or new.ref_qualifier is None:
        _decline(
            mangled,
            "ref-qualifier",
            old.ref_qualifier is None and new.ref_qualifier is None,
        )
        return []
    old_rq, new_rq = old.ref_qualifier, new.ref_qualifier
    if old_rq == new_rq:
        return []
    return [
        make_change(
            ChangeKind.FUNC_REF_QUAL_CHANGED,
            symbol=mangled,
            name=name,
            old=repr(old_rq),
            new=repr(new_rq),
            old_value=old_rq or "(none)",
            new_value=new_rq or "(none)",
            entity_id=entity_id,
        )
    ]


def _variadic_changes(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    # Tri-state: skipped when either side did not record variadicness.
    return bool_transition(
        old.is_variadic,
        new.is_variadic,
        mangled,
        skip_none=True,
        added=(
            ChangeKind.FUNC_VARIADIC_ADDED,
            f"Function became variadic (gained ...): {name}",
        ),
        added_values=("fixed-arity", "variadic"),
        removed=(
            ChangeKind.FUNC_VARIADIC_REMOVED,
            f"Function is no longer variadic (lost ...): {name}",
        ),
        removed_values=("variadic", "fixed-arity"),
        entity_id=entity_id,
    )


def _check_linkage_change(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    """Emit a change if the language linkage (extern \"C\" ↔ C++) was modified."""
    if old.is_extern_c is None or new.is_extern_c is None:
        _decline_flag(mangled, "linkage", old.is_extern_c, new.is_extern_c)
        return []
    if old.is_extern_c == new.is_extern_c:
        return []
    old_linkage = 'extern "C"' if old.is_extern_c else "C++"
    new_linkage = 'extern "C"' if new.is_extern_c else "C++"
    return [
        make_change(
            ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED,
            symbol=mangled,
            name=name,
            old=old_linkage,
            new=new_linkage,
            entity_id=entity_id,
        )
    ]


def _check_noexcept_change(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    """Emit a change if the noexcept specifier was added or removed."""
    if old.is_noexcept is None or new.is_noexcept is None:
        _decline_flag(mangled, "noexcept specifier", old.is_noexcept, new.is_noexcept)
        return []
    return bool_transition(
        old.is_noexcept,
        new.is_noexcept,
        mangled,
        added=(
            ChangeKind.FUNC_NOEXCEPT_ADDED,
            f"noexcept specifier added: {name}",
        ),
        removed=(
            ChangeKind.FUNC_NOEXCEPT_REMOVED,
            f"noexcept specifier removed: {name}",
        ),
        entity_id=entity_id,
    )


def _check_virtual_change(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    """Emit a change if the virtual specifier was added or removed."""
    if old.is_virtual is None or new.is_virtual is None:
        _decline_flag(mangled, "virtual specifier", old.is_virtual, new.is_virtual)
        return []
    return bool_transition(
        old.is_virtual,
        new.is_virtual,
        mangled,
        added=(ChangeKind.FUNC_VIRTUAL_ADDED, f"Function became virtual: {name}"),
        removed=(
            ChangeKind.FUNC_VIRTUAL_REMOVED,
            f"Function is no longer virtual: {name}",
        ),
        entity_id=entity_id,
    )


def _check_explicit_change(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    """Emit a change if the explicit specifier was added or removed.

    Tri-state: only fire when BOTH sides record explicit data. None means
    the dumper/loader couldn't determine it — typically an older snapshot
    that predates the field, or a Function/Destructor where ``explicit`` is
    N/A. Skipping in that case avoids false API_BREAK findings produced
    purely by snapshot schema evolution.
    """
    return bool_transition(
        old.is_explicit,
        new.is_explicit,
        mangled,
        skip_none=True,
        added=(
            ChangeKind.CTOR_EXPLICIT_ADDED,
            f"Constructor/conversion gained `explicit` specifier: {name}",
        ),
        added_values=("implicit", "explicit"),
        removed=(
            ChangeKind.CTOR_EXPLICIT_REMOVED,
            f"Constructor/conversion lost `explicit` specifier: {name}",
        ),
        removed_values=("explicit", "implicit"),
        entity_id=entity_id,
    )


def _check_contract_attributes_change(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    """Emit changes for gained/lost semantic contract attributes.

    Skips when either side did not capture attributes (None); an empty list
    means "captured, none present" and does participate. Calling-convention
    attribute flips (stdcall/regparm/ms_abi/...) route to the dedicated
    BREAKING ``CALLING_CONVENTION_CHANGED`` kind instead.
    """
    if old.contract_attributes is None or new.contract_attributes is None:
        return []
    old_attrs = set(old.contract_attributes)
    new_attrs = set(new.contract_attributes)
    if old_attrs == new_attrs:
        return []
    changes: list[Change] = []

    old_cc = {a for a in old_attrs if is_cc_attribute(a)}
    new_cc = {a for a in new_attrs if is_cc_attribute(a)}
    if old_cc != new_cc:
        changes.append(
            make_change(
                ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol=mangled,
                description=(
                    f"Calling-convention attribute changed for {name}: "
                    f"{', '.join(sorted(old_cc)) or '(default)'} → "
                    f"{', '.join(sorted(new_cc)) or '(default)'}"
                ),
                old_value=", ".join(sorted(old_cc)) or "(default)",
                new_value=", ".join(sorted(new_cc)) or "(default)",
                entity_id=entity_id,
            )
        )
        old_attrs -= old_cc
        new_attrs -= new_cc

    gained = sorted(new_attrs - old_attrs)
    lost = sorted(old_attrs - new_attrs)
    if gained:
        changes.append(
            make_change(
                ChangeKind.FUNC_CONTRACT_ATTRIBUTE_ADDED,
                symbol=mangled,
                name=name,
                detail=", ".join(gained),
                new_value=", ".join(gained),
                entity_id=entity_id,
            )
        )
    if lost:
        changes.append(
            make_change(
                ChangeKind.FUNC_CONTRACT_ATTRIBUTE_REMOVED,
                symbol=mangled,
                name=name,
                detail=", ".join(lost),
                old_value=", ".join(lost),
                entity_id=entity_id,
            )
        )
    return changes


def _check_exception_spec_change(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    """Emit a change if the dynamic exception specification changed.

    ``noexcept`` transitions keep their dedicated kinds; this covers the
    legacy ``throw(...)`` spellings only. Tri-state: None = not captured.
    """
    if old.exception_spec is None or new.exception_spec is None:
        return []
    if old.exception_spec == new.exception_spec:
        return []
    return [
        make_change(
            ChangeKind.FUNC_EXCEPTION_SPEC_CHANGED,
            symbol=mangled,
            name=name,
            old=old.exception_spec or "(none)",
            new=new.exception_spec or "(none)",
            entity_id=entity_id,
        )
    ]


def _check_vtable_index_change(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    """Emit a change when a persisting virtual method moved to another slot.

    ``vtable_index`` is modeled per-function; the per-type vtable array diff
    misses snapshots that carry indices but no reconstructed vtable list.
    Reuses TYPE_VTABLE_CHANGED — a moved slot IS a vtable reorder.
    """
    if old.vtable_index is None or new.vtable_index is None:
        return []
    if old.vtable_index == new.vtable_index:
        return []
    return [
        make_change(
            ChangeKind.TYPE_VTABLE_CHANGED,
            symbol=mangled,
            description=(
                f"vtable slot index changed for {name}: "
                f"{old.vtable_index} → {new.vtable_index}"
            ),
            old_value=str(old.vtable_index),
            new_value=str(new.vtable_index),
            entity_id=entity_id,
        )
    ]


def hidden_friend_changes(
    mangled: str,
    name: str,
    old: FunctionSignature,
    new: FunctionSignature,
    entity_id: EntityId | None,
) -> list[Change]:
    """Emit a change if the hidden-friend status transitioned.

    Hidden-friend transitions: an in-class ``friend`` declaration was
    added or removed across versions. Tri-state — skip when either
    side's snapshot did not record the flag (e.g. DWARF-only path or
    an older snapshot). Called both from the public-symbol pairing (a
    friend with an out-of-line definition, i.e. a real exported symbol)
    and from ``diff_inline_hidden_friends`` below for an inline-only
    friend that keeps the same mangled key on both sides but is HIDDEN
    on at least one — the public pairing never sees that case at all.

    ``caused_by_type`` carries the befriending class's qualified name (the
    side that is/was actually a hidden friend) so surface classification can
    key demotion off the *owner's* header origin rather than unconditionally
    retaining every hidden-friend finding (``surface.py``).
    """
    owner = new.hidden_friend_owner if new.is_hidden_friend else old.hidden_friend_owner
    return bool_transition(
        old.is_hidden_friend,
        new.is_hidden_friend,
        mangled,
        skip_none=True,
        added=(
            ChangeKind.HIDDEN_FRIEND_ADDED,
            f"Function became an in-class friend declaration: {name}",
        ),
        added_values=("non-friend", "hidden friend"),
        removed=(
            ChangeKind.HIDDEN_FRIEND_REMOVED,
            f"Function is no longer an in-class friend declaration: {name}",
        ),
        removed_values=("hidden friend", "non-friend"),
        caused_by_type=owner,
        entity_id=entity_id,
    )


def function_signature_changes(
    mangled: str,
    name: str,
    old_entity: CanonicalEntity | None,
    new_entity: CanonicalEntity | None,
    *,
    entity_id: EntityId | None,
    params_unconfirmed: bool = False,
    is_llp64: bool = False,
) -> list[Change]:
    """Every signature and qualifier change for one matched pair, in the
    order the findings were historically emitted (return, params,
    ref-qualifier, linkage, noexcept, virtual, hidden friend, explicit,
    variadic, contract attributes, exception spec, vtable slot)."""
    old, new = signature_of(old_entity), signature_of(new_entity)
    changes = _return_changes(mangled, name, old, new, entity_id, is_llp64)
    changes += _params_changes(
        mangled, name, old, new, entity_id, params_unconfirmed, is_llp64
    )
    changes += _ref_qualifier_changes(mangled, name, old, new, entity_id)
    for check in (
        _check_linkage_change,
        _check_noexcept_change,
        _check_virtual_change,
        hidden_friend_changes,
        _check_explicit_change,
    ):
        changes += check(mangled, name, old, new, entity_id)
    changes += _variadic_changes(mangled, name, old, new, entity_id)
    for check in (
        _check_contract_attributes_change,
        _check_exception_spec_change,
        _check_vtable_index_change,
    ):
        changes += check(mangled, name, old, new, entity_id)
    return changes
