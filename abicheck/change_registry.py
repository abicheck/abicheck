# Copyright 2026 Nikolay Petrov
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

"""Single-declaration ChangeKind registry — colocated metadata.

Each ChangeKind declares ALL its metadata in one place:
  - default_verdict (BREAKING / API_BREAK / COMPATIBLE / COMPATIBLE_WITH_RISK)
  - impact text (human-readable explanation of what goes wrong)
  - is_addition flag (for ADDITION_KINDS subset of COMPATIBLE)
  - policy_overrides (per-policy verdict downgrades)

The classification sets (BREAKING_KINDS, COMPATIBLE_KINDS, etc.) and the
IMPACT_TEXT / POLICY_REGISTRY dicts are all DERIVED from this registry.
Adding a new ChangeKind = adding one entry to the appropriate taxonomy
module below — no shotgun surgery.

ADR-061 D9's catalog taxonomy repartition: this module no longer holds any
``ChangeKindMeta`` entries directly. The declarative data lives in five
taxonomy modules under ``abicheck/model/change_catalog/`` — ``symbols.py``,
``types.py``, ``platform.py``, ``build.py``, ``source.py`` (see each
module's own docstring for its scope and the categorization methodology) —
and this module is now purely an *assembly* point: it imports each
taxonomy's entry list and constructs the single production ``REGISTRY``
from their concatenation. See ``AGENTS.md``'s "Adding a new ChangeKind" for
which taxonomy module a new entry belongs in.

Architecture review: Problem A — eliminates scattered metadata across 5+ locations.
"""

from __future__ import annotations

from .model.change_catalog.build import BUILD_ENTRIES
from .model.change_catalog.platform import PLATFORM_ENTRIES
from .model.change_catalog.registry import (  # noqa: F401
    ChangeKindMeta as ChangeKindMeta,
    ChangeKindRegistry as ChangeKindRegistry,
    Verdict as Verdict,
)
from .model.change_catalog.source import SOURCE_ENTRIES
from .model.change_catalog.symbols import SYMBOLS_ENTRIES
from .model.change_catalog.types import TYPES_ENTRIES

REGISTRY = ChangeKindRegistry([
    *SYMBOLS_ENTRIES,
    *TYPES_ENTRIES,
    *PLATFORM_ENTRIES,
    *BUILD_ENTRIES,
    *SOURCE_ENTRIES,
])
