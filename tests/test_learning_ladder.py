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

"""Tests for scripts/learning_ladder.py — the learning series' reading order
as data and the `learning-ladder` docs-contract rules over it.

Each rule is exercised on a miniature docs tree rather than the real one, so
a fixture states the exact violation it expects (a page missing from the
ladder, a level regression inside a sequence, a branch below its parent, a
link that is nowhere a member, a same-tier reversal in a role path, a
repeated page, a footer pointing at the wrong neighbour) — plus the one
shape that must *pass*: the concepts sequence restarting at `intermediate`
after `educational` ends at `advanced`. The real repository is checked once
at the end.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "learning_ladder", _SCRIPTS / "learning_ladder.py"
)
assert _spec and _spec.loader
ll = importlib.util.module_from_spec(_spec)
sys.modules["learning_ladder"] = ll
_spec.loader.exec_module(ll)


class _Findings:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []

    def err(self, check: str, msg: str) -> None:
        self.errors.append((check, msg))

    def messages(self) -> list[str]:
        return [m for _, m in self.errors]


BASE_LADDER = """
version: 1
hub: learn/hub.md
sequences:
  educational:
    tab: Learn
    tiers:
      - id: 1
        title: Start
        floor: beginner
        members:
          - learn/a.md
          - learn/b.md
      - id: 2
        title: Deeper
        floor: intermediate
        members:
          - page: learn/c.md
            branches:
              - learn/c-branch.md
          - learn/d.md
        links:
          - learn/x.md
          - use/tool.md
  concepts:
    tab: Concepts
    tiers:
      - id: c1
        title: Model
        floor: intermediate
        members:
          - learn/x.md
paths:
  - role: Reader
    pages:
      - learn/a.md
      - learn/c.md
      - learn/c-branch.md
      - learn/d.md
      - learn/x.md
    after: use/tool.md
"""

LEVELS = {
    "learn/a.md": "beginner",
    "learn/b.md": "beginner",
    "learn/c.md": "intermediate",
    "learn/c-branch.md": "advanced",
    "learn/d.md": "advanced",
    "learn/x.md": "intermediate",
}


def _page(level: str | None, title: str) -> str:
    fm = f"---\nlevel: {level}\n---\n\n" if level else ""
    return f"{fm}# {title}\n\nBody.\n"


def _build(
    tmp_path: Path,
    ladder_text: str = BASE_LADDER,
    levels: dict[str, str] | None = None,
    extra_pages: dict[str, str] | None = None,
    footers: bool = True,
) -> tuple[Path, Path]:
    docs = tmp_path / "docs"
    (docs / "_meta").mkdir(parents=True)
    ladder_path = docs / "_meta" / "learning-ladder.yaml"
    ladder_path.write_text(ladder_text, encoding="utf-8")
    levels = dict(LEVELS if levels is None else levels)
    pages = {p: _page(lvl, p) for p, lvl in levels.items()}
    pages["learn/hub.md"] = "# Hub\n"
    pages["use/tool.md"] = "# Tool\n"
    pages.update(extra_pages or {})
    for rel, text in pages.items():
        path = docs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if footers:
        _write_footers(docs, ladder_path)
    return docs, ladder_path


def _write_footers(docs: Path, ladder_path: Path) -> None:
    ladder = ll.load_ladder(ladder_path)
    for page in ladder.index:
        path = docs / page
        if not path.is_file():
            continue
        prev, nxt = ladder.neighbours(page)
        footer = (
            f"**Ladder:** ← [{prev}]({ll.relative_href(page, prev)}) · Tier · "
            f"[{nxt}]({ll.relative_href(page, nxt)}) →\n"
        )
        path.write_text(
            path.read_text(encoding="utf-8") + "\n---\n\n" + footer,
            encoding="utf-8",
        )


def _run(docs: Path, ladder_path: Path) -> list[str]:
    f = _Findings()
    ll.check_learning_ladder(f, docs=docs, ladder_path=ladder_path)
    return f.messages()


# A sidebar that is the BASE_LADDER: one group per step, titled
# "<id>. <title>", members in ladder order, the branch grouped at the end
# of its step, the hub alone directly under the tab, Concepts flat.
NAV_OK = """
site_name: x
nav:
  - Home: index.md
  - Learn:
    - Overview: learn/hub.md
    - "1. Start":
      - A: learn/a.md
      - B: learn/b.md
    - "2. Deeper":
      - C: learn/c.md
      - D: learn/d.md
      - Go Deeper (optional):
        - CB: learn/c-branch.md
  - Concepts:
    - X: learn/x.md
