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

"""The declaration store a ``SemanticIR`` owns (ADR-063 6B/Phase 10).

Every parsed declaration a snapshot carries -- its functions, variables,
records, enums, typedefs and constants, with the typedef/constant identity
sidecars -- lives here, inside the snapshot's ``SemanticIR``, in the order
its producer emitted them. There is no second copy on ``AbiSnapshot``: the
snapshot's ``functions=``/``types=``/... constructor arguments are builder
inputs that fill this store, and every reader goes through the IR.

The store is deliberately a plain, mutable container. Assembly code builds a
snapshot incrementally (filtering, backfilling, appending), and a frozen store
would force every such step to rebuild the whole IR; the IR itself stays
frozen as a binding, and the canonical per-occurrence facts
(``SemanticIR.occurrences``) stay immutable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .declarations import Function, Variable
from .entities import EnumType, RecordType
from .identity import EntityId

__all__ = ["DECLARATION_KINDS", "Declarations"]

#: Every field of :class:`Declarations`, in constructor order. The
#: ``AbiSnapshot`` builder inputs share these exact names.
DECLARATION_KINDS = (
    "functions",
    "variables",
    "types",
    "enums",
    "typedefs",
    "typedefs_qualified",
    "constants",
    "typedef_entity_ids",
    "constant_entity_ids",
)


@dataclass
class Declarations:
    """One snapshot's parsed declarations, in producer order."""

    functions: list[Function] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    types: list[RecordType] = field(default_factory=list)
    enums: list[EnumType] = field(default_factory=list)
    typedefs: dict[str, str] = field(default_factory=dict)
    typedefs_qualified: dict[str, str] = field(default_factory=dict)
    constants: dict[str, str] = field(default_factory=dict)
    typedef_entity_ids: dict[str, EntityId] = field(default_factory=dict)
    constant_entity_ids: dict[str, EntityId] = field(default_factory=dict)

    def copy(self) -> Declarations:
        """A shallow copy: new containers, the same declaration objects --
        what ``dataclasses.replace`` gave each list before the move."""
        return Declarations(
            functions=list(self.functions),
            variables=list(self.variables),
            types=list(self.types),
            enums=list(self.enums),
            typedefs=dict(self.typedefs),
            typedefs_qualified=dict(self.typedefs_qualified),
            constants=dict(self.constants),
            typedef_entity_ids=dict(self.typedef_entity_ids),
            constant_entity_ids=dict(self.constant_entity_ids),
        )


#: The order ``AbiSnapshot.__post_init__`` receives its builder inputs in:
#: the dataclass's own InitVar declaration order.
_BUILDER_INPUT_ORDER = (
    "functions",
    "variables",
    "types",
    "enums",
    "typedefs",
    "constants",
    "typedefs_qualified",
    "typedef_entity_ids",
    "constant_entity_ids",
)


def attach_declarations(snapshot: Any, builder_inputs: tuple[object, ...]) -> None:
    """``AbiSnapshot.__post_init__``'s first step: give the snapshot an IR
    that owns a declaration store built from its builder inputs.

    A builder input left ``None`` keeps whatever the given IR's store already
    holds for that kind (``dataclasses.replace`` of a snapshot, or a decoded
    document), and a new container is made either way, so two snapshots
    never share one store -- the same "new containers, same declaration
    objects" semantics ``replace`` had when these were plain fields.
    """
    from .semantic_ir import SemanticIR

    given = dict(zip(_BUILDER_INPUT_ORDER, builder_inputs, strict=True))
    ir = snapshot.__dict__.get("semantic_ir")
    if ir is None:
        ir = SemanticIR(canonical=False)
    base = ir.declarations
    values: dict[str, Any] = {
        kind: (
            value
            if (value := given[kind]) is not None
            else (getattr(base, kind) if base is not None else _empty(kind))
        )
        for kind in DECLARATION_KINDS
    }
    store = Declarations(**values)
    object.__setattr__(snapshot, "semantic_ir", ir.attached(store))


def _empty(kind: str) -> Any:
    return [] if kind in ("functions", "variables", "types", "enums") else {}


def reattach(current: Any, value: Any) -> Any:
    """What assigning *value* to a live snapshot's ``semantic_ir`` stores:
    *value* owning the snapshot's existing declarations when it brings none,
    and ``None`` read as "drop the canonical facts, keep the declarations"."""
    if value is None:
        return current.with_canonical(None)
    if value.declarations is None:
        return value.attached(current.declarations)
    return value


class _TransitionalDeclarationField:
    """Runtime-only read/write path from ``snapshot.<kind>`` to the store,
    kept while the test suite migrates to ``snapshot.declarations``
    (ADR-063 Phase 10, stage 3). The package itself no longer uses it --
    mypy rejects ``snapshot.functions`` there -- and it is deleted in
    stage 4."""

    def __init__(self, kind: str) -> None:
        self._kind = kind

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return None  # the InitVar's own default, which replace() reads
        return getattr(obj.declarations, self._kind)

    def __set__(self, obj: Any, value: Any) -> None:
        setattr(obj.declarations, self._kind, value)


def install_transitional_declaration_fields(cls: type) -> None:
    for kind in DECLARATION_KINDS:
        setattr(cls, kind, _TransitionalDeclarationField(kind))
