# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Pure JSON projection for canonical report documents."""

from __future__ import annotations

import json

from .document import ReportDocument


def render_json(document: ReportDocument, *, indent: int | None = 2) -> str:
    """Serialize *document* without deriving or changing report facts."""

    return json.dumps(document.to_mapping(), indent=indent)
