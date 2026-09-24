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

"""Encode/decode ``AbiSnapshot.surface_graph`` for the wire (ADR-063 Phase 3
D5, schema v29).

Mirrors ``storage/entity_id_codec.py``'s shape: an in-place fix-up over the
already-``asdict()``-ed snapshot dict, owned by ``storage`` (which may
depend on ``model``) rather than inlined into ``serialization.py``, itself
already at this repo's file-size debt ceiling.

**Why this can't be left to ``dataclasses.asdict()``, same reason
``build_source.source_graph`` already can't be**: ``SourceGraphSummary``'s
own ``to_dict()`` is a deliberate special case (an ``indexes`` key,
sparse-only ``entity_resolver``) that ``asdict()``'s naive dataclass
recursion does not reproduce. So this codec is handed the original,
still-typed ``snap.surface_graph`` object, never the raw dict
``asdict(snap)`` already produced for that key.

**One graph, two attribute paths — not two independently-encoded blobs.**
Whenever ``snap.build_source.source_graph`` is the *identical* object as
``snap.surface_graph`` (the ordinary case once the workflow-layer assembly
step threads one shared instance to both), the graph is written once, at
the top level, and ``build_source``'s own already-encoded ``source_graph``
key is dropped from the dict ``BuildSourcePack.to_embedded_dict()`` already
built for it — never re-encoding the same object twice. On load, the
top-level ``surface_graph`` is decoded once and ``build_source.
source_graph`` is rebound to that *same* object, restoring the alias.

**Never aliased in the other direction.** A document written before this
field existed carries no top-level ``surface_graph`` key at all; its nested
``build_source.source_graph`` (an L3-L5 evidence graph, decoded exactly as
it always was, independent of this codec) is never promoted to
``AbiSnapshot.surface_graph`` — that graph predates the public-surface
builder and was never populated with the edges
``policy.public_surface_query.PublicSurfaceQuery`` traverses, so treating it as
usable would silently skip the intentional approximate-backfill path in
favor of a graph that resolves to a *smaller* surface than either the
backfill or the pre-Phase-3 flat-snapshot traversal would. ``surface_graph``
stays ``None`` for such a document, exactly as it would for any other
snapshot predating this field.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..model.lazy_graph import PendingGraph, set_pending_graph
from .graph_table_codec import decode_graph_table, encode_graph_table, is_graph_table

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = ["DeferredGraphPayload", "decode_surface_graph", "encode_surface_graph"]


class DeferredGraphPayload:
    """A ``surface_graph`` document value whose section decode has not run.

    Placed in the flat document by ``storage.import_v1`` when a sectioned
    document is read with ``defer_graph=True``: the ``graph`` section's own
    DTO validation, migration and thaw run inside :meth:`load`, which
    :func:`decode_surface_graph` only calls on first access to the graph
    (storage-format-v2 Phase 2, A2.1). Not a ``dict``, so no other reader of
    the flat document can mistake it for a decoded payload.
    """

    __slots__ = ("_load",)

    def __init__(self, load: Callable[[], dict[str, Any]]) -> None:
        self._load = load

    def load(self) -> dict[str, Any]:
        """The section's ``surface_graph`` payload, validated as an eager
        read validates it (same errors)."""
        return self._load()


def encode_surface_graph(d: dict[str, Any], snap: AbiSnapshot) -> None:
    """In-place: replace ``d["surface_graph"]`` with ``snap.surface_graph``'s
    own canonical ``to_dict()`` encoding, or drop the key when nothing was
    built. Also pops the now-redundant ``source_graph`` key back out of
    ``d["build_source"]`` (already built by
    ``BuildSourcePack.to_embedded_dict()`` before this runs) whenever that
    pack's ``source_graph`` is the identical object — see module docstring.
    """
    graph = snap.surface_graph
    if graph is None:
        d.pop("surface_graph", None)
        return
    # Schema v49: the compact graph table, not ``to_dict()``'s per-entity
    # objects (storage/graph_table_codec.py).
    d["surface_graph"] = encode_graph_table(graph)  # type: ignore[arg-type]
    bs_dict = d.get("build_source")
    bs = snap.build_source
    if isinstance(bs_dict, dict) and bs is not None and bs.source_graph is graph:
        bs_dict.pop("source_graph", None)


def decode_surface_graph(d: dict[str, Any], snap: AbiSnapshot) -> None:
    """In-place: set ``snap.surface_graph`` from ``d``'s own top-level
    ``surface_graph`` key, when present, and rebind ``snap.build_source.
    source_graph`` to that same decoded instance -- but only when the
    encoder's own dedup actually ran, i.e. ``d["build_source"]`` carries no
    ``source_graph`` key of its own (the encoder popped it specifically
    because ``bs.source_graph is graph`` held). When a nested
    ``source_graph`` key is still present in *d*, the encoder deliberately
    kept two independently-encoded, genuinely distinct graphs (module
    docstring's "not two independently-encoded blobs" case does not apply)
    -- ``snap.build_source.source_graph`` was already correctly decoded
    from that nested key before this function runs, and rebinding it here
    unconditionally would silently discard that real L3-L5 evidence graph
    in favor of the unrelated public-surface one (Codex review, PR #962).

    A legacy document with no top-level key leaves both untouched —
    ``snap.surface_graph`` stays whatever the caller already set (``None``,
    by construction), and ``snap.build_source.source_graph`` keeps whatever
    it already decoded from its own nested key.

    The graph itself is decoded on first access, not here (storage-format-v2
    Phase 2, A2.1): both attributes receive one shared
    :class:`~abicheck.model.lazy_graph.PendingGraph`, so they still resolve
    to the identical object, and a decode error surfaces at that access.
    """
    raw = d.get("surface_graph")
    load_payload: Callable[[], Any]
    if isinstance(raw, DeferredGraphPayload):
        load_payload = raw.load
    elif isinstance(raw, dict):
        # Detached: the graph decodes later, and a caller may reuse or
        # mutate the document it handed in after the load returns.
        load_payload = _constant(copy.deepcopy(raw))
    else:
        return
    cell = PendingGraph(_graph_decoder(load_payload))
    set_pending_graph(snap, "surface_graph", cell)
    bs_dict = d.get("build_source")
    nested_source_graph_present = (
        isinstance(bs_dict, dict) and "source_graph" in bs_dict
    )
    if snap.build_source is not None and not nested_source_graph_present:
        set_pending_graph(snap.build_source, "source_graph", cell)


def _constant(value: Any) -> Callable[[], Any]:
    return lambda: value


def _graph_decoder(load_payload: Callable[[], Any]) -> Callable[[], Any]:
    def decode() -> Any:
        from ..model.graph_identity import identity_normalization_memo
        from ..model.source_graph import SourceGraphSummary

        payload = load_payload()
        if is_graph_table(payload):
            return decode_graph_table(payload)
        with identity_normalization_memo():
            return SourceGraphSummary.from_dict(payload)  # pre-v49 document

    return decode
