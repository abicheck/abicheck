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

"""Per-library public API added/removed lists for the release report (#1706).

A directory/package ``compare`` stated each member's addition count but no
removal or modification count, and the only way to see *which* declarations
moved was ``--view show=added,removed``, whose output was dominated by leaked
C++ internals. This module projects the member's already-computed surface
grouping (:func:`~abicheck.report.surface_changes.compute_surface_changes` --
no new detection, no new policy) into:

* ``surface_removals`` / ``surface_modifications`` -- the inclusive counts
  the Libraries table's ``Removed``/``Changed`` columns show;
* ``public_api`` -- the added/removed symbol lists, with every symbol whose
  owning scope is an internal namespace or an RTTI artifact
  (:func:`~abicheck.model.symbol_ownership.symbol_origin`, the classifier the
  report's existing public/internal breakdown uses) collapsed into an
  ``internal_omitted`` count. Collapsed, never dropped (ADR-067): the count is
  always stated, and the member's complete findings stay in the report.

``symbol_origin`` answers naming convention, not contract membership, so the
split is a *display* grouping only; it moves no verdict and no count.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..model.symbol_ownership import symbol_origin
from .change_operation import entity_for_change, operation_for_kind
from .surface_changes import PUBLIC_SURFACE_ENTITIES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import DiffResult

__all__ = [
    "MAX_PUBLIC_API_ITEMS",
    "member_public_api",
    "render_release_public_api_markdown",
    "split_public_internal",
    "stamp_member_public_api",
]

#: Per-group cap on the Markdown list; the JSON block is never capped.
MAX_PUBLIC_API_ITEMS = 50


def split_public_internal(symbols: Sequence[str]) -> tuple[list[str], int]:
    """Return the sorted, de-duplicated public symbols and the internal count."""
    public: set[str] = set()
    internal = 0
    for symbol in symbols:
        if symbol_origin(symbol) == "public":
            public.add(symbol)
        else:
            internal += 1
    return sorted(public), internal


def member_public_api(result: DiffResult) -> dict[str, object]:
    """The counts and ``public_api`` block for one member's *result*.

    Applies :func:`~abicheck.report.surface_changes.compute_surface_changes`'s
    exact grouping (public-surface entity, then catalog operation) directly
    over ``result.changes``. The grouping reads only the change's kind and
    entity -- never its resolved verdict/category -- so building a full
    ``ReportFinding``/``SurfaceChangeEntry`` per change (policy resolution for
    every change of every member) was pure overhead on the release path,
    where it dominated peak RSS at many members.
    """
    added_syms: list[str] = []
    removed_syms: list[str] = []
    modifications = 0
    for change in result.changes:
        kind = change.kind
        kind_value = kind.value if hasattr(kind, "value") else str(kind)
        if entity_for_change(change, kind_value) not in PUBLIC_SURFACE_ENTITIES:
            continue
        operation = operation_for_kind(kind_value)
        if operation == "added":
            added_syms.append(change.symbol)
        elif operation == "removed":
            removed_syms.append(change.symbol)
        else:
            modifications += 1
    added, added_internal = split_public_internal(added_syms)
    removed, removed_internal = split_public_internal(removed_syms)
    return {
        "surface_removals": len(removed_syms),
        "surface_modifications": modifications,
        "public_api": {
            "added": added,
            "removed": removed,
            "internal_omitted": {"added": added_internal, "removed": removed_internal},
        },
    }


def stamp_member_public_api(entry: dict[str, object], result: DiffResult) -> None:
    """Stamp :func:`member_public_api`'s fields onto a release entry."""
    entry.update(member_public_api(result))


def _group_lines(label: str, symbols: Sequence[str], internal: int) -> list[str]:
    lines = [f"**{label}** ({len(symbols)} public)", ""]
    shown = list(symbols[:MAX_PUBLIC_API_ITEMS])
    lines += [f"- `{s}`" for s in shown]
    if not shown:
        lines.append("- none")
    if len(symbols) > len(shown):
        lines.append(f"- … {len(symbols) - len(shown)} more omitted from this view")
    if internal:
        noun = "symbol" if internal == 1 else "symbols"
        lines.append(f"- _{internal} internal {noun} omitted_")
    lines.append("")
    return lines


def render_release_public_api_markdown(
    library_results: Sequence[Mapping[str, Any]],
) -> list[str]:
    """``## Public API changes``: per library, the added/removed public
    symbols. A library with nothing added or removed is skipped; the section
    is absent when every library is."""
    out: list[str] = []
    for lib in library_results:
        block = lib.get("public_api")
        if not isinstance(block, Mapping):
            continue
        added = list(block.get("added") or ())
        removed = list(block.get("removed") or ())
        omitted = block.get("internal_omitted") or {}
        added_int = int(omitted.get("added", 0) or 0)
        removed_int = int(omitted.get("removed", 0) or 0)
        if not (added or removed or added_int or removed_int):
            continue
        out += ["", f"### `{lib.get('library')}`", ""]
        out += _group_lines("Added", added, added_int)
        out += _group_lines("Removed", removed, removed_int)
    return ["", "## Public API changes", *out] if out else []
