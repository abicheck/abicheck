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
      - id: 0
        title: Start
        floor: beginner
        members:
          - learn/a.md
          - learn/b.md
      - id: 1
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
        path.write_text(path.read_text(encoding="utf-8") + "\n---\n\n" + footer)


def _run(docs: Path, ladder_path: Path) -> list[str]:
    f = _Findings()
    ll.check_learning_ladder(f, docs=docs, ladder_path=ladder_path)
    return f.messages()


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
    assert any("learn/c.md" in m and "below tier 1" in m for m in msgs)


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
    (docs / "learn" / "stray.md").write_text(_page("advanced", "stray"))
    msgs = _run(docs, ladder)
    assert any("learn/stray.md" in m and "member of no tier" in m for m in msgs)


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
        page.read_text().replace("[learn/c.md](c.md)", "[learn/d.md](d.md)")
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


def test_real_repository_ladder_is_clean() -> None:
    f = _Findings()
    ll.check_learning_ladder(f)
    assert f.errors == [], f.messages()
