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

"""The record an unobserved-dimension provider returns (leaf module).

Kept apart from the registry in ``_unobserved.py`` so each gap module can
build findings without importing the registry that imports it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

__all__ = ["Side", "UnobservedFinding", "gap_and_notes"]

Side = Literal["old", "new"]


@dataclass(frozen=True, slots=True)
class UnobservedFinding:
    """One side's unobserved evidence for one dimension.

    *reason* is the exact assurance note text. *lowers_status* is False only
    for an informational finding that is reported but must not move the
    rollup (an ``unrecorded`` section of a pre-availability-table document).
    """

    dimension: str
    side: Side
    reason: str
    lowers_status: bool = True


def gap_and_notes(findings: Iterable[UnobservedFinding]) -> tuple[bool, list[str]]:
    """The ``(gap, notes)`` view of one provider's findings."""
    items = list(findings)
    return any(f.lowers_status for f in items), [f.reason for f in items]
