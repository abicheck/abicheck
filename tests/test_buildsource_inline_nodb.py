# Copyright 2026 Nikolay Petrov
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

"""P09: a --sources/--build-info tree with no compile DB must warn (not fail
silently) with an actionable hint, while staying graceful (ADR-028 D3 — nothing
embedded, no error)."""

from __future__ import annotations

import json

from abicheck.cli_buildsource import embed_build_source
from abicheck.model import AbiSnapshot


def test_sources_without_compile_db_warns(tmp_path, capsys):
    # A source checkout with no compile_commands.json anywhere (autotools-style).
    (tmp_path / "foo.c").write_text("int foo(void){return 0;}\n", encoding="utf-8")
    snap = AbiSnapshot(library="l", version="1")

    embed_build_source(snap, None, tmp_path, collect_mode="source-target")

    # Graceful: nothing embedded, no exception (ADR-028 D3).
    assert snap.build_source is None
    # But NOT silent: an actionable warning names the build systems + escape hatch.
    err = capsys.readouterr().err
    assert "no compile_commands.json found" in err
    assert "bear -- make" in err
    assert "--build-info" in err
    assert "L3/L4/L5 not collected" in err


def test_compile_db_present_does_not_warn(tmp_path, capsys):
    # Sanity: when a compile DB IS discovered, no P09 warning fires and L3 lands.
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path), "file": str(src),
                     "command": f"cc -c {src}"}]),
        encoding="utf-8",
    )
    snap = AbiSnapshot(library="l", version="1")

    embed_build_source(snap, None, tmp_path, collect_mode="build")

    err = capsys.readouterr().err
    assert "no compile_commands.json found" not in err
    assert snap.build_source is not None
    assert snap.build_source.build_evidence.compile_units  # L3 collected


def test_graph_build_collect_mode_skips_l4(tmp_path):
    # P18: graph-build collects L3 + the L5 graph from build facts alone, with NO
    # L4 source replay — so the structural graph + build options are available even
    # where full L4 would be prohibitive (monorepos).
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path), "file": str(src),
                     "command": f"cc -c {src}"}]),
        encoding="utf-8",
    )
    snap = AbiSnapshot(library="l", version="1")

    embed_build_source(snap, None, tmp_path, collect_mode="graph-build")

    assert snap.build_source is not None
    bs = snap.build_source
    assert bs.build_evidence.compile_units          # L3 present
    assert bs.source_graph is not None              # L5 graph built
    assert bs.source_graph.nodes                     # ...with nodes folded from L3
    assert bs.source_abi is None                     # L4 skipped (no source replay)


def test_collection_for_ci_mode_graph_build():
    from abicheck.buildsource.source_replay import collection_for_ci_mode

    scope, layers = collection_for_ci_mode("graph-build")
    assert scope == "off"          # no replay
    assert layers == ("L3", "L5")  # build facts + graph, no L4


def test_meson_builddir_is_autodiscovered(tmp_path):
    # P12: a compile DB under the Meson-convention `builddir/` must be found
    # without an explicit --build-info (previously only `build`/`_build` matched).
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
    bd = tmp_path / "builddir"
    bd.mkdir()
    (bd / "compile_commands.json").write_text(
        json.dumps([{"directory": str(bd), "file": str(src),
                     "command": f"cc -c {src}"}]),
        encoding="utf-8",
    )
    snap = AbiSnapshot(library="l", version="1")

    embed_build_source(snap, None, tmp_path, collect_mode="build")

    assert snap.build_source is not None
    assert snap.build_source.build_evidence.compile_units  # discovered under builddir/
