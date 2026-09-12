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

"""`tests/conftest.py`'s skill-tree materialization must write only when the
published trees actually disagree with `skills-src/`.

The cost is secondary here. The correctness point is that
`gen_agent_skills.write_trees` *removes* each owned skill directory before
restoring it, so every superfluous writer reopens a window in which a
concurrent reader — another xdist worker's test, a parallel session, a
restarted worker — observes the tree absent. Under xdist the old hook ran
that rewrite once per worker plus once in the controller.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gen_agent_skills as gen  # noqa: E402

from tests.conftest import _materialize_generated_skill_trees  # noqa: E402


def test_no_write_when_the_trees_already_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """The steady state of every worker after the first, and of every rerun."""
    writes: list[dict[str, str]] = []
    monkeypatch.setattr(gen, "write_trees", lambda rendered, *a, **k: writes.append(rendered))

    # The session's own conftest hook already materialized the trees, so this
    # call sees them current.
    _materialize_generated_skill_trees()
    assert writes == []


def test_repeated_calls_never_write_more_than_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Idempotence over repeated invocations, whatever the starting state:
    the first call may or may not need to write, every later one must not."""
    real_write = gen.write_trees
    writes: list[int] = []

    def counting_write(rendered: dict[str, str], *args: object, **kwargs: object) -> None:
        writes.append(1)
        real_write(rendered, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(gen, "write_trees", counting_write)
    for _ in range(4):
        _materialize_generated_skill_trees()
    assert len(writes) <= 1


def test_the_staleness_oracle_is_content_keyed_not_existence_keyed(tmp_path: Path) -> None:
    """The decision rests on `check_trees`, which compares bytes. Exercised
    over every way a tree can be wrong — missing, truncated, byte-modified,
    carrying an extra generated file — not just the absent case, so a
    regression to an existence check ("the directory is there, skip") fails
    here."""
    rendered = gen.render_all()
    assert rendered, "the render must not be empty or this test is vacuous"
    root = tmp_path / "skills"
    roots = (root,)

    assert gen.check_trees(rendered, roots)  # nothing written yet
    gen.write_trees(rendered, roots)
    assert gen.check_trees(rendered, roots) == []

    some_rel = sorted(rendered)[0]
    target = root / some_rel

    original = target.read_text(encoding="utf-8")
    for corruption in ("", original + "\ndrift\n", original.replace("a", "b", 1)):
        if corruption == original:
            continue
        target.write_text(corruption, encoding="utf-8")
        assert gen.check_trees(rendered, roots), f"byte drift not detected: {corruption[:40]!r}"
        target.write_text(original, encoding="utf-8")
        assert gen.check_trees(rendered, roots) == []

    target.unlink()
    assert gen.check_trees(rendered, roots)
    gen.write_trees(rendered, roots)
    assert gen.check_trees(rendered, roots) == []

    stray = target.parent / "stray-generated-file.md"
    stray.write_text("not from the render\n", encoding="utf-8")
    assert gen.check_trees(rendered, roots), "an extra owned file is drift too"
