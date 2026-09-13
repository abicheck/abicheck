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

"""ADR-027's pattern-verdict ledger, projected into Markdown.

A sibling of ``contract_conflicts_markdown.py``: one section, a pure
render over already-resolved plain values from the shared
``ReportDocument``. Its own module rather than another block inside
``render_markdown_document.py``, which sits at its recorded
``architecture/debt.yaml`` no-growth baseline.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .markdown_text import md_cell


def render_pattern_modulations_from_mapping(
    d: Any, *, include_heading: bool = True
) -> list[str]:
    """The ADR-027 pattern-verdict ledger, rendered into the human artifact.

    A pattern rule that demotes a breaking finding is a *disposition*, so
    ADR-067's record-before-disposing rule applies to the report a user asked
    for, not only to the terminal: without this the rule and its reason
    reached stderr alone, and retiring `--view patterns` removed the last way
    to request them in the document (Codex review, PR #1284).

    The canonical `ReportDocument` already carried `pattern_modulations` --
    the JSON projection has emitted it all along -- so only this projection
    was missing. Absent when no rule fired, which is every run with ADR-027's
    opt-in `--pattern-verdicts` off, i.e. the default.
    """
    # `rule_id`, which is what `PatternModulation.to_dict()` actually writes
    # (and what `cli_audit` reads) -- not `rule`. Reading the wrong key
    # rendered `?` in every real report's Rule column while a hand-built
    # fixture passed, because the fixture was invented here rather than taken
    # from the producer (Codex review, PR #1284). The tests now build the
    # real dataclass.
    if not d:
        return []
    rows = [m for m in d if isinstance(m, Mapping)]
    if not rows:
        return []
    # `include_heading=False` for a caller that already opened its own
    # section and repeats this table per library (the release fan-out): the
    # rows are shared so the two documents cannot disagree about a
    # modulation's columns, while the surrounding structure is the caller's.
    lines = (
        [
            "",
            "## 🔁 Pattern-modulated findings",
            "",
            "A rule changed these findings' verdicts. Listed because an "
            "accepted result may not hide why it was accepted.",
            "",
        ]
        if include_heading
        else [""]
    )
    lines += [
        "| Symbol | Rule | Reason |",
        "|---|---|---|",
    ]
    for m in rows:
        lines.append(
            f"| `{md_cell(m.get('symbol', '?'))}` "
            f"| `{md_cell(m.get('rule_id', '?'))}` "
            f"| {md_cell(m.get('reason', ''))} |"
        )
    return lines
