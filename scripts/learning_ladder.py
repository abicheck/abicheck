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

"""learning_ladder.py — the learning series' reading order as data, and the
`learning-ladder` docs-contract rules over it.

`docs/_meta/learning-ladder.yaml` is the one machine-readable owner of tier
membership and reading order for `docs/learn/` (plan:
`docs/contribute/plans/learning-series-page-specs.md` §A1). This module is
the leaf both consumers build on:

- `scripts/gen_learning_ladder.py` renders the hub's ladder and role-path
  tables from it (it only renders and drift-checks);
- `scripts/check_docs_contract.py` calls `check_learning_ladder` so one gate
  owns every `docs/_meta/*.yaml` contract.

Rules the file encodes (each is an ERROR):

- **Completeness.** Every `docs/learn/**/*.md` except the hub appears exactly
  once as a member or branch across both sequences; a listed file must exist.
- **Monotonicity per sequence.** Walking a sequence's members in order, each
  page's front-matter `level:` is >= the previous member's. Branches are
  checked only against the page they hang from; links are never checked.
- **Level is declared once; floors are data.** A page's level lives in its
  front matter only; every member and branch must be >= its tier's `floor:`.
- **Links must be members elsewhere** (or an existing tool-track page).
- **Paths are walks up the ladder.** Every `paths:` page resolves to its full
  ladder index (sequence, tier, member position; a branch takes its
  parent's) and that index is strictly increasing along the entry.
- **Footers match the ladder.** Every member/branch page carries one
  `**Ladder:**` footer line whose two links are its ladder neighbours.

Pure Python + PyYAML, importable; no repository side effects.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_DIR = Path(__file__).resolve().parent.parent
DOCS = REPO_DIR / "docs"
LADDER_PATH = DOCS / "_meta" / "learning-ladder.yaml"
CHECK = "learning-ladder"

LEVEL_RANK: dict[str, int] = {
    "beginner": 0,
    "intermediate": 1,
    "advanced": 2,
    "expert": 3,
}

_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_LEVEL_RE = re.compile(r"^level:\s*['\"]?([A-Za-z]+)['\"]?\s*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^title:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_FOOTER_LINE_RE = re.compile(r"^\*\*Ladder:\*\*(.*)$", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class Entry:
    """Where one page sits in the ladder."""

    page: str
    sequence: str
    sequence_index: int
    tier_id: str
    tier_index: int
    member_index: int
    parent: str | None = None  # set for a branch

    @property
    def is_branch(self) -> bool:
        return self.parent is not None

    @property
    def order_key(self) -> tuple[int, int, int, int]:
        # A branch sorts right after the page it hangs from and before that
        # page's next member, which is where a reader who took it rejoins.
        return (
            self.sequence_index,
            self.tier_index,
            self.member_index,
            1 if self.parent is not None else 0,
        )


@dataclass
class Tier:
    id: str
    title: str
    floor: str
    members: list[str] = field(default_factory=list)
    branches: dict[str, list[str]] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)


@dataclass
class Sequence:
    key: str
    tab: str
    tiers: list[Tier] = field(default_factory=list)

    def ordered_members(self) -> list[str]:
        return [m for tier in self.tiers for m in tier.members]


@dataclass
class ReadingPath:
    role: str
    pages: list[str]
    after: str | None


@dataclass
class Ladder:
    hub: str
    sequences: list[Sequence]
    paths: list[ReadingPath]
    index: dict[str, Entry] = field(default_factory=dict)

    def tier_of(self, page: str) -> Tier:
        entry = self.index[page]
        return self.sequences[entry.sequence_index].tiers[entry.tier_index]

    def neighbours(self, page: str) -> tuple[str, str]:
        """(previous, next) ladder neighbours of a member or branch page.

        A branch's previous is the page it hangs from and its next is that
        page's own next, so a reader who took the branch rejoins the spine.
        The first member of a sequence points back at the hub, and so does
        the last member's next.
        """
        entry = self.index[page]
        if entry.parent is not None:
            prev = entry.parent
            _, nxt = self.neighbours(entry.parent)
            return prev, nxt
        members = self.sequences[entry.sequence_index].ordered_members()
        pos = members.index(page)
        prev = members[pos - 1] if pos > 0 else self.hub
        nxt = members[pos + 1] if pos + 1 < len(members) else self.hub
        return prev, nxt


class LadderError(ValueError):
    """The ladder file is structurally unusable (not a page-level finding)."""


def _as_str_list(value: object, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise LadderError(f"{where}: expected a list of page paths")
    return list(value)


def load_ladder(path: Path = LADDER_PATH) -> Ladder:
    """Parse the ladder file into a `Ladder`, raising `LadderError` on a
    malformed document (duplicate placement included)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - exercised via tests
        raise LadderError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise LadderError(f"{path.name}: top level must be a mapping")
    hub = raw.get("hub")
    if not isinstance(hub, str):
        raise LadderError(f"{path.name}: `hub` must be a page path")
    sequences_raw = raw.get("sequences")
    if not isinstance(sequences_raw, dict) or not sequences_raw:
        raise LadderError(f"{path.name}: `sequences` must be a non-empty mapping")

    ladder = Ladder(hub=hub, sequences=[], paths=[])
    for seq_index, (key, seq_raw) in enumerate(sequences_raw.items()):
        if not isinstance(seq_raw, dict):
            raise LadderError(f"sequences.{key}: expected a mapping")
        seq = Sequence(key=str(key), tab=str(seq_raw.get("tab", key)))
        tiers_raw = seq_raw.get("tiers")
        if not isinstance(tiers_raw, list) or not tiers_raw:
            raise LadderError(f"sequences.{key}: `tiers` must be a non-empty list")
        for tier_index, tier_raw in enumerate(tiers_raw):
            if not isinstance(tier_raw, dict):
                raise LadderError(
                    f"sequences.{key}.tiers[{tier_index}]: expected a mapping"
                )
            where = f"sequences.{key}.tiers[{tier_index}]"
            floor = tier_raw.get("floor")
            if not isinstance(floor, str) or floor not in LEVEL_RANK:
                raise LadderError(
                    f"{where}: `floor` must be one of {sorted(LEVEL_RANK)} (got {floor!r})"
                )
            tier = Tier(
                id=str(tier_raw.get("id", tier_index)),
                title=str(tier_raw.get("title", "")),
                floor=floor,
                links=_as_str_list(tier_raw.get("links"), f"{where}.links"),
            )
            members_raw = tier_raw.get("members")
            if members_raw is None:
                members_raw = []
            if not isinstance(members_raw, list):
                raise LadderError(f"{where}.members: expected a list")
            for member_index, member_raw in enumerate(members_raw):
                if isinstance(member_raw, str):
                    page, branches = member_raw, []
                elif isinstance(member_raw, dict) and isinstance(
                    member_raw.get("page"), str
                ):
                    page = member_raw["page"]
                    branches = _as_str_list(
                        member_raw.get("branches"),
                        f"{where}.members[{member_index}].branches",
                    )
                else:
                    raise LadderError(
                        f"{where}.members[{member_index}]: expected a page path or "
                        "a {page:, branches:} mapping"
                    )
                entry = Entry(
                    page, seq.key, seq_index, tier.id, tier_index, member_index
                )
                _place(ladder, entry)
                tier.members.append(page)
                if branches:
                    tier.branches[page] = branches
                for branch in branches:
                    _place(
                        ladder,
                        Entry(
                            branch,
                            seq.key,
                            seq_index,
                            tier.id,
                            tier_index,
                            member_index,
                            parent=page,
                        ),
                    )
            seq.tiers.append(tier)
        ladder.sequences.append(seq)

    for i, path_raw in enumerate(raw.get("paths") or []):
        if not isinstance(path_raw, dict) or not isinstance(path_raw.get("role"), str):
            raise LadderError(f"paths[{i}]: expected a mapping with a `role`")
        after = path_raw.get("after")
        if after is not None and not isinstance(after, str):
            raise LadderError(f"paths[{i}]: `after` must be a page path")
        ladder.paths.append(
            ReadingPath(
                role=path_raw["role"],
                pages=_as_str_list(path_raw.get("pages"), f"paths[{i}].pages"),
                after=after,
            )
        )
    if ladder.hub in ladder.index:
        raise LadderError(
            f"{ladder.hub}: the hub is exempt from the ladder and may not be placed in it"
        )
    return ladder


