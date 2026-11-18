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

"""The ``call_axis_shadow`` block (report schema 7.6, ADR-094 D9).

Projects ``DiffResult.call_axis_shadow`` (built by
``workflows/call_axis_shadow.py``) into the JSON report and the Markdown
report. Advisory: the block states what the ``call`` axis would say about
each function whose signature changed, and where that disagrees with the
existing verdict; it never changes either.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .markdown_text import md_text

if TYPE_CHECKING:
    from ..checker_types import DiffResult

__all__ = ["add_call_axis_shadow", "render_call_axis_shadow_from_mapping"]

_DISAGREEMENT_TEXT = {
    "breaking_but_call_preserved": "verdict says breaking; old callers still call it correctly",
    "compatible_but_call_broken": "verdict says compatible; old callers no longer call it correctly",
}


def add_call_axis_shadow(d: dict[str, object], result: DiffResult) -> None:
    """Attach ``call_axis_shadow`` to JSON mapping *d* when it was computed."""
    if result.call_axis_shadow:
        d["call_axis_shadow"] = [dict(e) for e in result.call_axis_shadow]


def render_call_axis_shadow_from_mapping(entries: Any) -> list[str]:
    """The Markdown section for the shadow ``call`` axis; ``[]`` when absent."""
    rows = [e for e in entries or () if isinstance(e, Mapping)]
    if not rows:
        return []
    lines = [
        "## Call axis (shadow, ADR-094)",
        "",
        "> Advisory only: does not change the verdict or the exit code.",
        "",
        "| Function | Call axis | Existing verdict | Disagreement | Reason |",
        "|----------|-----------|------------------|--------------|--------|",
    ]
    for e in rows:
        disagreement = e.get("disagreement")
        note = _DISAGREEMENT_TEXT.get(str(disagreement), "") if disagreement else ""
        lines.append(
            f"| {md_text(e.get('symbol', ''), code=True, table=True)} "
            f"| {md_text(e.get('status', ''), table=True)} "
            f"| {md_text(e.get('existing_verdict', ''), table=True)} "
            f"| {md_text(note, table=True) if note else '—'} "
            f"| {md_text(e.get('reason', ''), table=True)} |"
        )
    lines.append("")
    return lines