"""


def _run_with_nav(tmp_path: Path, nav: str) -> list[str]:
    docs, ladder = _build(tmp_path)
    (docs.parent / "mkdocs.yml").write_text(nav, encoding="utf-8")
    return _run(docs, ladder)


# --- the baseline fixture is clean --------------------------------------


def test_clean_fixture_has_no_findings(tmp_path: Path) -> None:
    docs, ladder = _build(tmp_path)
    assert _run(docs, ladder) == []


def test_concepts_may_restart_lower_than_educational_ends(tmp_path: Path) -> None:
    """Sequences are ordered independently: `educational` ends at advanced
    and `concepts` restarts at intermediate — that is not a regression."""
    docs, ladder = _build(tmp_path)
    assert ll.load_ladder(ladder).sequences[1].tiers[0].floor == "intermediate"
    assert _run(docs, ladder) == []


# --- completeness / existence -------------------------------------------


def test_learn_page_missing_from_ladder_is_an_error(tmp_path: Path) -> None:
    docs, ladder = _build(
        tmp_path, extra_pages={"learn/orphan.md": _page("beginner", "o")}
    )
    msgs = _run(docs, ladder)
    assert any("learn/orphan.md" in m and "not placed" in m for m in msgs)


def test_listed_page_that_does_not_exist_is_an_error(tmp_path: Path) -> None:
    docs, ladder = _build(tmp_path)
    (docs / "learn" / "b.md").unlink()
    msgs = _run(docs, ladder)
    assert any("learn/b.md" in m and "does not exist" in m for m in msgs)


def test_page_placed_twice_is_a_load_error(tmp_path: Path) -> None:
    text = BASE_LADDER.replace(
        "          - learn/d.md\n", "          - learn/d.md\n          - learn/a.md\n"
    )
    docs, ladder = _build(tmp_path, ladder_text=text, footers=False)
    msgs = _run(docs, ladder)
    assert len(msgs) == 1 and "placed twice" in msgs[0]


def test_hub_may_not_be_placed(tmp_path: Path) -> None:
    text = BASE_LADDER.replace(
        "          - learn/a.md\n          - learn/b.md\n",
        "          - learn/hub.md\n          - learn/a.md\n          - learn/b.md\n",
        1,
    )
    docs, ladder = _build(tmp_path, ladder_text=text, footers=False)
    msgs = _run(docs, ladder)
    assert any("hub is exempt" in m for m in msgs)


# --- levels: monotonicity, floors, branches ----------------------------


def test_level_regression_inside_a_sequence_is_an_error(tmp_path: Path) -> None:
    levels = dict(LEVELS, **{"learn/d.md": "beginner"})
    docs, ladder = _build(tmp_path, levels=levels)
    msgs = _run(docs, ladder)
    assert any("learn/d.md" in m and "regresses" in m for m in msgs)
    assert any("learn/d.md" in m and "floor" in m for m in msgs)


def test_member_below_tier_floor_is_an_error_even_when_monotonic(
    tmp_path: Path,
) -> None:
    """Monotonicity alone would let a whole tier be downgraded; the floor
    closes that."""
    text = BASE_LADDER.replace(
        "floor: intermediate\n        members:\n          - page: learn/c.md",
        "floor: advanced\n        members:\n          - page: learn/c.md",
    )
    docs, ladder = _build(tmp_path, ladder_text=text)
    msgs = _run(docs, ladder)
    assert any("learn/c.md" in m and "below step 2" in m for m in msgs)


def test_branch_below_its_parent_is_an_error(tmp_path: Path) -> None:
    levels = dict(LEVELS, **{"learn/c-branch.md": "beginner"})
    docs, ladder = _build(tmp_path, levels=levels)
    msgs = _run(docs, ladder)
    assert any("learn/c-branch.md" in m and "below its parent" in m for m in msgs)


def test_branch_is_outside_the_spine_monotonicity(tmp_path: Path) -> None:
    """An advanced branch between two intermediate members is fine."""
    levels = dict(
        LEVELS, **{"learn/d.md": "intermediate", "learn/c-branch.md": "advanced"}
    )
    docs, ladder = _build(tmp_path, levels=levels)
    assert _run(docs, ladder) == []


def test_page_without_level_is_an_error(tmp_path: Path) -> None:
    levels = dict(LEVELS, **{"learn/b.md": None})  # type: ignore[dict-item]
    docs, ladder = _build(tmp_path, levels=levels)
    msgs = _run(docs, ladder)
    assert any("learn/b.md" in m and "no front-matter `level:`" in m for m in msgs)


# --- links --------------------------------------------------------------


def test_link_that_is_nowhere_a_member_is_an_error(tmp_path: Path) -> None:
    text = BASE_LADDER.replace(
        "        links:\n          - learn/x.md\n",
        "        links:\n          - learn/x.md\n          - learn/stray.md\n",
    )
    docs, ladder = _build(tmp_path, ladder_text=text)
    # the stray page exists but is not placed; it is reported both ways
    (docs / "learn" / "stray.md").write_text(
        _page("advanced", "stray"), encoding="utf-8"
    )
    msgs = _run(docs, ladder)
    assert any("learn/stray.md" in m and "member of no step" in m for m in msgs)


def test_tool_track_link_must_exist(tmp_path: Path) -> None:
    text = BASE_LADDER.replace(
        "          - use/tool.md\n", "          - use/missing.md\n", 1
    )
    docs, ladder = _build(tmp_path, ladder_text=text)
    msgs = _run(docs, ladder)
    assert any("use/missing.md" in m and "does not exist" in m for m in msgs)


# --- paths --------------------------------------------------------------


def test_path_stepping_down_a_tier_is_an_error(tmp_path: Path) -> None:
    text = BASE_LADDER.replace(
        "      - learn/d.md\n      - learn/x.md\n",
        "      - learn/d.md\n      - learn/b.md\n      - learn/x.md\n",
    )
    docs, ladder = _build(tmp_path, ladder_text=text)
    msgs = _run(docs, ladder)
    assert any("learn/b.md does not come after learn/d.md" in m for m in msgs)


def test_same_tier_reversal_in_a_path_is_an_error(tmp_path: Path) -> None:
    """Comparing tier positions alone would let two members of one tier
    appear in reverse order; the full ladder index catches it."""
    text = BASE_LADDER.replace(
        "      - learn/a.md\n      - learn/c.md\n",
        "      - learn/b.md\n      - learn/a.md\n      - learn/c.md\n",
    )
    docs, ladder = _build(tmp_path, ladder_text=text)
    msgs = _run(docs, ladder)
    assert any("learn/a.md does not come after learn/b.md" in m for m in msgs)


def test_repeated_page_in_a_path_is_an_error(tmp_path: Path) -> None:
    text = BASE_LADDER.replace(
        "      - learn/d.md\n      - learn/x.md\n",
        "      - learn/d.md\n      - learn/d.md\n      - learn/x.md\n",
    )
    docs, ladder = _build(tmp_path, ladder_text=text)
    msgs = _run(docs, ladder)
    assert any("learn/d.md is listed twice" in m for m in msgs)


def test_branch_sorts_after_its_parent_and_before_the_next_member(
    tmp_path: Path,
) -> None:
    ladder = ll.load_ladder(_build(tmp_path)[1])
    keys = [
        ladder.index[p].order_key
        for p in ("learn/c.md", "learn/c-branch.md", "learn/d.md")
    ]
    assert keys == sorted(keys) and len(set(keys)) == 3


_TWO_BRANCHES = BASE_LADDER.replace(
    "            branches:\n              - learn/c-branch.md\n",
    "            branches:\n              - learn/c-branch.md\n              - learn/c-branch2.md\n",
)
_TWO_BRANCH_LEVELS = dict(LEVELS, **{"learn/c-branch2.md": "advanced"})


def test_sibling_branches_have_distinct_increasing_keys(tmp_path: Path) -> None:
    """Two branches of one parent are two side reads in YAML order, not one
    position: a path may walk both in that order and is rejected for
    walking them reversed."""
    docs, ladder_path = _build(
        tmp_path, ladder_text=_TWO_BRANCHES, levels=_TWO_BRANCH_LEVELS
    )
    ladder = ll.load_ladder(ladder_path)
    keys = [
        ladder.index[p].order_key
        for p in ("learn/c.md", "learn/c-branch.md", "learn/c-branch2.md", "learn/d.md")
    ]
    assert keys == sorted(keys) and len(set(keys)) == 4
    assert _run(docs, ladder_path) == []
    forward = _TWO_BRANCHES.replace(
        "      - learn/c-branch.md\n      - learn/d.md\n",
        "      - learn/c-branch.md\n      - learn/c-branch2.md\n      - learn/d.md\n",
    )
    assert forward != _TWO_BRANCHES
    docs, ladder_path = _build(
        tmp_path / "fwd", ladder_text=forward, levels=_TWO_BRANCH_LEVELS
    )
    assert _run(docs, ladder_path) == []
    reversed_ = _TWO_BRANCHES.replace(
        "      - learn/c-branch.md\n      - learn/d.md\n",
        "      - learn/c-branch2.md\n      - learn/c-branch.md\n      - learn/d.md\n",
    )
    docs, ladder_path = _build(
        tmp_path / "rev", ladder_text=reversed_, levels=_TWO_BRANCH_LEVELS
    )
    msgs = _run(docs, ladder_path)
    assert any(
        "learn/c-branch.md does not come after learn/c-branch2.md" in m for m in msgs
    )


def test_after_must_be_a_tool_track_page(tmp_path: Path) -> None:
    text = BASE_LADDER.replace("    after: use/tool.md\n", "    after: learn/x.md\n")
    docs, ladder = _build(tmp_path, ladder_text=text)
    msgs = _run(docs, ladder)
    assert any("`after` must be a tool-track page" in m for m in msgs)


# --- footers ------------------------------------------------------------


def test_missing_footer_is_an_error(tmp_path: Path) -> None:
    docs, ladder = _build(tmp_path, footers=False)
    msgs = _run(docs, ladder)
    assert sum("no `**Ladder:**` footer" in m for m in msgs) == len(LEVELS)


def test_footer_pointing_at_the_wrong_neighbour_is_an_error(tmp_path: Path) -> None:
    docs, ladder = _build(tmp_path)
    page = docs / "learn" / "b.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "[learn/c.md](c.md)", "[learn/d.md](d.md)"
        ),
        encoding="utf-8",
    )
    msgs = _run(docs, ladder)
    assert any("learn/b.md" in m and "do not match" in m for m in msgs)


def test_neighbours_follow_members_then_hub_and_branches_rejoin(tmp_path: Path) -> None:
    ladder = ll.load_ladder(_build(tmp_path)[1])
    assert ladder.neighbours("learn/a.md") == ("learn/hub.md", "learn/b.md")
    assert ladder.neighbours("learn/d.md") == ("learn/c.md", "learn/hub.md")
    assert ladder.neighbours("learn/c-branch.md") == ("learn/c.md", "learn/d.md")
    assert ladder.neighbours("learn/x.md") == ("learn/hub.md", "learn/hub.md")


@pytest.mark.parametrize(
    "text, title",
    [
        (
            "---\ntitle: From Front Matter\nlevel: beginner\n---\n# H1\n",
            "From Front Matter",
        ),
        ("# Just the H1 — with dash\n\nbody\n", "Just the H1 — with dash"),
        ("no heading\n", ""),
    ],
)
def test_page_title_prefers_front_matter_then_h1(text: str, title: str) -> None:
    assert ll.page_title(text) == title


# --- the real repository -------------------------------------------------


# --- the sidebar rule -----------------------------------------------------


def test_sidebar_that_is_the_ladder_is_clean(tmp_path: Path) -> None:
    assert _run_with_nav(tmp_path, NAV_OK) == []


def test_no_mkdocs_file_skips_the_sidebar_rule(tmp_path: Path) -> None:
    """A fixture tree without a mkdocs.yml is checked for everything else;
    an explicit path that does not exist is the same skip."""
    docs, ladder = _build(tmp_path)
    f = _Findings()
    ll.check_learning_ladder(
        f, docs=docs, ladder_path=ladder, mkdocs_path=tmp_path / "nope.yml"
    )
    assert f.messages() == []


def test_sidebar_group_titles_must_be_the_numbered_steps(tmp_path: Path) -> None:
    nav = NAV_OK.replace('"2. Deeper"', "Deeper")
    msgs = _run_with_nav(tmp_path, nav)
    assert any("not the ladder's steps" in m and "'2. Deeper'" in m for m in msgs)


def test_sidebar_groups_must_be_in_step_order(tmp_path: Path) -> None:
    nav = """