def _place(ladder: Ladder, entry: Entry) -> None:
    if entry.page in ladder.index:
        raise LadderError(
            f"{entry.page}: placed twice (tier {ladder.index[entry.page].tier_id} and "
            f"tier {entry.tier_id}); every page belongs to exactly one tier"
        )
    ladder.index[entry.page] = entry


# ---------------------------------------------------------------------------
# Page facts
# ---------------------------------------------------------------------------


def front_matter_text(text: str) -> str:
    m = _FRONT_MATTER_RE.match(text)
    return m.group(1) if m else ""


def page_level(text: str) -> str | None:
    m = _LEVEL_RE.search(front_matter_text(text))
    return m.group(1) if m else None


def page_title(text: str) -> str:
    """Front-matter `title:` if present, else the first H1, else ''."""
    m = _TITLE_RE.search(front_matter_text(text))
    if m:
        return m.group(1).strip()
    body = (
        text[_FRONT_MATTER_RE.match(text).end() :]
        if _FRONT_MATTER_RE.match(text)
        else text
    )
    h1 = _H1_RE.search(body)
    return h1.group(1).strip() if h1 else ""


def footer_links(page: str, text: str) -> list[str] | None:
    """The docs-relative targets of the page's `**Ladder:**` footer links, in
    order, or None when the page carries no footer. Hrefs are resolved
    relative to the page's own directory and stripped of fragments."""
    m = _FOOTER_LINE_RE.search(text)
    if not m:
        return None
    base = Path(page).parent
    targets: list[str] = []
    for _, href in _MD_LINK_RE.findall(m.group(1)):
        href = href.split("#", 1)[0]
        targets.append(Path(os.path.normpath(base / href)).as_posix())
    return targets


