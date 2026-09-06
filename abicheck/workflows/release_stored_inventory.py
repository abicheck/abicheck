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

"""ADR-065 D2/D8: what a **stored** release side asserts about itself.

Two persisted markers a ``ProjectSnapshot`` package carries and a live
directory or archive never does: the per-member *degraded capture* marker
(D8 -- a member whose own dump failed, so the next run must read *missing
data*, not an impoverished old side) and the whole-side ``inventory_complete``
assertion (D2 -- the capture's statement that the selected variant's declared
composition is the whole release). Both fail closed: a damaged marker refuses
the comparison rather than reading as "nothing is degraded" / "no assertion
was made".

Split out of ``release_scope.py`` (ADR-065 S3), which reached the 800-line
production cap when the declared-component-inventory work landed there. The
record builder and these readers were only ever neighbours: nothing here
constructs a :class:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord`,
and nothing there reads a stored package's disk layout.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import SnapshotError

__all__ = [
    "StoredDegradedMembers",
    "stored_degraded_members",
    "stored_side_degraded_members",
    "stored_side_inventory_complete",
]


def stored_side_degraded_members(
    side_dir: Path, *, variant_id: str | None
) -> dict[str, str]:
    """One side's own persisted ADR-065 D8 marker, ``{release match key:
    reason}`` -- non-empty only for a stored ``ProjectSnapshot`` package
    (a live directory or archive carries none)."""
    from .release_package import resolve_release_package_degraded_members
    from .storage import is_project_snapshot_package_dir

    if not (side_dir.is_dir() and is_project_snapshot_package_dir(side_dir)):
        return {}
    try:
        return resolve_release_package_degraded_members(side_dir, variant_id=variant_id)
    except (SnapshotError, OSError, ValueError, TypeError, KeyError) as exc:
        # Fail closed (Codex review): a damaged marker section must not
        # read as "no member is degraded".
        raise SnapshotError(
            f"{side_dir}: the stored package's degraded-member marker could not "
            f"be read ({exc}); refusing to compare its members as complete evidence"
        ) from exc


@dataclass(frozen=True)
class StoredDegradedMembers:
    """Every *selected* member either side's stored ``ProjectSnapshot``
    package marks degraded (ADR-065 D8), split by where it sits in the
    release: ``matched`` (in both maps -- skipped by the fan-out and
    recorded ``failed``), ``old_unmatched``/``new_unmatched`` (in one map
    only -- fed to the record builder's ``old_failed``/``new_failed`` so
    the member is ``FAILED`` there, never ``NOT_SUPPLIED``: a degraded
    capture is not the kind of evidence a proven inventory on the other
    side may turn into a removal or an addition). Values are the reason
    text the scope record and library results carry."""

    matched: dict[str, str] = field(default_factory=dict)
    old_unmatched: dict[str, str] = field(default_factory=dict)
    new_unmatched: dict[str, str] = field(default_factory=dict)


def stored_side_inventory_complete(side_dir: Path, *, variant_id: str | None) -> bool:
    """One side's own persisted ADR-065 D2 ``inventory_complete`` assertion
    -- ``True`` only for a stored ``ProjectSnapshot`` package whose capture
    made it (a live directory or archive never does). Fails closed the way
    :func:`stored_side_degraded_members` does: a damaged composition must
    not read as "unasserted" any more than as "nothing is degraded"."""
    from .release_package import resolve_release_package_inventory_complete
    from .storage import is_project_snapshot_package_dir

    if not (side_dir.is_dir() and is_project_snapshot_package_dir(side_dir)):
        return False
    try:
        return resolve_release_package_inventory_complete(
            side_dir, variant_id=variant_id
        )
    except (SnapshotError, OSError, ValueError, TypeError, KeyError) as exc:
        raise SnapshotError(
            f"{side_dir}: the stored package's inventory assertion could not "
            f"be read ({exc}); refusing to compare its members as complete evidence"
        ) from exc


def stored_degraded_members(
    old_dir: Path,
    new_dir: Path,
    old_map: Mapping[str, Path],
    new_map: Mapping[str, Path],
    *,
    old_variant: str | None,
    new_variant: str | None,
) -> StoredDegradedMembers:
    """Read both sides' D8 markers and place each marked *selected* member
    (a key of *old_map*/*new_map*) into :class:`StoredDegradedMembers`.
    A live directory or archive side carries no marker and contributes
    nothing; a marker on a key neither map selects is not this run's
    concern (the reader already refused a key the package does not store).
    An OLD-only degraded member used to be dropped here as "not matched",
    which let a proven-complete NEW side promote it to a removal even though
    its OLD acquisition failed (Codex review, twenty-seventh round).
    """
    found: dict[str, str] = {}
    for label, side_dir, variant in (
        ("OLD", old_dir, old_variant),
        ("NEW", new_dir, new_variant),
    ):
        degraded = stored_side_degraded_members(side_dir, variant_id=variant)
        for key, reason in degraded.items():
            found.setdefault(
                key,
                f"{label} side was captured degraded ({reason}); comparison skipped (ADR-065 D8)",
            )
    result = StoredDegradedMembers()
    for key, reason in found.items():
        in_old, in_new = key in old_map, key in new_map
        if in_old and in_new:
            result.matched[key] = reason
        elif in_old:
            result.old_unmatched[key] = reason
        elif in_new:
            result.new_unmatched[key] = reason
    return result
