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

"""Dependency scoping for the snapshot's flat constant/typedef maps.

``dumper_scoping.scope_snapshot_excluding_dependencies`` filters functions,
variables, records and enums by their declaring header, but its closing
``dataclasses.replace`` used to carry ``constants``/``constant_entity_ids``/
``typedefs``/``typedefs_qualified``/``typedef_entity_ids`` over verbatim --
the same omission PR #1001 closed for ``semantic_ir``. A constant declared
only in a ``--exclude-header``-ed (or toolchain) header therefore survived
scoping and gated a comparison whose own report said that header's
declarations were not observed. It could not be fixed at that call site,
because the maps carried no header at all; producers now attach one
(:mod:`abicheck.model.declaration_headers`), and this module applies the
same header-origin rule the other kinds already follow:

* a **constant** whose declaring header is a dependency is dropped. Nothing
  in the kept surface can *reference* a constant's value, so there is no
  retention case -- the rule the other kinds apply ("dropped unless the
  library's public surface references it") reduces to "dropped";
* a **typedef** whose declaring header is a dependency is dropped unless its
  leaf identifier occurs in the kept declarations' own signature text (a
  public ``size_t`` parameter keeps ``size_t``). Deliberately generous:
  over-retaining a typedef reproduces the old behaviour, under-retaining one
  would hide an alias the public surface really names;
* a key with **unknown** origin (no attribution, e.g. a loaded snapshot, or a
  producer that could not locate the declaration) is always kept.

The matching ``SemanticIR`` typedef/constant occurrences are dropped with
their flat entries, so an IR-aware consumer cannot see more than a
flat-field one does (the invariant ``_scoped_semantic_ir`` states for the
other kinds).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping

from ..model import AbiSnapshot
from ..model.declaration_headers import (
    HeaderAttributedMap,
    declaring_header,
    has_attribution,
)
from ..model.identity import EntityId, EntityKind
from ..model.semantic_ir import SemanticIR, semantic_ir_conflict_key

__all__ = ["FlatMapScope", "scope_flat_maps"]

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FLAT_KINDS = (EntityKind.TYPEDEF, EntityKind.CONSTANT)


def _leaf_identifier(name: str) -> str:
    """The last identifier of *name* (``std::size_t`` -> ``size_t``)."""
    found = _IDENTIFIER_RE.findall(name)
    return found[-1] if found else name


def _kept_keys(
    mapping: Mapping[str, str],
    is_dep: Callable[[str | None], bool],
    retain: Callable[[str], bool],
) -> set[str] | None:
    """The keys of *mapping* that survive, or ``None`` when all do."""
    if not has_attribution(mapping):
        return None
    dropped = {
        key
        for key in mapping
        if (header := declaring_header(mapping, key))
        and is_dep(header)
        and not retain(key)
    }
    return None if not dropped else set(mapping) - dropped


def _restrict(mapping: dict[str, str], keep: set[str] | None) -> dict[str, str]:
    if keep is None:
        return mapping
    headers = {k: declaring_header(mapping, k) for k in keep}
    return HeaderAttributedMap({k: v for k, v in mapping.items() if k in keep}, headers)


@dataclasses.dataclass(frozen=True)
class FlatMapScope:
    """The scoped flat maps plus the (possibly further-scoped) IR."""

    constants: dict[str, str]
    constant_entity_ids: dict[str, EntityId]
    typedefs: dict[str, str]
    typedefs_qualified: dict[str, str]
    typedef_entity_ids: dict[str, EntityId]
    semantic_ir: SemanticIR | None
    semantic_ir_conflicts: dict[str, str]


def scope_flat_maps(
    snap: AbiSnapshot,
    is_dep: Callable[[str | None], bool],
    kept_signature_text: str,
    semantic_ir: SemanticIR | None,
    semantic_ir_conflicts: dict[str, str],
) -> FlatMapScope:
    """*snap*'s flat maps scoped by declaring header.

    *semantic_ir*/*semantic_ir_conflicts* are the already-scoped IR from
    ``_scoped_semantic_ir``; they come back further narrowed only when a
    typedef/constant occurrence was dropped. An unattributed snapshot comes
    back with every map as the identical object it passed in.
    """
    referenced = set(_IDENTIFIER_RE.findall(kept_signature_text))

    def retain_typedef(key: str) -> bool:
        return _leaf_identifier(key) in referenced

    keep_constants = _kept_keys(snap.constants, is_dep, lambda _k: False)
    keep_typedefs = _kept_keys(snap.typedefs, is_dep, retain_typedef)
    keep_qualified = _kept_keys(snap.typedefs_qualified, is_dep, retain_typedef)
    unchanged = FlatMapScope(
        snap.constants,
        snap.constant_entity_ids,
        snap.typedefs,
        snap.typedefs_qualified,
        snap.typedef_entity_ids,
        semantic_ir,
        semantic_ir_conflicts,
    )
    if keep_constants is None and keep_typedefs is None and keep_qualified is None:
        return unchanged

    dropped_ids: set[EntityId] = set()
    kept_ids: set[EntityId] = set()
    for ids, keep in (
        (snap.constant_entity_ids, keep_constants),
        (snap.typedef_entity_ids, keep_qualified),
    ):
        for key, entity_id in ids.items():
            (kept_ids if keep is None or key in keep else dropped_ids).add(entity_id)
    dropped_ids -= kept_ids

    scoped = dataclasses.replace(
        unchanged,
        constants=_restrict(snap.constants, keep_constants),
        typedefs=_restrict(snap.typedefs, keep_typedefs),
        typedefs_qualified=_restrict(snap.typedefs_qualified, keep_qualified),
        constant_entity_ids=_restrict_ids(snap.constant_entity_ids, keep_constants),
        typedef_entity_ids=_restrict_ids(snap.typedef_entity_ids, keep_qualified),
    )
    if semantic_ir is None or not dropped_ids:
        return scoped
    excluded = [
        (occ_id, entity)
        for occ_id, entity in semantic_ir.occurrences.items()
        if occ_id.entity_id.kind in _FLAT_KINDS and occ_id.entity_id in dropped_ids
    ]
    if not excluded:
        return scoped
    excluded_occ = {occ_id for occ_id, _ in excluded}
    excluded_keys = {
        semantic_ir_conflict_key(occ_id, fact_name)
        for occ_id, entity in excluded
        for fact_name, _fact in entity.fact_items()
    }
    return dataclasses.replace(
        scoped,
        semantic_ir=dataclasses.replace(
            semantic_ir,
            occurrences={
                o: e
                for o, e in semantic_ir.occurrences.items()
                if o not in excluded_occ
            },
        ),
        semantic_ir_conflicts={
            k: v for k, v in semantic_ir_conflicts.items() if k not in excluded_keys
        },
    )


def _restrict_ids(
    ids: dict[str, EntityId], keep: set[str] | None
) -> dict[str, EntityId]:
    return ids if keep is None else {k: v for k, v in ids.items() if k in keep}
