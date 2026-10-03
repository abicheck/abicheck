# SPDX-License-Identifier: Apache-2.0
"""``compare --dry-run`` prices the compile DB the run reads.

Bug class: ``config.propagation_completeness`` -- a preview that re-derives
an input the run resolves, and so describes a different run. The cost
preview's L3 TU count came from its own compile-DB discovery, which never saw
``.abicheck.yml``'s ``build.compile_db`` (in the source tree or named by
``--config``) and, given a ``--build-info`` directory holding no
``compile_commands.json``, fell back to the source tree where the run collects
nothing. It now asks ``buildsource.inline.plan_compile_db``, the order the
run's own L3 collection follows.

Oracle: the run itself -- ``buildsource.embed.embed_build_source``, the
collector ``compare``'s inline dump calls, over the same inputs; the preview
must count exactly the compile units it embeds. The one place the two cannot
agree by construction is abicheck's inferred build-system query (the preview
runs nothing), so there the preview must say it is counting source files in
lieu of an inferred DB, and the run -- in a tree with no build system --
collects nothing.
"""

from __future__ import annotations

import itertools
import json
import re
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.embed import embed_build_source
from abicheck.cli import main
from abicheck.model import AbiSnapshot

_HINT_TUS = 2  # build/compile_commands.json, a conventional location
_CONFIG_TUS = 3  # odd/place/compile_commands.json, named by build.compile_db
_BUILD_INFO_TUS = 1  # <build_info>/compile_commands.json
_SOURCE_FILES = 5

_DB_LAYOUTS = ("none", "hint", "config_path", "both")
_CONFIGS = ("none", "in_tree", "explicit", "explicit_stale")
_BUILD_INFOS = ("none", "with_db", "without_db")


def _db(path: Path, root: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "directory": str(root),
                    "file": f"src/u{i}.c",
                    "arguments": ["cc", "-c", f"src/u{i}.c"],
                }
                for i in range(count)
            ]
        ),
        encoding="utf-8",
    )


def _case(tmp_path: Path, layout: str, config: str, build_info: str):
    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    for i in range(_SOURCE_FILES):
        (tree / "src" / f"u{i}.c").write_text(f"int f{i}(void);\n", encoding="utf-8")
    if layout in ("hint", "both"):
        _db(tree / "build" / "compile_commands.json", tree, _HINT_TUS)
    if layout in ("config_path", "both"):
        _db(tree / "odd" / "place" / "compile_commands.json", tree, _CONFIG_TUS)
    named = "odd/place/compile_commands.json"
    explicit = None
    if config == "in_tree":
        (tree / ".abicheck.yml").write_text(
            f"build:\n  compile_db: {named}\n", encoding="utf-8"
        )
    elif config in ("explicit", "explicit_stale"):
        explicit = tmp_path / "operator.yml"
        target = named if config == "explicit" else "gone/compile_commands.json"
        explicit.write_text(f"build:\n  compile_db: {target}\n", encoding="utf-8")
    bi = None
    if build_info != "none":
        bi = tmp_path / "bi"
        bi.mkdir()
        if build_info == "with_db":
            _db(bi / "compile_commands.json", tree, _BUILD_INFO_TUS)
    return tree, explicit, bi


def _collected(tree: Path, explicit: Path | None, bi: Path | None) -> int:
    snap = AbiSnapshot(library="libx.so", version="1")
    embed_build_source(snap, bi, tree, build_config=explicit, collect_mode="build")
    be = snap.build_source.build_evidence if snap.build_source else None
    return len(be.compile_units) if be is not None else 0


