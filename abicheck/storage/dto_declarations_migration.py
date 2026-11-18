# SPDX-License-Identifier: Apache-2.0
"""The ``"declarations"`` section's v1 -> v2 migration step (generation 82).

Kept out of ``dto.py`` only to hold that module under its line cap; it is
registered there in ``_MIGRATIONS`` like every other step.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["declarations_v1_to_v2"]


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
    """Declarations v2 -> v3: ``Function.return_by_value_fact`` is new. A v2
    writer never wrote the key, so the payload is unchanged and every function
    reads as "not determined" (unset), which never fires the by-value
    return-convention check. A v2 payload that already carries the key was not
    written by a v2 writer, so it is refused rather than migrated."""
    for fn in payload.get("functions", ()):
        if isinstance(fn, Mapping) and "return_by_value_fact" in fn:
            raise ValueError(
                "'declarations' section v2 carries 'return_by_value_fact', "
                "which only a v3 writer emits"
            )
    return payload
