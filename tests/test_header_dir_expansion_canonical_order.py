"""A `-H <dir>` expansion is already in ADR-082's canonical header order.

The header-order witness skips its second, sorted-order dump only when the
declared set is already canonical. A directory expansion sorted differently
(`Path` component order puts `foo/bar.h` before `foo.h`) made every tree with
a `foo.h` beside a `foo/` directory pay a full second dump per side.
"""

from __future__ import annotations

import random
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.extract.header_order_witness import canonical_header_order
from abicheck.header_utils import iter_directory_headers
from tests._abicheck_subprocess import abicheck_command, abicheck_env

_STEMS = ("foo", "foo-x", "foo_x", "foo.x", "a", "a0", "z")


def _random_tree(root: Path, rng: random.Random) -> None:
    """Pairs of `<stem>.h` beside `<stem>/`, nested, plus lone files."""

    def fill(d: Path, depth: int) -> None:
        d.mkdir(parents=True, exist_ok=True)
        for stem in rng.sample(_STEMS, rng.randint(1, len(_STEMS))):
            kind = rng.choice(("pair", "file", "dir"))
            if kind in ("pair", "file"):
                (d / f"{stem}.h").write_text("#pragma once\n")
            if kind in ("pair", "dir"):
                sub = d / stem
                if depth < 2:
                    fill(sub, depth + 1)
                else:
                    sub.mkdir(exist_ok=True)
                    (sub / "leaf.h").write_text("#pragma once\n")

    fill(root, 0)


@pytest.mark.parametrize("seed", range(40))
def test_directory_expansion_is_canonical(tmp_path: Path, seed: int) -> None:
    _random_tree(tmp_path / "inc", random.Random(seed))
    found = iter_directory_headers(tmp_path / "inc")
    # Oracle independent of either implementation: byte order of the
    # resolved path strings, the order ADR-082 records a proof for.
    as_text = [str(p.resolve()) for p in found]
    assert as_text == sorted(as_text, key=lambda s: s.encode())
    # And the witness's own short-circuit condition holds, so it never re-dumps.
    assert [p.resolve() for p in canonical_header_order(found)] == [
        p.resolve() for p in found
    ]


def test_sibling_file_sorts_before_its_directory(tmp_path: Path) -> None:
    inc = tmp_path / "lib"
    (inc / "saveload").mkdir(parents=True)
    for rel in ("saveload.h", "saveload/bootstrap.h", "saveload/save.h"):
        (inc / rel).write_text("#pragma once\n")
    assert [p.relative_to(inc).as_posix() for p in iter_directory_headers(inc)] == [
        "saveload.h",
        "saveload/bootstrap.h",
        "saveload/save.h",
    ]


@pytest.mark.integration
def test_dir_compare_parses_each_side_once(tmp_path: Path) -> None:
    """End to end: one clang AST cache entry per side, not two."""
    if not (shutil.which("g++") and shutil.which("clang++")):
        pytest.skip("g++ and clang++ are required")
    for side in ("old", "new"):
        lib = tmp_path / side / "include" / "svs" / "lib"
        for stem in ("saveload", "threads"):
            (lib / stem).mkdir(parents=True)
            (lib / f"{stem}.h").write_text(
                f"#pragma once\nnamespace svs{{struct {stem.title()}{{int a;}};"
                f" int {stem}({stem.title()}*);}}\n"
            )
            (lib / stem / "inner.h").write_text(
                f'#pragma once\n#include "svs/lib/{stem}.h"\n'
                f"namespace svs{{struct {stem.title()}In{{{stem.title()} s;}};}}\n"
            )
        src = tmp_path / side / "a.cpp"
        src.write_text(
            '#include "svs/lib/saveload.h"\n#include "svs/lib/threads.h"\n'
            "int svs::saveload(Saveload*s){return s->a;}\n"
            "int svs::threads(Threads*t){return t->a;}\n"
        )
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                f"-I{tmp_path / side / 'include'}",
                "-o",
                str(tmp_path / side / "libsvs.so"),
                str(src),
            ],
            check=True,
        )
    cache = tmp_path / "cache"
    # The AST cache root is LOCALAPPDATA on Windows, XDG_CACHE_HOME elsewhere.
    env = abicheck_env(
        {
            "XDG_CACHE_HOME": str(cache),
            "LOCALAPPDATA": str(cache),
            "ABICHECK_AST_FRONTEND": "clang",
        }
    )
    args: list[str] = [str(tmp_path / "old/libsvs.so"), str(tmp_path / "new/libsvs.so")]
    for side in ("old", "new"):
        args += [
            "-H",
            f"{side}={tmp_path / side / 'include/svs/lib'}",
            "-I",
            f"{side}={tmp_path / side / 'include'}",
        ]
    proc = subprocess.run(
        abicheck_command("compare", *args, "--depth", "headers"),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    entries = [
        p
        for p in cache.rglob("clang/*.json")
        if not p.name.endswith(".projection.json")
    ]
    assert len(entries) == 2, sorted(p.name for p in entries)