nav:
  - Learn:
    - Overview: learn/hub.md
    - "2. Deeper":
      - C: learn/c.md
      - D: learn/d.md
      - CB: learn/c-branch.md
    - "1. Start":
      - A: learn/a.md
      - B: learn/b.md
  - Concepts:
    - X: learn/x.md
"""
    msgs = _run_with_nav(tmp_path, nav)
    assert any("one group per step in step order" in m for m in msgs)


def test_missing_group_reports_once_without_a_page_cascade(tmp_path: Path) -> None:
    """Groups are matched to steps by title: one missing group is one
    finding naming the fix, not every later page reported twice."""
    nav = NAV_OK.replace(
        '    - "2. Deeper":\n      - C: learn/c.md\n      - D: learn/d.md\n'
        "      - Go Deeper (optional):\n        - CB: learn/c-branch.md\n",
        "",
    )
    msgs = _run_with_nav(tmp_path, nav)
    assert len(msgs) == 1 and "one group per step in step order" in msgs[0]


def test_page_in_another_steps_group_is_reported_from_both_sides(
    tmp_path: Path,
) -> None:
    nav = NAV_OK.replace("      - D: learn/d.md\n", "").replace(
        "      - B: learn/b.md\n", "      - B: learn/b.md\n      - D: learn/d.md\n"
    )
    msgs = _run_with_nav(tmp_path, nav)
    assert any("2. Deeper" in m and "learn/d.md is missing" in m for m in msgs)
    assert any(
        "1. Start" in m and "learn/d.md" in m and "places it elsewhere" in m
        for m in msgs
    )


def test_members_out_of_ladder_order_inside_a_group_is_an_error(
    tmp_path: Path,
) -> None:
    nav = NAV_OK.replace(
        "      - A: learn/a.md\n      - B: learn/b.md\n",
        "      - B: learn/b.md\n      - A: learn/a.md\n",
    )
    msgs = _run_with_nav(tmp_path, nav)
    assert any("1. Start" in m and "not in ladder order" in m for m in msgs)


def test_branch_before_its_parent_in_the_sidebar_is_an_error(tmp_path: Path) -> None:
    nav = """
