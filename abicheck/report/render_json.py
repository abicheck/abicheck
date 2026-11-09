# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Pure JSON projection for canonical report documents."""

from __future__ import annotations

import json
from collections.abc import Mapping

from .document import FrozenObject, ReportDocument


def _frozen_object_as_mapping(value: object) -> dict[str, object]:
    """A shallow projection; the JSON encoder visits frozen children itself.

    Tuples already serialize as arrays. Returning just this object's mapping
    lets the encoder retain only the current branch rather than a complete
    second, mutable report tree. The immutable document stays unchanged.
    """
    if isinstance(value, FrozenObject):
        return dict(value.items)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def render_json(document: ReportDocument, *, indent: int | None = 2) -> str:
    """Serialize *document* without deriving or changing report facts."""

    return json.dumps(document.root, default=_frozen_object_as_mapping, indent=indent)


def render_mapping_as_json(value: Mapping[str, object]) -> str:
    """Freeze an already-completed JSON-shaped report and render it.

    The one-step form for a builder that assembles its whole report as a
    mapping and then serializes it — the native JSON report and SARIF both
    do. It exists so those builders declare the boundary in one call instead
    of each repeating ``render_json(ReportDocument.from_mapping(...))``, and
    so the freeze can never be skipped by a caller reaching for ``json.dumps``
    because the two-step form looked like ceremony.
    """
    return render_json(ReportDocument.from_mapping(value))
