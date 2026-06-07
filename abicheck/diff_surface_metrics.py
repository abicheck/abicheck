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

"""Surface-metric drift detection (ADR-027 A1 / D1.2).

Computes the same single-snapshot metrics as ``surface-report`` for the old and
new snapshots and surfaces the *aggregate deltas* as informational
``COMPATIBLE`` findings. These never drive a breaking verdict on their own — the
individual additions/removals are already reported per-symbol; this is the net
roll-up signal, emitted only with ``--surface-metrics``.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .model import AbiSnapshot
from .surface_graph import SurfaceMetrics, compute_surface_metrics

# Minimum rise in the undocumented-export fraction (in absolute ratio points)
# before it is reported, so floating-point noise / a single symbol on a tiny
# surface does not trip the signal.
_RATIO_EPSILON = 0.01


def _public_decl_count(m: SurfaceMetrics) -> int:
    return m.public_functions + m.public_variables + m.public_types + m.public_enums


def diff_surface_metrics(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Return informational metric-drift findings between *old* and *new*.

    Deterministic and side-effect free. All emitted findings are COMPATIBLE.
    """
    om = compute_surface_metrics(old)
    nm = compute_surface_metrics(new)
    changes: list[Change] = []

    old_count = _public_decl_count(om)
    new_count = _public_decl_count(nm)
    if new_count > old_count:
        changes.append(
            Change(
                kind=ChangeKind.PUBLIC_SURFACE_GREW,
                symbol="<surface>",
                description=(
                    f"public surface grew: {old_count} → {new_count} "
                    f"declarations (+{new_count - old_count})"
                ),
                old_value=str(old_count),
                new_value=str(new_count),
            )
        )
    elif new_count < old_count:
        changes.append(
            Change(
                kind=ChangeKind.PUBLIC_SURFACE_SHRANK,
                symbol="<surface>",
                description=(
                    f"public surface shrank: {old_count} → {new_count} "
                    f"declarations ({new_count - old_count})"
                ),
                old_value=str(old_count),
                new_value=str(new_count),
            )
        )

    if nm.undocumented_export_ratio - om.undocumented_export_ratio > _RATIO_EPSILON:
        changes.append(
            Change(
                kind=ChangeKind.UNDOCUMENTED_EXPORT_RATIO_INCREASED,
                symbol="<surface>",
                description=(
                    "undocumented-export ratio rose: "
                    f"{om.undocumented_export_ratio:.1%} → "
                    f"{nm.undocumented_export_ratio:.1%} "
                    "(symbols exported without a public header)"
                ),
                old_value=f"{om.undocumented_export_ratio:.4f}",
                new_value=f"{nm.undocumented_export_ratio:.4f}",
            )
        )

    return changes
