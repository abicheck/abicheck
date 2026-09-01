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

"""On-load snapshot migrations for legacy-format documents.

``serialization.snapshot_from_dict`` calls these right after a document is
parsed, before the result reaches any detector or index. Kept out of
``serialization.py`` itself (ADR-061 D1: ``storage/`` is the canonical owner
of a snapshot format's schemas and migrations; that file is debt-baselined
at its adoption ceiling, so new load-time migration logic belongs here, with
only a thin call left at the call site). ``abicheck.qualified_name_segments``
is a ``public_root_surfaces`` entry (its own docstring already frames it as
a stable, dependency-free leaf shared across detectors), the same exemption
``abicheck.serialization`` itself already uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..name_classification import strip_anonymous_type_location
from ..qualified_name_segments import (
    _LAMBDA_IDENTITY_FIELDS,
    _lambda_identity_containers_and_strings,
    _walk_rewrite_strings,
)

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot


def normalize_anonymous_type_spellings_on_load(snapshot: AbiSnapshot) -> AbiSnapshot:
    """Strip a checkout-dependent path out of any closure/anonymous-type
    marker still in its raw ``(lambda at <path>:<line>:<col>)`` form,
    mutating *snapshot* in place. Returns *snapshot* unchanged (for
    chaining at the call site's own ``return``).

    A baseline written before ``strip_anonymous_type_location`` existed (or
    by a header-mode dumper build that never called it) still carries that
    raw spelling on disk -- the two header-mode dumpers only ever call it at
    extraction time, never on this load path. Without this step,
    ``qualified_name_segments.renumber_anonymous_closure_identities``'s own
    marker regex (which requires the already-stripped
    ``(lambda:<basename>:<line>:<col>)`` form) leaves such a baseline's
    closures completely unrenumbered, comparing them against a freshly
    dumped snapshot's ordinal-form spellings as if the two were unrelated
    declarations. Idempotent: ``strip_anonymous_type_location`` is a no-op
    on text with no ``" at "`` left to strip, so this is safe to call
    unconditionally on every load, including an already-normalized one.

    Known, accepted limitation shared with ``renumber_anonymous_closure_
    identities`` (Codex review; see ``docs/contribute/known-gaps.md``'s "The
    L5 source graph's own node identities are never renumbered..." entry,
    Codex review on PR #868): this only rewrites
    :data:`~abicheck.qualified_name_segments._LAMBDA_IDENTITY_FIELDS` on the
    flat snapshot. A schema-v29+ document's ``AbiSnapshot.surface_graph`` is
    decoded earlier in ``snapshot_from_dict`` (``decode_surface_graph``) and
    is not touched here, so a loaded raw-marker baseline's attached graph
    keeps its own un-stripped node/edge identities even after the flat
    fields are normalized -- the identical flat-vs-graph mismatch the
    existing entry documents for dump-time renumbering, now also reachable
    from this load-time path. Not fixed here for the same reason: closing it
    needs the graph's own node/edge strings folded into the same
    strip-and-renumber pass, verified against a case mixing flat-visible and
    graph-only closures -- a real, cross-cutting change, not a same-PR
    reactive patch.
    """
    collected = _lambda_identity_containers_and_strings(snapshot)
    if collected is None:
        return snapshot
    containers, _strings = collected
    for field_name, container in zip(_LAMBDA_IDENTITY_FIELDS, containers):
        new_container = _walk_rewrite_strings(container, strip_anonymous_type_location)
        if new_container is not container:
            setattr(snapshot, field_name, new_container)
    return snapshot


def backfill_missing_elf_binding(snap: AbiSnapshot) -> None:
    """Backfill Function/Variable.elf_binding from an already-loaded
    ``elf.symbols`` for a snapshot serialized before this field existed
    (Codex review, fresh evidence).

    A pre-this-PR snapshot's own ``elf`` block already carries the exact
    same fact ``dumper_elf_symbols._populate_elf_visibility`` reads it
    from at dump time -- only the newer per-declaration ``elf_binding``
    key was never written at serialization time, so loading it as ``None``
    (the ordinary missing-key convention) would make a fresh ``binding:``
    suppression selector fail closed against *every* already-archived
    baseline, not just genuinely-unknown-binding cases, until each one is
    regenerated -- which is not always possible for an archived release.
    Only fills a ``None``: an explicitly serialized value from a v16+
    writer is preserved untouched, never recomputed.

    Deliberately scoped to ``elf_binding`` alone -- ``elf_visibility`` has
    the identical legacy-backfill gap but predates this PR, is unrelated to
    the field this PR adds, and is left as it already was.
    """
    if snap.elf is None:
        return
    sym_map = snap.elf.symbol_map
    for func in snap.functions:
        if func.elf_binding is None:
            elf_sym = sym_map.get(func.mangled)
            if elf_sym is not None:
                func.elf_binding = elf_sym.binding
    for var in snap.variables:
        if var.elf_binding is None:
            elf_sym = sym_map.get(var.mangled)
            if elf_sym is not None:
                var.elf_binding = elf_sym.binding
