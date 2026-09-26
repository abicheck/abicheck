# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The headers a clang header-AST parse dropped, carried with the AST tree and
its cache entry (evidence-entity-model gap A3).

``dumper_clang_errors.retry_excluding_error_headers`` drops top-level headers
that ``#error`` when included directly and re-parses the rest. That used to be
only logged, so nothing downstream could tell a declaration missing because its
header was dropped from one the library does not declare. The list rides on the
tree (:data:`HEADER_PARSE_EXCLUDED_KEY`), in a sidecar beside the cache entry so
a warm run keeps it, and from there into ``AbiSnapshot.ast_toolchain``
(``model.header_parse_coverage``).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = [
    "HEADER_PARSE_EXCLUDED_KEY",
    "attach_parse_exclusions",
    "record_parse_exclusions",
]

#: Key under which a clang AST tree carries the dropped headers.
HEADER_PARSE_EXCLUDED_KEY = "_abicheck_header_parse_excluded"


def _sidecar(cache_path: Path) -> Path:
    return cache_path.with_name(cache_path.name + ".excluded.json")


def _write_sidecar(cache_path: Path, excluded: list[str]) -> None:
    sidecar = _sidecar(cache_path)
    if not excluded:
        sidecar.unlink(missing_ok=True)
        return
    fd, tmp = tempfile.mkstemp(dir=sidecar.parent, prefix=sidecar.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(sorted(excluded), fh)
        os.replace(tmp, sidecar)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def record_parse_exclusions(
    excluded: Iterable[str],
    root: dict[str, Any] | None,
    cache_path: Path,
    *,
    cache_write: bool,
) -> None:
    """Carry *excluded* onto *root* and beside the cache entry (removing a
    stale record when nothing was dropped). The sidecar is best effort, like
    the entry it sits beside."""
    dropped = [str(e) for e in excluded]
    if cache_write:
        try:
            _write_sidecar(cache_path, dropped)
        except OSError:
            pass
    if dropped and root is not None:
        root[HEADER_PARSE_EXCLUDED_KEY] = dropped


def attach_parse_exclusions(root: Any, cache_path: Path) -> None:
    """Restore a cached tree's dropped-header record."""
    sidecar = _sidecar(cache_path)
    if not isinstance(root, dict) or not sidecar.exists():
        return
    try:
        excluded = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(excluded, list) and excluded:
        root[HEADER_PARSE_EXCLUDED_KEY] = [str(e) for e in excluded]
