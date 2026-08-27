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

"""Compatibility re-export shim for the ChangeKind registry's core types.

The real implementation (``Verdict``, ``ChangeKindMeta``,
``ChangeKindRegistry``, ``VALID_BASE_POLICIES``, ``TEMPLATE_VOCAB``, and the
D9 catalog-validation logic) moved to ``abicheck.model.change_catalog.registry``
(ADR-061 Phase 5, D9's target owner for this logic) — this module re-exports
every name unchanged so every existing import path
(``from abicheck.change_registry_types import Verdict``, and the transitive
``from abicheck.change_registry import Verdict``) keeps working. New callers
should import from ``abicheck.model.change_catalog.registry`` directly rather
than through this shim.
"""

from __future__ import annotations

from .model.change_catalog.registry import (
    _VERDICT_BLIND_POLICIES as _VERDICT_BLIND_POLICIES,
    TEMPLATE_VOCAB as TEMPLATE_VOCAB,
    VALID_BASE_POLICIES as VALID_BASE_POLICIES,
    ChangeKindMeta as ChangeKindMeta,
    ChangeKindRegistry as ChangeKindRegistry,
    Verdict as Verdict,
    _validate_entry as _validate_entry,
)
