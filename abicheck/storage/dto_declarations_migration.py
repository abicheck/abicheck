# SPDX-License-Identifier: Apache-2.0
"""The ``"declarations"`` section's migration steps: v1 -> v2 (generation
82) and v2 -> v3 (generation 85).

Kept out of ``dto.py`` only to hold that module under its line cap; it is
registered there in ``_MIGRATIONS`` like every other step.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

__all__ = [
    "DECLARATIONS_MIGRATIONS",
    "declarations_v1_to_v2",
    "declarations_v2_to_v3",
]


def declarations_v1_to_v2(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Declarations v1 -> v2: ``Variable.initializer_hex`` is new. A v1 writer
    never wrote the key, so the payload is unchanged and every variable reads
    as "not read" (``None``), which never compares as a changed initializer. A
    v1 payload that already carries the key was not written by a v1 writer, so
    it is refused rather than migrated."""
    for var in payload.get("variables", ()):
        if isinstance(var, Mapping) and "initializer_hex" in var:
            raise ValueError(
                "'declarations' section v1 carries 'initializer_hex', which "
                "only a v2 writer emits"
            )
    return payload


def declarations_v2_to_v3(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Declarations v2 -> v3: ``Function.member_kind_fact`` is new. A v2 writer
    never wrote the key, so the payload is unchanged and every function reads
    its member kind as not collected, which keeps the call axis ``UNKNOWN``
    for it. A v2 payload that already carries the key was not written by a v2
    writer, so it is refused rather than migrated."""
    for fn in payload.get("functions", ()):
        if isinstance(fn, Mapping) and "member_kind_fact" in fn:
            raise ValueError(
                "'declarations' section v2 carries 'member_kind_fact', which "
                "only a v3 writer emits"
            )
    return payload


#: The section's migration steps by source version, registered in
#: ``dto._MIGRATIONS``.
DECLARATIONS_MIGRATIONS: Mapping[
    int, Callable[[Mapping[str, Any]], Mapping[str, Any]]
] = {1: declarations_v1_to_v2, 2: declarations_v2_to_v3}
