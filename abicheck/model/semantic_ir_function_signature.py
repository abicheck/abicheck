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

"""A function's per-position signature facts on its ``SemanticIR``
occurrences (ADR-063 6B, function-signature cohort).

``FUNC_RETURN_CHANGED``/``FUNC_PARAMS_CHANGED``/``FUNC_REF_QUAL_CHANGED``/
``FUNC_VARIADIC_ADDED``/``FUNC_VARIADIC_REMOVED`` read
``CanonicalEntity.return_type_spelling``/``parameter_type_spellings``/
``parameter_kinds``/``ref_qualifier``/``is_variadic``. This module is the one
formula from a parsed ``Function`` to those facts: the normalizer
(``extract``), the legacy adapter and the comparison-time re-projection
(:func:`with_declaration_signature`) all use it, so no two paths read the
same declaration differently.

There is no load-time fill. A document written before ``semantic_ir``
version 3 carries none of these facts, and the comparison indexes project a
function the IR cannot speak for through the legacy adapter -- and re-project
every paired declaration over its occurrence anyway, so a persisted copy is
never the authority over the declaration it was copied from.
"""

from __future__ import annotations

import dataclasses
import functools
from typing import Any

from .availability import FactStatus
from .declarations import Function
from .fact import Fact
from .semantic_ir import CanonicalEntity
from .semantic_ir_declaration_facts import (
    declaration_facts_from_inputs,
    declaration_inputs,
)

__all__ = [
    "LEGACY_SIGNATURE_DIAGNOSTIC",
    "overlay_established_facts",
    "SIGNATURE_FIELDS",
    "function_signature_entity",
    "function_signature_facts",
    "with_declaration_signature",
]

#: The ``CanonicalEntity`` fields this cohort owns, in declaration order.
SIGNATURE_FIELDS = (
    "return_type_spelling",
    "parameter_type_spellings",
    "parameter_kinds",
    "ref_qualifier",
    "is_variadic",
    # The function-qualifier cohort (same document version, same fill).
    "is_extern_c",
    "is_noexcept",
    "is_virtual",
    "is_explicit",
    "is_hidden_friend",
    "hidden_friend_owner",
    "contract_attributes",
    "exception_spec",
    "vtable_index",
    "is_override",
    "is_inline",
    "is_deleted",
    "deleted_from_dwarf",
    # The parameter cohort.
    "parameter_names",
    "parameter_defaults",
    "parameter_pointer_depths",
    "parameter_restrict",
    "parameter_va_list",
    "return_pointer_depth",
)

#: Stamped on a signature fact decoded from a pre-v3 ``semantic_ir`` document.
LEGACY_SIGNATURE_DIAGNOSTIC = (
    "function signature not recorded before semantic_ir document version 3"
)


def _param_kind(param: Any) -> str:
    """The parameter's ``ParamKind`` value, or ``""`` when its producer did
    not establish it (``compare_facts``' "incomplete"/"unsupported" sides)."""
    fact = param.kind_fact
    if fact is None or fact.status not in (FactStatus.PRESENT, FactStatus.PARTIAL):
        return ""
    kind = fact.value if fact.value is not None else param.kind
    return str(getattr(kind, "value", kind))


@functools.lru_cache(maxsize=8192, typed=True)
def _shared_present(value: Any) -> Fact[Any]:
    return Fact.present(value)


def _present(value: Any) -> Fact[Any]:
    """``Fact.present(value)``, one shared instance per hashable value.

    A signature's facts repeat across a library (``"int"``, ``()``, depth
    ``0``, ``("",)``...), and ``Fact`` is frozen, so the comparison-time
    projection hands out shared instances instead of allocating ~25 per
    function. ``typed`` keeps ``0``/``False`` apart at the top level; no
    signature tuple mixes ``bool`` with ``int``. An unhashable value (a
    producer that left a list) is allocated as before."""
    try:
        return _shared_present(value)
    except TypeError:
        return Fact.present(value)


def _optional(value: Any) -> Fact[Any]:
    """``PRESENT`` for a captured value, ``NOT_COLLECTED`` for ``None``."""
    return Fact.not_collected() if value is None else _present(value)


def _signature_inputs(fn: Function) -> tuple[Any, ...]:
    """Every value of *fn* the signature facts are built from, already
    reduced to plain hashable data, in :func:`_facts_from_inputs`' order.

    The split is what makes caching exact: :func:`_facts_from_inputs` sees
    nothing but this tuple, so two functions with equal inputs have equal
    facts by construction. Each position has one value type (never ``bool``
    in one function and ``int`` in another), so tuple equality cannot
    conflate ``True`` with ``1``."""

    def _flag(fact: Any) -> str:
        """A per-parameter ``Fact[bool]`` as ``"true"``/``"false"``, or ``""``
        when its producer did not establish it (``compare_facts``'
        non-available sides)."""
        if fact is None or fact.status not in (FactStatus.PRESENT, FactStatus.PARTIAL):
            return ""
        return "true" if fact.value else "false"

    params = fn.params
    attrs = fn.contract_attributes
    return (
        declaration_inputs(fn),
        bool(fn.is_extern_c),
        bool(fn.is_noexcept),
        bool(fn.is_virtual),
        fn.is_explicit,
        fn.is_hidden_friend,
        fn.hidden_friend_owner,
        None if attrs is None else tuple(attrs),
        fn.exception_spec,
        fn.vtable_index,
        fn.is_override,
        bool(fn.is_inline),
        bool(fn.is_deleted),
        bool(fn.deleted_from_dwarf),
        tuple(p.name or "" for p in params),
        tuple(p.default for p in params),
        tuple(int(p.pointer_depth) for p in params),
        tuple(_flag(p.is_restrict_fact) for p in params),
        tuple(_flag(p.is_va_list_fact) for p in params),
        int(fn.return_pointer_depth),
        fn.return_type,
        tuple(p.type for p in params),
        tuple(_param_kind(p) for p in params),
        fn.ref_qualifier or "",
        None if fn.is_variadic is None else bool(fn.is_variadic),
    )


