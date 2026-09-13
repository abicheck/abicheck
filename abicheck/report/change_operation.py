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

"""Which display dimensions does a finding belong to?

One classifier for both canonical dimensions, shared by ``ShowOnlyFilter``'s
``--view show=`` tokens and the JSON report's per-finding ``entity`` and
``operation`` fields, so the two cannot drift apart.

This module is main's `report/change_operation.py` kept as the *home* with
plan slice 7o's implementation inside it. main had split the old classifier
out of ``reporter_markdown.py`` for two reasons -- that file carries a
``no_growth`` baseline, and "classifying a kind is not that module's
rendering job in any case" -- and both reasons survive the change of
implementation, so the split is honoured rather than undone.

What did change is where the answer comes from. The superseded classifier
read the kind's *name*: a suffix rule with a growing override table for
operation, a prefix table plus an exact-match escape list for entity. Both
are now read off ``ChangeKindMeta`` -- the same single registration that
declares a kind's verdict and impact. That is not a refactor: the name-based
element table matched nothing at all for 238 of the 407 kinds, and the
suffix rule conflated *what was observed* with *which evidence saw it*, so
``func_added_elf_only`` was reported as a removal (main's own
``test_evidence_tier_registry_parity.py`` was written against that defect;
it passes unchanged here, because a declared dimension cannot make that
mistake).
"""

from __future__ import annotations

from ..change_registry import REGISTRY, ChangeEntity, ChangeOperation

#: CLI ``--view show=`` element tokens -> the canonical
#: :class:`~abicheck.model.change_catalog.registry.ChangeEntity` each names.
#: The plural CLI spellings and the ``elf`` alias for ``BINARY`` are kept
#: exactly as the vocabulary has always spelled them (plan slice 7o's
#: acceptance bar: the old user task keeps a simple, supported invocation);
#: ``build``, ``source`` and ``analysis`` are new tokens for the three
#: dimensions the superseded name-prefix table could not express at all.
ELEMENT_TOKEN_ENTITIES: dict[str, ChangeEntity] = {
    "functions": ChangeEntity.FUNCTION,
    "variables": ChangeEntity.VARIABLE,
    "types": ChangeEntity.TYPE,
    "enums": ChangeEntity.ENUM,
    "elf": ChangeEntity.BINARY,
    "binary": ChangeEntity.BINARY,
    "build": ChangeEntity.BUILD,
    "source": ChangeEntity.SOURCE,
    "analysis": ChangeEntity.ANALYSIS,
}

#: CLI ``--view show=`` action tokens -> the canonical
#: :class:`~abicheck.model.change_catalog.registry.ChangeOperation`. The CLI
#: has always spelled ``ChangeOperation.MODIFIED`` "changed"; that stays.
ACTION_TOKEN_OPERATIONS: dict[str, ChangeOperation] = {
    "added": ChangeOperation.ADDED,
    "removed": ChangeOperation.REMOVED,
    "changed": ChangeOperation.MODIFIED,
}


def entity_for_kind(kind_val: str) -> str | None:
    """The declared :class:`ChangeEntity` value for *kind_val*, or ``None``.

    Plan slice 7o: reads the one registration in the change catalog. The
    superseded implementation was a table of name *prefixes*
    (``func_``/``var_``/``type_``/``enum_``/``soname_``...) plus an
    exact-match escape list for the kinds whose names those prefixes miss,
    maintained beside the catalog rather than in it -- which is why 238 of
    the 407 kinds matched no element at all and were invisible to every
    ``--view show=`` element token. ``None`` means the kind is not in the
    registry (a hand-built ``Change`` in a test), never "unclassified": a
    registered entry without a declared entity fails at import time.
    """
    entity = REGISTRY.entity_for(kind_val)
    return entity.value if entity is not None else None


def entity_for_change(change: object, kind_val: str) -> str | None:
    """The display entity for one *finding*, not merely for its kind.

    Identical to :func:`entity_for_kind` for every kind whose entity is a
    property of the kind itself. For a *polymorphic* kind -- one a detector
    emits for more than one entity type -- the catalog declares which of the
    finding's own attributes states the concrete entity
    (``ChangeKindMeta.entity_from_field``), and this reads it. Both
    ``experimental_graduated`` and
    ``experimental_removed_without_replacement`` are emitted for functions
    and types alike, so declaring either statically excluded a graduated
    *function* from ``--view show=functions`` and serialized a wrong
    ``entity`` (Codex review, PR #1284).

    Falls back to the declared entity whenever the named field is absent or
    does not spell a known :class:`ChangeEntity` -- an unresolvable
    dimension would drop the finding from every element filter, which is a
    worse failure than a coarse one.
    """
    declared = entity_for_kind(kind_val)
    field_name = REGISTRY.entity_from_field_for(kind_val)
    if field_name is None:
        return declared
    stated = getattr(change, field_name, None)
    if not isinstance(stated, str):
        return declared
    try:
        return ChangeEntity(stated).value
    except ValueError:
        return declared


def operation_for_kind(kind_val: str) -> str:
    """Classify a ``ChangeKind.value`` into "added"/"removed"/"modified".

    Plan slice 7o: this reads ``ChangeKindMeta.operation`` -- the same single
    registration that declares the kind's verdict and impact -- instead of
    matching name suffixes (``*_added``/``*_removed``) with a 30-entry
    override table for every kind whose name ends in ``_added`` while naming
    a trait *gained by a persisting entity* (``func_noexcept_added``,
    ``type_field_added``, ``virtual_method_added``, ...). That override table
    was itself the evidence that a name is not the fact.

    Shared, as before, between the display filter's action tokens and the
    JSON report's per-finding ``operation`` field, so the two cannot drift.
    An unregistered kind reads "modified", the same neutral answer the
    superseded suffix rule gave a name it did not recognize.
    """
    operation = REGISTRY.operation_for(kind_val)
    return operation.value if operation is not None else ChangeOperation.MODIFIED.value
