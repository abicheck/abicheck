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

"""Letting the *final* consumer of a clang AST take something cheaper instead.

A parsed clang AST is the most expensive object this codebase builds: on a
real library the document and the dicts built from it are resident together
inside ``json.load``, and that pair -- not the tree alone -- is a dump's
measured peak. Some consumers do not want the tree at all. The header-graph
attach wants four compact projections of it
(:mod:`abicheck.buildsource.header_graph_ast_projection`) and nothing else,
and once it has them the tree is garbage.

This module is the mechanism that lets such a consumer say so, and get
handed the AST's *location* rather than its contents:

* :func:`derived_ast_scope` opens the offer, around the acquisition call;
* :func:`offer_derived_ast_source` is what each acquisition path calls to
  make it, once per place an AST becomes available;
* :class:`DerivedAstArtifact` is what comes back -- ``used`` being the
  discriminator, never a type check on the opaque marker.

A **callback** rather than knowledge of any particular derived type,
because the layers that own those types (``buildsource``) import the cache,
never the reverse, and ``scripts/check_architecture.py`` enforces that
direction. Owned by ``storage`` (ADR-061's cache-management responsibility) and
extracted from ``dumper_cache`` once it had two call sites in two modules
and its own contract to state: it is no longer "a branch inside
the cache read", it is the rule both a warm entry and a freshly written
document answer to.

Only a consumer that genuinely needs nothing else from the AST may open a
scope -- a run inside one may receive no AST at all.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DerivedAstArtifact:
    """What a final AST consumer got instead of the AST, and where from.

    ``used`` is the discriminator callers branch on -- never identity on the
    marker :func:`load_cached_ast` returns, which is private to this module.
    ``cache_path`` is filled in even on a miss, so a caller that had to parse
    can store its derived artifact beside the entry it came from.
    """

    value: Any = None
    used: bool = False
    cache_path: Path | None = None


_derived_ast_slot: contextvars.ContextVar[tuple[DerivedAstArtifact, Any] | None] = (
    contextvars.ContextVar("_derived_ast_slot", default=None)
)

#: Returned by :func:`load_cached_ast` when a derived artifact superseded the
#: AST. Private on purpose: callers read ``DerivedAstArtifact.used`` instead,
#: so no call site depends on this object's identity.
_AST_SUPERSEDED = object()


@contextmanager
def derived_ast_scope(loader: Any) -> Iterator[DerivedAstArtifact]:
    """Let the *final* consumer of an AST substitute a cheap derived form.

    Inside this scope, :func:`load_cached_ast` offers *loader* the AST cache
    entry's own path before reading it. If *loader* returns a value, the AST
    is never read or parsed at all and that value is reported here instead.

    Exists as a callback rather than as knowledge of any particular derived
    type because this module must not import the layers that own those types
    -- ``buildsource`` imports the cache, never the reverse, and
    ``check_architecture.py`` enforces that direction.

    Only a consumer that genuinely needs nothing else from the AST may open
    one: a run inside this scope may receive no AST at all.
    """
    slot = DerivedAstArtifact()
    token = _derived_ast_slot.set((slot, loader))
    try:
        yield slot
    finally:
        _derived_ast_slot.reset(token)


def offer_derived_ast_source(
    path: Path, *, is_cache_entry: bool = False, tree_in_hand: bool = False
) -> Any | None:
    """Offer the AST document at *path* to an active derived-artifact consumer.

    Returns an opaque non-``None`` marker when the consumer took it, meaning
    the caller must **not** parse *path* -- there is no tree, and the value
    the caller would have returned is on the
    :class:`DerivedAstArtifact` the scope handed out. Returns ``None``
    whenever there is no such consumer, or it declined, in which case the
    caller proceeds exactly as before.

    Two call sites offer, because an AST reaches its final consumer two
    ways and the cold one is the expensive one:

    * :func:`load_cached_ast` offers the **cache entry**, which is what PR
      #1339 added this mechanism for -- a warm run then never reads it.
      ``is_cache_entry`` records the path on the artifact, so a consumer
      that ends up computing its own derived form can store it beside the
      entry it belongs to.
    * ``dumper_clang_errors._parse_clang_ast_result`` offers the document
      ``clang`` just wrote, on a run where no cache entry existed at all.
      Without this, a cold run still paid the full ``json.load`` -- the
      whole 2 GiB peak that work was about -- and the caching only ever
      helped the second run.

    *is_cache_entry* is what keeps those two distinct: only the first is a
    stable, content-addressed location, so only it may claim
    ``cache_path``. The fresh document is a temp file the caller unlinks.

    *tree_in_hand* is passed through to the loader (``loader(path,
    tree_in_hand=...)``). It is true when the caller already holds the
    parsed tree -- the in-process memo handoff from a primary clang-frontend
    pass. The loader should then accept only a derived form that is cheaper
    than projecting that tree (a stored sidecar), never re-read *path*
    itself. The offer is still made on that path so the entry is recorded
    in ``cache_path``: without it, a clang-frontend run projected the tree
    but had nowhere to store the result, so its projection cache never
    warmed.
    """
    derived = _derived_ast_slot.get()
    if derived is None:
        return None
    holder, loader = derived
    if is_cache_entry:
        holder.cache_path = path
    value = loader(path, tree_in_hand=tree_in_hand)
    if value is None:
        return None
    holder.value = value
    holder.used = True
    return _AST_SUPERSEDED
