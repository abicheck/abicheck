# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Immutable, format-neutral report document contracts.

``ReportDocument`` is the ownership boundary between report construction and
format-specific rendering.  It deliberately stores JSON-shaped values rather
than a ``DiffResult``: renderers cannot mutate workflow state or recover facts
by running policy a second time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias, Union

JSONScalar: TypeAlias = str | int | float | bool | None
FrozenJSON: TypeAlias = Union[JSONScalar, tuple["FrozenJSON", ...], "FrozenObject"]


@dataclass(frozen=True, slots=True)
class FrozenObject:
    """An ordered, immutable JSON object."""

    items: tuple[tuple[str, FrozenJSON], ...]


def _freeze(value: object) -> FrozenJSON:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return FrozenObject(
            tuple((str(key), _freeze(item)) for key, item in value.items())
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    raise TypeError(f"report value is not JSON-compatible: {type(value).__name__}")


def _thaw(value: FrozenJSON) -> object:
    if isinstance(value, FrozenObject):
        return {key: _thaw(item) for key, item in value.items}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ReportDocument:
    """A completed report whose contents cannot be changed by a renderer.

    ``from_mapping`` takes a defensive immutable snapshot.  ``to_mapping``
    returns a fresh mutable projection for serializers that require ordinary
    ``dict`` and ``list`` instances.
    """

    root: FrozenObject

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ReportDocument:
        frozen = _freeze(value)
        if not isinstance(frozen, FrozenObject):  # pragma: no cover - type guard
            raise TypeError("a report document must have an object root")
        return cls(frozen)

    def to_mapping(self) -> dict[str, object]:
        return {key: _thaw(value) for key, value in self.root.items}
