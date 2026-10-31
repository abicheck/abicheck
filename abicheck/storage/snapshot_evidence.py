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

"""ADR-062 D16's `evidence` role for an `AbiSnapshot`: its memoized digest,
the persisted digest a stored operand was loaded with, and the cheapest
order of work for "do these two snapshots carry the same evidence?".

The document-level digest itself is `evidence_digest.py`; the run-scoped
memo (keyed by role) is `snapshot_digest_cache.py`.
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Any

from .evidence_digest import (
    UNORDERED_LISTS,
    VOLATILE_CAPTURE_FIELDS,
    evidence_digest_of_document,
)
from .snapshot_digest_cache import EVIDENCE, memoized_digest, peek_digest
from .snapshot_encode import _VERBATIM_STR_FIELDS, snapshot_to_dict
from .wire_extensions import extensions_of

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "evidence_identity_digests",
    "record_stored_evidence_digest",
    "snapshot_evidence_digest",
    "stored_evidence_digest",
]

#: `id(snapshot) -> (weakref, evidence digest)` for snapshots a loader built
#: from a stored package whose `ArtifactRef` recorded `evidence_digest`. A
#: side table rather than a model field: `dataclasses.replace` (depth
#: projection, test helpers) builds a new object that must not inherit a
#: digest of facts it may no longer hold, and a weak reference drops the
#: entry with the snapshot.
_STORED: dict[int, tuple[weakref.ref[Any], str]] = {}


def record_stored_evidence_digest(snap: AbiSnapshot, digest: str) -> None:
    """Remember that *snap* was decoded from a stored document whose
    `evidence` digest is *digest* (`ArtifactRef.evidence_digest`)."""
    key = id(snap)
    _STORED[key] = (weakref.ref(snap, lambda _r: _STORED.pop(key, None)), digest)


def stored_evidence_digest(snap: AbiSnapshot) -> str | None:
    """The persisted `evidence` digest *snap* was loaded with, if any."""
    entry = _STORED.get(id(snap))
    return entry[1] if entry is not None and entry[0]() is snap else None


def _uncached_snapshot_evidence_digest(snap: AbiSnapshot) -> str:
    carried = extensions_of(snap)
    return evidence_digest_of_document(
        snapshot_to_dict(snap),
        extensions=None if carried is None else carried.evidence_form(),
    )


def snapshot_evidence_digest(snap: AbiSnapshot) -> str:
    """*snap*'s ADR-062 D16 `evidence` digest (`sha256:<hex>`), memoized
    under the `evidence` role for an open :func:`digest_scope`. Computed
    from the snapshot, never from a stored value: see
    :func:`evidence_identity_digests` for when the stored one answers."""
    return memoized_digest(snap, _uncached_snapshot_evidence_digest, role=EVIDENCE)


def _evidence_provably_differs(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """``True`` only when the evidence forms certainly differ: a declared
    string field written verbatim, or a record count, disagrees."""
    for name in _VERBATIM_STR_FIELDS:
        if name in VOLATILE_CAPTURE_FIELDS:
            continue
        a, b = getattr(old, name, None), getattr(new, name, None)
        if type(a) is str and type(b) is str and a != b:
            return True
    da, db = old.declarations, new.declarations
    for attr in ("functions", "variables", "types", "enums"):
        a, b = getattr(da, attr, None), getattr(db, attr, None)
        if isinstance(a, list) and isinstance(b, list) and len(a) != len(b):
            return True
    return False


def _identity_orders(snap: AbiSnapshot) -> tuple[list[tuple[str, ...]], ...]:
    """Each `evidence_digest.UNORDERED_LISTS` list's identity keys, in
    producer order, read off the model (no serialization)."""
    decls = snap.declarations
    out = []
    for container, name in UNORDERED_LISTS:
        holder = decls if container is None else getattr(snap, container, None)
        items = getattr(holder, name, None) or ()
        key = UNORDERED_LISTS[(container, name)]
        out.append([key(item) for item in items])
    return tuple(out)


def _evidence_form_tracks_publication(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """Whether the evidence normalization is a no-op *between* *old* and
    *new*: no volatile capture field differs, and every unordered list has
    the same identity order on both sides. Then
    the two evidence forms differ wherever the publication forms do."""
    for name in VOLATILE_CAPTURE_FIELDS:
        if getattr(old, name, None) != getattr(new, name, None):
            return False
    try:
        return _identity_orders(old) == _identity_orders(new)
    except Exception:
        return False


def evidence_identity_digests(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[str | None, str | None]:
    """Two digests that are equal exactly when *old* and *new* carry the
    same evidence (ADR-062 D16), or ``(None, None)`` when that is provably
    not so. Cheapest answer first, so the bounded compare path never
    serializes a snapshot twice for one question:

    1. both loaded with a persisted `evidence` digest: those, no work;
    2. a declared field or a record count differs: ``(None, None)``;
    3. both `publication` digests already computed in this scope: equal,
       those (equal publication implies equal evidence; the string's role
       does not matter to a consumer that only tests equality); different,
       and nothing the evidence form normalizes differs either (a volatile
       capture field, or the identity order of an unordered list),
       ``(None, None)`` -- the evidence forms then differ
       exactly where the publication forms do;
    4. otherwise the `evidence` digests, computed lazily and memoized.

    Fails closed (``(None, None)``) on any encoding failure.
    """
    stored = stored_evidence_digest(old), stored_evidence_digest(new)
    if stored[0] is not None and stored[1] is not None:
        return stored
    if old is not new and _evidence_provably_differs(old, new):
        return None, None
    published = peek_digest(old), peek_digest(new)
    if published[0] is not None and published[1] is not None:
        if published[0] == published[1]:
            return published
        if _evidence_form_tracks_publication(old, new):
            return None, None
    try:
        return snapshot_evidence_digest(old), snapshot_evidence_digest(new)
    except Exception:
        return None, None