def _facts_from_inputs(inputs: tuple[Any, ...]) -> dict[str, Any]:
    (
        decl,
        extern_c,
        noexcept,
        virtual,
        explicit,
        hidden_friend,
        friend_owner,
        attrs,
        exception_spec,
        vtable_index,
        override,
        inline,
        deleted,
        deleted_from_dwarf,
        names,
        defaults,
        depths,
        restrict,
        va_list,
        return_depth,
        return_type,
        types,
        kinds,
        ref_qualifier,
        variadic,
    ) = inputs
    return {
        # Declaration facts every function also carries (deprecation,
        # access).
        **declaration_facts_from_inputs(decl),
        "is_extern_c": _present(extern_c),
        "is_noexcept": _present(noexcept),
        "is_virtual": _present(virtual),
        "is_explicit": _optional(explicit),
        "is_hidden_friend": _optional(hidden_friend),
        "hidden_friend_owner": _optional(friend_owner),
        "contract_attributes": _optional(attrs),
        "exception_spec": _optional(exception_spec),
        "vtable_index": _optional(vtable_index),
        "is_override": _optional(override),
        "is_inline": _present(inline),
        "is_deleted": _present(deleted),
        "deleted_from_dwarf": _present(deleted_from_dwarf),
        "parameter_names": _present(names),
        "parameter_defaults": _present(defaults),
        "parameter_pointer_depths": _present(depths),
        "parameter_restrict": _present(restrict),
        "parameter_va_list": _present(va_list),
        "return_pointer_depth": _present(return_depth),
        "return_type_spelling": _present(return_type),
        "parameter_type_spellings": _present(types),
        "parameter_kinds": _present(kinds),
        "ref_qualifier": _present(ref_qualifier),
        "is_variadic": _optional(variadic),
    }


@functools.lru_cache(maxsize=16384)
def _cached_facts(inputs: tuple[Any, ...]) -> tuple[tuple[str, Any], ...]:
    return tuple(_facts_from_inputs(inputs).items())


@functools.lru_cache(maxsize=16384)
def _cached_entity(inputs: tuple[Any, ...]) -> CanonicalEntity:
    return CanonicalEntity(
        canonical_spelling=Fact.not_collected(),
        **dict(_cached_facts(inputs)),
        _trusted=True,
    )


def function_signature_entity(fn: Function) -> CanonicalEntity:
    """*fn*'s projected signature entity (the legacy adapter's payload),
    shared across functions with equal :func:`_signature_inputs`: an entity
    is frozen and carries no identity of its own."""
    inputs = _signature_inputs(fn)
    try:
        return _cached_entity(inputs)
    except TypeError:
        return CanonicalEntity(
            canonical_spelling=Fact.not_collected(),
            **_facts_from_inputs(inputs),
            _trusted=True,
        )


def function_signature_facts(fn: Function) -> dict[str, Any]:
    """Every :data:`SIGNATURE_FIELDS` fact for *fn*, keyed by field name.

    Built once per distinct :func:`_signature_inputs` tuple: a library's
    functions repeat signatures heavily, and ``Fact`` is frozen, so equal
    inputs share instances. An unhashable input (a producer that left a
    list where a value belongs) is built uncached."""
    inputs = _signature_inputs(fn)
    try:
        return dict(_cached_facts(inputs))
    except TypeError:
        return _facts_from_inputs(inputs)


def with_declaration_signature(
    entity: CanonicalEntity, fn: Function
) -> CanonicalEntity:
    """*entity* with every signature fact *fn* establishes taken from *fn*;
    *entity* itself when they already agree.

    The occurrence's copy goes stale when *fn* is edited after load, so the
    declaration wins wherever it establishes a fact; a fact *fn* does not
    establish keeps the occurrence's own value (a producer's
    ``FAILED``/``UNSUPPORTED``)."""
    return overlay_established_facts(entity, function_signature_facts(fn))


def overlay_established_facts(
    entity: CanonicalEntity, facts: dict[str, Fact[Any]]
) -> CanonicalEntity:
    """*entity* with each established fact of *facts* replacing its own
    when the two differ; *entity* itself when nothing differs."""
    updates: dict[str, Any] = {}
    for name, value in facts.items():
        if value.status is FactStatus.NOT_COLLECTED:
            continue
        held = getattr(entity, name)
        if held.status is not value.status or held.value != value.value:
            updates[name] = value
    return dataclasses.replace(entity, **updates) if updates else entity
