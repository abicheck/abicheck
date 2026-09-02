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

"""Tests for scripts/learning_nav_order.py — the `learning-nav-order` gate.

The nav reader is exercised on a synthetic `mkdocs.yml` (grouped tab,
flat tab, quoted titles, comments, an unrelated tab) and the level rule on
a synthetic docs tree; the real repository is checked once at the end.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "learning_nav_order", _SCRIPTS / "learning_nav_order.py"
)
assert _spec and _spec.loader
lno = importlib.util.module_from_spec(_spec)
sys.modules["learning_nav_order"] = lno
_spec.loader.exec_module(lno)


NAV = """
site_name: x
nav:
  - Home: index.md
  - ABI/API Compatibility:
    - Start:
      - Overview: learn/hub.md
      - "Five Minutes": learn/five.md
    - Mechanics:   # a comment
      - Part 2: learn/p2.md
      - 'Deep': learn/deep.md
      - Part 5: learn/p5.md
  - Reference:
    - CLI: reference/cli.md
  - Concepts:
    - Verdicts: learn/verdicts.md
    - Internals: learn/internals.md
plugins:
  - search
"""


def test_nav_groups_reads_grouped_and_flat_tabs_only() -> None:
    groups = lno.nav_groups(NAV)
    assert groups == {
        "ABI/API Compatibility / Start": ["learn/hub.md", "learn/five.md"],
        "ABI/API Compatibility / Mechanics": [
            "learn/p2.md",
            "learn/deep.md",
            "learn/p5.md",
        ],
        "Concepts / Concepts": ["learn/verdicts.md", "learn/internals.md"],
    }


def test_nav_subgroups_keep_the_nesting_nav_groups_folds() -> None:
    nav = NAV.replace(
        "      - Part 5: learn/p5.md\n",
        "      - Part 5: learn/p5.md\n      - Go Deeper (optional):\n        - X: learn/x.md\n        - Y: learn/y.md\n      - Part 6: learn/p6.md\n",
    )
    assert lno.nav_subgroups(nav) == {
        "ABI/API Compatibility / Mechanics": [
            ("Go Deeper (optional)", ["learn/x.md", "learn/y.md"])
        ]
    }
    # the flat view still lists every page in nav order
    assert lno.nav_groups(nav)["ABI/API Compatibility / Mechanics"] == [
        "learn/p2.md",
        "learn/deep.md",
        "learn/p5.md",
        "learn/x.md",
        "learn/y.md",
        "learn/p6.md",
    ]
    assert lno.nav_subgroups(NAV) == {}


def test_nav_page_count_sees_every_tab() -> None:
    assert lno.nav_page_count(NAV, "learn/hub.md") == 1
    everywhere = NAV.replace(
        "    - CLI: reference/cli.md\n",
        "    - CLI: reference/cli.md\n    - Hub: learn/hub.md\n",
    )
    assert lno.nav_page_count(everywhere, "learn/hub.md") == 2
    assert lno.nav_page_count(NAV, "learn/nope.md") == 0


def test_duplicate_nav_groups_are_named_once_in_order() -> None:
    nav = NAV.replace(
        "    - Mechanics:   # a comment\n",
        "    - Start:\n      - Extra: learn/extra.md\n    - Mechanics:   # a comment\n",
    )
    assert lno.duplicate_nav_groups(nav) == ["ABI/API Compatibility / Start"]
    assert lno.duplicate_nav_groups(NAV) == []
    twice = NAV.replace(
        "  - Reference:\n", "  - Concepts:\n    - Y: learn/y.md\n  - Reference:\n"
    )
    assert lno.duplicate_nav_groups(twice) == ["Concepts"]
    # the merged view still holds both halves, in order
    assert lno.nav_groups(nav)["ABI/API Compatibility / Start"] == [
        "learn/hub.md",
        "learn/five.md",
        "learn/extra.md",
    ]


def _docs(tmp_path: Path, levels: dict[str, str | None]) -> Path:
    docs = tmp_path / "docs"
    for page, level in levels.items():
        path = docs / page
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = f"---\nlevel: {level}\n---\n" if level else ""
        path.write_text(f"{fm}# {page}\n", encoding="utf-8")
    return docs


def test_non_decreasing_groups_are_clean_and_hub_is_skipped(tmp_path: Path) -> None:
    docs = _docs(
        tmp_path,
        {
            "learn/hub.md": None,
            "learn/five.md": "beginner",
            "learn/p2.md": "intermediate",
            "learn/deep.md": "advanced",
            "learn/p5.md": "advanced",
            "learn/verdicts.md": "intermediate",
            "learn/internals.md": "advanced",
        },
    )
    assert lno.nav_order_findings(NAV, docs=docs, hub="learn/hub.md") == []


def test_branch_page_regression_inside_a_group_is_reported(tmp_path: Path) -> None:
    """An advanced side read followed by an intermediate Part regresses the
    sidebar even though the ladder spine exempts the branch."""
    docs = _docs(
        tmp_path,
        {
            "learn/hub.md": None,
            "learn/five.md": "beginner",
            "learn/p2.md": "intermediate",
            "learn/deep.md": "advanced",
            "learn/p5.md": "intermediate",
            "learn/verdicts.md": "intermediate",
            "learn/internals.md": "advanced",
        },
    )
    findings = lno.nav_order_findings(NAV, docs=docs, hub="learn/hub.md")
    assert len(findings) == 1
    assert "Mechanics" in findings[0]
    assert "learn/p5.md (intermediate) follows learn/deep.md (advanced)" in findings[0]


def test_missing_level_is_reported(tmp_path: Path) -> None:
    docs = _docs(
        tmp_path,
        {
            "learn/hub.md": None,
            "learn/five.md": None,
            "learn/p2.md": "intermediate",
            "learn/deep.md": "advanced",
            "learn/p5.md": "advanced",
            "learn/verdicts.md": "intermediate",
            "learn/internals.md": "advanced",
        },
    )
    findings = lno.nav_order_findings(NAV, docs=docs, hub="learn/hub.md")
    assert findings == [
        "ABI/API Compatibility / Start: learn/five.md has no valid front-matter level"
    ]


def test_real_repository_nav_is_non_decreasing() -> None:
    findings = lno.nav_order_findings(lno.MKDOCS.read_text(encoding="utf-8"))
    assert findings == []