nav:
  - Learn:
    - Overview: learn/hub.md
    - "1. Start":
      - A: learn/a.md
      - B: learn/b.md
    - "2. Deeper":
      - CB: learn/c-branch.md
      - C: learn/c.md
      - D: learn/d.md
  - Concepts:
    - X: learn/x.md
"""
    msgs = _run_with_nav(tmp_path, nav)
    assert any("learn/c-branch.md must follow learn/c.md directly" in m for m in msgs)


def test_sibling_branches_out_of_ladder_order_is_an_error(tmp_path: Path) -> None:
    """All branches are advanced, so the level gate cannot tell two of them
    apart; the sidebar must still list them in the ladder's own order."""
    ladder_text = BASE_LADDER.replace(
        "              - learn/c-branch.md\n",
        "              - learn/c-branch.md\n              - learn/c-branch2.md\n",
    )
    levels = {**LEVELS, "learn/c-branch2.md": "advanced"}
    docs, ladder = _build(tmp_path, ladder_text=ladder_text, levels=levels)
    nav_ok = NAV_OK.replace(
        "        - CB: learn/c-branch.md\n",
        "        - CB: learn/c-branch.md\n        - CB2: learn/c-branch2.md\n",
    )
    (docs.parent / "mkdocs.yml").write_text(nav_ok, encoding="utf-8")
    assert _run(docs, ladder) == []
    swapped = NAV_OK.replace(
        "        - CB: learn/c-branch.md\n",
        "        - CB2: learn/c-branch2.md\n        - CB: learn/c-branch.md\n",
    )
    (docs.parent / "mkdocs.yml").write_text(swapped, encoding="utf-8")
    msgs = _run(docs, ladder)
    assert any("go-deeper pages are not in ladder order" in m for m in msgs)


