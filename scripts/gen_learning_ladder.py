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

"""gen_learning_ladder.py — render the learning-series hub's ladder and
role-path tables from `docs/_meta/learning-ladder.yaml`.

Splices two blocks of `docs/learn/abi-api-handling.md`, between
`<!-- BEGIN GENERATED: learning-ladder -->` / `<!-- END GENERATED:
learning-ladder -->` and `<!-- BEGIN GENERATED: learning-paths -->` / `<!--
END GENERATED: learning-paths -->` sentinels (the same splice-into-hand-
authored-file pattern `gen_platform_matrix.py` uses). The rest of the hub
stays hand-authored.

This script only renders and drift-checks. The ladder's *rules*
(completeness, monotonicity, floors, links, paths, footers) live in
`scripts/learning_ladder.py` and run under `scripts/check_docs_contract.py`,
so one gate owns every `docs/_meta/*.yaml` contract.

Usage:
    python scripts/gen_learning_ladder.py            # write the hub's blocks
    python scripts/gen_learning_ladder.py --check    # verify in sync (CI)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR / "scripts"))

from learning_ladder import (  # noqa: E402
    DOCS,
    LADDER_PATH,
    Ladder,
    Tier,
    load_ladder,
    page_level,
    page_title,
    relative_href,
)

LADDER_MARKER = "learning-ladder"
PATHS_MARKER = "learning-paths"

GENERATED_NOTE = (
    "<!-- This block is rendered from docs/_meta/learning-ladder.yaml by "
    "gen_learning_ladder.py — do not edit by hand. Edit the YAML and run "
    "`python scripts/gen_learning_ladder.py`. -->"
)


def _title(docs: Path, page: str) -> str:
    text = (docs / page).read_text(encoding="utf-8")
    return page_title(text) or Path(page).stem


def _level(docs: Path, page: str) -> str:
    text = (docs / page).read_text(encoding="utf-8")
    return page_level(text) or "?"


def _link(docs: Path, hub: str, page: str) -> str:
    return f"[{_title(docs, page)}]({relative_href(hub, page)})"


def _tier_label(seq_key: str, tier: Tier) -> str:
    return f"Tier {tier.id}" if seq_key == "educational" else f"{tier.id}"


def _level_badge(docs: Path, tier: Tier) -> str:
    levels = [_level(docs, m) for m in tier.members]
    if not levels:
        return tier.floor
    if levels[0] == levels[-1]:
        return levels[0]
    return f"{levels[0]} → {levels[-1]}"


def _link_note(ladder: Ladder, page: str) -> str:
    entry = ladder.index.get(page)
    if entry is None:
        return "tool guide"
    return f"on the {ladder.sequences[entry.sequence_index].tab} tab"


def render_ladder(ladder: Ladder, docs: Path) -> str:
    hub = ladder.hub
    out: list[str] = [GENERATED_NOTE, ""]
    for seq in ladder.sequences:
        out.append(f"**{seq.tab}**")
        out.append("")
        out.append("| Tier | Level | Pages |")
        out.append("|---|---|---|")
        for tier in seq.tiers:
            cells: list[str] = []
            for member in tier.members:
                cell = _link(docs, hub, member)
                branches = tier.branches.get(member, [])
                if branches:
                    deeper = ", ".join(_link(docs, hub, b) for b in branches)
                    cell += f" (go deeper: {deeper})"
                cells.append(cell)
            pages = (
                " → ".join(cells) if cells else "*(pages land as the plan's PRs merge)*"
            )
            if tier.links:
                also = "; ".join(
                    f"{_link(docs, hub, link)} ({_link_note(ladder, link)})"
                    for link in tier.links
                )
                pages += f"<br>also: {also}"
            out.append(
                f"| {_tier_label(seq.key, tier)} · {tier.title} | {_level_badge(docs, tier)} | {pages} |"
            )
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def render_paths(ladder: Ladder, docs: Path) -> str:
    hub = ladder.hub
    out: list[str] = [GENERATED_NOTE, ""]
    out.append("| Role | Path (tier · page) | Then |")
    out.append("|---|---|---|")
    for rp in ladder.paths:
        steps = []
        for page in rp.pages:
            entry = ladder.index[page]
            tier = ladder.sequences[entry.sequence_index].tiers[entry.tier_index]
            steps.append(f"{tier.id} · {_link(docs, hub, page)}")
        then = _link(docs, hub, rp.after) if rp.after else "—"
        out.append(f"| {rp.role} | {' → '.join(steps)} | {then} |")
    out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _splice(text: str, marker: str, inner: str, hub_path: Path) -> str:
    begin = re.search(rf"<!-- BEGIN GENERATED: {marker} -->\n", text)
    end = re.search(rf"\n<!-- END GENERATED: {marker} -->", text)
    if not begin or not end or begin.end() > end.start():
        raise ValueError(f"{hub_path}: missing/misordered sentinels for {marker!r}")
    return text[: begin.end()] + inner + text[end.start() :]


def render_hub(current: str, ladder: Ladder, docs: Path, hub_path: Path) -> str:
    text = _splice(current, LADDER_MARKER, render_ladder(ladder, docs), hub_path)
    return _splice(text, PATHS_MARKER, render_paths(ladder, docs), hub_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed hub is in sync instead of writing it",
    )
    parser.add_argument("--docs", type=Path, default=DOCS, help=argparse.SUPPRESS)
    parser.add_argument(
        "--ladder", type=Path, default=LADDER_PATH, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)

    ladder = load_ladder(args.ladder)
    hub_path = args.docs / ladder.hub
    current = hub_path.read_text(encoding="utf-8")
    rendered = render_hub(current, ladder, args.docs, hub_path)
    rel = (
        hub_path.relative_to(REPO_DIR)
        if hub_path.is_relative_to(REPO_DIR)
        else hub_path
    )

    if args.check:
        if rendered != current:
            print(
                f"{rel}'s generated learning-ladder/learning-paths blocks are stale — "
                "regenerate with:",
                file=sys.stderr,
            )
            print("  python scripts/gen_learning_ladder.py", file=sys.stderr)
            return 1
        print(f"{rel}'s learning-ladder blocks are in sync ({len(ladder.index)} pages)")
        return 0

    hub_path.write_text(rendered, encoding="utf-8")
    print(f"wrote {rel}'s learning-ladder blocks ({len(ladder.index)} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
