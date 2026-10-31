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

"""Lazy loading of the ``declarations``, ``types`` and debug-layout sections
(ADR-062 D18 step 2, storage-format-v2 A2.1).

Those three sections decode into one attribute, ``AbiSnapshot.semantic_ir``
(the IR owns the declaration store), so they are deferred together, with the
``semantic_ir`` section itself and the per-entity half of
``extraction_scope``: :func:`decode_lazily` builds the snapshot from every
other section and leaves a pending cell whose decoder
(``model.lazy_declarations.DeferredSemanticIR``) rebuilds exactly the
snapshot the eager decode would have built and hands over its IR.

A document is decoded this way only when every join that reads or rewrites
those sections is a no-op for it (:func:`lazy_ir_eligible`); any other
document takes the eager path unchanged:

* ``from_headers`` is recorded, so its inference never reads the surface;
* ``schema_version`` is at or above every ADR-063 legacy fact-backfill
  rule's threshold (:data:`LEGACY_BACKFILL_FLOOR`), so the backfill, which
  rewrites decoded declarations in place, changes nothing;
* the document text was scanned and holds no closure/anonymous marker
  (``closure_marking.source_proven_marker_free``), so the closure-identity
  renumbering of ``finalize_snapshot`` walks nothing.

The generation spelling migration runs on the whole document before any
section decodes, in both modes. The two load-time steps that rewrite
declarations and run on every document -- the ELF-binding backfill and the
stored export-absence reconciliation -- are queued into the cell
(``model.lazy_declarations.defer_declaration_step``) and run, in the eager
order, when it decodes. A decode error in a deferred section is raised at
first access, the same error the eager load raises, never an empty store.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ..model import AbiSnapshot
from ..model.lazy_declarations import DeferredSemanticIR
from ..model.lazy_graph import PendingGraph, set_pending_graph
from ..model.semantic_ir_legacy_adapter import finalize_snapshot_semantic_ir
from ..model.snapshot_reliability import FACT_FAMILIES, flag_name
from .acyclic_json import gc_paused
from .closure_marking import source_proven_marker_free
from .extraction_scope_codec import (
    attach_extraction_scope_ownership,
    decode_extraction_scope_record,
)
from .fact_schema_versions import (
    _FACT_FIELDS_SCHEMA_VERSION,
    _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
    _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS,
    _MIN_SCHEMA_VERSION_FOR_PARAM_KIND_FACT,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
)
from .semantic_ir_codec import (
    PredecodedSemanticIR,
    decode_semantic_ir,
    semantic_ir_from_document,
)
from .snapshot_reliability_flags import decode_reliability_flags
from .snapshot_section_decode import (
    decode_binary_section,
    decode_build_section,
    decode_debug_section,
    decode_declaration_blocks,
    decode_declaration_store,
    decode_layout_section,
    decode_provenance_section,
    decode_types_section,
    view_for,
)

__all__ = [
    "LEGACY_BACKFILL_FLOOR",
    "decode_lazily",
    "eager_section_decode",
    "lazy_ir_eligible",
]

#: The lowest ``schema_version`` at which ``fact_backfill.
#: apply_legacy_fact_backfill`` changes nothing: every rule it applies is
#: skipped at or above its own ``min_schema_version``.
#: ``tests/unit/storage/test_snapshot_lazy_decode.py`` checks this against
#: the rules the backfill actually passes.
LEGACY_BACKFILL_FLOOR = max(
    _FACT_FIELDS_SCHEMA_VERSION,
    _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
    _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS,
    _MIN_SCHEMA_VERSION_FOR_PARAM_KIND_FACT,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
)

_DEBUG_LAYOUT_KEYS = ("dwarf", "dwarf_advanced")

_EAGER: ContextVar[bool] = ContextVar("abicheck_storage_eager_sections", default=False)


@contextmanager
def eager_section_decode() -> Iterator[None]:
    """Decode every section eagerly inside this block -- the oracle the lazy
    path is tested against, and a switch for a caller that must surface a
    decode error at load."""
    token = _EAGER.set(True)
    try:
        yield
    finally:
        _EAGER.reset(token)


def lazy_ir_eligible(d: dict[str, Any], schema_version: int) -> bool:
    """Whether every join touching the deferred sections is a no-op for the
    flat document *d* (this module's docstring)."""
    return (
        not _EAGER.get()
        and source_proven_marker_free()
        and schema_version >= LEGACY_BACKFILL_FLOOR
        and "from_headers" in view_for(d, "from_headers")
    )


def decode_lazily(d: dict[str, Any], schema_version: int) -> AbiSnapshot:
    """The snapshot of flat document *d* with its declaration store pending.

    Only for a document :func:`lazy_ir_eligible` accepts. Every field outside
    the store is decoded now, exactly as the eager path decodes it."""
    sv = schema_version
    declarations = view_for(d, "declarations")
    types = view_for(d, "types")
    semantic_ir = view_for(d, "semantic_ir")
    scope = view_for(d, "extraction_scope")
    debug_view = view_for(d, "debug")
    for key in _DEBUG_LAYOUT_KEYS:
        debug_view.pop(key, None)
    debug = decode_debug_section(debug_view, sv)
    for key in _DEBUG_LAYOUT_KEYS:
        del debug[key]
    from_headers = bool(view_for(d, "from_headers")["from_headers"])
    flags = decode_reliability_flags(
        view_for(d, "reliability_flags"),
        from_headers=from_headers,
        ast_producer_value=debug["ast_producer"],
        schema_version=sv,
    )
    fields: dict[str, Any] = {
        **decode_provenance_section(view_for(d, "provenance"), sv),
        **decode_binary_section(view_for(d, "binary"), sv),
        **decode_declaration_blocks(declarations),
        **debug,
        **decode_build_section(view_for(d, "build"), sv),
        **decode_layout_section(view_for(d, "layout"), sv),
        "from_headers": from_headers,
        "from_headers_inferred": False,
        "stale_fact_families": frozenset(
            family for family in FACT_FAMILIES if not flags[flag_name(family)]
        ),
    }
    snap = AbiSnapshot(**fields)
    _decode_semantic_ir_conflicts(semantic_ir, snap)
    decode_extraction_scope_record(scope, snap)
    full_debug = view_for(d, "debug")

    def build() -> AbiSnapshot:
        # Same scheduling as `serialization.load_snapshot`'s eager decode:
        # the decode allocates millions of acyclic containers.
        with gc_paused():
            return _build()

    def _build() -> AbiSnapshot:
        layout = decode_debug_section(full_debug, sv)
        scratch = AbiSnapshot(
            **fields,
            **decode_declaration_store(declarations, sv),
            **decode_types_section(types, sv),
            **{key: layout[key] for key in _DEBUG_LAYOUT_KEYS},
        )
        decode_semantic_ir(semantic_ir, scratch)
        attach_extraction_scope_ownership(scope, scratch)
        finalize_snapshot_semantic_ir(scratch)
        return scratch

    set_pending_graph(snap, "semantic_ir", PendingGraph(DeferredSemanticIR(build)))
    return snap


def _decode_semantic_ir_conflicts(d: dict[str, Any], snap: AbiSnapshot) -> None:
    """The ``semantic_ir_conflicts`` half of ``semantic_ir_codec.
    decode_semantic_ir``: a snapshot field, decoded eagerly."""
    predecoded = d.get("semantic_ir")
    if isinstance(predecoded, PredecodedSemanticIR):
        if predecoded.conflicts is not None:
            snap.semantic_ir_conflicts = predecoded.conflicts
        return
    if "semantic_ir_conflicts" in d:
        _, conflicts = semantic_ir_from_document(
            {"semantic_ir_conflicts": d["semantic_ir_conflicts"]}
        )
        snap.semantic_ir_conflicts = conflicts
