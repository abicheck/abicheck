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
(``extract``), the legacy adapter and the load-time fill
(:func:`sync_snapshot_function_signatures`) all use it, so no two paths read
the same declaration differently.

The fill mirrors ``semantic_ir_record_layout``: a document written before
``semantic_ir`` version 3 carries none of these facts, so the load path fills
an occurrence's ``NOT_COLLECTED`` facts from the snapshot's own ``Function``
of the same identity. An established fact is never overwritten, and two
functions under one identity with different signatures fill nothing.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any

from .availability import FactStatus
from .declarations import Function
from .fact import Fact
from .identity import EntityId, EntityKind
from .occurrence import OccurrenceId
from .semantic_ir import CanonicalEntity, SemanticIR
from .semantic_ir_declaration_facts import declaration_facts

__all__ = [
    "LEGACY_SIGNATURE_DIAGNOSTIC",
    "overlay_established_facts",
    "SIGNATURE_FIELDS",
    "function_signature_facts",
    "sync_snapshot_function_signatures",
    "with_declaration_signature",
    "with_function_signatures",
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


def _optional(value: Any) -> Fact[Any]:
    """``PRESENT`` for a captured value, ``NOT_COLLECTED`` for ``None``."""
    return Fact.not_collected() if value is None else Fact.present(value)


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
        "is_extern_c": Fact.present(bool(fn.is_extern_c)),
        "is_noexcept": Fact.present(bool(fn.is_noexcept)),
        "is_virtual": Fact.present(bool(fn.is_virtual)),
        "is_explicit": _optional(fn.is_explicit),
        "is_hidden_friend": _optional(fn.is_hidden_friend),
        "hidden_friend_owner": _optional(fn.hidden_friend_owner),
        "contract_attributes": _optional(None if attrs is None else tuple(attrs)),
        "exception_spec": _optional(fn.exception_spec),
        "vtable_index": _optional(fn.vtable_index),
        "is_override": _optional(fn.is_override),
        "is_inline": Fact.present(bool(fn.is_inline)),
        "is_deleted": Fact.present(bool(fn.is_deleted)),
        "deleted_from_dwarf": Fact.present(bool(fn.deleted_from_dwarf)),
        "parameter_names": Fact.present(tuple(p.name or "" for p in fn.params)),
        "parameter_defaults": Fact.present(tuple(p.default for p in fn.params)),
        "parameter_pointer_depths": Fact.present(
            tuple(int(p.pointer_depth) for p in fn.params)
        ),
        "parameter_restrict": Fact.present(
            tuple(_flag(p.is_restrict_fact) for p in fn.params)
        ),
        "parameter_va_list": Fact.present(
            tuple(_flag(p.is_va_list_fact) for p in fn.params)
        ),
        "return_pointer_depth": Fact.present(int(fn.return_pointer_depth)),
        "return_type_spelling": Fact.present(fn.return_type),
        "parameter_type_spellings": Fact.present(tuple(p.type for p in fn.params)),
        "parameter_kinds": Fact.present(tuple(_param_kind(p) for p in fn.params)),
        "ref_qualifier": Fact.present(fn.ref_qualifier or ""),
        "is_variadic": (
            Fact.not_collected()
            if fn.is_variadic is None
            else Fact.present(bool(fn.is_variadic))
        ),
    }


def with_declaration_signature(
    entity: CanonicalEntity, fn: Function
) -> CanonicalEntity:
    """*entity* with every signature fact *fn* establishes taken from *fn*;
    *entity* itself when they already agree.

    The comparison-time counterpart of :func:`with_function_signatures`:
    that fill only writes ``NOT_COLLECTED`` facts, so it cannot repair a copy
    made stale by a later edit of *fn*. A fact *fn* does not establish keeps
    the occurrence's own value (a producer's ``FAILED``/``UNSUPPORTED``)."""
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


def with_function_signatures(
    ir: SemanticIR, functions: Iterable[Function]
) -> SemanticIR:
    """*ir* with every function occurrence's missing signature facts filled
    from *functions*; *ir* itself when nothing changes."""
    by_id: dict[EntityId, dict[str, Any] | None] = {}
    for fn in functions:
        eid = fn.entity_id
        if eid is None or eid.kind is not EntityKind.FUNCTION:
            continue
        facts = function_signature_facts(fn)
        if eid in by_id and by_id[eid] != facts:
            by_id[eid] = None  # two different signatures under one identity
        else:
            by_id.setdefault(eid, facts)
    if not by_id:
        return ir
    changed: dict[OccurrenceId, CanonicalEntity] = {}
    for occ_id, entity in ir.occurrences.items():
        known = by_id.get(occ_id.entity_id)
        if known is None:
            continue
        # Only "never collected": a FAILED/UNSUPPORTED fact is the producer's
        # own statement and is not overridden by another source.
        updates = {
            name: value
            for name, value in known.items()
            if getattr(entity, name).status is FactStatus.NOT_COLLECTED
            and value.status is not FactStatus.NOT_COLLECTED
        }
        if updates:
            changed[occ_id] = dataclasses.replace(entity, **updates)
    if not changed:
        return ir
    return dataclasses.replace(ir, occurrences={**ir.occurrences, **changed})


def sync_snapshot_function_signatures(snapshot: object) -> None:
    """Fill *snapshot*'s ``semantic_ir`` function signatures from its own
    functions -- see ``semantic_ir_record_layout.sync_snapshot_record_layout``
    for where and why this runs."""
    ir = getattr(snapshot, "semantic_ir", None)
    if ir is None:
        return
    synced = with_function_signatures(ir, snapshot.declarations.functions)  # type: ignore[attr-defined]
    if synced is not ir:
        snapshot.semantic_ir = synced  # type: ignore[attr-defined]
