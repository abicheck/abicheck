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

Round 9's fix: don't be a ``dict`` at all. :class:`FrozenStrDict` became a
``collections.abc.Mapping`` instead, backed by a private ``_data`` plain
``dict`` -- closing the base-class-mutator bypass above, since ``Mapping``
declares no mutating method to call directly on any class in its MRO.

Why round 9's ``_data`` plain ``dict`` still wasn't enough (Codex review,
fresh evidence, PR #1221, round 12 -- this round): ``__setattr__`` blocked
*replacing* ``_data`` wholesale (``instance._data = {...}``), but that does
nothing to stop *mutating the dict object ``_data`` already points to*:
``instance._data["GLIBC"] = "2.34"`` reaches straight through the
(unblocked, and un-blockable -- attribute *reads* must stay legal for the
class to function at all) attribute read and calls ``dict.__setitem__`` on
that dict directly, exactly like round 9's own finding did to the previous
design's storage, just one indirection later: guarding *reassignment* of a
private attribute is not the same as the attribute never pointing at a
mutable object in the first place.

The fix: back the mapping with a ``tuple`` of ``(key, value)`` pairs
(``_items: tuple[tuple[str, str], ...]``) instead of a ``dict``, and
implement the three methods ``Mapping`` requires (``__getitem__``,
``__iter__``, ``__len__``) via a linear scan over that tuple. A ``tuple``
does not support item assignment at all (``instance._items["GLIBC"] =
"2.34"`` raises ``TypeError`` immediately -- there is no ``__setitem__`` on
``tuple`` to call, base class or otherwise), so there is no mutable
container reachable from a :class:`FrozenStrDict` instance via *any* normal
attribute access, direct or indirect. A ``dict``-backed cache (e.g. a
``functools.cached_property`` lookup dict for O(1) access) was considered
and rejected for the same reason ``_data`` itself was: a ``cached_property``
result is stored in ``instance.__dict__[name]`` (or, here, would need its
own ``__slots__`` entry) exactly like ``_data`` was, so it would reopen the
identical hole one attribute over. ``__getitem__`` is therefore a genuine
O(n) linear scan, not O(1) -- an accepted cost given ``runtime_floors`` (this
class's one production caller) is expected to hold a handful of entries at
most (deployment-floor prefixes like ``GLIBC``/``CXXABI``, not an
open-ended user-supplied collection).

**What immutability guarantee this now provides, stated precisely:** no
*normal Python attribute access* (an ordinary ``.`` read/write, item
assignment through a discovered attribute, or any of this class's own
methods) can mutate an instance's contents or change its hash, because
there is no mutable container -- not ``_items`` itself, not anything
``_items`` points to, not anything any other attribute on the instance
points to -- reachable that way. This is **not** a claim that mutation is
*impossible* in the way it is for a true value type in a language with
enforced immutability: Python's own reflection machinery
(``object.__setattr__(instance, "_items", other_tuple)`` bypassing this
class's own ``__setattr__`` override, direct ``__dict__``/``__slots__``
descriptor manipulation, or ``ctypes``-level memory access) can still reach
around it, the same way ``object.__setattr__`` can always bypass
``@dataclass(frozen=True)`` -- and every field on this codebase's own
``EnvironmentMatrix``/``SyclConstraints``/``CudaConstraints``
(``environment_matrix.py``) relies on exactly that same, narrower
"immutable against normal attribute access" guarantee, not a stronger one.
Promising more than that here would set an inconsistent, unachievable bar
against this codebase's own established idiom for the identical class of
problem (see this module's own history above: two individually-falsified
"chase the next bypass" rounds -- the ``dict``-subclass base-method bypass,
then the ``_data``-dict in-place-mutation bypass -- are enough; per this
repository's own testing-discipline principle for a proposed heuristic
falsified twice, the right response is to close the reachable-via-normal-
attribute-access class of bypass soundly and stop, not to keep chasing
reflection-level bypasses no design in this codebase defends against
anywhere else).

This does mean giving up the free ride ``_asdict_inner``'s
``isinstance(obj, dict)`` branch gave the original ``dict``-subclass
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
    ``dict``) so no direct base-class mutator call can reach its storage,
    and backed by a ``tuple`` of pairs (never a ``dict``) so no attribute of
    the instance -- including its private backing store itself -- is ever a
    mutable container reachable via normal attribute access. See this
    module's own docstring for the full two-round history and exactly what
    guarantee that provides (and does not).

    Backed by a private ``_items`` ``tuple[tuple[str, str], ...]``, set
    exactly once in ``__init__`` and never exposed for mutation -- there is
    no ``__setitem__``/``__delitem__``/etc. at all, on this class or on
    ``Mapping`` (whose only abstract/mixin methods are read-only:
    ``__getitem__``, ``__iter__``, ``__len__``, plus the read-only mixins
    ``get``/``keys``/``items``/``values``/``__contains__``/``__eq__``/
    ``__ne__`` those three provide) -- and, unlike a ``dict``-backed private
    attribute, ``_items`` itself is a ``tuple``, which has no mutating
    method whatsoever to reach even via direct attribute access
    (``instance._items[...] = ...`` raises ``TypeError`` immediately: there
    is no ``tuple.__setitem__``).

    Constructs from anything ``dict(...)`` itself accepts (another mapping,
    an iterable of key/value pairs, or keyword arguments), so
    ``FrozenStrDict(some_dict)``/``FrozenStrDict(other_frozen_str_dict)``
    both work the way earlier versions of this class did.

    Lookup is a linear scan over ``_items`` (O(n) in the number of entries)
    rather than O(1) -- an accepted cost given this class's one production
    caller (``EnvironmentMatrix.runtime_floors``) holds only a handful of
    entries; see this module's own docstring for why a ``dict``-backed
    lookup cache was considered and rejected.
    """

    __slots__ = ("_items",)
    _items: tuple[tuple[str, str], ...]

    def __init__(
        self,
        data: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        /,
        **kwargs: str,
    ) -> None:
        # Codex review, PR #1221, round 11 (still applicable after the
        # round-9 `Mapping` redesign and this round's tuple-backing switch):
        # calling `.__init__(...)` a *second* time directly on an
        # already-constructed instance must not silently repopulate
        # `_items` in place -- the same hash-invariant violation every
        # other mutation vector this class closes would cause. `hasattr`
        # is a reliable one-shot sentinel here since `_items` is declared
        # via `__slots__` and is unset until this method's own first
        # `object.__setattr__` call below.
        if hasattr(self, "_items"):
            raise TypeError("FrozenStrDict is immutable")
        # `dict(data, **kwargs)` gives the same construction surface (and
        # last-write-wins duplicate-key semantics) `dict(...)` itself
        # accepts; the *stored* representation is this dict's `.items()`
        # frozen into a tuple, not the dict object itself.
        object.__setattr__(self, "_items", tuple(dict(data, **kwargs).items()))

    # -- Mapping's three abstract methods -----------------------------------

    def __getitem__(self, key: str) -> str:
        for item_key, item_value in self._items:
            if item_key == key:
                return item_value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (item_key for item_key, _item_value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    # -- Genuine immutability: no attribute (re)assignment either -----------

    def __setattr__(self, name: str, value: Any) -> None:
        # Blocks `instance._items = (...)` -- reassigning the private
        # backing tuple wholesale would be an equally effective mutation
        # vector to the ones this class exists to close. Unlike the
        # earlier `dict`-backed `_data` design, there is also no in-place
        # mutation vector left to worry about once `_items` itself is a
        # `tuple` (see this module's own docstring's round-12 account), so
        # this check alone is once again sufficient.
        raise TypeError("FrozenStrDict is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("FrozenStrDict is immutable")

    # -- repr/equality/hash ---------------------------------------------------

    def __repr__(self) -> str:
        return f"{type(self).__name__}({dict(self._items)!r})"

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(tuple(sorted(self._items)))

    # -- pickle ---------------------------------------------------------------

    def __reduce__(self) -> tuple[Any, tuple[dict[str, str]]]:
        # Reconstructs through the normal constructor, producing another
        # genuine (immutable) `FrozenStrDict` -- pickling is not the call
        # `dataclasses.asdict()` makes (see `__deepcopy__` below for that
        # one), so there is no JSON-serialization pressure here forcing a
        # plain-`dict` result the way there is for deepcopy.
        return (self.__class__, (dict(self._items),))

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
        return _copy.deepcopy(dict(self._items), memo)
