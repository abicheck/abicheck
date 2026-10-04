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
from .semantic_ir_declaration_facts import declaration_facts

__all__ = [
    "LEGACY_SIGNATURE_DIAGNOSTIC",
    "overlay_established_facts",
    "SIGNATURE_FIELDS",
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


def function_signature_facts(fn: Function) -> dict[str, Any]:
    """Every :data:`SIGNATURE_FIELDS` fact for *fn*, keyed by field name."""

    def _flag(fact: Any) -> str:
        """A per-parameter ``Fact[bool]`` as ``"true"``/``"false"``, or ``""``
        when its producer did not establish it (``compare_facts``' non-available
        sides)."""
        if fact is None or fact.status not in (FactStatus.PRESENT, FactStatus.PARTIAL):
            return ""
        return "true" if fact.value else "false"

    attrs = fn.contract_attributes
    return {
        # Declaration facts every function also carries (deprecation,
        # access) -- one fill writes both groups.
        **declaration_facts(fn),
        "is_extern_c": _present(bool(fn.is_extern_c)),
        "is_noexcept": _present(bool(fn.is_noexcept)),
        "is_virtual": _present(bool(fn.is_virtual)),
        "is_explicit": _optional(fn.is_explicit),
        "is_hidden_friend": _optional(fn.is_hidden_friend),
        "hidden_friend_owner": _optional(fn.hidden_friend_owner),
        "contract_attributes": _optional(None if attrs is None else tuple(attrs)),
        "exception_spec": _optional(fn.exception_spec),
        "vtable_index": _optional(fn.vtable_index),
        "is_override": _optional(fn.is_override),
        "is_inline": _present(bool(fn.is_inline)),
        "is_deleted": _present(bool(fn.is_deleted)),
        "deleted_from_dwarf": _present(bool(fn.deleted_from_dwarf)),
        "parameter_names": _present(tuple(p.name or "" for p in fn.params)),
        "parameter_defaults": _present(tuple(p.default for p in fn.params)),
        "parameter_pointer_depths": _present(
            tuple(int(p.pointer_depth) for p in fn.params)
        ),
        "parameter_restrict": _present(
            tuple(_flag(p.is_restrict_fact) for p in fn.params)
        ),
        "parameter_va_list": _present(
            tuple(_flag(p.is_va_list_fact) for p in fn.params)
        ),
        "return_pointer_depth": _present(int(fn.return_pointer_depth)),
        "return_type_spelling": _present(fn.return_type),
        "parameter_type_spellings": _present(tuple(p.type for p in fn.params)),
        "parameter_kinds": _present(tuple(_param_kind(p) for p in fn.params)),
        "ref_qualifier": _present(fn.ref_qualifier or ""),
        "is_variadic": (
            Fact.not_collected()
            if fn.is_variadic is None
            else _present(bool(fn.is_variadic))
        ),
    }


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
