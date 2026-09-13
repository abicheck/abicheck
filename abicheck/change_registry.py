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

from typing import TYPE_CHECKING

from .model.change_catalog.build import BUILD_ENTRIES
from .model.change_catalog.platform import PLATFORM_ENTRIES
from .model.change_catalog.registry import (  # noqa: F401
    ChangeEntity as ChangeEntity,
    ChangeKindMeta as ChangeKindMeta,
    ChangeKindRegistry as ChangeKindRegistry,
    ChangeOperation as ChangeOperation,
    Verdict as Verdict,
)
from .model.change_catalog.source import SOURCE_ENTRIES
from .model.change_catalog.symbols import SYMBOLS_ENTRIES
from .model.change_catalog.types import TYPES_ENTRIES

if TYPE_CHECKING:
    from collections.abc import Iterable

REGISTRY = ChangeKindRegistry(
    [
        *SYMBOLS_ENTRIES,
        *TYPES_ENTRIES,
        *PLATFORM_ENTRIES,
        *BUILD_ENTRIES,
        *SOURCE_ENTRIES,
    ]
)


def unanimous_entity_for(kind_values: Iterable[str]) -> str | None:
    """The display entity every kind in *kind_values* agrees on, else ``None``.

    For an *overlay* finding a detector composes from one or more triggering
    changes: the overlay is about whatever they were about. ``internal_symbol_
    required_by_public_api`` is emitted for a removed internal function and
    equally for a removed internal global a public inline function references,
    so one fixed entity hid the latter from ``--view show=variables`` (Codex
    review, PR #1284).

    Unanimous-or-nothing rather than first-wins: a decl that changed several
    ways at once has no single honest entity, and ``None`` applies the
    catalog's declared fallback -- coarse rather than wrong. Lives here
    because it is a query over ``REGISTRY``, not detector logic; contract in
    ``tests/test_change_catalog_dimensions.py``.
    """
    entities = {REGISTRY.entity_for(k) for k in kind_values}
    if len(entities) != 1:
        return None
    only = entities.pop()
    return None if only is None else str(only.value)
