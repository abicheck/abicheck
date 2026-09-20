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

"""Cache the header graph's AST *projection* instead of re-parsing the AST.

The attach step's clang AST is enormous and entirely transient: on oneDAL
2024.7 the cached document is **822 MiB**, parsing it costs ~1 GiB of
resident dicts, and the only thing downstream reads is the four compact
values :mod:`~abicheck.buildsource.header_graph_ast_projection` names --
**16.5 MiB** of them, 50x smaller. So a warm run pays a gigabyte to
reconstruct something it already computed identically last time.

This module stores that projection beside the AST cache entry it was
derived from, so a warm attach loads 16.5 MiB instead of parsing 822.
Measured end to end, same library, fresh process:

===========================  ==========  ========
run                          peak RSS    time
===========================  ==========  ========
parse the cached AST         2093.4 MiB   ~9 s
load the cached projection     96.4 MiB   0.18 s
===========================  ==========  ========

Why a *sidecar* rather than its own keyed cache
-----------------------------------------------
The projection is valid for exactly the inputs its AST was, so it must
invalidate exactly when the AST cache entry does. Re-deriving the AST cache
key here would mean a second copy of ``dumper_ast_config._cache_key``'s
inputs -- including the ones ``_clang_header_dump`` resolves internally
(system includes, tool identities, the C-vs-C++ decision) -- and a copy that
missed one of them would serve a *stale projection*, which is silent wrong
evidence rather than a slow run. Naming the file after the AST entry makes
that class of bug unrepresentable: same key, same file stem, one lifetime.

Why the schema is self-describing
----------------------------------
Edges are stored positionally, so the field order of
:class:`~abicheck.buildsource.type_graph.TypeEdge` and
:class:`~abicheck.buildsource.call_graph.CallEdge` *is* the on-disk schema.
A bare version integer would have to be bumped by whoever adds a field, and
the one thing this repository has learned repeatedly is that a registry
entry nobody is forced to update goes stale (AGENTS.md's ``changekind-*``
gates exist for that reason). So the document records the field names it was
written with and a load rejects anything that disagrees -- a reordered or
extended dataclass invalidates its own cache automatically, with no number
to remember. A rejected entry is a cache *miss*: the caller re-parses, which
is exactly today's behaviour.
"""

from __future__ import annotations

import json
import os
from dataclasses import fields
from pathlib import Path

from .call_graph import CallEdge
from .header_graph_ast_projection import HeaderGraphAstProjection
from .type_graph import TypeEdge

__all__ = [
    "PROJECTION_CACHE_SCHEMA",
    "decode_projection",
    "encode_projection",
    "load_cached_projection",
    "projection_sidecar_path",
    "store_cached_projection",
]

#: Bumped only for a change the field lists below cannot describe (a changed
#: *meaning* for an unchanged field). Ordinary field additions/reorders are
#: caught by the field lists themselves.
PROJECTION_CACHE_SCHEMA = "abicheck-header-graph-projection/1"

_TYPE_EDGE_FIELDS = [f.name for f in fields(TypeEdge)]
_CALL_EDGE_FIELDS = [f.name for f in fields(CallEdge)]


def projection_sidecar_path(ast_cache_path: Path) -> Path:
    """The projection entry belonging to *ast_cache_path*.

    Deliberately derived from the AST entry's own path rather than recomputed
    from a key -- see this module's docstring for why that is a correctness
    property and not a convenience.
    """
    return ast_cache_path.with_name(ast_cache_path.name + ".projection.json")


def encode_projection(projection: HeaderGraphAstProjection) -> str:
    """Serialize *projection*, stamping the schema it was written under."""
    return json.dumps(
        {
            "schema": PROJECTION_CACHE_SCHEMA,
            "type_edge_fields": _TYPE_EDGE_FIELDS,
            "call_edge_fields": _CALL_EDGE_FIELDS,
            "type_files": projection.type_files,
            "entity_files": projection.entity_files,
            "type_edges": [
                [getattr(e, n) for n in _TYPE_EDGE_FIELDS]
                for e in projection.type_edges
            ],
            "call_edges": [
                [getattr(e, n) for n in _CALL_EDGE_FIELDS]
                for e in projection.call_edges
            ],
        },
        separators=(",", ":"),
    )


