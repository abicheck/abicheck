"""The per-file include memo of ``_expand_with_quoted_includes`` never changes
its answer.

Bug class: a whole-directory ``-H`` re-ran the quoted-include expansion once
per header *group* (bisecting header-graph parse, unparseable-header probes,
both language-mode polarities), re-preprocessing every file each time --
O(groups x files). The fix memoizes the per-file include list by content
digest and polarity. The invariant: for any header forest, any group order,
any polarity, and across file edits between calls, the memoized expansion
equals an independent brute-force reachability oracle written here.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from abicheck.extract import quoted_include_expansion as qie


def _oracle(roots: list[Path], *, cxx_guard_reachable: bool) -> list[Path]:
    """Brute-force BFS over the restricted header language generated below:
    lines are ``#include "x"``, ``// #include "x"`` (comment), or an
    ``#ifdef __cplusplus`` block around one include (reachable only when
    ``cxx_guard_reachable``)."""
    seen: set[Path] = set()
    out: list[Path] = []
    queue = list(roots)
    while queue:
        p = queue.pop(0)
        r = p.resolve()
        if r in seen:
            continue
        seen.add(r)
        out.append(p)
        lines = p.read_text().splitlines()
        in_cxx = False
        for line in lines:
            if line.startswith("#ifdef __cplusplus"):
                in_cxx = True
                continue
            if line.startswith("#endif"):
                in_cxx = False
                continue
            m = re.fullmatch(r'#include "([^"]+)"', line)
            if not m or (in_cxx and not cxx_guard_reachable):
                continue
            target = p.parent / m.group(1)
            if target.is_file():
                queue.append(target)
    return out


def _write_forest(root: Path, rng: random.Random, n: int) -> list[Path]:
    paths = [root / f"h{i}.h" for i in range(n)]
    for p in paths:
        body: list[str] = []
        for _ in range(rng.randint(0, 4)):
            target = rng.choice([*paths, root / "missing.h"]).name
            kind = rng.randrange(3)
            if kind == 0:
                body.append(f'#include "{target}"')
            elif kind == 1:
                body.append(f'// #include "{target}"')
            else:
                body += ["#ifdef __cplusplus", f'#include "{target}"', "#endif"]
        p.write_text("\n".join(body) + "\n")
    return paths


@pytest.mark.parametrize("seed", range(12))
def test_memoized_expansion_matches_bruteforce_oracle(
    tmp_path: Path, seed: int
) -> None:
    rng = random.Random(seed)
    paths = _write_forest(tmp_path, rng, rng.randint(3, 12))
    qie._QUOTED_INCLUDES_MEMO.clear()
    for round_ in range(4):
        if round_ == 2:
            # Edit files between calls: same paths, new content.
            paths = _write_forest(tmp_path, rng, len(paths))
        for _ in range(5):
            group = rng.sample(paths, rng.randint(1, len(paths)))
            for flag in (False, True):
                got = qie._expand_with_quoted_includes(
                    group, for_language_mode_decision=flag
                )
                # The C-mode decision (flag=True) masks __cplusplus guards.
                want = _oracle(group, cxx_guard_reachable=not flag)
                assert got == want, (seed, round_, flag)


def test_identical_content_in_different_directories_resolves_per_directory(
    tmp_path: Path,
) -> None:
    """The memo is keyed on content only; resolution must stay relative to
    each including file's own directory."""
    for d, has_target in (("a", True), ("b", False)):
        (tmp_path / d).mkdir()
        (tmp_path / d / "top.h").write_text('#include "inc.h"\n')
        if has_target:
            (tmp_path / d / "inc.h").write_text("\n")
    qie._QUOTED_INCLUDES_MEMO.clear()
    a = qie._expand_with_quoted_includes([tmp_path / "a" / "top.h"])
    unresolved: list[Path] = []
    b = qie._expand_with_quoted_includes(
        [tmp_path / "b" / "top.h"], unresolved=unresolved
    )
    assert a == [tmp_path / "a" / "top.h", tmp_path / "a" / "inc.h"]
    assert b == [tmp_path / "b" / "top.h"]
    assert unresolved == [tmp_path / "b" / "inc.h"]
