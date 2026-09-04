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

"""The fact/capability registry's assembled entry list (ADR-063 D7, Phase 5).

A pure assembly point, the same shape ``change_registry.py`` already is for
``ChangeKindMeta``: the real ``FactDefinition`` entries live in three
owner-family sibling modules (``fact_registry_entries_types.py``,
``_symbols.py``, ``_platform.py``), and this module only concatenates them
into the single production ``FACT_REGISTRY``. Registration order is
types -> symbols -> platform; ``FactRegistry`` itself is keyed by
``FactDefinition.id``, so the order is presentational (it is what
``gen_fact_capability_matrix.py`` renders) rather than semantic.

Imports its vocabulary from ``fact_registry_schema.py`` (not from
``fact_registry.py`` itself) so the dependency stays one-directional --
``fact_registry.py`` is a thin facade that imports ``FACT_REGISTRY`` back
from here and re-exports it, and a facade importing its own entries while
those entries import the facade would be a real cycle. Every existing
``from .fact_registry import FACT_REGISTRY`` call site is unaffected."""

from __future__ import annotations

from .fact_registry_entries_platform import PLATFORM_FACTS
from .fact_registry_entries_symbols import SYMBOL_FACTS
from .fact_registry_entries_types import TYPE_FACTS
from .fact_registry_schema import FactRegistry

__all__ = ["FACT_REGISTRY"]

FACT_REGISTRY = FactRegistry([*TYPE_FACTS, *SYMBOL_FACTS, *PLATFORM_FACTS])
