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

A ``dict`` *subclass* like :class:`FrozenStrDict` sidesteps this entirely:
``_asdict_inner``'s ``isinstance(obj, dict)`` branch recognizes it natively,
and ``type(obj)(...)`` reconstructs an instance of this class from the
recursed key/value pairs -- and since every mutation method below is
disabled, the result is exactly as immutable as the input. A ``dict``
subclass instance is itself accepted natively by ``json.dumps()`` too (the
encoder tests ``isinstance(o, dict)``, not ``type(o) is dict``), so
``json.dumps(dataclasses.asdict(...))`` round-trips such a field losslessly
with no manual unwrapping required.
"""
from __future__ import annotations

from typing import Any


class FrozenStrDict(dict[str, str]):
    """An immutable, hashable ``str -> str`` mapping; a ``dict`` subclass so
    ``dataclasses.asdict()`` recurses into it natively (see this module's
    own docstring for the full rationale).

    Construction (``FrozenStrDict(some_dict)``) is unaffected by the
    disabled mutators below: CPython's ``dict.__init__``/``dict.update``
    populate a dict's storage directly at the C level rather than through
    the Python-visible ``__setitem__`` slot, so building a new instance from
    a plain mapping still works; only *post-construction* mutation raises.
    ``__reduce__`` makes both ``pickle`` and ``copy.deepcopy`` reconstruct
    through that same safe constructor path (``self.__class__(dict(self))``)
    rather than ``copy``'s generic item-by-item ``_reconstruct``, which
    would otherwise call the disabled ``__setitem__`` and raise.
    """

    def __setitem__(self, key: str, value: str) -> None:
        raise TypeError("FrozenStrDict is immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("FrozenStrDict is immutable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("FrozenStrDict is immutable")

    def pop(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("FrozenStrDict is immutable")

    def popitem(self) -> tuple[str, str]:
        raise TypeError("FrozenStrDict is immutable")

    def clear(self) -> None:
        raise TypeError("FrozenStrDict is immutable")

    def setdefault(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("FrozenStrDict is immutable")

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(tuple(sorted(self.items())))

    def __reduce__(self) -> tuple[Any, tuple[dict[str, str]]]:
        return (self.__class__, (dict(self),))
