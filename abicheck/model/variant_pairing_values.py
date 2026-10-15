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

"""ADR-062 D9 / storage-format-v2 A1.6: the result of pairing two stored
packages' ``VariantRef`` sets by ``variant_id``.

The value types only; the pairing algorithm is
``compare.variant_pairing.pair_variant_views`` and reading the views off a
package is ``workflows.bundle_variants_capture.package_variant_views``.

Three outcomes per variant id, following ADR-065's "absent is not removed":

* ``paired`` -- both packages carry the variant. ``declared_changes`` is a
  **variant-boundary change** (the variant's *identity* coordinates moved:
  target triple, compiler family, a feature toggle), reported distinctly from
  ``captured_changes`` (state that may legitimately change inside one variant
  between releases: compiler version, producer string -- an ordinary version
  bump).
* ``unmatched_old`` / ``unmatched_new`` -- only one package carries it. This
  is *never* a removal/addition claim: a package lacking a variant says only
  that its capture did not produce it (an optional variant may simply have
  been skipped). ``required`` is the carrying side's own recorded
  ``required:`` flag (``None`` when the package predates it), so a required
  variant without a counterpart is visible as exactly that.

A leaf ``model`` module (standard library only).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PAIRED",
    "UNMATCHED_NEW",
    "UNMATCHED_OLD",
    "VARIANT_PAIRING_SCHEMA_VERSION",
    "VariantPair",
    "VariantPairing",
    "VariantView",
]

#: Bumped when the ``variant_pairing`` report block changes shape.
VARIANT_PAIRING_SCHEMA_VERSION = 1

PAIRED = "paired"
UNMATCHED_OLD = "unmatched_old"
UNMATCHED_NEW = "unmatched_new"


@dataclass(frozen=True)
class VariantView:
    """What one package records about one variant: its id, both coordinate
    maps (never merged), and its ``required`` flag (``None`` = unrecorded)."""

    variant_id: str
    declared: Mapping[str, str] = field(default_factory=dict)
    captured: Mapping[str, str] = field(default_factory=dict)
    required: bool | None = None


def _changes(
    old: Mapping[str, str], new: Mapping[str, str]
) -> dict[str, dict[str, str | None]]:
    return {
        key: {"old": old.get(key), "new": new.get(key)}
        for key in sorted(set(old) | set(new))
        if old.get(key) != new.get(key)
    }


@dataclass(frozen=True)
class VariantPair:
    """One variant id's pairing outcome (see the module docstring)."""

    variant_id: str
    status: str
    old: VariantView | None = None
    new: VariantView | None = None

    def __post_init__(self) -> None:
        expected = {
            PAIRED: (True, True),
            UNMATCHED_OLD: (True, False),
            UNMATCHED_NEW: (False, True),
        }.get(self.status)
        if expected is None:
            raise ValueError(f"unknown variant pairing status {self.status!r}")
        if (self.old is not None, self.new is not None) != expected:
            raise ValueError(
                f"variant pair {self.variant_id!r}: status {self.status!r} "
                "does not match which sides are present"
            )

    @property
    def declared_changes(self) -> dict[str, dict[str, str | None]]:
        """Identity-coordinate differences -- a variant-boundary change."""
        if self.old is None or self.new is None:
            return {}
        return _changes(self.old.declared, self.new.declared)

    @property
    def captured_changes(self) -> dict[str, dict[str, str | None]]:
        """Observed-state differences inside one variant (a version bump)."""
        if self.old is None or self.new is None:
            return {}
        return _changes(self.old.captured, self.new.captured)

    @property
    def variant_boundary_changed(self) -> bool:
        return bool(self.declared_changes)

    @property
    def required(self) -> bool | None:
        """The carrying side's ``required`` flag (NEW's wins when paired)."""
        for view in (self.new, self.old):
            if view is not None and view.required is not None:
                return view.required
        return None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "variant_id": self.variant_id,
            "status": self.status,
            "required": self.required,
        }
        for side, view in (("old", self.old), ("new", self.new)):
            if view is not None:
                out[side] = {
                    "declared": dict(view.declared),
                    "captured": dict(view.captured),
                    "required": view.required,
                }
        if self.status == PAIRED:
            out["variant_boundary_changed"] = self.variant_boundary_changed
            out["declared_changes"] = self.declared_changes
            out["captured_changes"] = self.captured_changes
        else:
            out["reason"] = (
                f"only the {'OLD' if self.status == UNMATCHED_OLD else 'NEW'} "
                "package carries this variant; absence is not removal -- the "
                "other capture did not produce it"
            )
        return out


@dataclass(frozen=True)
class VariantPairing:
    """Every variant id either package carries, exactly once, sorted by id,
    plus which variant each side's library comparison actually ran over
    (``None`` when that side is not a stored package)."""

    pairs: tuple[VariantPair, ...]
    compared_old: str | None = None
    compared_new: str | None = None

    def __post_init__(self) -> None:
        ids = [p.variant_id for p in self.pairs]
        if len(ids) != len(set(ids)):
            raise ValueError("variant pairing lists a variant_id twice")
        object.__setattr__(
            self, "pairs", tuple(sorted(self.pairs, key=lambda p: p.variant_id))
        )

    @property
    def boundary_changes(self) -> tuple[VariantPair, ...]:
        return tuple(p for p in self.pairs if p.variant_boundary_changed)

    @property
    def unmatched_required(self) -> tuple[VariantPair, ...]:
        """Unmatched variants whose carrying side recorded ``required: true``."""
        return tuple(p for p in self.pairs if p.status != PAIRED and p.required is True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VARIANT_PAIRING_SCHEMA_VERSION,
            "compared": {"old": self.compared_old, "new": self.compared_new},
            "pairs": [p.to_dict() for p in self.pairs],
            "variant_boundary_changes": [p.variant_id for p in self.boundary_changes],
            "unmatched_required": [p.variant_id for p in self.unmatched_required],
        }
