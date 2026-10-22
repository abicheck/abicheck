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

"""Wording helper shared by the source-graph risk detectors.

Both detectors name a few example targets in a finding's description, so the
helper lives in one leaf module instead of being copied into each detector.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: How many example targets a description names per direction before "+N more".
EXAMPLE_LIMIT = 2


def example_names(targets: Sequence[str], labels: dict[str, str], prefix: str) -> str:
    """``" Added: a, b (+N more)."`` for *targets*, or ``""`` when there are none."""
    if not targets:
        return ""
    shown = ", ".join(labels.get(t, t) for t in targets[:EXAMPLE_LIMIT])
    more = len(targets) - EXAMPLE_LIMIT
    extra = f" (+{more} more)" if more > 0 else ""
    return f" {prefix} {shown}{extra}."
