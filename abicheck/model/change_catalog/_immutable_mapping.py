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

"""``ChangeKindMeta.policy_overrides``'s immutable mapping type.

Extracted from ``registry.py`` unchanged (plan slice 7o) so that module
stays under the ADR-061 new-file line ceiling while the catalog entry gains
its two mandatory display dimensions: this class is a general-purpose,
self-contained container with no knowledge of the catalog at all -- the
kind of thing the ceiling exists to push out of a module that has other
work to do. ``registry.py`` re-exports the name, so every caller (including
pickles naming ``registry._ImmutableDict``) is unaffected.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType
from typing import Any


class _ImmutableDict(Mapping[str, Any]):
    """An immutable mapping that is deliberately *not* a ``dict`` subclass.

    ``ChangeKindMeta.policy_overrides`` needs to be immutable after
    construction (see ``__post_init__`` below) *and* round-trip cleanly
    through ``dataclasses.asdict()``/``copy.deepcopy()``/``pickle`` the same
    way an ordinary ``dict`` field already does. Three earlier designs each
    closed one gap and left another (Codex review, PR #882, fresh evidence
    each time):

    - ``types.MappingProxyType`` gives immutability for free but cannot be
      pickled at all (``asdict()``'s recursive dict handling only
      special-cases a literal ``dict``; anything else falls back to a plain
      ``copy.deepcopy()``, which mappingproxy has no support for).
    - A plain ``dict`` subclass overriding the mutating methods
      (``__setitem__``/``update``/``__ior__``/re-invoked ``__init__``, etc.)
      fixes that, and round-trips correctly once given a custom
      ``__reduce__`` (the *default* pickle/deepcopy protocol for a dict
      subclass reconstructs item-by-item, which hits the very mutators being
      overridden). But being a genuine ``dict`` instance means its storage
      is still reachable through ``dict``'s own *unbound* methods called
      directly: ``dict.__setitem__(entry.policy_overrides, "unknown",
      Verdict.API_BREAK)`` mutates the underlying hash table in C, bypassing
      every overridden Python-level method entirely — there is no override
      that can intercept a call to the base type's own descriptor.

    The only way to close that last gap is to not be a ``dict`` at all:
    ``dict.__setitem__(obj, ...)`` requires its first argument to *be* a
    ``dict`` instance (or subclass), and raises ``TypeError`` immediately
    for anything else. This class implements the read-only
    ``collections.abc.Mapping`` protocol (``__getitem__``/``__iter__``/
    ``__len__``, which is all ``Mapping`` needs to derive ``__contains__``/
    ``keys()``/``values()``/``items()``/``get()``) over ``self._data`` — a
    private ``types.MappingProxyType`` view (not a plain ``dict``: a plain
    dict there would itself be reachable and mutable one attribute access
    away, via ``entry.policy_overrides._data["unknown"] = ...`` — Codex
    review, PR #882, fresh evidence; the earlier "wrap a mutable dict, only
    guard access through this class's own methods" framing missed exactly
    this). ``Mapping`` supplies no ``__setitem__``/``update``/``pop``/etc.
    at all — those are ``MutableMapping``-only mixin methods, and this
    class implements only the read-only ``Mapping`` protocol. Separately,
    neither ABC defines ``__or__``/``__ior__`` at all (Codex review, PR
    #882, fresh evidence corrected an earlier revision of this docstring
    that mis-attributed them to ``MutableMapping``): PEP 584's `|`/`|=`
    are a ``dict``-specific addition to the concrete type, not a mixin any
    ABC provides. Either way, ``entry.policy_overrides["x"] = y`` and
    ``entry.policy_overrides |= {...}`` both raise ``TypeError`` from
    Python's own attribute/operator resolution — no per-method overriding
    needed to block them. Two methods
    are still overridden below to close the remaining reflection-level
    gaps: ``__init__`` guards against ``entry.policy_overrides.__init__
    ({...})`` re-invoking it directly on an already-constructed instance
    (the same shape of bypass a plain dict subclass has, just for this
    class's own constructor instead of ``dict.__init__``), and
    ``__setattr__`` guards against reassigning ``_data``/``_initialized``
    directly (``entry.policy_overrides._data = {...}``), which would
    otherwise swap in an unvalidated mapping wholesale without going
    through ``__init__`` at all — the two guards share the same
    ``_initialized`` flag, so together they reject every attribute write on
    a real instance after its one legitimate ``__init__`` call.

    ``isinstance(x, dict)`` does not hold for this class, unlike the earlier
    dict-subclass design — checked against every consumer of
    ``ChangeKindMeta.policy_overrides``/``ChangeKindRegistry.
    policy_overrides_for()`` in this codebase: none relies on ``dict``-ness
    specifically, only on the ``Mapping`` protocol (``.items()``,
    ``[key]``, ``in``), which this class provides. ``dataclasses.asdict()``
    is the one place ``dict``-ness *is* observable indirectly: its generic
    branch reaches every non-dict/list/tuple/dataclass field via
    ``copy.deepcopy()``, so ``__deepcopy__`` below deliberately returns a
    plain, mutable ``dict`` — the disconnected copy ``asdict()``/
    ``copy.deepcopy()`` produce is ordinary and JSON-serializable, matching
    exactly what an ordinary ``dict`` field would give you, while the
    *original* entry's own ``policy_overrides`` stays immutable regardless.
    Pickling is a different mechanism (``__reduce__``) and keeps
    reconstructing a genuine, immutable ``_ImmutableDict``.
    """

    __slots__ = ("_data", "_initialized")

    def __init__(
        self,
        source: Mapping[str, Any] | Iterable[tuple[str, Any]] = (),
    ) -> None:
        # A second call on an already-constructed instance
        # (``entry.policy_overrides.__init__({"unknown": ...})``) would
        # otherwise silently replace ``_data`` with unvalidated content —
        # ``__init__`` is legitimately invoked exactly once per real object,
        # by ``ChangeKindMeta.__post_init__`` and by ``__reduce__``'s
        # reconstruction below, always on a brand-new instance. This also
        # doubles as the guard ``__setattr__`` below relies on.
        if getattr(self, "_initialized", False):
            raise TypeError("policy_overrides is immutable after construction")
        # ``_data`` is itself a ``types.MappingProxyType`` view over a
        # private dict with no other reference anywhere, not a plain dict —
        # a plain dict here would still be reachable and mutable through
        # ``entry.policy_overrides._data["unknown"] = ...`` (Codex review,
        # PR #882, fresh evidence): "no public mutator" only protects the
        # ``Mapping`` interface, not an attribute one attribute-access away.
        # This has none of MappingProxyType's earlier pickling problems —
        # those applied to the *field's* own type (asdict()/deepcopy()/
        # pickle handling a bare mappingproxy value), not to something used
        # purely as this class's own private storage, which its own
        # __reduce__/__deepcopy__ above already convert to a plain dict
        # before handing off to pickle/deepcopy's machinery.
        self._data = MappingProxyType(dict(source))
        self._initialized = True

    def __setattr__(self, name: str, value: Any) -> None:
        # Blocks the sibling bypass to the one above: reassigning ``_data``
        # directly (``entry.policy_overrides._data = {...}``) would swap in
        # an unvalidated mapping wholesale, without going through
        # ``__init__`` at all (Codex review, PR #882, fresh evidence).
        # ``_initialized`` is only ever ``True`` after ``__init__`` has
        # already set both slots, so this rejects every later attribute
        # write on a real instance while still allowing ``__init__`` itself
        # to set them the first time.
        if getattr(self, "_initialized", False):
            raise TypeError("policy_overrides is immutable after construction")
        object.__setattr__(self, name, value)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        # Deliberately returns a plain, ordinary (mutable) dict rather than
        # another _ImmutableDict — matching exactly what an *ordinary* dict
        # field would produce under copy.deepcopy() (a disconnected copy,
        # unremarkable in every way, keys/values already immutable so a
        # shallow dict() copy is a real deep copy here). This is also what
        # makes dataclasses.asdict() work: its generic-value branch calls
        # copy.deepcopy() on any field that isn't itself a dict/list/tuple/
        # dataclass, so without this override asdict()'s output kept the
        # live _ImmutableDict — a non-dict Mapping json.dumps() cannot
        # serialize, unlike the plain dict an ordinary field would have
        # produced (Codex review, PR #882, fresh evidence). The *original*
        # entry's own policy_overrides attribute is completely unaffected —
        # this only governs what a disconnected copy of it looks like.
        # pickle round-trips take a different path (__reduce__ below) and
        # keep reconstructing a genuine, immutable _ImmutableDict, since
        # pickle's job is faithfully reconstructing the same object/type,
        # not producing JSON-primitive-friendly output.
        return dict(self._data)

    def __reduce__(self) -> tuple[Any, ...]:
        return (self.__class__, (dict(self._data),))