def test_branch_between_later_members_is_an_error(tmp_path: Path) -> None:
    """After its parent but before a later member is a position the ladder
    has no reading for: neither the parent's own tail nor the step's
    trailing go-deeper block."""
    ladder_text = BASE_LADDER.replace(
        "          - learn/d.md\n", "          - learn/d.md\n          - learn/e.md\n"
    )
    levels = {**LEVELS, "learn/e.md": "advanced"}
    docs, ladder = _build(tmp_path, ladder_text=ladder_text, levels=levels)
    nav = NAV_OK.replace(
        "      - D: learn/d.md\n      - Go Deeper (optional):\n        - CB: learn/c-branch.md\n",
        "      - D: learn/d.md\n      - CB: learn/c-branch.md\n      - E: learn/e.md\n",
    )
    (docs.parent / "mkdocs.yml").write_text(nav, encoding="utf-8")
    msgs = _run(docs, ladder)
    assert any("learn/c-branch.md must follow learn/c.md directly" in m for m in msgs)
    trailing = NAV_OK.replace(
        "      - D: learn/d.md\n", "      - D: learn/d.md\n      - E: learn/e.md\n"
    )
    (docs.parent / "mkdocs.yml").write_text(trailing, encoding="utf-8")
    assert _run(docs, ladder) == []


