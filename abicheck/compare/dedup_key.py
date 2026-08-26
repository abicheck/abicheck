# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Make an arbitrary value usable as a dedup/grouping key.

A leaf module: it imports nothing from this package, so any layer may
depend on it.
"""

from __future__ import annotations


def hashable_value(value: object) -> object:
    """A hash-safe stand-in for ``value``, stable across calls.

    Detector findings are deduplicated by putting derived key tuples into a
    ``set``, which requires every component to be hashable. Several value
    slots that are *annotated* as scalars are not enforced as such and do
    legitimately receive a list -- ``Change.old_value``/``new_value`` are
    annotated ``str | None`` while ``diff_python.py`` stores lists there at
    seven sites, and ``reporter.py`` serializes those as JSON arrays, making
    the list the published contract rather than a producer bug to correct.

    The contract this guarantees, and which its property tests state:

    - the result is always hashable;
    - equal inputs give equal results (so a dedup still dedups);
    - unequal inputs give unequal results (so a dedup never *over*-merges),
      including across the type conversions performed here -- ``["a"]`` and
      ``"a"`` remain distinct keys.

    A list becomes a tuple, applied recursively so a nested list converts
    too. Anything else unhashable falls back to a type-tagged ``repr``: it
    keys stably and, being type-tagged, cannot collide with a genuine string
    of the same text.
    """
    if isinstance(value, list):
        value = tuple(hashable_value(item) for item in value)
    try:
        hash(value)
    except TypeError:
        return (type(value).__name__, repr(value))
    return value
