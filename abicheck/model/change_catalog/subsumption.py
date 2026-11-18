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

"""Read-only view of the declared ``subsumed_by`` relations (ADR-093 D4).

A finding is *subsumed* when another finding on the same subject makes it
redundant (today: an AST-lane finding that reports the same fact a DWARF-lane
finding reports). The declaration lives on the registry entry; this module is
the one model-side read of it, so ``compare/`` can consume the relation
without importing ``policy/``. Resolution and disposition recording stay in
``policy/kind_relations.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .assembled import REGISTRY
from .observability import Subsumption


def _subsumption_index() -> Mapping[str, tuple[Subsumption, ...]]:
    """``{kind value: its declared subsumed_by}``, for kinds that declare any."""
    return MappingProxyType(
        {
            kind: meta.relations.subsumed_by
            for kind, meta in REGISTRY.entries.items()
            if meta.relations.subsumed_by
        }
    )


#: Computed once: the registry is immutable.
SUBSUMED_BY: Mapping[str, tuple[Subsumption, ...]] = _subsumption_index()


def subsumptions_of(kind: str) -> tuple[Subsumption, ...]:
    """The ``(kind, key)`` pairs *kind* declares it is subsumed by, or ``()``."""
    return SUBSUMED_BY.get(kind, ())


#: ``{kind value: the kind it reports the same event as}`` (ADR-093 D4
#: ``same_event_as``): an ELF-only export loss and the header-aware visibility
#: finding that names the same event. Computed once from the registry.
SAME_EVENT_AS: Mapping[str, str] = MappingProxyType(
    {
        kind: meta.relations.same_event_as
        for kind, meta in REGISTRY.entries.items()
        if meta.relations.same_event_as
    }
)