def test_hub_must_sit_directly_under_the_first_sequences_tab(
    tmp_path: Path,
) -> None:
    moved = NAV_OK.replace("    - Overview: learn/hub.md\n", "").replace(
        "  - Concepts:\n", "  - Concepts:\n    - Overview: learn/hub.md\n"
    )
    msgs = _run_with_nav(tmp_path, moved)
    assert any(
        "Learn: the hub learn/hub.md must be the first entry directly under" in m
        for m in msgs
    )
    assert any("Concepts / Concepts: the hub" in m for m in msgs)


def test_hub_listed_after_the_steps_is_an_error(tmp_path: Path) -> None:
    last = NAV_OK.replace("    - Overview: learn/hub.md\n", "").replace(
        "  - Concepts:\n", "    - Overview: learn/hub.md\n  - Concepts:\n"
    )
    msgs = _run_with_nav(tmp_path, last)
    assert any("must be the first entry directly under" in m for m in msgs)


def test_hub_inside_a_step_group_is_an_error(tmp_path: Path) -> None:
    inside = NAV_OK.replace("    - Overview: learn/hub.md\n", "").replace(
        '    - "1. Start":\n', '    - "1. Start":\n      - Overview: learn/hub.md\n'
    )
    msgs = _run_with_nav(tmp_path, inside)
    assert any(
        "Learn: the hub learn/hub.md must be the first entry directly under" in m
        for m in msgs
    )
    assert any("Learn / 1. Start: the hub" in m for m in msgs)


