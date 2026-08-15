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

"""The one-line ``LABEL: detail (N total) [...]`` summary format.

A standalone leaf module (not folded into ``reporter_markdown.py``, which is
already at the AI-readiness file-size cap) so two callers can share the exact
text layout instead of drifting apart: :func:`reporter_markdown.to_stat` (the
full-library one-liner) and ``cli_compare_fold._ScopedFold.into_oneline``
(CLI cleanup phase two, PR 1's ``--profile quick`` + ``--used-by``/
``--required-symbol`` scoped-gate fix, which renders the identical format
from the *scoped* verdict/counts instead).
"""

from __future__ import annotations


def format_stat_line(
    label: str,
    *,
    breaking: int,
    source_breaks: int,
    risk_count: int,
    compatible_additions: int,
    total_changes: int,
    redundant_count: int = 0,
    gate_note: str = "",
) -> str:
    """Render already-resolved counts/label as the one-line summary."""
    parts = []
    if breaking:
        parts.append(f"{breaking} breaking")
    if source_breaks:
        parts.append(f"{source_breaks} source-level breaks")
    if risk_count:
        parts.append(f"{risk_count} risk")
    if compatible_additions:
        parts.append(f"{compatible_additions} compatible")
    detail = ", ".join(parts) if parts else "no changes"
    redundant_note = (
        f" [{redundant_count} redundant hidden]" if redundant_count > 0 else ""
    )
    return f"{label}: {detail} ({total_changes} total){redundant_note}{gate_note}"
