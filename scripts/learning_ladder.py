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
  ladder index (sequence, tier, member position, branch ordinal; a branch
  sorts right after its parent and before the parent's next member, and
  sibling branches keep their YAML order) and that index is strictly
  increasing along the entry.
- **Footers match the ladder.** Every member/branch page carries one
  `**Ladder:**` footer line whose two links are its ladder neighbours.
- **The sidebar is the ladder.** In `mkdocs.yml`, a sequence's tab either
  carries one nav group per step — titled `<id>. <title>`, in step order,
  holding exactly that step's members in ladder order, each branch
  somewhere after its parent and branches in ladder order among
  themselves, each branch either directly after its parent or in the
  trailing go-deeper block — or lists the whole sequence flat in the same
  order (the Concepts tab). The hub is the first entry directly under the
  first sequence's tab, as its overview, and appears nowhere else.
  This is what keeps the sidebar, the hub's step list, and every page's
  footer telling one reading order instead of three.

Pure Python + PyYAML, importable; no repository side effects.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from learning_nav_order import nav_groups

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
    branch_index: int = 0  # 1-based position among the parent's branches

    @property
    def is_branch(self) -> bool:
        return self.parent is not None

    @property
    def order_key(self) -> tuple[int, int, int, int]:
        # A branch sorts right after the page it hangs from and before that
        # page's next member, which is where a reader who took it rejoins;
        # sibling branches keep their YAML order, so a path may walk two of
        # them in that order and is rejected for walking them reversed.
        return (
            self.sequence_index,
            self.tier_index,
            self.member_index,
            self.branch_index,
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

    @property
    def numbered(self) -> bool:
        """Readers see this sequence as `Step 1` … `Step n` (ids are the step
        numbers); the hub renders it as an ordered list."""
        return all(t.id.isdigit() for t in self.tiers)


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
                for branch_index, branch in enumerate(branches, 1):
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
                            branch_index=branch_index,
                        ),
                    )
            seq.tiers.append(tier)
        if seq.numbered:
            ids = [t.id for t in seq.tiers]
            want = [str(i) for i in range(1, len(ids) + 1)]
            if ids != want:
                # Markdown numbers an ordered list positionally, so the hub
                # would silently show different numbers from the sidebar's
                # "<id>. <title>" groups and the footers' "Step <id>".
                raise LadderError(
                    f"sequences.{key}: numbered steps must be ids {want} in order "
                    f"(got {ids}); the hub renders them as an ordered list"
                )
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
            f"{entry.page}: placed twice (step {ladder.index[entry.page].tier_id} and "
            f"step {entry.tier_id}); every page belongs to exactly one step"
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


_PART_TITLE_RE = re.compile(r"^(Part \d+ — [^:]+?)\s*(?::.*)?$")
_SUBTITLE_SPLIT_RE = re.compile(r"\s*(?::|—)\s+")


def short_title(title: str) -> str:
    """A page title without its subtitle, for the hub's step list and the
    page footers: `Part 1 — Foundations: From Source Code to …` → `Part 1 —
    Foundations`, `What Each Level Sees — a level-by-level …` → `What Each
    Level Sees`. A title with no `: ` or ` — ` subtitle is returned as is."""
    m = _PART_TITLE_RE.match(title)
    if m:
        return m.group(1).strip()
    return _SUBTITLE_SPLIT_RE.split(title, 1)[0].strip()


def step_label(seq: Sequence, tier: Tier) -> str:
    """How a step is named to readers: `Step 3` on the educational sequence
    (whose ids are the step numbers), `Concepts c2` elsewhere."""
    return f"Step {tier.id}" if seq.numbered else f"{seq.tab} {tier.id}"


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
# The sidebar rule
# ---------------------------------------------------------------------------


def nav_group_title(tier: Tier) -> str:
    """The nav group title a step must carry: `3. How Breaks Happen`."""
    return f"{tier.id}. {tier.title}"


def _order_findings(
    label: str, pages: list[str], members: list[str], branches: dict[str, list[str]]
) -> list[str]:
    """`pages` (one nav group, or a flat tab) must hold exactly `members`
    plus every branch, with members in ladder order and each branch after
    the page it hangs from."""
    out: list[str] = []
    # Ladder order of the branches: parents in member order, then each
    # parent's branches in YAML order -- never the mapping's own iteration
    # order.
    all_branches = [b for m in members for b in branches.get(m, [])]
    expected = members + all_branches
    for page in expected:
        if page not in pages:
            out.append(f"{label}: {page} is missing from the sidebar")
    for page in pages:
        if page not in expected:
            out.append(
                f"{label}: {page} is in this nav group but the ladder places it elsewhere"
            )
    position = {p: i for i, p in enumerate(pages)}
    placed_members = [m for m in members if m in position]
    if [p for p in pages if p in placed_members] != placed_members:
        out.append(
            f"{label}: members are not in ladder order (expected "
            f"{' → '.join(placed_members)})"
        )
    # Branches keep the ladder's own order among themselves too (parents in
    # member order, siblings in YAML order): they are all "advanced", so the
    # level gate would let two of them swap while the sidebar stopped
    # matching the hub's "go deeper" lists.
    placed_branches = [b for b in all_branches if b in position]
    if [p for p in pages if p in placed_branches] != placed_branches:
        out.append(
            f"{label}: go-deeper pages are not in ladder order (expected "
            f"{' → '.join(placed_branches)})"
        )
    # A parent's branches sit as one block in one of the two places the hub
    # also renders: directly after the page they hang from, or in the
    # trailing "go deeper" block after the step's last member. Split between
    # the two, before the parent, or between two later members is a
    # position the ladder has no reading for.
    last_member = max((position[m] for m in placed_members), default=-1)
    for parent, bs in branches.items():
        present = [b for b in bs if b in position]
        if parent not in position or not present:
            continue
        inline = pages[position[parent] + 1 : position[parent] + 1 + len(present)]
        trailing = all(position[b] > last_member for b in present)
        if inline != present and not trailing:
            out.append(
                f"{label}: {', '.join(present)} must follow {parent} directly as one "
                "block or close the step together after its last member"
            )
    return out


