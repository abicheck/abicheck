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

"""One size budget for every Markdown section that lists findings.

The Markdown report is read in places with hard size limits: a GitHub step
summary holds 1 MiB, a PR comment 65,536 characters. A C++ library that
leaks libstdc++ template instantiations produced a 574 KB report on a
byte-identical rebuild (SVS field report), because several sections itemised
one row per finding and each row carried a multi-KB demangled name. Each
section had grown its own cap, or none.

This module is the one place those bounds live:

- a **name** is clipped to :data:`NAME_LIMIT` characters (a bare mangled
  symbol, the entity's identity, to :data:`MANGLED_NAME_LIMIT`), and the
  whole-document demangle pass clips each demangled half to
  :data:`DEMANGLED_NAME_LIMIT` while keeping the exact mangled spelling in
  brackets, so a reader can still paste the identity into ``nm`` or a
  suppression rule;
- a **line** is clipped to :data:`LINE_LIMIT`;
- a non-gating **list** itemises at most :data:`ITEM_LIMIT` entries
  (:data:`NOTE_ITEM_LIMIT` for a note restating findings counted elsewhere)
  and then says how many more there are and which JSON field holds them all;
- an **examples** list names at most :data:`EXAMPLE_LIMIT` items.

Only Markdown is bounded. JSON, SARIF and JUnit carry every finding: a
bound here is a presentation decision, never a disposition (root
``AGENTS.md``'s "record before disposing"). Every cut says what it cut.

A dependency-free leaf, so any renderer can import it without a cycle.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TypeVar

_T = TypeVar("_T")

#: Characters of one entity name shown.
NAME_LIMIT = 160
#: Characters of a demangled name shown beside its mangled spelling. The
#: mangled half is kept whole: it is the identity, the demangled half is a
#: reading aid.
DEMANGLED_NAME_LIMIT = 160
#: Characters of a bare mangled symbol shown. Far above :data:`NAME_LIMIT`
#: because the mangled spelling is the identity a reader copies; a clipped
#: one identifies nothing, so only a pathological name reaches this.
MANGLED_NAME_LIMIT = 1000
#: Characters of one rendered line.
LINE_LIMIT = 1000
#: Entries itemised in one non-gating list before the rest are counted.
ITEM_LIMIT = 25
#: Entries itemised in a note whose findings are already counted elsewhere
#: (suppressed, not evaluated, the environment-drift regrouping): those rows
#: repeat facts the report states in full, so they get fewer.
NOTE_ITEM_LIMIT = 10
#: Names given as examples of a counted population.
EXAMPLE_LIMIT = 5

ELLIPSIS = "…"


def clip(text: object, limit: int = LINE_LIMIT) -> str:
    """*text* cut to *limit* characters, with an explicit ``…`` marker."""
    value = str(text)
    return value if len(value) <= limit else value[: limit - 1] + ELLIPSIS


def clip_name(text: object) -> str:
    """One entity name cut to :data:`NAME_LIMIT`.

    A bare mangled symbol is the entity's identity -- what a reader pastes
    into ``nm`` or a suppression rule -- so it is kept up to
    :data:`MANGLED_NAME_LIMIT`; the demangle pass then clips only the
    demangled half it adds beside it (:data:`DEMANGLED_NAME_LIMIT`).
    """
    value = str(text)
    if _is_bare_mangled(value):
        return clip(value, MANGLED_NAME_LIMIT)
    return clip(value, NAME_LIMIT)


def _is_bare_mangled(value: str) -> bool:
    return value.startswith(("_Z", "__Z")) and not any(c.isspace() for c in value)


def split_items(
    items: Sequence[_T], limit: int = ITEM_LIMIT
) -> tuple[Sequence[_T], int]:
    """``(shown, hidden_count)`` for a list itemised up to *limit* entries."""
    return items[:limit], max(len(items) - limit, 0)


def more_line(hidden: int, json_field: str, *, prefix: str = "- ") -> str | None:
    """The disclosure line for *hidden* entries not itemised, or ``None``."""
    if hidden <= 0:
        return None
    return (
        f"{prefix}… and {hidden} more; itemised in full in the "
        f"machine-readable report (JSON `{json_field}`)."
    )


def example_names(
    names: Iterable[object], *, total: int | None = None, limit: int = EXAMPLE_LIMIT
) -> str:
    """Up to *limit* clipped names in code spans, then ``… and K more``.

    *total* is the size of the population the names are drawn from when it
    is larger than *names* itself (a family whose examples were already
    sampled); it defaults to ``len(names)``.
    """
    shown = [str(name) for name in names]
    count = len(shown) if total is None else total
    text = ", ".join(f"`{clip_name(name)}`" for name in shown[:limit])
    more = count - min(len(shown), limit)
    if more > 0:
        text += f" … and {more} more"
    return text


__all__ = [
    "DEMANGLED_NAME_LIMIT",
    "ELLIPSIS",
    "EXAMPLE_LIMIT",
    "ITEM_LIMIT",
    "LINE_LIMIT",
    "MANGLED_NAME_LIMIT",
    "NAME_LIMIT",
    "NOTE_ITEM_LIMIT",
    "clip",
    "clip_name",
    "example_names",
    "more_line",
    "split_items",
]
