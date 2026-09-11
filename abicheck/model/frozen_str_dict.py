# Copyright 2026 Nikolay Petrov
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

"""A genuinely immutable ``str -> str`` mapping that ``dataclasses.asdict()``
can serialize natively (Codex review, PR #1221, Finding 2).

Split out of ``environment_matrix.py`` (``EnvironmentMatrix.runtime_floors``'
own concrete type) into this leaf ``model`` module so any other frozen
dataclass elsewhere in the codebase that needs the same "immutable,
hashable, JSON-safe through a bare ``dataclasses.asdict()`` call" mapping
field can reuse it directly, instead of every such field re-deriving the
same fix independently.

Why not ``types.MappingProxyType``: a plain object with no ``__deepcopy__``
falls back, under ``pickle``/``copy.deepcopy``, to ``copyreg``'s reduction
machinery, which stdlib registers no support for at all for
``MappingProxyType`` (a registerable ``copyreg`` reducer fixes that, and one
is registered for it elsewhere in this codebase for other, direct
``MappingProxyType`` users -- see ``environment_matrix._reduce_mapping_proxy``
for that fix and why it still leaves this residual gap). But
``dataclasses.asdict()``'s own ``_asdict_inner`` special-cases plain
``dict`` (recursing into a fresh ``dict``), and a ``MappingProxyType`` is not
a ``dict`` instance, so it still falls to the generic ``copy.deepcopy(obj)``
branch -- which, even with that reducer registered, reconstructs *another*
``MappingProxyType`` (the reducer's whole point is producing a
genuinely-frozen result), not the plain, JSON-serializable ``dict``
``json.dumps()`` needs.

Why not a ``dict`` *subclass* either (Codex review, fresh evidence, PR
#1221, round 9): an earlier version of this class *was* a ``dict``
subclass, with every mutating method overridden to raise. That closes every
mutation reachable through the subclass's own bound methods, but not one
that bypasses them entirely: ``dict.__setitem__(instance, "GLIBC", "2.34")``
-- calling the *base class's* method directly, rather than
``instance.__setitem__(...)`` -- still mutates the instance's underlying
``dict`` storage in place, because that storage lives in the object itself
(every ``dict`` subclass shares the same C-level slots) and
``dict.__setitem__`` operates on whatever object it is given, irrespective
of which subclass overrides that name. No amount of instance-method
overriding can close this: it is not a gap in which methods were
overridden, it is that *being* a ``dict`` at the C level always exposes
this back door. If an :class:`~abicheck.workflows.contracts.CompareRequest`
carrying this mapping is already a set/dict member, that mutation silently
changes its hash out from under the container -- exactly the hash-invariant
violation every previous round of this fix was trying to close, just
reached through one more entry point.

The fix: don't be a ``dict`` at all. :class:`FrozenStrDict` now subclasses
``collections.abc.Mapping`` instead, backed by a private ``_data`` plain
``dict`` that is never exposed for mutation and has no relationship to
``instance``'s own class the way subclass storage does -- there is no
``Mapping.__setitem__`` (or any other base-class mutator) to call directly
in the first place, on this class or any ancestor, so the base-class-method
bypass is categorically impossible rather than merely unencountered.

This does mean giving up the free ride ``_asdict_inner``'s
``isinstance(obj, dict)`` branch gave the previous, ``dict``-subclass
design: a ``Mapping``-but-not-``dict`` object instead falls to
``_asdict_inner``'s final ``copy.deepcopy(obj)`` branch (verified against
this repository's supported Python versions' actual
``dataclasses._asdict_inner`` source), so :meth:`FrozenStrDict.__deepcopy__`
is what makes ``dataclasses.asdict()`` over a field of this type still
produce a plain, JSON-serializable ``dict`` -- see that method's own
docstring for why it deliberately returns a plain ``dict`` rather than
another :class:`FrozenStrDict`, and
:meth:`abicheck.environment_matrix.EnvironmentMatrix.__deepcopy__` for how a
*direct* ``copy.deepcopy()`` of the containing dataclass (a different call
than the one ``asdict()`` makes) still keeps this field's own immutability.
"""

from __future__ import annotations

import copy as _copy
from collections.abc import Iterable, Iterator, Mapping
from typing import Any


