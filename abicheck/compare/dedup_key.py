# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Make an arbitrary value usable as a dedup/grouping key.

A leaf module: it imports nothing from this package, so any layer may
depend on it.
"""

from __future__ import annotations


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
_OPAQUE = _Tag("opaque")


def hashable_value(value: object) -> object:
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

    Every converted form is therefore tagged with a private marker object
    rather than returned as a bare tuple. Without a tag, a list and a tuple
    of the same items both key as that tuple (``["a"]`` and ``("a",)`` are
    unequal inputs), and a genuine ``("dict", "{'a': 1}")`` collides with the
    fallback below. A tag cannot be forged: ``_Tag`` is identity-compared and
    not exported, so no detector value can contain one.

    One limit, stated rather than papered over: the last-resort branch keys
    on ``repr``, so two *equal* values whose reprs differ -- mappings equal
    but differently ordered -- key apart. That is the safe direction (an
    under-merge), and it is unreachable for the lists this exists for.
    """
    if isinstance(value, list):
        return (_LIST, tuple(hashable_value(item) for item in value))
    if isinstance(value, tuple):
        return (_TUPLE, tuple(hashable_value(item) for item in value))
    try:
        hash(value)
    except TypeError:
        return (_OPAQUE, type(value).__name__, repr(value))
    return value
