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

"""ADR-061 D9's target owner for the ``ChangeKind`` catalog.

``registry.py`` holds ``Verdict``, ``ChangeKindMeta``, ``ChangeKindRegistry``,
and the catalog-validation logic D9 assigns to the assembled registry. The
397-entry data table itself is fully repartitioned by taxonomy into five
sibling modules — ``symbols.py``, ``types.py``, ``platform.py``,
``build.py``, ``source.py`` (see each module's own docstring for its scope
and categorization methodology) — which ``abicheck/change_registry.py``
imports and concatenates into the single production ``REGISTRY``.
"""

from __future__ import annotations

from .registry import (
    TEMPLATE_VOCAB as TEMPLATE_VOCAB,
    VALID_BASE_POLICIES as VALID_BASE_POLICIES,
    ChangeKindMeta as ChangeKindMeta,
    ChangeKindRegistry as ChangeKindRegistry,
    Verdict as Verdict,
)

__all__ = [
    "TEMPLATE_VOCAB",
    "VALID_BASE_POLICIES",
    "ChangeKindMeta",
    "ChangeKindRegistry",
    "Verdict",
]