def test_branch_right_after_its_parent_is_also_accepted(tmp_path: Path) -> None:
    """The branch may follow its parent directly instead of closing the step."""
    nav = NAV_OK.replace(
        "      - C: learn/c.md\n      - D: learn/d.md\n      - Go Deeper (optional):\n        - CB: learn/c-branch.md\n",
        "      - C: learn/c.md\n      - CB: learn/c-branch.md\n      - D: learn/d.md\n",
    )
    assert _run_with_nav(tmp_path, nav) == []


def test_page_directly_under_the_tab_other_than_the_hub_is_an_error(
    tmp_path: Path,
) -> None:
    nav = NAV_OK.replace("      - B: learn/b.md\n", "").replace(
        "    - Overview: learn/hub.md\n",
        "    - Overview: learn/hub.md\n    - B: learn/b.md\n",
    )
    msgs = _run_with_nav(tmp_path, nav)
    assert any("learn/b.md sits directly under the tab" in m for m in msgs)


def test_flat_tab_must_list_its_sequence_in_order(tmp_path: Path) -> None:
    nav = NAV_OK.replace(
        "    - X: learn/x.md\n", "    - A: learn/a.md\n    - X: learn/x.md\n"
    )
    msgs = _run_with_nav(tmp_path, nav)
    assert any("Concepts" in m and "learn/a.md" in m and "elsewhere" in m for m in msgs)


def test_missing_tab_is_an_error(tmp_path: Path) -> None:
    nav = "nav:\n  - Home: index.md\n"
    msgs = _run_with_nav(tmp_path, nav)
    assert any("Learn: tab not found" in m for m in msgs)
    assert any("Concepts: tab not found" in m for m in msgs)


# --- reader-facing spellings -----------------------------------------------


@pytest.mark.parametrize(
    ("title", "short"),
    [
        (
            "Part 1 — Foundations: From Source Code to a Running Process",
            "Part 1 — Foundations",
        ),
        (
            "Part 0 — Compatibility as a Product Contract",
            "Part 0 — Compatibility as a Product Contract",
        ),
        (
            "Detecting Breaks: Evidence, Tools, and Why One Method Is Never Enough",
            "Detecting Breaks",
        ),
        (
            "What Each Level Sees — a level-by-level walk-through",
            "What Each Level Sees",
        ),
        ("Exception Unwinding: The Machinery Behind `noexcept`", "Exception Unwinding"),
        ("Data, Wire & Storage Compatibility", "Data, Wire & Storage Compatibility"),
        ("ABI/API Compatibility — A Learning Series", "ABI/API Compatibility"),
    ],
)
def test_short_title_drops_the_subtitle_but_keeps_a_parts_name(
    title: str, short: str
) -> None:
    assert ll.short_title(title) == short


