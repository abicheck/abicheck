"""A `-H <dir>` expansion is already in ADR-082's canonical header order.

The header-order witness skips its second, sorted-order dump only when the
declared set is already canonical. A directory expansion sorted differently
(`Path` component order puts `foo/bar.h` before `foo.h`) made every tree with
a `foo.h` beside a `foo/` directory pay a full second dump per side.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
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


@pytest.mark.integration
def test_skipped_witness_dump_matches_the_witnessed_reference(tmp_path: Path) -> None:
    """Harness H5 cell (family F5): the witness's "already canonical" skip
    equals running the witness.

    Optimized: ``-H <dir>``, now canonical by construction, so the witness
    records its proof without a second dump. Reference: each side dumped
    standalone (``abicheck dump``, which always takes the proof) with the same
    headers declared one by one in the old directory-walk order (``x/inner.h``
    before ``x.h``, not canonical), so each witness re-dumps in sorted order;
    the two stored snapshots are then compared.
    The canonical JSON reports must be equal, and the optimized run must
    write fewer clang AST cache entries than the reference.
    """
    from _family_f5_support import canonical_report, first_difference

    if sys.platform != "linux" or not (shutil.which("g++") and shutil.which("clang++")):
        pytest.skip("needs g++ emitting ELF and clang++")
    walk_order: dict[str, list[Path]] = {}
    for side, extra in (("old", ""), ("new", " int added;")):
        lib = tmp_path / side / "include" / "svs" / "lib"
        (lib / "saveload").mkdir(parents=True)
        (lib / "saveload.h").write_text(
            "#pragma once\nnamespace svs{struct Save{int a;"
            + extra
            + "}; int save(Save*);}\n"
        )
        (lib / "saveload" / "inner.h").write_text(
            '#pragma once\n#include "svs/lib/saveload.h"\n'
            "namespace svs{struct Boot{Save s;}; int boot(Boot*);}\n"
        )
        walk_order[side] = [lib / "saveload" / "inner.h", lib / "saveload.h"]
        src = tmp_path / side / "a.cpp"
        src.write_text(
            '#include "svs/lib/saveload/inner.h"\n'
            "int svs::save(Save*s){return s->a;}\nint svs::boot(Boot*b){return b->s.a;}\n"
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

    def _env(cache: Path) -> dict[str, str]:
        return abicheck_env(
            {"XDG_CACHE_HOME": str(cache), "ABICHECK_AST_FRONTEND": "clang"}
        )

    def _entries(cache: Path) -> int:
        return sum(
            not p.name.endswith(".projection.json") for p in cache.rglob("clang/*.json")
        )

    def _observed(report: Path) -> str:
        # What was observed, not how the inputs were spelled: the report also
        # records each input's provenance (a directory, listed files, a snapshot).
        doc = json.loads(canonical_report(report.read_text()))
        return json.dumps(
            {k: doc.get(k) for k in ("verdict", "changes")}, sort_keys=True, indent=1
        )

    def run_live(header_args: list[str]) -> tuple[int, str, int]:
        cache, report = tmp_path / "cache-optimized", tmp_path / "optimized.json"
        args = [str(tmp_path / "old/libsvs.so"), str(tmp_path / "new/libsvs.so")]
        for side in ("old", "new"):
            args += ["-I", f"{side}={tmp_path / side / 'include'}"]
        proc = subprocess.run(
            abicheck_command(
                "compare",
                *args,
                *header_args,
                "--depth",
                "headers",
                "-o",
                f"json={report}",
            ),
            env=_env(cache),
            capture_output=True,
            text=True,
        )
        assert report.exists(), proc.stderr
        return proc.returncode, _observed(report), _entries(cache)

    def run_witnessed() -> tuple[int, str, int]:
        cache, report = tmp_path / "cache-reference", tmp_path / "reference.json"
        snaps = []
        for side in ("old", "new"):
            snap = tmp_path / f"{side}.abi.json"
            headers = [a for h in walk_order[side] for a in ("-H", str(h))]
            proc = subprocess.run(
                abicheck_command(
                    "dump",
                    str(tmp_path / side / "libsvs.so"),
                    *headers,
                    "-I",
                    str(tmp_path / side / "include"),
                    "-o",
                    str(snap),
                ),
                env=_env(cache),
                capture_output=True,
                text=True,
            )
            assert snap.exists(), proc.stderr
            snaps.append(str(snap))
        proc = subprocess.run(
            abicheck_command("compare", *snaps, "-o", f"json={report}"),
            env=_env(cache),
            capture_output=True,
            text=True,
        )
        assert report.exists(), proc.stderr
        return proc.returncode, _observed(report), _entries(cache)

    dir_args: list[str] = []
    for side in ("old", "new"):
        dir_args += ["-H", f"{side}={tmp_path / side / 'include/svs/lib'}"]
    exit_opt, optimized, opt_entries = run_live(dir_args)
    exit_ref, reference, ref_entries = run_witnessed()
    assert exit_opt == exit_ref
    assert "added" in optimized
    assert optimized == reference, first_difference(optimized, reference)
    assert opt_entries < ref_entries, (opt_entries, ref_entries)


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)
@pytest.mark.parametrize("walk_reversed", [False, True])
def test_aliases_of_one_header_expand_in_a_fixed_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, walk_reversed: bool
) -> None:
    """Two spellings of one file share the resolved-path key; the spelled path
    breaks the tie, so the walk's enumeration order cannot pick the survivor."""
    inc = tmp_path / "inc"
    inc.mkdir()
    (tmp_path / "real.h").write_text("#pragma once\n")
    for name in ("b_alias.h", "a_alias.h"):
        (inc / name).symlink_to(tmp_path / "real.h")
    real_walk = os.walk

    def walk(top: Path):  # type: ignore[no-untyped-def]
        for dirpath, dirnames, filenames in real_walk(top):
            yield dirpath, dirnames, sorted(filenames, reverse=walk_reversed)

    monkeypatch.setattr(os, "walk", walk)
    assert [p.name for p in iter_directory_headers(inc)] == ["a_alias.h", "b_alias.h"]