def relative_href(from_page: str, to_page: str) -> str:
    """`to_page` as a relative Markdown href from `from_page` (both docs-relative)."""
    return Path(os.path.relpath(to_page, Path(from_page).parent)).as_posix()


def learn_pages(docs: Path) -> list[str]:
    return sorted(
        p.relative_to(docs).as_posix() for p in (docs / "learn").rglob("*.md")
    )


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def check_learning_ladder(
    f, docs: Path = DOCS, ladder_path: Path = LADDER_PATH
) -> None:
    """Report every ladder-rule violation on `f` (a Findings with `.err`)."""
    if not ladder_path.is_file():
        f.err(CHECK, f"{ladder_path.name}: missing")
        return
    try:
        ladder = load_ladder(ladder_path)
    except LadderError as exc:
        f.err(CHECK, str(exc))
        return

    texts: dict[str, str] = {}

    def read(page: str) -> str | None:
        if page not in texts:
            p = docs / page
            texts[page] = p.read_text(encoding="utf-8") if p.is_file() else None  # type: ignore[assignment]
        return texts[page]

    def level_of(page: str) -> str | None:
        text = read(page)
        if text is None:
            return None
        lvl = page_level(text)
        if lvl is None:
            f.err(
                CHECK,
                f"{page}: no front-matter `level:` (every ladder page must declare one)",
            )
            return None
        if lvl not in LEVEL_RANK:
            f.err(CHECK, f"{page}: level {lvl!r} is not one of {sorted(LEVEL_RANK)}")
            return None
        return lvl

    # Existence + completeness.
    for page in ladder.index:
        if not (docs / page).is_file():
            f.err(CHECK, f"{page}: listed in the ladder but the file does not exist")
    if not (docs / ladder.hub).is_file():
        f.err(CHECK, f"{ladder.hub}: hub page does not exist")
    for page in learn_pages(docs):
        if page != ladder.hub and page not in ladder.index:
            f.err(
                CHECK,
                f"{page}: not placed in docs/_meta/learning-ladder.yaml (every learn page "
                "except the hub is a member or branch of exactly one tier)",
            )

    # Floors, monotonicity, branches.
    for seq in ladder.sequences:
        previous: tuple[str, str] | None = None
        for tier in seq.tiers:
            for member in tier.members:
                lvl = level_of(member)
                if lvl is None:
                    continue
                if LEVEL_RANK[lvl] < LEVEL_RANK[tier.floor]:
                    f.err(
                        CHECK,
                        f"{member}: level {lvl} is below tier {tier.id} ({tier.title}) floor {tier.floor}",
                    )
                if previous is not None and LEVEL_RANK[lvl] < LEVEL_RANK[previous[1]]:
                    f.err(
                        CHECK,
                        f"{member}: level {lvl} regresses below {previous[0]} ({previous[1]}) in "
                        f"sequence {seq.key!r}; the reading order must be non-decreasing",
                    )
                previous = (member, lvl)
                for branch in tier.branches.get(member, []):
                    blvl = level_of(branch)
                    if blvl is None:
                        continue
                    if LEVEL_RANK[blvl] < LEVEL_RANK[lvl]:
                        f.err(
                            CHECK,
                            f"{branch}: branch level {blvl} is below its parent {member} ({lvl})",
                        )
                    if LEVEL_RANK[blvl] < LEVEL_RANK[tier.floor]:
                        f.err(
                            CHECK,
                            f"{branch}: level {blvl} is below tier {tier.id} floor {tier.floor}",
                        )
            for link in tier.links:
                if link in ladder.index:
                    continue
                if link.startswith("learn/"):
                    f.err(
                        CHECK,
                        f"{link}: linked from tier {tier.id} but a member of no tier (a link "
                        "is a pointer, not a place to hide an unclassified page)",
                    )
                elif not (docs / link).is_file():
                    f.err(
                        CHECK, f"{link}: linked from tier {tier.id} but does not exist"
                    )

    # Paths.
    for rp in ladder.paths:
        last: tuple[tuple[int, int, int], str] | None = None
        seen: set[str] = set()
        for page in rp.pages:
            entry = ladder.index.get(page)
            if entry is None:
                f.err(
                    CHECK,
                    f"path {rp.role!r}: {page} is not a member or branch of any tier",
                )
                continue
            if page in seen:
                f.err(CHECK, f"path {rp.role!r}: {page} is listed twice")
            seen.add(page)
            key = entry.order_key
            if last is not None and key <= last[0]:
                f.err(
                    CHECK,
                    f"path {rp.role!r}: {page} does not come after {last[1]} in the ladder "
                    "(paths walk the ladder strictly upward)",
                )
            last = (key, page)
        if rp.after is not None:
            if rp.after.startswith("learn/"):
                f.err(
                    CHECK,
                    f"path {rp.role!r}: `after` must be a tool-track page, not {rp.after}",
                )
            elif not (docs / rp.after).is_file():
                f.err(
                    CHECK, f"path {rp.role!r}: `after` page {rp.after} does not exist"
                )

    # Footers.
    for page in ladder.index:
        text = read(page)
        if text is None:
            continue
        found = footer_links(page, text)
        prev, nxt = ladder.neighbours(page)
        if found is None:
            f.err(
                CHECK,
                f"{page}: no `**Ladder:**` footer (expected ← {prev} · {nxt} →)",
            )
            continue
        if found != [prev, nxt]:
            f.err(
                CHECK,
                f"{page}: ladder footer links {found} do not match the ladder neighbours "
                f"[{prev}, {nxt}]",
            )
