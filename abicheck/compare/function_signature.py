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
from typing import TYPE_CHECKING, Any

from ..diff_helpers import bool_transition, make_change
from ..diff_symbols_scalar import _abi_equivalent_scalar
from ..model.change_catalog.kinds import ChangeKind
from ..model.fact import Fact
from ..model.identity import EntityKind
from ..model.semantic_ir import CanonicalEntity, SemanticIR
from ..model.semantic_ir_index import SemanticIRIndex
from ..model.semantic_ir_legacy_adapter import (
    legacy_function_signature_occurrences,
    semantic_ir_covers_kind,
)
from ..name_classification import (
    canonicalize_type_name,
    cv_qualifiers_only_differ,
    func_signature_cv_only_differ,
)
from .declined_comparisons import record_declined

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model.declarations import Function
    from ..model.identity import EntityId

__all__ = [
    "FunctionSignature",
    "FunctionSignatureIndex",
    "function_signature_changes",
    "function_signature_index",
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


def function_signature_index(
    semantic_ir: SemanticIR | None, functions: Iterable[Function]
) -> FunctionSignatureIndex:
    """One side's index -- see the module docstring for the rule.

    *functions* are the objects the caller will pair; the adapter half is
    keyed by object.
    """
    functions = list(functions)
    covered = semantic_ir is not None and semantic_ir_covers_kind(
        semantic_ir, EntityKind.FUNCTION
    )
    index = SemanticIRIndex(
        semantic_ir if covered and semantic_ir is not None else SemanticIR()
    )
    named = {
        eid: entity
        for eid, entity in index.entities_of_kind(EntityKind.FUNCTION).items()
        if entity.return_type_spelling.is_present
    }
    unnamed = [f for f in functions if f.entity_id is None or f.entity_id not in named]
    ir, order = legacy_function_signature_occurrences(unnamed)
    projected = {
        id(f): ir.occurrences[occ] for f, occ in zip(unnamed, order, strict=True)
    }
    return FunctionSignatureIndex(index=index, projected=projected)


def _decline(mangled: str, what: str, both: bool) -> None:
    record_declined(
        mangled,
        f"function {what} not established on {'both sides' if both else 'one side'}",
    )


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


def function_signature_changes(
    mangled: str,
    name: str,
    old_entity: CanonicalEntity | None,
    new_entity: CanonicalEntity | None,
    *,
    entity_id: EntityId | None,
    params_unconfirmed: bool = False,
    is_llp64: bool = False,
) -> tuple[list[Change], list[Change], list[Change]]:
    """The return/params changes, the ref-qualifier changes, and the
    variadic changes for one matched pair -- returned in three groups so the
    caller can keep its historical finding order (return, params,
    ref-qualifier, ..., variadic)."""
    old, new = signature_of(old_entity), signature_of(new_entity)
    head = _return_changes(mangled, name, old, new, entity_id, is_llp64)
    head += _params_changes(
        mangled, name, old, new, entity_id, params_unconfirmed, is_llp64
    )
    return (
        head,
        _ref_qualifier_changes(mangled, name, old, new, entity_id),
        _variadic_changes(mangled, name, old, new, entity_id),
    )
