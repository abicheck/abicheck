#!/usr/bin/env python3
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

"""learning_nav_order.py — the `learning-nav-order` AI-readiness gate.

For each nav group under the two learning tabs in `mkdocs.yml` (the
"ABI/API Compatibility" tab's groups and the "Concepts" tab itself) the
pages' front-matter `level:` values must be non-decreasing in nav order,
skipping only the series hub. This is the learning-series plan's Goal
criterion "each nav group is non-decreasing in level", made executable.

Since the sidebar became the ladder itself (one nav group per step of
`docs/_meta/learning-ladder.yaml`, enforced by `learning_ladder.py`'s
sidebar rule), a regression here means the ladder's own order regressed or
a "go deeper" branch was placed ahead of a lower-level member; the fix is
in the ladder file and `mkdocs.yml` together. Branch pages are included:
the ladder exempts them from the spine's monotonicity, not from the
sidebar's. `nav_groups` is also the nav reader that sidebar rule builds on.

Split out of `check_ai_readiness.py` the same way `adr_status_sync.py` was
(that script is past the 2000-line hard cap). Pure stdlib — it reads the
nav block with an indentation walk rather than PyYAML, matching the parent
script's no-third-party constraint.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MKDOCS = ROOT / "mkdocs.yml"
CHECK = "learning-nav-order"

LEARNING_TABS: tuple[str, ...] = ("ABI/API Compatibility", "Concepts")
HUB = "learn/abi-api-handling.md"

LEVEL_RANK: dict[str, int] = {
    "beginner": 0,
    "intermediate": 1,
    "advanced": 2,
    "expert": 3,
}

_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_LEVEL_RE = re.compile(r"^level:\s*['\"]?([A-Za-z]+)['\"]?\s*$", re.MULTILINE)
_NAV_ITEM_RE = re.compile(
    r"^(\s*)-\s+(?:\"([^\"]*)\"|'([^']*)'|([^:]+?))\s*:\s*(\S.*)?$"
)


def _strip_comment(line: str) -> str:
    """Drop a `# ...` YAML comment that is not inside quotes."""
    out: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def _unquote(value: str) -> str:
    """A nav item's value with one layer of matching YAML quotes removed:
    `"learn/x.md"` and `'learn/x.md'` name the same page as `learn/x.md`."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _nav_events(
    mkdocs_text: str, tabs: tuple[str, ...]
) -> list[tuple[str, str, str | None]]:
    """The nav block as ordered events: `("tab", tab, None)` when one of
    `tabs` opens, `("group", key, None)` when a group under it opens,
    `("subgroup", key, title)` when a group nested inside that group opens,
    `("subpage", key, path)` for a page inside such a nested group and
    `("page", key, path)` for a page directly in the group, where `key` is
    `"<tab> / <group>"` (`"<tab> / <tab>"` for a page directly under the
    tab). Nesting below a subgroup folds into that subgroup."""
    lines = mkdocs_text.split("\n")
    in_nav = False
    events: list[tuple[str, str, str | None]] = []
    tab_indent: int | None = None
    current_tab: str | None = None
    current_group: str | None = None
    sub_indent: int | None = None  # indent of the open nested group's title
    for raw in lines:
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if not in_nav:
            if line.startswith("nav:"):
                in_nav = True
            continue
        if not line.startswith(" ") and not line.startswith("-"):
            break  # next top-level key
        m = _NAV_ITEM_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        title = (m.group(2) or m.group(3) or m.group(4) or "").strip()
        value = _unquote((m.group(5) or "").strip())
        if tab_indent is None:
            tab_indent = indent
        if indent == tab_indent:
            current_tab = title if title in tabs and not value else None
            current_group = None
            sub_indent = None
            if current_tab is not None:
                events.append(("tab", current_tab, None))
            continue
        if current_tab is None:
            continue
        if indent == tab_indent + 2:
            sub_indent = None
            if value:
                # a page directly under the tab: the tab is the group
                events.append(("page", f"{current_tab} / {current_tab}", value))
                current_group = None
            else:
                current_group = f"{current_tab} / {title}"
                events.append(("group", current_group, None))
            continue
        key = current_group or f"{current_tab} / {current_tab}"
        if sub_indent is not None and indent <= sub_indent:
            sub_indent = None  # back out of the nested group
        if value:
            events.append(("subpage" if sub_indent is not None else "page", key, value))
        elif sub_indent is None:
            sub_indent = indent
            events.append(("subgroup", key, title))
        else:
            # a group nested inside the nested group: recorded as another
            # subgroup opening (the rule rejects more than one), its pages
            # folding into the enclosing nested group
            events.append(("subgroup", key, title))
    return events


