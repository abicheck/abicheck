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

"""ADR-062 D16: one digest per question, for a single `AbiSnapshot`.

Two roles exist for a snapshot today:

* **publication** -- `snapshot_publication_digest`: the sha256 of the exact
  JSON a write publishes, capture metadata (`created_at`) and collection
  order included. This is the digest `snapshot_content_digest` has always
  computed; it keeps that meaning and gains this explicit name. It answers
  "is this the exact artifact that was recorded?".
* **evidence** -- `snapshot_evidence_digest`: a `semantic_digest` over the
  D5-normalized facts. Volatile capture metadata (`VOLATILE_CAPTURE_FIELDS`)
  is excluded and every declared unordered collection
  (`UNORDERED_COLLECTIONS`) is sorted by its records' canonical form, so a
  re-dump that only differs in timestamp or producer traversal order has the
  same evidence digest. It answers "is this the same evidence?".

The two never share a spelling -- publication is bare hex, evidence is
`"sha256:<hex>"` -- so neither can be mistaken for the other: a new role is a
new function, never a reinterpretation of the old one. A collection not declared here keeps its
written order in the evidence digest -- the safe direction, since a missed
equality only costs a duplicate, never a false "same evidence".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .canonical import canonical_json, semantic_digest
from .snapshot_encode import snapshot_content_digest, snapshot_to_dict

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "UNORDERED_COLLECTIONS",
    "VOLATILE_CAPTURE_FIELDS",
    "evidence_form",
    "snapshot_evidence_digest",
    "snapshot_publication_digest",
]

#: Top-level snapshot fields that record *when* a capture ran, not what it
#: observed (D5: timestamps and wall-clock values live outside the
#: semantic-hash domain). Paths are not listed: D5 leaves path normalization
#: unspecified, so a path is hashed as written.
VOLATILE_CAPTURE_FIELDS = frozenset({"created_at", "source_mtime"})

#: Record collections whose order carries no meaning -- each entity is
#: matched by identity, never by position -- as `(parent, field)` paths into
#: the `snapshot_to_dict` document (`None` parent: the document root).
#: Semantically ordered arrays (parameters, fields, bases, `DT_NEEDED`)
#: are deliberately absent.
UNORDERED_COLLECTIONS: tuple[tuple[str | None, str], ...] = (
    (None, "functions"),
    (None, "variables"),
    (None, "types"),
    (None, "enums"),
    ("elf", "symbols"),
    ("elf", "imports"),
)


def _sorted_records(records: list[Any]) -> list[Any]:
    return sorted(records, key=canonical_json)


def evidence_form(document: dict[str, Any]) -> dict[str, Any]:
    """*document* (a `snapshot_to_dict` result) reduced to the evidence
    domain: volatile capture fields dropped, unordered collections sorted.
    Returns a new mapping; *document* is not modified."""
    out = {k: v for k, v in document.items() if k not in VOLATILE_CAPTURE_FIELDS}
    for parent, field in UNORDERED_COLLECTIONS:
        container = out if parent is None else out.get(parent)
        if not isinstance(container, dict):
            continue
        records = container.get(field)
        if not isinstance(records, list):
            continue
        if parent is not None:
            container = dict(container)
            out[parent] = container
        container[field] = _sorted_records(records)
    return out


def snapshot_evidence_digest(snap: AbiSnapshot) -> str:
    """The `evidence` role (D16): `"sha256:<hex>"` over `evidence_form`."""
    return semantic_digest(evidence_form(snapshot_to_dict(snap)))


def snapshot_publication_digest(snap: AbiSnapshot) -> str:
    """The `publication` role (D16): the plain sha256 hex of the exact JSON
    a snapshot write publishes -- `snapshot_content_digest`, by name."""
    return snapshot_content_digest(snap)
