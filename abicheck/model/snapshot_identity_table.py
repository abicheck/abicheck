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

"""The one entry point every graph producer uses to build a real
``AbiSnapshot``'s identity table (evidence-entity-model invariant I1).

``model.graph_entity_identity.snapshot_identities`` stays a leaf -- it never
imports ``model.snapshot`` -- so the export evidence its ctor/dtor variant
rule reads is supplied here, once, for every producer: the L2 header graph,
the public-surface builder and the ``exports``/``debug_type_of`` joins all
call :func:`identities_for_snapshot` and therefore agree on every node id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .export_index import snapshot_export_names
from .graph_entity_identity import SnapshotIdentities, snapshot_identities

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = ["identities_for_snapshot"]


def identities_for_snapshot(snap: AbiSnapshot) -> SnapshotIdentities:
    """:func:`snapshot_identities` with the snapshot's own export tables."""
    return snapshot_identities(snap, export_names=snapshot_export_names(snap))
