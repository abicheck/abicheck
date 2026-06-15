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

"""Core types for the single-declaration ChangeKind registry.

Leaf module holding the verdict enum, the per-kind metadata dataclass, and
the registry container that derives the classification sets. These are split
out of ``change_registry.py`` (which keeps the large ``REGISTRY`` data table)
so the data table file stays under the source-size cap. ``change_registry``
re-exports every name here, so the public import surface
(``from abicheck.change_registry import Verdict``) is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    COMPATIBLE = "COMPATIBLE"
    COMPATIBLE_WITH_RISK = "COMPATIBLE_WITH_RISK"
    API_BREAK = "API_BREAK"
    BREAKING = "BREAKING"


@dataclass(frozen=True)
class ChangeKindMeta:
    """All metadata for a single ChangeKind, declared in one place."""

    kind: str  # ChangeKind enum value (e.g. "func_removed")
    default_verdict: Verdict
    impact: str = ""
    is_addition: bool = False
    policy_overrides: dict[str, Verdict] = field(default_factory=dict)
    # Optional ``str.format``-style template for a finding's per-change
    # ``description`` (C6). Detectors build their Change via
    # ``diff_helpers.make_change`` and pass structured fields rather than
    # hand-rolling an f-string, so the wording for a kind lives in one place.
    # Placeholders are drawn from the fixed vocabulary
    # ``{symbol} {name} {old} {new} {detail}`` (``make_change`` validates this).
    # ``None`` means the kind keeps a *bespoke* per-call-site description — used
    # when the text embeds computed offsets, demangled signatures, vtable slot
    # indices, counts, etc. that no fixed template can express.
    description_template: str | None = None


class ChangeKindRegistry:
    """Registry of ChangeKindMeta entries, deriving classification sets.

    Usage::

        registry = ChangeKindRegistry(entries)
        breaking = registry.kinds_for_verdict(Verdict.BREAKING)
        impact = registry.impact_for("func_removed")
    """

    def __init__(self, entries: list[ChangeKindMeta]) -> None:
        self._entries: dict[str, ChangeKindMeta] = {}
        for e in entries:
            if e.kind in self._entries:
                raise ValueError(f"Duplicate registry entry for {e.kind!r}")
            self._entries[e.kind] = e

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, kind_value: str) -> bool:
        return kind_value in self._entries

    def get(self, kind_value: str) -> ChangeKindMeta | None:
        return self._entries.get(kind_value)

    def kinds_for_verdict(self, verdict: Verdict) -> frozenset[str]:
        """Return all kind values whose default_verdict matches."""
        return frozenset(
            e.kind for e in self._entries.values() if e.default_verdict == verdict
        )

    def addition_kinds(self) -> frozenset[str]:
        """Return kind values flagged as additions (subset of COMPATIBLE)."""
        return frozenset(e.kind for e in self._entries.values() if e.is_addition)

    def policy_overrides_for(self, policy: str) -> dict[str, Verdict]:
        """Return {kind_value: overridden_verdict} for a given policy name."""
        return {
            e.kind: e.policy_overrides[policy]
            for e in self._entries.values()
            if policy in e.policy_overrides
        }

    def impact_text(self) -> dict[str, str]:
        """Return {kind_value: impact} for all entries with non-empty impact."""
        return {e.kind: e.impact for e in self._entries.values() if e.impact}

    def description_template_for(self, kind_value: str) -> str | None:
        """Return the description template for a kind, or None if bespoke/unknown."""
        e = self._entries.get(kind_value)
        return e.description_template if e is not None else None

    def templated_kinds(self) -> frozenset[str]:
        """Return kind values that own a description template (C6 migration set)."""
        return frozenset(
            e.kind for e in self._entries.values() if e.description_template is not None
        )

    @property
    def entries(self) -> dict[str, ChangeKindMeta]:
        return dict(self._entries)
