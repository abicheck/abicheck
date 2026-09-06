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

"""E-S1 (docs/contribute/plans/vision-api-abi-evolution.md section E /
cli-cleanup-phase-two.md Block 5): which registered layout-bearing
detectors ran with zero usable DWARF/DWARF-advanced evidence on either
side of a comparison.

Split out of ``analysis_assurance.py`` (which sits at this repo's
``architecture/debt.yaml`` no-growth baseline) rather than added there --
this module depends only on ``model.AbiSnapshot``, a real leaf.

**The gap this closes:** a snapshot pair with no debug info on either side
still gets an ordinary ``DetectorResult(enabled=True, changes_count=0)``
from the ``layout_descriptor`` detector -- it registers with no
``requires_support`` gate at all, unlike ``dwarf``/``advanced_dwarf``,
which ``diff_platform._has_any_dwarf`` already keeps from running in this
exact case (recorded ``not_evaluated``, C-S1/#1082 -- deliberately left
untouched by this slice, see this function's own docstring). That zero is
indistinguishable from "checked this pair's layout, found nothing" without
an independent signal derived straight from each side's own evidence,
rather than from ``DetectorRegistry`` state -- this module is that signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import AbiSnapshot

__all__ = ["DWARF_ONLY_LAYOUT_DETECTORS", "layout_unverified_detectors"]

#: Registered detector names (``detector_registry.registry`` names) whose
#: own layout findings this module knows rest entirely on basic-``dwarf``/
#: ``dwarf_advanced`` evidence -- see
#: :attr:`analysis_assurance.AnalysisAssurance.layout_unverified_detectors`.
DWARF_ONLY_LAYOUT_DETECTORS: tuple[str, ...] = (
    "dwarf",
    "advanced_dwarf",
    "layout_descriptor",
)


def layout_unverified_detectors(old: AbiSnapshot, new: AbiSnapshot) -> tuple[str, ...]:
    """Which of :data:`DWARF_ONLY_LAYOUT_DETECTORS` ran (or were skipped)
    with zero usable DWARF/DWARF-advanced evidence on *either* side of
    *old*/*new* -- see this module's own docstring.
    """

    def _has_dwarf(meta: Any) -> bool:
        return bool(meta is not None and meta.has_dwarf)

    if (
        _has_dwarf(old.dwarf)
        or _has_dwarf(new.dwarf)
        or _has_dwarf(old.dwarf_advanced)
        or _has_dwarf(new.dwarf_advanced)
    ):
        return ()
    return DWARF_ONLY_LAYOUT_DETECTORS