def nav_findings(ladder: Ladder, mkdocs_text: str) -> list[str]:
    """Every way `mkdocs.yml`'s learning tabs disagree with the ladder."""
    groups = nav_groups(mkdocs_text, tabs=tuple(s.tab for s in ladder.sequences))
    out: list[str] = []
    # The hub renders the ladder, so it belongs directly under the first
    # sequence's tab as that tab's overview -- not inside a step, and not on
    # another learning tab.
    home = ladder.sequences[0].tab
    home_key = f"{home} / {home}"
    # `nav_groups` keeps nav order across its keys, so "first entry under
    # the tab" means: the tab's loose pages come before any step group, and
    # the hub heads them.
    home_keys = [key for key in groups if key.startswith(f"{home} / ")]
    if home_keys[:1] != [home_key] or groups[home_key][:1] != [ladder.hub]:
        out.append(
            f"{home}: the hub {ladder.hub} must be the first entry directly under "
            "this tab, as its overview"
        )
    for key, pages in groups.items():
        if key != home_key and ladder.hub in pages:
            out.append(
                f"{key}: the hub {ladder.hub} belongs directly under the {home} "
                "tab, nowhere else"
            )
    for seq in ladder.sequences:
        prefix = f"{seq.tab} / "
        flat_key = f"{seq.tab} / {seq.tab}"  # pages directly under the tab
        flat = [p for p in groups.get(flat_key, []) if p != ladder.hub]
        grouped = [
            (key[len(prefix) :], pages)
            for key, pages in groups.items()
            if key.startswith(prefix) and key != flat_key
        ]
        if not grouped and not flat and flat_key not in groups:
            out.append(f"{seq.tab}: tab not found in mkdocs.yml nav")
            continue
        if not grouped:
            members = seq.ordered_members()
            branches = {p: bs for t in seq.tiers for p, bs in t.branches.items()}
            out.extend(_order_findings(seq.tab, flat, members, branches))
            continue
        for page in flat:
            out.append(
                f"{seq.tab}: {page} sits directly under the tab; only the hub may, "
                "every other page belongs inside its step's group"
            )
        expected_titles = [nav_group_title(t) for t in seq.tiers]
        actual_titles = [title for title, _ in grouped]
        if actual_titles != expected_titles:
            out.append(
                f"{seq.tab}: nav groups {actual_titles} are not the ladder's steps "
                f"{expected_titles}, one group per step in step order"
            )
        # Match groups to steps by title, not position: with one group
        # missing or out of place, a positional pairing would check every
        # later group against the wrong step and bury the one real finding
        # above under a page-by-page cascade.
        by_title = dict(grouped)
        for tier in seq.tiers:
            pages = by_title.get(nav_group_title(tier))
            if pages is None:
                continue
            out.extend(
                _order_findings(
                    f"{seq.tab} / {nav_group_title(tier)}",
                    pages,
                    tier.members,
                    tier.branches,
                )
            )
    return out


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def check_learning_ladder(
    f,
    docs: Path = DOCS,
    ladder_path: Path = LADDER_PATH,
    mkdocs_path: Path | None = None,
) -> None:
    """Report every ladder-rule violation on `f` (a Findings with `.err`).

    `mkdocs_path` defaults to the `mkdocs.yml` beside the docs tree
    (`<docs>/../mkdocs.yml`, the repository's own for the real tree); the
    sidebar rule is skipped when that file does not exist."""
    if not ladder_path.is_file():
        f.err(CHECK, f"{ladder_path.name}: missing")
        return
    try:
        ladder = load_ladder(ladder_path)
    except LadderError as exc:
        f.err(CHECK, str(exc))
        return

    if mkdocs_path is None:
        mkdocs_path = docs.resolve().parent / "mkdocs.yml"
    if mkdocs_path.is_file():
        for msg in nav_findings(ladder, mkdocs_path.read_text(encoding="utf-8")):
            f.err(CHECK, msg)

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
                "except the hub is a member or branch of exactly one step)",
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
                        f"{member}: level {lvl} is below step {tier.id} ({tier.title}) floor {tier.floor}",
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
                            f"{branch}: level {blvl} is below step {tier.id} floor {tier.floor}",
                        )
            for link in tier.links:
                if link in ladder.index:
                    continue
                if link.startswith("learn/"):
                    f.err(
                        CHECK,
                        f"{link}: linked from step {tier.id} but a member of no step (a link "
                        "is a pointer, not a place to hide an unclassified page)",
                    )
                elif not (docs / link).is_file():
                    f.err(
                        CHECK, f"{link}: linked from step {tier.id} but does not exist"
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
                    f"path {rp.role!r}: {page} is not a member or branch of any step",
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
