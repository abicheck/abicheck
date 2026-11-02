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
"""A bundle's scope policy as ``compare`` arguments (``bundles.<id>.required``/
``on_incomplete``, see :mod:`abicheck.buildsource.project_bundles`).

``compare``'s release selection (ADR-065 S1) is keyed by the canonical
release-matching key of a member's *file* (``libfoo.so`` for
``libfoo.so.1.2``), not by the project's target id. A bundle names its
members by target id, so each id is translated through the baseline's own
member -> staged-binary map (``resolve-baseline``'s ``binary-paths``):
every member of a resolved bundle has one, because ``resolve_bundle``
refuses a baseline missing any member. Naming a required member by its
target id would select a key no discovered library carries, so the member
would read as expected-not-produced on a run where it is present.

Every member is selected: the required ones with ``--select-required``, the
rest with ``--select``. Leaving a member out would mark it out of scope and
drop it from the comparison, which is not what "optional" means here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..model.release_selection import BUNDLE_ON_INCOMPLETE_TO_SCOPE
from .extraction import _canonical_library_key

__all__ = ["BundleSelection", "BundleSelectionError", "bundle_selection"]


class BundleSelectionError(ValueError):
    """The policy cannot be stated as a selection; refuse rather than guess."""


@dataclass(frozen=True)
class BundleSelection:
    """``compare`` arguments for one bundle check."""

    required_keys: tuple[str, ...]
    optional_keys: tuple[str, ...]
    #: ``"block"``/``"warn"``, or ``""`` when the bundle declared none (the
    #: project's own ``scope.on_incomplete`` then decides).
    scope_on_incomplete: str

    def compare_args(self) -> list[str]:
        """``--select-required K``/``--select K`` tokens, required first."""
        out: list[str] = []
        for key in self.required_keys:
            out += ["--select-required", key]
        for key in self.optional_keys:
            out += ["--select", key]
        return out

    def set_line(self) -> str:
        """The ``--set`` assignment, or ``""``."""
        if not self.scope_on_incomplete:
            return ""
        return f"scope.on_incomplete={self.scope_on_incomplete}"


def bundle_selection(
    members: Sequence[str],
    required: Sequence[str],
    binary_paths: Mapping[str, str],
    on_incomplete: str = "",
) -> BundleSelection:
    """Translate a bundle's scope policy into canonical-key selections.

    *required* empty means every member is required. Raises
    :class:`BundleSelectionError` when a member has no baseline binary, a
    required id is not a member, two members share a canonical key, or a key
    cannot travel as one whitespace-split ``extra-args`` token.
    """
    if not members:
        raise BundleSelectionError("bundle has no members")
    if on_incomplete and on_incomplete not in BUNDLE_ON_INCOMPLETE_TO_SCOPE:
        raise BundleSelectionError(
            f"on_incomplete must be one of {sorted(BUNDLE_ON_INCOMPLETE_TO_SCOPE)}, "
            f"got {on_incomplete!r}"
        )
    outside = [r for r in required if r not in members]
    if outside:
        raise BundleSelectionError(
            f"required member(s) {outside!r} are not bundle members {list(members)!r}"
        )
    keys: dict[str, str] = {}
    owner: dict[str, str] = {}
    for member in members:
        path = binary_paths.get(member)
        if not path:
            raise BundleSelectionError(
                f"member {member!r} has no baseline binary to name it by"
            )
        key = _canonical_library_key(Path(path))
        if not key or any(ch.isspace() for ch in key):
            raise BundleSelectionError(
                f"member {member!r}: canonical key {key!r} cannot be passed as "
                "one selection argument"
            )
        if key in owner:
            raise BundleSelectionError(
                f"members {owner[key]!r} and {member!r} share the release key "
                f"{key!r}; a selection cannot tell them apart"
            )
        owner[key] = member
        keys[member] = key
    wanted = set(required) or set(members)
    return BundleSelection(
        required_keys=tuple(keys[m] for m in members if m in wanted),
        optional_keys=tuple(keys[m] for m in members if m not in wanted),
        scope_on_incomplete=BUNDLE_ON_INCOMPLETE_TO_SCOPE.get(on_incomplete, ""),
    )
