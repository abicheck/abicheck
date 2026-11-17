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
