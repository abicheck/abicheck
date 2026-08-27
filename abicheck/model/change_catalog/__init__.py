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

"""ADR-061 D9's target owner for the ``ChangeKind`` catalog's core types.

``registry.py`` holds ``Verdict``, ``ChangeKindMeta``, ``ChangeKindRegistry``,
and the catalog-validation logic D9 assigns to the assembled registry. The
397-entry data table itself (``change_registry.py`` and its
``change_registry_<topic>.py`` siblings) has not yet been repartitioned into
this package — see the ADR's Phase 5 section for the remaining scope.
"""

from __future__ import annotations

from .registry import (
    TEMPLATE_VOCAB as TEMPLATE_VOCAB,
    VALID_BASE_POLICIES as VALID_BASE_POLICIES,
    ChangeKindMeta as ChangeKindMeta,
    ChangeKindRegistry as ChangeKindRegistry,
    Verdict as Verdict,
)