def nav_groups(
    mkdocs_text: str, tabs: tuple[str, ...] = LEARNING_TABS
) -> dict[str, list[str]]:
    """Map `"<tab> / <group>"` to the ordered page paths under it.

    Only groups under `tabs` are returned. A tab whose direct children are
    pages (the Concepts tab) is one group named after the tab itself; a tab
    whose children are groups yields one entry per group. Deeper nesting
    folds into the nearest enclosing group. Two groups with the same title
    fold into one key too — `duplicate_nav_groups` names them.
    """
    groups: dict[str, list[str]] = {}
    for kind, key, value in _nav_events(mkdocs_text, tabs):
        if kind == "tab":
            continue
        pages = groups.setdefault(key, [])
        if kind in ("page", "subpage") and value is not None:
            pages.append(value)
    return groups


def nav_subgroups(
    mkdocs_text: str, tabs: tuple[str, ...] = LEARNING_TABS
) -> dict[str, list[tuple[str, list[str]]]]:
    """Per group key, the nested groups opened inside it as `(title, pages)`
    in nav order — the structure `nav_groups` folds away. A page inside a
    nested group is listed under the most recently opened nested group."""
    subs: dict[str, list[tuple[str, list[str]]]] = {}
    for kind, key, value in _nav_events(mkdocs_text, tabs):
        if kind == "subgroup" and value is not None:
            subs.setdefault(key, []).append((value, []))
        elif kind == "subpage" and value is not None and subs.get(key):
            subs[key][-1][1].append(value)
    return subs


def duplicate_nav_groups(
    mkdocs_text: str, tabs: tuple[str, ...] = LEARNING_TABS
) -> list[str]:
    """Group keys that open more than once under one of `tabs`, and tabs
    that themselves open more than once (reported by their bare name), in
    order of first appearance — `nav_groups` would silently merge their
    pages either way."""
    seen: list[str] = []
    dupes: list[str] = []
    for kind, key, _ in _nav_events(mkdocs_text, tabs):
        if kind not in ("tab", "group"):
            continue
        if key in seen and key not in dupes:
            dupes.append(key)
        seen.append(key)
    return dupes


def nav_page_count(mkdocs_text: str, page: str) -> int:
    """How many times `page` is listed anywhere in the nav block — every tab,
    not only the learning ones the other readers are scoped to."""
    count = 0
    in_nav = False
    for raw in mkdocs_text.split("\n"):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if not in_nav:
            in_nav = line.startswith("nav:")
            continue
        if not line.startswith(" ") and not line.startswith("-"):
            break
        m = _NAV_ITEM_RE.match(line)
        if m and _unquote((m.group(5) or "").strip()) == page:
            count += 1
    return count


def page_level(docs: Path, page: str) -> str | None:
    path = docs / page
    if not path.is_file():
        return None
    m = _FRONT_MATTER_RE.match(path.read_text(encoding="utf-8"))
    if not m:
        return None
    lm = _LEVEL_RE.search(m.group(1))
    return lm.group(1) if lm else None


def nav_order_findings(
    mkdocs_text: str, docs: Path = DOCS, hub: str = HUB
) -> list[str]:
    """Every level regression inside a learning nav group, as messages."""
    findings: list[str] = []
    for group, pages in nav_groups(mkdocs_text).items():
        previous: tuple[str, str] | None = None
        for page in pages:
            if page == hub:
                continue
            level = page_level(docs, page)
            if level is None or level not in LEVEL_RANK:
                findings.append(f"{group}: {page} has no valid front-matter level")
                continue
            if previous is not None and LEVEL_RANK[level] < LEVEL_RANK[previous[1]]:
                findings.append(
                    f"{group}: {page} ({level}) follows {previous[0]} ({previous[1]}); "
                    "reorder inside the group so levels never decrease"
                )
            previous = (page, level)
    return findings


def check_learning_nav_order(f) -> None:
    """The gate: an ERROR per level regression in a learning nav group."""
    if not MKDOCS.is_file():
        return
    for msg in nav_order_findings(MKDOCS.read_text(encoding="utf-8")):
        f.err(CHECK, msg)
