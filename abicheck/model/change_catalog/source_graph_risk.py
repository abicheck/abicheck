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


"""Source-graph risk kinds: a finding derived from a header or L5 graph's
approximate joins, never an artifact-proven break (G29 Phase 6).

Kept beside ``source.py`` so that module stays under its line cap. The entries
are registered in :mod:`abicheck.change_registry` with the other taxonomy
modules.
"""

from __future__ import annotations

from .registry import ChangeEntity, ChangeKindMeta, ChangeOperation, Verdict

_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation

SOURCE_GRAPH_RISK_ENTRIES: list[ChangeKindMeta] = [
    _E(
        "public_consumer_compiled_dependency_changed",
        _R,
        impact="A public inline or template body compiled into every consumer's code changed its dependencies; consumers must recompile. Header-graph risk, never a proven break: no consumer build evidence exists.",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
]
