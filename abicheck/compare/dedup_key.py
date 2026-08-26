# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Make an arbitrary value usable as a dedup/grouping key.

A leaf module: it imports nothing from this package, so any layer may
depend on it.
"""

from __future__ import annotations

from typing import Any


class _Tag:
    """A private marker that no value under conversion can contain.

    Identity-hashed and identity-compared (the default), and never exported,
    so a converted value cannot be forged by a detector value that merely
    *looks* like one -- see `hashable_value`'s no-collision requirement.
    """

    __slots__ = ("kind",)

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<dedup-key {self.kind}>"


_LIST = _Tag("list")
_TUPLE = _Tag("tuple")
_DICT = _Tag("dict")
_SET = _Tag("set")


class _Opaque:
    """An identity-based key for a value with no structure to encode.

    Deliberately *not* a `repr`: two unequal values of one type can share a
    representation -- two independently built ``{"v": float("nan")}`` are
    unequal, since `nan != nan`, yet print identically -- and keying them
    together is the over-merge that drops a real finding. Identity cannot
    do that.

    The trade runs the other way instead: two equal but distinct opaque
    values key apart, so a dedup reports one finding twice rather than
    dropping one. That is the direction to fail in.

    Holding the value keeps it alive for the key's lifetime, so an `id`
    freed and reused cannot resurrect the collision this exists to avoid.
    """

    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value

    def __hash__(self) -> int:
        return hash(("dedup-key opaque", type(self.value).__name__))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Opaque) and self.value is other.value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<dedup-key opaque {type(self.value).__name__}>"


def hashable_value(value: object) -> Any:
    """A hash-safe stand-in for ``value``, stable across calls.

    Detector findings are deduplicated by putting derived key tuples into a
    ``set``, which requires every component to be hashable. Several value
    slots that are *annotated* as scalars are not enforced as such and do
    legitimately receive a list -- ``Change.old_value``/``new_value`` are
    annotated ``str | None`` while ``diff_python.py`` stores lists there at
    seven sites, and ``reporter.py`` serializes those as JSON arrays, making
    the list the published contract rather than a producer bug to correct.

    The contract, which its property tests state as invariants:

    - the result is always hashable;
    - equal inputs give equal results, so a dedup still dedups;
    - **unequal inputs give unequal results**, so a dedup never *over*-merges
      two genuinely different findings. That direction matters more: an
      over-merge silently drops a real finding, where an under-merge only
      reports one twice.

    Three rules, in order:

    1. A container is encoded *structurally*, recursively, under a private
       tag. The tag is what keeps ``["a"]`` and ``("a",)`` -- unequal inputs
       -- from both keying as ``("a",)``, and stops a detector value shaped
       like a converted one from forging it: `_Tag` is identity-compared and
       not exported. Mappings and sets encode as frozensets of encoded
       members, so member order never reaches the key.
    2. An already-hashable value is returned unchanged. This is exact by
       construction: the consuming ``set`` then performs the very comparison
       the original values would have.
    3. Anything else -- unhashable with no structure to encode -- keys by
       identity via `_Opaque`, for the reason given there.
    """
    if isinstance(value, list):
        return (_LIST, tuple(hashable_value(item) for item in value))
    if isinstance(value, tuple):
        return (_TUPLE, tuple(hashable_value(item) for item in value))
    if isinstance(value, dict):
        return (
            _DICT,
            frozenset(
                (hashable_value(k), hashable_value(v)) for k, v in value.items()
            ),
        )
    if isinstance(value, (set, frozenset)):
        return (_SET, frozenset(hashable_value(item) for item in value))
    try:
        hash(value)
    except TypeError:
        return _Opaque(value)
    return value
