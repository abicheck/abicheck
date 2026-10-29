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

"""Retypes between ABI-identical fundamental types (schema v69).

``FUNC_PARAMS_CHANGED``, ``FUNC_RETURN_CHANGED``, ``TYPE_FIELD_TYPE_CHANGED``
and ``VAR_TYPE_CHANGED`` compare the *spelled* type of a slot. A change of
spelling between two types the compiler passes, stores and returns identically
(``long`` -> ``long long`` on LP64, ``char`` -> ``signed char``,
``enum E`` -> ``unsigned int``, ``__int128`` -> ``unsigned __int128``) is not
an ABI change, so it must not read as a break.

This pass runs once per comparison, after the detectors. It re-reads, for each
such change, the two declarations that *same-symbol* pairing matched and
compares only the slots whose spelling differs. A change is re-classified as
``ABI_EQUIVALENT_TYPE_CHANGED`` (compatible with risk: callers still see a
different declared type, which matters for overloads, templates and function
pointer types) only when **every** differing slot carries an ABI fact on both
sides and the facts are equal (:func:`abicheck.model.abi_fundamental.
abi_equivalent`), and nothing else about the slot differs (the pointer kind).

It never raises a verdict. A slot without a fact on either side (a pointer, a
struct, a pre-v69 document, a producer that did not record one) keeps its
break, as before. A replacement whose old and new symbol differ is not
reclassified here: the old symbol is gone, which is a break whatever the
types.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import cast

from ..model.abi_fundamental import AbiFundamental, abi_equivalent
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.declarations import Function, Param, Variable
from ..model.entities import RecordType, TypeField
from ..model.fact import Fact
from ..model.snapshot import AbiSnapshot
from .fact_comparison import FactComparison, compare_facts

__all__ = ["refine_abi_equivalent_retypes"]

_RETYPE_KINDS = frozenset(
    {
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        # The DWARF layout tier's spelling of a field retype (type_layout_diff):
        # its change names the record and the field the same way.
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.VAR_TYPE_CHANGED,
        ChangeKind.TYPEDEF_BASE_CHANGED,
    }
)


#: ``compare_facts``'s ``default``. It is read only for a PARTIAL fact's value,
#: which :func:`_comparable_equal` rejects, so no value is ever taken from it.
_UNREAD_DEFAULT: AbiFundamental = cast(AbiFundamental, None)


def _comparable_equal(comparison: FactComparison[AbiFundamental]) -> bool:
    """Whether a comparable, fully PRESENT pair of slots is ABI-equal."""
    if not comparison.is_comparable or comparison.degraded:
        return False
    if comparison.old_value is None or comparison.new_value is None:
        return False
    return abi_equivalent(comparison.old_value, comparison.new_value)


def _params_retyped_only(
    old_params: Sequence[Param], new_params: Sequence[Param]
) -> bool:
    """Whether the parameter lists differ only in slots ABI-equal by fact.

    Requires equal length, and that at least one slot's spelling differs (a
    change with no differing spelling is not a retype and is left alone).
    """
    if len(old_params) != len(new_params) or not old_params:
        return False
    differing = False
    for old_p, new_p in zip(old_params, new_params):
        if old_p.kind != new_p.kind:
            return False
        if old_p.type == new_p.type:
            continue
        if not _comparable_equal(
            compare_facts(old_p.type_abi_fact, new_p.type_abi_fact, _UNREAD_DEFAULT)
        ):
            return False
        differing = True
    return differing


def _function_retype(change: Change, old: AbiSnapshot, new: AbiSnapshot) -> bool:
    old_fn: Function | None = old.function_map.get(change.symbol)
    new_fn: Function | None = new.function_map.get(change.symbol)
    if old_fn is None or new_fn is None:
        return False
    if change.kind is ChangeKind.FUNC_RETURN_CHANGED:
        return old_fn.return_type != new_fn.return_type and _comparable_equal(
            compare_facts(
                old_fn.return_type_abi_fact,
                new_fn.return_type_abi_fact,
                _UNREAD_DEFAULT,
            )
        )
    return _params_retyped_only(old_fn.params, new_fn.params)


def _field_retype(change: Change, old: AbiSnapshot, new: AbiSnapshot) -> bool:
    # TYPE_FIELD_TYPE_CHANGED names the record in ``symbol``; the DWARF layout tier's
    # STRUCT_FIELD_TYPE_CHANGED names ``record::field`` there.
    name = change.qualified_name or change.symbol
    if change.kind is ChangeKind.STRUCT_FIELD_TYPE_CHANGED and "::" in name:
        name = name.rsplit("::", 1)[0]
    old_rec: RecordType | None = old.type_by_name(name)
    new_rec: RecordType | None = new.type_by_name(name)
    if old_rec is None or new_rec is None or not change.field_name:
        return False
    old_f = _field_named(old_rec, change.field_name)
    new_f = _field_named(new_rec, change.field_name)
    if old_f is None or new_f is None or old_f.type == new_f.type:
        return False
    return _comparable_equal(
        compare_facts(old_f.type_abi_fact, new_f.type_abi_fact, _UNREAD_DEFAULT)
    )


def _field_named(record: RecordType, field_name: str) -> TypeField | None:
    for fld in record.fields:
        if fld.name == field_name:
            return fld
    return None


def _variable_retype(change: Change, old: AbiSnapshot, new: AbiSnapshot) -> bool:
    old_var: Variable | None = old.var_by_mangled(change.symbol)
    new_var: Variable | None = new.var_by_mangled(change.symbol)
    if old_var is None or new_var is None or old_var.type == new_var.type:
        return False
    return _comparable_equal(
        compare_facts(old_var.type_abi_fact, new_var.type_abi_fact, _UNREAD_DEFAULT)
    )


def _typedef_fact(snap: AbiSnapshot, bare: str) -> Fact[AbiFundamental] | None:
    """The target fact of the one typedef named *bare* (any scope), or None.

    A bare name shared by two typedefs is not one slot, so it reads no fact.
    """
    names = {
        q
        for q in (*snap.declarations.typedefs_qualified, *snap.declarations.typedefs)
        if q.rsplit("::", 1)[-1] == bare
    }
    if len(names) != 1:
        return None
    (name,) = names
    return (snap.typedef_abi_facts or {}).get(name)


def _typedef_retype(change: Change, old: AbiSnapshot, new: AbiSnapshot) -> bool:
    if change.old_value == change.new_value:
        return False
    return _comparable_equal(
        compare_facts(
            _typedef_fact(old, change.symbol),
            _typedef_fact(new, change.symbol),
            _UNREAD_DEFAULT,
        )
    )


def _retyped(change: Change, old: AbiSnapshot, new: AbiSnapshot) -> bool:
    if change.kind is ChangeKind.TYPEDEF_BASE_CHANGED:
        return _typedef_retype(change, old, new)
    if change.kind in (ChangeKind.FUNC_PARAMS_CHANGED, ChangeKind.FUNC_RETURN_CHANGED):
        return _function_retype(change, old, new)
    if change.kind in (
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
    ):
        return _field_retype(change, old, new)
    return _variable_retype(change, old, new)


def refine_abi_equivalent_retypes(
    changes: list[Change], old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """*changes* with each provably ABI-equal retype re-kinded.

    Every other change, and every change this pass cannot prove, passes through
    unchanged and in order. The re-kinded change keeps its symbol, names and
    old/new spellings; only its kind and description change.
    """
    result: list[Change] = []
    for change in changes:
        if change.kind in _RETYPE_KINDS and _retyped(change, old, new):
            result.append(
                replace(
                    change,
                    kind=ChangeKind.ABI_EQUIVALENT_TYPE_CHANGED,
                    description=(
                        f"Type spelling changed between ABI-identical types: "
                        f"{change.symbol} {change.old_value} -> {change.new_value} "
                        f"(same size, alignment and register class; a source-level "
                        f"change only)"
                    ),
                )
            )
        else:
            result.append(change)
    return result
