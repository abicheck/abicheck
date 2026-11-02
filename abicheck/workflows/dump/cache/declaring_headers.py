# Copyright 2026 Nikolay Petrov
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

"""The dump cache's declaring-header sidecar (closed gap: "re-scoping a
cache-reloaded snapshot keeps typedefs that scoping the fresh extraction
drops").

Root cause of that gap: the flat ``constants``/``typedefs``/
``typedefs_qualified`` maps carry their declaring headers in a runtime-only
:class:`~abicheck.model.declaration_headers.HeaderAttributedMap`, which the
snapshot codec deliberately never persists (a *loaded baseline* cannot
license its own attribution). Dependency scoping drops a flat-map entry only
when that attribution proves it dependency-only, so a snapshot reloaded from
the cache kept every system typedef the fresh extraction drops.

A cache entry is not a baseline: it is this machine's own extraction output,
keyed on its inputs. So the cache stores the attribution beside the entry, in
a cache-private sidecar, and re-attaches it on read; the public snapshot
schema is unchanged. A header-derived entry whose sidecar is missing (written
before this module, or evicted on its own) is a miss rather than an
unattributed hit, so a reload can never scope differently from a fresh run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ....model.declaration_headers import HeaderAttributedMap
from ....model.fact import replace_with_fact_sync
from ....storage.atomic_file import atomic_write

if TYPE_CHECKING:
    from ....model import AbiSnapshot

__all__ = ["SIDECAR_SUFFIX", "attach", "sidecar_path", "write_sidecar"]

#: Counted by the tier's byte-quota eviction like an entry, so an orphan
#: sidecar ages out instead of leaking.
SIDECAR_SUFFIX = ".declhdr"

_FIELDS = ("constants", "typedefs", "typedefs_qualified")


def sidecar_path(base: Path, key: str) -> Path:
    return base / f"{key}{SIDECAR_SUFFIX}"


def _payload(snap: AbiSnapshot) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _FIELDS:
        mapping = getattr(snap.declarations, name)
        out[name] = (
            {k: list(v) for k, v in mapping.declaring_headers.items()}
            if isinstance(mapping, HeaderAttributedMap)
            else None
        )
    return out


def write_sidecar(snap: AbiSnapshot, base: Path, key: str) -> None:
    """Write *snap*'s attribution; a no-op for a snapshot with no headers.
    Called *before* the entry is published, so an entry never exists
    without the sidecar it was written with."""
    if not snap.from_headers:
        return
    data = json.dumps(_payload(snap), sort_keys=True, separators=(",", ":"))
    atomic_write(sidecar_path(base, key), data.encode())


def attach(snap: AbiSnapshot, base: Path, key: str) -> AbiSnapshot | None:
    """*snap* with its stored attribution re-attached, or ``None`` (a miss)
    when a header-derived entry has no readable sidecar."""
    if not snap.from_headers:
        return snap
    try:
        payload = json.loads(sidecar_path(base, key).read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    updates: dict[str, Any] = {}
    for name in _FIELDS:
        headers = payload.get(name)
        if headers is None:
            continue
        mapping = getattr(snap.declarations, name)
        updates[name] = HeaderAttributedMap(
            mapping, {k: tuple(v) for k, v in headers.items()}
        )
    return replace_with_fact_sync(snap, **updates) if updates else snap
