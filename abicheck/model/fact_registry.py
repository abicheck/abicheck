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

"""The fact/capability registry (ADR-063 D7, Phase 5) -- public facade.

This module's own public surface (``FACT_REGISTRY``, ``FactDefinition``,
``FactLifecycle``, ``FactRegistry``, ``KNOWN_PRODUCING_BACKENDS``,
``REFERENCE_FLAG_COVERAGE``, ``KNOWN_UNCONVERTED_ELIGIBLE_FACTS``) is
unchanged from before this split; every existing ``from .fact_registry
import FACT_REGISTRY``-shaped call site keeps working. The real content
lives in two sibling modules, split out once the combined vocabulary +
entry-list content crossed this repo's 800-line new-file cap
(AI-readiness ``new-file-size`` gate):

- ``fact_registry_schema.py`` -- the ``FactLifecycle``/``FactDefinition``/
  ``FactRegistry`` vocabulary and the case-(a)/case-(b) unconverted-field
  allowlists.
- ``fact_registry_entries.py`` -- the actual ``FACT_REGISTRY =
  FactRegistry([...])`` entry list, D7's per-fact declarations.

Both import from ``fact_registry_schema.py``; neither imports from this
facade or from each other, so importing this module can never form a
cycle. See ``docs/contribute/plans/one-semantic-pipeline.md``'s Phase 5
section for the full design discussion this registry implements.
"""

from __future__ import annotations

from .fact_registry_entries import FACT_REGISTRY as FACT_REGISTRY
from .fact_registry_schema import (
    KNOWN_PRODUCING_BACKENDS as KNOWN_PRODUCING_BACKENDS,
    KNOWN_UNCONVERTED_ELIGIBLE_FACTS as KNOWN_UNCONVERTED_ELIGIBLE_FACTS,
    REFERENCE_FLAG_COVERAGE as REFERENCE_FLAG_COVERAGE,
    FactDefinition as FactDefinition,
    FactLifecycle as FactLifecycle,
    FactRegistry as FactRegistry,
)

__all__ = [
    "FACT_REGISTRY",
    "KNOWN_UNCONVERTED_ELIGIBLE_FACTS",
    "KNOWN_PRODUCING_BACKENDS",
    "REFERENCE_FLAG_COVERAGE",
    "FactDefinition",
    "FactLifecycle",
    "FactRegistry",
]