def decode_projection(blob: str) -> HeaderGraphAstProjection | None:
    """*blob* as a projection, or ``None`` if this build cannot trust it.

    Every rejection is a cache miss, never an error: the caller re-parses the
    AST, which is what it would have done without this cache at all.
    """
    try:
        doc = json.loads(blob)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    if doc.get("schema") != PROJECTION_CACHE_SCHEMA:
        return None
    if doc.get("type_edge_fields") != _TYPE_EDGE_FIELDS:
        return None
    if doc.get("call_edge_fields") != _CALL_EDGE_FIELDS:
        return None
    try:
        return HeaderGraphAstProjection(
            type_files=dict(doc["type_files"]),
            entity_files=dict(doc["entity_files"]),
            type_edges=[TypeEdge(*row) for row in doc["type_edges"]],
            call_edges=[CallEdge(*row) for row in doc["call_edges"]],
        )
    except (KeyError, TypeError, ValueError):
        return None


#: Everything a filesystem operation in this module may raise that must not
#: reach the caller. `OSError` is the obvious half; `UnicodeDecodeError` is
#: not, and it is the one that matters -- `read_text(encoding="utf-8")`
#: raises it (a `ValueError`, not an `OSError`) for a sidecar holding
#: invalid UTF-8, which a truncated write or an unrelated binary file landing
#: at that name produces. Escaping here would abort the header-graph dump
#: and leave the bad entry in place to do it again, which is the same
#: ADR-028 D3 violation -- a cache failure becoming a run failure -- as the
#: `UnboundLocalError` this change already fixed once (CodeRabbit review,
#: PR #1339).
_CACHE_IO_ERRORS = (OSError, UnicodeDecodeError)


def _discard_sidecar(path: Path) -> None:
    """Remove a sidecar this build will not use, best-effort.

    Eviction is an optimisation -- it saves the *next* run a rejection, not
    this one -- so a read-only directory or a Windows sharing violation must
    not turn a cache miss into a failed dump.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def load_cached_projection(ast_cache_path: Path) -> HeaderGraphAstProjection | None:
    """The projection stored beside *ast_cache_path*, or ``None``.

    Never raises, for the same reason :func:`store_cached_projection` does
    not: every outcome here is either a projection or a re-parse, and a
    re-parse is what the run would have done without this cache at all.

    An unreadable or untrusted entry is discarded rather than kept, so a
    corrupt sidecar costs one re-parse instead of every future one.
    """
    path = projection_sidecar_path(ast_cache_path)
    try:
        blob = path.read_text(encoding="utf-8")
    except _CACHE_IO_ERRORS:
        _discard_sidecar(path)
        return None
    projection = decode_projection(blob)
    if projection is None:
        _discard_sidecar(path)
    return projection


def store_cached_projection(
    ast_cache_path: Path, projection: HeaderGraphAstProjection
) -> None:
    """Write *projection* beside *ast_cache_path*, atomically.

    Never raises: this is a cache, and a run that cannot write one must still
    produce its result. Written through a temporary in the same directory so
    a concurrent reader sees either the old entry or the new one -- a
    half-written sidecar would be rejected by :func:`decode_projection`
    anyway, but it would also cost that reader a re-parse.
    """
    path = projection_sidecar_path(ast_cache_path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(encode_projection(projection), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # Through `_discard_sidecar`, not a bare `unlink`: the cleanup of a
        # failed write runs in exactly the conditions that made the write
        # fail (a full or read-only filesystem), so it is the likeliest of
        # these calls to raise a second time -- and "never raises" must hold
        # on the failure path too, not only the happy one.
        _discard_sidecar(tmp)
