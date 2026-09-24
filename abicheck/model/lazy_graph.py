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

"""Decode-on-first-access for a stored graph (storage-format-v2 Phase 2,
A2.1; evidence-entity-model Phase 5a).

A loaded snapshot's header graph is the largest section it carries, and many
runs never read it. :class:`PendingGraph` holds the *undecoded* section
payload plus the decoder that turns it into a graph object, and runs that
decoder at most once, on first access, under a lock. :class:`LazyGraphField`
is the data descriptor that makes an ordinary dataclass attribute
(``AbiSnapshot.surface_graph``, ``BuildSourcePack.source_graph``) resolve a
pending cell transparently, so no reader changes.

Contract, which ``tests/test_lazy_graph_loading.py`` states as tests:

* **Same value as eager decoding.** ``__get__`` returns exactly what the
  decoder returns; equality, ``repr`` and ``dataclasses.replace`` read the
  attribute and therefore see the decoded graph.
* **Same object through every alias.** Two attributes holding the *same*
  cell resolve to the *identical* graph -- the one-graph, two-attribute-paths
  alias ``storage/surface_graph_codec.py`` restores on load.
* **A failure is never an empty graph.** If the decoder raises, the error
  propagates at access time and the cell stays pending, so a second access
  raises again instead of reading as ``None`` (AGENTS.md: a failed extractor
  is an error, never an empty surface).
* **Pickling resolves first.** ``__reduce__`` pickles the decoded graph,
  never the payload, the decoder or the lock -- a pickled snapshot
  round-trips to an equal, already-decoded one.
* **Copies keep laziness.** A shallow ``copy.copy`` shares the cell, i.e.
  shares the graph, exactly as a shallow copy shares an eagerly decoded
  graph object. ``copy.deepcopy`` of an undecoded cell yields an independent
  undecoded cell (``policy/depth_projection.py`` deep-copies a snapshot it
  then strips the graph from); the copy memo keeps two aliases of one cell
  aliased in the copy.

A model-layer module: it knows nothing about how a payload is encoded. The
decoder is a plain callable supplied by ``storage``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

__all__ = [
    "LazyGraphField",
    "PendingGraph",
    "install_lazy_graph_field",
    "is_graph_decoded",
    "set_pending_graph",
]


class PendingGraph:
    """One undecoded graph and the decoder that produces it."""

    __slots__ = ("_decoder", "_done", "_lock", "_value")

    def __init__(self, decoder: Callable[[], Any]) -> None:
        self._decoder: Callable[[], Any] | None = decoder
        self._lock = threading.Lock()
        self._value: Any = None
        self._done = False

    @classmethod
    def resolved(cls, value: Any) -> PendingGraph:
        """A cell that already holds *value* (the unpickling constructor)."""
        cell = cls(_no_decoder)
        cell._value = value
        cell._done = True
        cell._decoder = None
        return cell

    @property
    def decoded(self) -> bool:
        """Whether the decoder has run successfully."""
        return self._done

    def resolve(self) -> Any:
        """The decoded graph, decoding it now if this is the first access."""
        if self._done:
            return self._value
        with self._lock:
            if not self._done:
                decoder = self._decoder
                if decoder is None:  # unreachable: _done is set with it cleared
                    raise RuntimeError("PendingGraph lost its decoder")
                # Raises through; the cell stays pending so the next access
                # raises the same way rather than reading as empty.
                self._value = decoder()
                self._done = True
                self._decoder = None
        return self._value

    def __deepcopy__(self, memo: dict[int, Any]) -> PendingGraph:
        """An undecoded cell copies *without decoding*: the copy gets its
        own cell over the same decoder, so each side decodes an independent
        graph if and when it is read -- the result a deep copy of an eagerly
        decoded graph gives. A decoded cell deep-copies its value."""
        import copy

        with self._lock:
            if self._done:
                return PendingGraph.resolved(copy.deepcopy(self._value, memo))
            decoder = self._decoder
        if decoder is None:  # unreachable: _done is set with it cleared
            raise RuntimeError("PendingGraph lost its decoder")
        return PendingGraph(decoder)

    def __reduce__(self) -> tuple[Any, ...]:
        return (PendingGraph.resolved, (self.resolve(),))

    def __repr__(self) -> str:
        state = "decoded" if self._done else "pending"
        return f"<PendingGraph {state}>"


def _no_decoder() -> Any:
    raise RuntimeError("an already-resolved PendingGraph has no decoder")


class LazyGraphField:
    """Data descriptor resolving a :class:`PendingGraph` stored under the
    attribute's own name in the instance ``__dict__``.

    Anything else stored there (a graph, ``None``) is returned unchanged, so
    an eagerly constructed object behaves exactly as before.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return None  # the dataclass field's own default
        value = obj.__dict__.get(self._name)
        if type(value) is PendingGraph:
            return value.resolve()
        return value

    def __set__(self, obj: Any, value: Any) -> None:
        obj.__dict__[self._name] = value


def install_lazy_graph_field(cls: type, name: str) -> None:
    """Replace dataclass *cls*'s already-processed field *name* (whose default
    must be ``None``) with a :class:`LazyGraphField`.

    Installed after ``@dataclass`` ran, so the generated ``__init__``,
    ``__eq__`` and ``__repr__`` are unchanged; they simply read and write
    through the descriptor.
    """
    if getattr(cls, name, None) is not None:
        raise TypeError(f"{cls.__name__}.{name} must default to None")
    setattr(cls, name, LazyGraphField(name))


def set_pending_graph(obj: Any, name: str, cell: PendingGraph) -> None:
    """Store *cell* as *obj*'s value for lazy attribute *name*."""
    if not isinstance(type(obj).__dict__.get(name), LazyGraphField):
        raise TypeError(f"{type(obj).__name__}.{name} is not a lazy graph field")
    obj.__dict__[name] = cell


def is_graph_decoded(obj: Any, name: str) -> bool:
    """Whether reading *obj.name* would run no decoder (never decodes)."""
    value = obj.__dict__.get(name)
    return not (type(value) is PendingGraph and not value.decoded)
