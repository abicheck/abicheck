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

"""Which of an ``AbiSnapshot``'s fields actually reach persisted content.

``storage.snapshot_encode.snapshot_to_dict`` drops a few fields rather
than persisting them, and drops one more conditionally. A consumer asking
"are these two snapshots the same *persisted* content" has to know that --
and ``policy.analysis_assurance_schema_staleness._same_content``, which
asks exactly that, may not import ``storage`` at all (``architecture/
modules.yaml``: ``policy -> model, compare``).

Its own module rather than more lines in ``model/snapshot.py``, which sits
one edit under the architecture gate's 800-line production ceiling.

``tests/test_snapshot_runtime_only_fields.py`` pins these against the real
codec by *executing* it -- perturbing a listed field must not change
``snapshot_content_digest`` -- so this stays a shared rule rather than a
second copy of the codec's pop list, free to drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = [
    "RUNTIME_ONLY_FIELDS",
    "UNPERSISTED_FIELDS_BY_TYPE",
    "persisted_field_value",
    "persisted_from_headers",
    "unpersisted_fields_for",
]


#: Fields ``snapshot_to_dict`` drops unconditionally: the three lazy
#: lookup caches (populated by a mere ``AbiSnapshot.index()`` call, so
#: comparing them would make "was this side indexed yet" decide the
#: answer) plus the runtime-only provenance qualifier.
RUNTIME_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "_func_by_mangled",
        "_var_by_mangled",
        "_type_by_name",
        "from_headers_inferred",
    }
)


def persisted_from_headers(snap: AbiSnapshot) -> bool | None:
    """``from_headers`` as it is actually persisted.

    ``None`` when it was merely *inferred*: the codec then drops the key
    entirely so a reload re-runs the same inference rather than promoting
    a guess to explicit provenance. Two snapshots therefore persist the
    same ``from_headers`` evidence whenever this agrees, even when the
    in-memory field does not -- and differ when it does not, even when the
    in-memory field agrees.
    """
    return None if snap.from_headers_inferred else snap.from_headers


#: The same rule for the nested objects a snapshot embeds, keyed by class
#: name so a consumer can look one up without importing every owning
#: module. ``BuildSourcePack.root`` is where the pack was *loaded from*, and
#: ``to_embedded_dict`` deliberately embeds only the normalized facts (see
#: its own docstring, ADR-028 D4) -- so two snapshots whose packs came from
#: different directories are the same persisted content, and comparing
#: ``root`` made assurance turn on a load path (Codex review, PR #1229).
UNPERSISTED_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    "AbiSnapshot": RUNTIME_ONLY_FIELDS,
    "BuildSourcePack": frozenset({"root"}),
}


def unpersisted_fields_for(value: object) -> frozenset[str]:
    """Which of *value*'s fields never reach persisted content.

    Empty for anything not listed, so an unrecognized nested object is
    compared in full -- the safe direction, since an over-strict compare
    reports `degraded` for a pair that persists identically while an
    over-lax one claims two distinct captures are the same.
    """
    return UNPERSISTED_FIELDS_BY_TYPE.get(type(value).__name__, frozenset())


#: Fields whose *persisted* value is derived rather than stored verbatim,
#: keyed by class name then field name. ``SourceGraphSummary.to_dict``
#: serializes ``graph_id or compute_graph_id()``, so an unset id and the
#: computed one are the same persisted content -- and a STALE stored id is
#: genuinely different content, which is why this normalizes rather than
#: excluding the field (Codex review, PR #1229). Applied by duck typing:
#: this module names the type but never imports it, so ``model`` keeps
#: depending on nothing.
_DERIVED_FIELD_FALLBACKS: dict[str, dict[str, str]] = {
    "SourceGraphSummary": {"graph_id": "compute_graph_id"},
}


def persisted_field_value(owner: object, field_name: str, value: object) -> object:
    """*value* as it would be persisted for ``owner.field_name``.

    The identity for everything but the handful of derived fields above,
    where an unset in-memory value is serialized as its computed form.
    """
    method = _DERIVED_FIELD_FALLBACKS.get(type(owner).__name__, {}).get(field_name)
    if method is None or value:
        return value
    computed = getattr(owner, method, None)
    return computed() if callable(computed) else value
