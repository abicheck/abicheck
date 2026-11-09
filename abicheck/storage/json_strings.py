# SPDX-License-Identifier: Apache-2.0
"""Parse owned JSON while optionally sharing short immutable string values.

Keep dict/list ownership unchanged. Sharing mutable fact payloads would couple
otherwise independent declarations during normalization or later edits.
"""

from __future__ import annotations

import json
from typing import Any

from ..model.execution_cache import clear_memoized, memoized


def parse_document(text: str, *, pool_strings: bool = False) -> Any:
    if not pool_strings:
        return json.loads(text)

    @memoized(maxsize=4096)
    def shared(value: str) -> str:
        return value

    def object_hook(value: dict[str, Any]) -> dict[str, Any]:
        for key, item in value.items():
            if type(item) is str and len(item) <= 256:
                value[key] = shared(item)
        return value

    try:
        return json.loads(text, object_hook=object_hook)
    finally:
        # No process-wide retention of document contents or cross-run cache.
        clear_memoized(shared)