class FrozenStrDict(Mapping[str, str]):
    """An immutable, hashable ``str -> str`` mapping; a ``Mapping`` (never a
    ``dict``) so no direct base-class mutator call can reach its storage --
    see this module's own docstring for the full rationale and why an
    earlier ``dict``-subclass design could not close that gap by overriding
    instance methods alone.

    Backed by a private ``_data`` plain ``dict``, set exactly once in
    ``__init__`` and never exposed for mutation -- there is no
    ``__setitem__``/``__delitem__``/etc. at all, on this class or on
    ``Mapping`` (whose only abstract/mixin methods are read-only:
    ``__getitem__``, ``__iter__``, ``__len__``, plus the read-only mixins
    ``get``/``keys``/``items``/``values``/``__contains__``/``__eq__``/
    ``__ne__`` those three provide).

    Constructs from anything ``dict(...)`` itself accepts (another mapping,
    an iterable of key/value pairs, or keyword arguments), so
    ``FrozenStrDict(some_dict)``/``FrozenStrDict(other_frozen_str_dict)``
    both work the way the previous ``dict``-subclass version did.
    """

    __slots__ = ("_data",)
    _data: dict[str, str]

    def __init__(
        self,
        data: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        /,
        **kwargs: str,
    ) -> None:
        # Codex review, PR #1221, round 11 (still applicable after the
        # round-9 `Mapping` redesign): calling `.__init__(...)` a *second*
        # time directly on an already-constructed instance must not
        # silently repopulate `_data` in place -- the same hash-invariant
        # violation every other mutation vector this class closes would
        # cause. `hasattr` is a reliable one-shot sentinel here since
        # `_data` is declared via `__slots__` and is unset until this
        # method's own first `object.__setattr__` call below.
        if hasattr(self, "_data"):
            raise TypeError("FrozenStrDict is immutable")
        object.__setattr__(self, "_data", dict(data, **kwargs))

    # -- Mapping's three abstract methods -----------------------------------

    def __getitem__(self, key: str) -> str:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    # -- Genuine immutability: no attribute (re)assignment either -----------

    def __setattr__(self, name: str, value: Any) -> None:
        # Blocks `instance._data = {...}` -- reassigning the private backing
        # dict wholesale would be an equally effective mutation vector to
        # the ones this class exists to close, and unlike a `dict` subclass
        # this class has no C-level storage a base-class method could reach
        # around `__setattr__` in the first place, so this check alone is
        # sufficient (no separate `dict.__setitem__`-style bypass exists for
        # a `Mapping`-only object -- there is no base class with mutating
        # methods to call directly).
        raise TypeError("FrozenStrDict is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("FrozenStrDict is immutable")

    # -- repr/equality/hash ---------------------------------------------------

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(tuple(sorted(self._data.items())))

    # -- pickle ---------------------------------------------------------------

    def __reduce__(self) -> tuple[Any, tuple[dict[str, str]]]:
        # Reconstructs through the normal constructor, producing another
        # genuine (immutable) `FrozenStrDict` -- pickling is not the call
        # `dataclasses.asdict()` makes (see `__deepcopy__` below for that
        # one), so there is no JSON-serialization pressure here forcing a
        # plain-`dict` result the way there is for deepcopy.
        return (self.__class__, (dict(self._data),))

    # -- deepcopy ---------------------------------------------------------------

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, str]:
        """Return a *plain*, independent ``dict`` deep copy -- deliberately
        **not** another :class:`FrozenStrDict`.

        ``dataclasses.asdict()``'s ``_asdict_inner`` recurses into a plain
        ``dict`` field natively, but falls back to literally
        ``copy.deepcopy(obj)`` for any value whose type is neither a
        dataclass, ``list``, ``dict``/subclass, nor ``tuple``/subclass --
        which is exactly the branch a ``Mapping``-but-not-``dict`` value
        like this one takes (see this module's own docstring). That is the
        *only* mechanism available to make ``asdict()``'s output JSON-safe
        for this field now that it can no longer ride the ``dict``-subclass
        branch, which is the entire reason ``FrozenStrDict`` exists. A
        *direct* ``copy.deepcopy()`` of the dataclass that holds this field
        (rather than of this object standing alone, or via ``asdict()``) is
        a different call -- see
        ``EnvironmentMatrix.__deepcopy__``, which reconstructs a genuine,
        still-immutable ``FrozenStrDict`` for that path instead of
        delegating to this method.
        """
        return _copy.deepcopy(self._data, memo)