def test_step_label_numbers_the_educational_sequence_only(tmp_path: Path) -> None:
    _, ladder_path = _build(tmp_path)
    ladder = ll.load_ladder(ladder_path)
    edu, concepts = ladder.sequences
    assert ll.step_label(edu, edu.tiers[1]) == "Step 2"
    assert ll.step_label(concepts, concepts.tiers[0]) == "Concepts c1"
    assert ll.nav_group_title(edu.tiers[1]) == "2. Deeper"


def test_numbered_steps_must_be_contiguous_from_one(tmp_path: Path) -> None:
    """The hub renders a numbered sequence as a Markdown ordered list, which
    numbers items positionally, so ids that are not 1..n would show one
    number on the hub and another in the sidebar and footers."""
    text = BASE_LADDER.replace(
        "      - id: 2\n        title: Deeper", "      - id: 3\n        title: Deeper"
    )
    docs, ladder = _build(tmp_path, ladder_text=text, footers=False)
    msgs = _run(docs, ladder)
    assert len(msgs) == 1 and "numbered steps must be ids ['1', '2']" in msgs[0]


class TestOrderFindingsProperties:
    """`_order_findings` is a reusable grouping-order primitive, so its
    contract is stated as invariants over a small exhaustive domain rather
    than only through the sidebar tests above: every permutation of one
    step's pages is either one of the layouts the rule admits — built here
    by an independent construction — or reported, never both."""

    MEMBERS = ["m1", "m2", "m3"]
    BRANCHES = {"m1": ["b1", "b2"], "m2": ["b3"]}
    PAGES = MEMBERS + ["b1", "b2", "b3"]

    @classmethod
    def accepted_layouts(cls) -> set[tuple[str, ...]]:
        """Each parent's branches sit either directly after it or in the
        trailing block after the last member, and the branches keep their
        ladder order among themselves."""
        layouts: set[tuple[str, ...]] = set()
        parents = [m for m in cls.MEMBERS if m in cls.BRANCHES]
        ladder_order = [b for bs in cls.BRANCHES.values() for b in bs]
        for inline in itertools.product([True, False], repeat=len(parents)):
            pages: list[str] = []
            trailing: list[str] = []
            for member in cls.MEMBERS:
                pages.append(member)
                branches = cls.BRANCHES.get(member, [])
                if member in parents and inline[parents.index(member)]:
                    pages.extend(branches)
                else:
                    trailing.extend(branches)
            layout = pages + trailing
            if [p for p in layout if p in ladder_order] == ladder_order:
                layouts.add(tuple(layout))
        return layouts

    def test_exactly_the_admitted_layouts_are_clean(self) -> None:
        accepted = self.accepted_layouts()
        assert len(accepted) == 3  # both inline, both trailing, m1 inline only
        for perm in itertools.permutations(self.PAGES):
            findings = ll._order_findings("g", list(perm), self.MEMBERS, self.BRANCHES)
            assert (findings == []) == (perm in accepted), (perm, findings)

    def test_branch_mapping_iteration_order_never_changes_the_verdict(
        self,
    ) -> None:
        reversed_branches = dict(reversed(list(self.BRANCHES.items())))
        for perm in itertools.permutations(self.PAGES):
            a = ll._order_findings("g", list(perm), self.MEMBERS, self.BRANCHES)
            b = ll._order_findings("g", list(perm), self.MEMBERS, reversed_branches)
            assert (a == []) == (b == []), perm

    def test_every_absent_page_and_every_stray_page_is_named(self) -> None:
        for dropped in self.PAGES:
            pages = [p for p in self.PAGES if p != dropped]
            findings = ll._order_findings("g", pages, self.MEMBERS, self.BRANCHES)
            assert any(f"{dropped} is missing" in m for m in findings), dropped
        findings = ll._order_findings(
            "g", self.PAGES + ["stray"], self.MEMBERS, self.BRANCHES
        )
        assert any("stray is in this nav group" in m for m in findings)


def test_real_repository_ladder_is_clean() -> None:
    f = _Findings()
    ll.check_learning_ladder(f)
    assert f.errors == [], f.messages()