def _preview(
    tmp_path: Path, tree: Path, explicit: Path | None, bi: Path | None
) -> tuple[int, str]:
    stub = tmp_path / "libx.so"
    data = bytearray(64)
    data[0:4] = b"\x7fELF"
    data[4], data[5] = 2, 1
    struct.pack_into("<H", data, 16, 3)
    stub.write_bytes(bytes(data))
    args = ["compare", str(stub), str(stub), "--depth", "build", "--dry-run"]
    args += ["--sources", f"old={tree}", "--sources", f"new={tree}"]
    if bi is not None:
        args += ["--build-info", f"old={bi}", "--build-info", f"new={bi}"]
    if explicit is not None:
        args += ["--config", str(explicit)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    match = re.search(r"L3_build: (\d+) TU\(s\), [^-]*-- (.*)", result.output)
    assert match, result.output
    # Both operands are the same side, so the pair total is twice one side.
    return int(match.group(1)) // 2, match.group(2)


@pytest.mark.parametrize(
    ("layout", "config", "build_info"),
    list(itertools.product(_DB_LAYOUTS, _CONFIGS, _BUILD_INFOS)),
)
def test_the_preview_counts_what_the_run_collects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    layout: str,
    config: str,
    build_info: str,
) -> None:
    monkeypatch.chdir(tmp_path)  # no project config discovered above the case
    tree, explicit, bi = _case(tmp_path, layout, config, build_info)
    priced, note = _preview(tmp_path, tree, explicit, bi)
    collected = _collected(tree, explicit, bi)
    if "infers one from the build system" in note:
        assert priced == _SOURCE_FILES
        assert collected == 0
    else:
        assert priced == collected, note


def test_the_domain_reaches_every_source_of_a_compile_db(tmp_path: Path) -> None:
    """Vacuity guard: the cases above exercise each DB the run can read, an
    explicit miss, and the inferred fallback."""
    seen = set()
    for n, (layout, config, build_info) in enumerate(
        itertools.product(_DB_LAYOUTS, _CONFIGS, _BUILD_INFOS)
    ):
        root = tmp_path / str(n)
        root.mkdir()
        seen.add(_collected(*_case(root, layout, config, build_info)))
    assert seen == {0, _BUILD_INFO_TUS, _HINT_TUS, _CONFIG_TUS}


_DEPTHS = ("binary", "headers", "build", "source")
_SEEDS = ((), ("src/u0.c",), ("include/api.h",), ("src/u0.c", "src/u1.c"))


@pytest.mark.parametrize(("depth", "seed"), list(itertools.product(_DEPTHS, _SEEDS)))
def test_the_preview_states_the_run_s_changed_path_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, depth: str, seed: tuple[str, ...]
) -> None:
    """ADR-043 D7, written out: ``--depth source`` replays the changed
    translation units only when a seed was given, else the whole target;
    shallower depths replay nothing. The preview charges every unit for a
    changed header, since it builds no include graph to say which units
    include it. The dry run used to state "target" whatever the seed, and
    priced the whole target."""
    monkeypatch.chdir(tmp_path)
    tree, _explicit, _bi = _case(tmp_path, "hint", "none", "none")
    stub = tmp_path / "libx.so"
    stub.write_bytes(b"\x7fELF" + bytes(60))
    args = ["compare", str(stub), str(stub), "--depth", depth, "--dry-run"]
    args += ["--sources", f"old={tree}", "--sources", f"new={tree}"]
    for path in seed:
        args += ["--changed-path", path]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    mode = re.search(r"effective collect mode: (\S+)", result.output)
    assert mode, result.output

    expected_mode = {
        "binary": "off",
        "headers": "off",
        "build": "build",
        "source": "source-changed" if seed else "source-target",
    }[depth]
    assert mode.group(1) == expected_mode
    scope = re.search(r"source scope: (\w+) on each side", result.output)
    if depth == "source":
        assert scope and scope.group(1) == ("changed" if seed else "target")
        replay = re.search(r"L4_source_abi: (\d+) TU\(s\)", result.output)
        assert replay, result.output
        sources = [p for p in seed if p.endswith(".c")]
        per_side = len(sources) if seed and len(sources) == len(seed) else _HINT_TUS
        assert int(replay.group(1)) == 2 * per_side
    else:
        assert scope is None
