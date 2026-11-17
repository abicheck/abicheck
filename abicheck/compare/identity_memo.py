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

"""Per-run memo of canonical finding identities, shared by the dedup passes."""

from __future__ import annotations

from dataclasses import fields

from ..model.change import Change
from .finding_identity import resolve_change_identity

#: Every ``Change`` field, read as the memo's validity snapshot. Conservative:
#: a field that does not feed identity still invalidates, which only costs a
#: recompute, never a stale key.
INPUT_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Change))


class IdentityMemo:
    """Per-run memo of ``resolve_change_identity(c).primary_id``.

    One dedup run asks for the same change's identity from both
    :func:`_dedup_exact` and the cross-detector pass. This memo computes it
    once per change and reuses it.

    Stale-proof by construction: each entry stores a snapshot of the change's
    input fields (:data:`INPUT_FIELDS`) beside its primary id. A hit requires
    the current snapshot to equal the stored one, so any later rewrite of an
    input (for example the enum-name bridge setting ``qualified_name``) forces a
    recompute, with no call site needing to remember to invalidate.

    Keyed by ``id(change)`` and holding a strong reference to each change, so a
    freed object's id is never reused while its entry lives. Owned by one
    pipeline run (the ``PipelineContext``), so the memo lives exactly as long as
    that run.
    """

    __slots__ = ("entries",)

    def __init__(self) -> None:
        #: ``id(change)`` -> ``(change, input_snapshot, primary_id)``. Public so
        #: :func:`dedup_identity_key` can validate a hit without a second call.
        self.entries: dict[int, tuple[Change, tuple[object, ...], str]] = {}

    def primary_id(self, change: Change) -> str:
        snapshot = tuple(getattr(change, name) for name in INPUT_FIELDS)
        hit = self.entries.get(id(change))
        if hit is not None and hit[0] is change and hit[1] == snapshot:
            return hit[2]
        primary = resolve_change_identity(change).primary_id
        self.entries[id(change)] = (change, snapshot, primary)
        return primary
