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

"""Summarising a section that one ``ChangeKind`` has flooded.

Intel MKL's report ran to roughly 120,000 lines, about 40,000 of them a
single non-gating kind. Itemising those tells a reader nothing the count
does not, and buries the findings that do need reading.

So a *non-gating* section holding more than
:data:`KIND_ROLLUP_THRESHOLD` findings of one kind is summarised: the count,
a few named samples, and nothing else. Three properties are deliberate.
Gating sections are never rolled up -- a break is always itemised. Only
**Markdown** renders this way; every machine format carries every finding
unchanged, because a rollup is a presentation decision and a consumer
parsing JSON is not the reader being protected. And the rollup is a
*partition*: every finding is either itemised or counted in exactly one
rollup, never dropped (``AGENTS.md``'s "record before disposing").

This module holds both halves, because they are one decision. The
``compute_``/``render_`` split ``abicheck/report/AGENTS.md`` requires still
applies within it: :func:`roll_up_large_kinds` decides and returns frozen
plain values, :func:`render_kind_rollups` only formats them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..checker_types import Change

#: A kind contributing more findings than this to one non-gating section is
#: summarised rather than itemised in the Markdown report.
#:
#: Chosen from the failure it fixes rather than from taste: a single
#: conda-forge MKL comparison emitted 39,956 ``exported_not_public``
#: findings, 39,955 of them ``persistent``, producing a ~120,000-line
#: report in which no other finding could be found. Any threshold in the
#: tens makes that report readable; a reader who wants every line has
#: always had ``-o json=...``, which is unaffected. Deliberately well above
#: the release summary's own per-library cap of 10
#: (``report/release_display_limits.py``): this is a single-library report,
#: where a reader legitimately expects to see individual findings, so it
#: only engages for a genuine flood.
KIND_ROLLUP_THRESHOLD = 25
#: Symbols named inline for a rolled-up kind. Enough to recognise what the
#: kind is picking up, few enough that the count stays the headline.
KIND_ROLLUP_SAMPLES = 5


@dataclass(frozen=True, slots=True)
class KindRollup:
    """One ``ChangeKind``'s findings, summarised instead of itemised.

    A *presentation* value and nothing else -- the machine document still
    carries every finding, exactly as it does under the release summary's own
    display cap (``report/release_display_limits.py``). Holds only plain
    values: deciding *whether* a kind rolls up is the ``compute_*`` half's
    job (``reporter_markdown``), and this module formats what it is given.
    """

    kind: str
    count: int
    sample_symbols: tuple[str, ...]


def render_kind_rollups(rollups: tuple[KindRollup, ...]) -> list[str]:
    """One line per rolled-up kind, naming the count and a few examples.

    The count is the point: "39,956 findings of kind X" is the fact a reader
    needs, and 39,956 individual lines actively hide it. A handful of symbols
    is enough to recognise what the kind is picking up; the rest are one
    ``-o json=...`` export away, which is where they have always been.
    """
    if not rollups:
        return []
    lines: list[str] = []
    for r in rollups:
        shown = ", ".join(f"`{s}`" for s in r.sample_symbols)
        remaining = r.count - len(r.sample_symbols)
        more = f", and {remaining:,} more" if remaining > 0 else ""
        lines.append(
            f"- **{r.kind}**: {r.count:,} findings — e.g. {shown}{more}. "
            "Itemised in full in the machine-readable report (`-o json=...`)."
        )
    return lines


def roll_up_large_kinds(
    changes: list[Change],
) -> tuple[list[Change], tuple[KindRollup, ...]]:
    """Split *changes* into (itemised, rolled-up-per-kind).

    The ``compute_*`` half of the rollup: it decides *which* kinds are
    summarised and gathers plain values; ``report.render_markdown`` formats
    them and decides nothing (``abicheck/report/AGENTS.md``).

    Applied only to non-gating sections. A breaking finding is never rolled
    up however many there are -- a reader approving or rejecting a release
    has to see each one, and a flood of them is itself the signal.
    """
    by_kind: dict[str, list[Change]] = {}
    for c in changes:
        by_kind.setdefault(getattr(c.kind, "value", str(c.kind)), []).append(c)
    itemised: list[Change] = []
    rollups: list[KindRollup] = []
    for kind, group in by_kind.items():
        if len(group) <= KIND_ROLLUP_THRESHOLD:
            itemised.extend(group)
            continue
        rollups.append(
            KindRollup(
                kind=kind,
                count=len(group),
                sample_symbols=tuple(
                    str(c.symbol) for c in group[:KIND_ROLLUP_SAMPLES] if c.symbol
                ),
            )
        )
    # Stable order: the itemised remainder keeps the caller's order, and the
    # rollups are sorted by descending count so the biggest contributor --
    # the one a reader is most likely looking for -- reads first.
    rollups.sort(key=lambda r: (-r.count, r.kind))
    return itemised, tuple(rollups)
