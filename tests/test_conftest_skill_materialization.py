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


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(2, "No such file or directory"),
        NotADirectoryError(20, "Not a directory"),
        OSError(5, "Input/output error"),
    ],
    ids=["missing", "not_a_dir", "generic"],
)
def test_a_concurrent_writer_does_not_abort_pytest_configuration(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    """`check_trees` enumerates the tree and then reads each file, so a file
    another process removes in between raises. That can only happen while a
    writer is mid-`write_trees` (it removes each owned directory before
    restoring it) — and this runs from `pytest_configure`, so letting it
    escape aborts the whole session.

    Stated over several `OSError` shapes rather than the one the race
    produces: the distinction that matters is "the tree is being rebuilt
    underneath us", not which errno the filesystem reported.
    """
    calls: list[str] = []

    def exploding_check(rendered: dict[str, str], *args: object, **kwargs: object):
        calls.append("check")
        if len(calls) == 1:  # only the unlocked fast path races
            raise error
        return []

    monkeypatch.setattr(gen, "check_trees", exploding_check)
    monkeypatch.setattr(gen, "write_trees", lambda *a, **k: calls.append("write"))

    _materialize_generated_skill_trees()  # must not raise

    # It treated the race as "possibly stale" and went to the lock, where the
    # re-check found the tree settled — so no redundant destructive rewrite.
    assert calls == ["check", "check"]


def test_a_real_interleaving_is_survived(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The same property against the real `check_trees` walking a tree that is
    genuinely disappearing under it, rather than a raised stand-in."""
    rendered = gen.render_all()
    root = tmp_path / "skills"
    gen.write_trees(rendered, (root,))
    owned = sorted(p for p in root.rglob("*") if p.is_file())
    assert owned, "fixture must contain files or this test is vacuous"

    real_check = gen.check_trees
    state = {"n": 0}

    def racing_check(r: dict[str, str], *args: object, **kwargs: object):
        state["n"] += 1
        if state["n"] == 1:
            # Stand in for a concurrent `write_trees`: enumerate, then delete.
            victim = owned[0]
            original = victim.read_bytes()
            victim.unlink()
            try:
                return real_check(r, (root,))
            finally:
                victim.write_bytes(original)
        return []

    monkeypatch.setattr(gen, "check_trees", racing_check)
    monkeypatch.setattr(gen, "write_trees", lambda *a, **k: None)

    _materialize_generated_skill_trees()  # must not raise
    assert state["n"] >= 1
