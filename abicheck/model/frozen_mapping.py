# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Small deeply read-only mapping value used by shareable model facts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Generic, TypeVar

_K = TypeVar("_K")
_V = TypeVar("_V")


class FrozenMapping(Mapping[_K, _V], Generic[_K, _V]):
    """An owned immutable mapping with cheap copy/deepcopy semantics."""

    __slots__ = ("_data",)

    def __init__(self, source: Mapping[_K, _V] = MappingProxyType({})) -> None:
        self._data = MappingProxyType(dict(source))

    def __getitem__(self, key: _K) -> _V:
        return self._data[key]

    def __iter__(self) -> Iterator[_K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __deepcopy__(self, memo: dict[int, object]) -> FrozenMapping[_K, _V]:
        del memo
        return self

    def __reduce__(self) -> tuple[type[FrozenMapping[_K, _V]], tuple[dict[_K, _V]]]:
        return type(self), (dict(self._data),)

    def __repr__(self) -> str:
        return f"FrozenMapping({dict(self._data)!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and self._data == other
