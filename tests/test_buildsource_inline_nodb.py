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
from pathlib import Path

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


def test_derive_l2_include_dirs_from_compile_db(tmp_path):
    # The L2 header parse needs the build's include dirs when the user gives no
    # -I (e.g. pvxs public headers include EPICS Base). derive_l2_include_dirs
    # must surface the -I/-isystem dirs from the discovered compile DB, filtering
    # out non-existent ones, so scan/dump --sources can parse dependency headers.
    from abicheck.buildsource.inline import derive_l2_include_dirs

    inc = tmp_path / "inc"
    inc.mkdir()
    sysinc = tmp_path / "sysinc"
    sysinc.mkdir()
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
    missing = tmp_path / "nope"  # a dir that does not exist
    # Use the `arguments` array form (not `command`) so paths are never shell-split
    # — a Windows `C:\...` path in a `command` string would be mangled by shlex.
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{
            "directory": str(tmp_path),
            "file": str(src),
            "arguments": ["cc", f"-I{inc}", "-isystem", str(sysinc),
                          f"-I{missing}", "-c", str(src)],
        }]),
        encoding="utf-8",
    )

    dirs, cleanups = derive_l2_include_dirs(build_info=None, sources=tmp_path)
    assert str(inc) in dirs
    assert str(sysinc) in dirs
    assert str(missing) not in dirs  # non-existent dirs filtered out
    for fn in cleanups:  # a plain compile DB has none, but drain defensively
        fn()


def test_derive_l2_include_dirs_no_inputs_is_empty():
    from abicheck.buildsource.inline import derive_l2_include_dirs

    # No sources and no build-info → nothing to derive, and never raises.
    assert derive_l2_include_dirs(build_info=None, sources=None) == ([], [])


def test_derive_l2_include_dirs_honors_config_compile_db(tmp_path):
    # When the compile DB is located only via a trusted --config `build.compile_db`
    # (not auto-discoverable at the tree root), the derivation must still find it —
    # otherwise the L2 fallback silently ignores the same config embed_build_source
    # uses later. Passing build_config=None (the old bug) would return no dirs here.
    import yaml

    from abicheck.buildsource.inline import derive_l2_include_dirs

    inc = tmp_path / "inc"
    inc.mkdir()
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
    dbdir = tmp_path / "out"
    dbdir.mkdir()
    (dbdir / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path), "file": str(src),
                     "arguments": ["cc", f"-I{inc}", "-c", str(src)]}]),
        encoding="utf-8",
    )
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        yaml.safe_dump({"build": {"compile_db": "out/compile_commands.json"}}),
        encoding="utf-8",
    )

    dirs, cleanups = derive_l2_include_dirs(
        build_info=None, sources=tmp_path, build_config=cfg
    )
    for fn in cleanups:
        fn()
    assert str(inc) in dirs


def test_derive_l2_include_dirs_expands_redacted_home_paths(tmp_path):
    # CompileDbAdapter redacts home-rooted paths (a CI runner's
    # /home/runner/work) to a literal ``~`` via DEFAULT_REDACTION. The fallback
    # must expand ~ back before the existence check, or every home-rooted include
    # dir — the exact CI case it targets — is dropped. Use a real dir under $HOME
    # so the adapter redacts it and the helper has to un-redact it.
    import os
    import tempfile

    from abicheck.buildsource.inline import derive_l2_include_dirs

    home = Path(os.path.expanduser("~"))
    with tempfile.TemporaryDirectory(dir=home) as home_dir:
        inc = Path(home_dir) / "inc"
        inc.mkdir()
        src = tmp_path / "foo.c"
        src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
        # `arguments` array (not `command`) so a Windows `C:\...` home path is not
        # shell-split; the absolute home path is what the adapter redacts to ~/...
        (tmp_path / "compile_commands.json").write_text(
            json.dumps([{
                "directory": str(tmp_path),
                "file": str(src),
                "arguments": ["cc", f"-I{inc}", "-c", str(src)],
            }]),
            encoding="utf-8",
        )
        dirs, _ = derive_l2_include_dirs(build_info=None, sources=tmp_path)
        # The returned path must be the real, expanded dir (usable by castxml/clang),
        # not the ~-prefixed redacted form.
        assert str(inc) in dirs
        assert not any(d.startswith("~") for d in dirs)


def test_derive_l2_include_dirs_empty_when_no_build(tmp_path):
    from abicheck.buildsource.inline import derive_l2_include_dirs

    # A source tree with no compile DB and no build-system marker resolves no
    # compile units → the fallback yields nothing and drains cleanly (never raises).
    (tmp_path / "notes.txt").write_text("hi\n", encoding="utf-8")
    dirs, cleanups = derive_l2_include_dirs(build_info=None, sources=tmp_path)
    assert dirs == []
    assert cleanups == []


def test_derive_l2_include_dirs_from_pack(tmp_path):
    # A collected BuildSourcePack passed as --build-info supplies compile units
    # directly (via base_build), so its include dirs must be surfaced too — the
    # non-compile-DB build-info form the earlier hand-rolled resolver missed.
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import derive_l2_include_dirs
    from abicheck.buildsource.pack import BuildSourcePack

    inc = tmp_path / "pkginc"
    inc.mkdir()
    pack_dir = tmp_path / "pack"
    pack = BuildSourcePack.empty(pack_dir)
    pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://foo", include_paths=[str(inc)])]
    )
    pack.write()

    dirs, cleanups = derive_l2_include_dirs(build_info=pack_dir, sources=None)
    for fn in cleanups:
        fn()
    assert str(inc) in dirs


def test_derive_l2_include_dirs_from_sources_pack(tmp_path):
    # A collected BuildSourcePack passed as --sources also carries its own L3
    # build_evidence, which embed_build_source/_combine_packs use for L3 when no
    # --build-info pack supplies one. The L2 seeding must mirror that: surface the
    # source pack's compile-unit include dirs so `scan/dump -H include --sources
    # path/to/pack` with no -I can parse dependency headers the pack already knows
    # (Codex review). Previously the sources pack was dropped to None and its
    # include dirs were silently ignored.
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import derive_l2_include_dirs
    from abicheck.buildsource.pack import BuildSourcePack

    inc = tmp_path / "srcpkginc"
    inc.mkdir()
    pack_dir = tmp_path / "srcpack"
    pack = BuildSourcePack.empty(pack_dir)
    pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://foo", include_paths=[str(inc)])]
    )
    pack.write()

    dirs, cleanups = derive_l2_include_dirs(build_info=None, sources=pack_dir)
    for fn in cleanups:
        fn()
    assert str(inc) in dirs


def test_derive_l2_include_dirs_build_info_pack_wins_over_sources_pack(tmp_path):
    # When BOTH --build-info and --sources are packs, --build-info wins L3 (mirrors
    # _combine_packs, whose L3 supplier order is bi_pack, src_pack, embedded). The
    # seeding must therefore use the build-info pack's include dirs and NOT fold in
    # the sources pack's — the source pack only backfills when build-info supplies
    # no L3.
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import derive_l2_include_dirs
    from abicheck.buildsource.pack import BuildSourcePack

    bi_inc = tmp_path / "biinc"
    bi_inc.mkdir()
    src_inc = tmp_path / "srcinc"
    src_inc.mkdir()

    bi_dir = tmp_path / "bipack"
    bi_pack = BuildSourcePack.empty(bi_dir)
    bi_pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://bi", include_paths=[str(bi_inc)])]
    )
    bi_pack.write()

    src_dir = tmp_path / "srcpack"
    src_pack = BuildSourcePack.empty(src_dir)
    src_pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://src", include_paths=[str(src_inc)])]
    )
    src_pack.write()

    dirs, cleanups = derive_l2_include_dirs(build_info=bi_dir, sources=src_dir)
    for fn in cleanups:
        fn()
    assert str(bi_inc) in dirs  # build-info pack wins L3
    assert str(src_inc) not in dirs  # source pack does not override it


def test_derive_l2_include_dirs_raw_build_info_wins_over_sources_pack(tmp_path):
    # A raw --build-info (compile DB / build dir), NOT a pack, combined with a
    # --sources pack: the explicit raw build-info must win L3, so its include dirs
    # are what seed L2 — not the reused source pack's. Folding the source pack into
    # base_build would make collect_inline_pack skip the raw build DB and parse -H
    # headers against stale source-pack dirs (Codex review). Guard is
    # `build_info is None`, not `base_build is None`.
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import derive_l2_include_dirs
    from abicheck.buildsource.pack import BuildSourcePack

    # Raw build-info: a build dir holding a fresh compile_commands.json with bi_inc.
    bi_inc = tmp_path / "freshinc"
    bi_inc.mkdir()
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
    bidir = tmp_path / "bd"
    bidir.mkdir()
    (bidir / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path), "file": str(src),
                     "arguments": ["cc", f"-I{bi_inc}", "-c", str(src)]}]),
        encoding="utf-8",
    )

    # Sources pack carrying a *different* (stale) include dir in its L3 evidence.
    stale_inc = tmp_path / "staleinc"
    stale_inc.mkdir()
    src_dir = tmp_path / "srcpack"
    src_pack = BuildSourcePack.empty(src_dir)
    src_pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://stale", include_paths=[str(stale_inc)])]
    )
    src_pack.write()

    dirs, cleanups = derive_l2_include_dirs(build_info=bidir, sources=src_dir)
    for fn in cleanups:
        fn()
    assert str(bi_inc) in dirs        # raw --build-info wins L3
    assert str(stale_inc) not in dirs  # reused source pack does not override it


def test_derive_l2_include_dirs_skips_inferred_query_for_l2_only(tmp_path, monkeypatch):
    # An L2-only caller (allow_inferred_build_query=False) with a build-system tree
    # (CMakeLists.txt) but no existing compile DB must NOT run the inferred query —
    # the L2-only depth contract forbids spinning up a build just to hint includes.
    from abicheck.buildsource import build_query as bq
    from abicheck.buildsource.inline import derive_l2_include_dirs

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (tmp_path / "foo.cpp").write_text("int foo(){return 0;}\n", encoding="utf-8")

    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("inferred query must not run for L2-only")

    monkeypatch.setattr(bq, "run_inferred_build_query", boom)

    dirs, cleanups = derive_l2_include_dirs(
        build_info=None, sources=tmp_path, allow_inferred_build_query=False
    )
    for fn in cleanups:
        fn()
    assert dirs == []
    assert called["n"] == 0  # never invoked


def test_derive_l2_include_dirs_runs_inferred_query_when_allowed(tmp_path, monkeypatch):
    # The complement: with the default (allow_inferred_build_query=True) the same
    # no-DB tree DOES reach the inferred query (stubbed here so no real cmake runs).
    from abicheck.buildsource import build_query as bq
    from abicheck.buildsource.inline import derive_l2_include_dirs

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (tmp_path / "foo.cpp").write_text("int foo(){return 0;}\n", encoding="utf-8")

    called = {"n": 0}

    def stub(sources, merged, extractors, cleanup=None):
        called["n"] += 1
        return None  # pretend the query produced no DB

    monkeypatch.setattr(bq, "run_inferred_build_query", stub)

    derive_l2_include_dirs(
        build_info=None, sources=tmp_path, allow_inferred_build_query=True
    )
    assert called["n"] == 1  # inferred query reached


def test_derive_l2_include_dirs_passive_discovery_survives_l2_only(tmp_path):
    # Gating the inferred query must NOT disable passive discovery: an existing
    # compile_commands.json still seeds include dirs even for an L2-only caller.
    from abicheck.buildsource.inline import derive_l2_include_dirs

    inc = _compile_db_tree(tmp_path)
    dirs, cleanups = derive_l2_include_dirs(
        build_info=None, sources=tmp_path, allow_inferred_build_query=False
    )
    for fn in cleanups:
        fn()
    assert str(inc) in dirs  # discovered DB still used


def test_seed_l2_includes_threads_l2_only_gate(tmp_path, monkeypatch):
    # seed_l2_includes must pass allow_inferred_build_query through to the derivation
    # so an L2-only scan/dump never triggers a build via the seed.
    from abicheck.buildsource import build_query as bq
    from abicheck.buildsource.inline import seed_l2_includes

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (tmp_path / "foo.cpp").write_text("int foo(){return 0;}\n", encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("inferred query must not run for L2-only seed")

    monkeypatch.setattr(bq, "run_inferred_build_query", boom)

    incs, pending = seed_l2_includes(
        headers=[tmp_path / "h.h"], includes=[], sources=tmp_path,
        build_info=None, build_config=None, defer_cleanup=None,
        allow_inferred_build_query=False,
    )
    assert incs == []
    assert pending == []


def test_seed_l2_includes_noop_when_gcc_options_supply_includes(tmp_path):
    # Include dirs supplied via --gcc-options '-I ...' are as explicit as -I, so the
    # fallback must stay a no-op — seeding compile-DB dirs as extra_includes would
    # front-run the user's SDK in the dumper's search order (Codex review).
    from abicheck.buildsource.inline import seed_l2_includes

    _compile_db_tree(tmp_path)
    incs, pending = seed_l2_includes(
        headers=[tmp_path / "h.h"], includes=[], sources=tmp_path,
        build_info=None, build_config=None, defer_cleanup=None,
        gcc_options=f"-I {tmp_path / 'sdk'}",
    )
    assert incs == []       # no-op: user's -I via --gcc-options wins
    assert pending == []


def test_seed_l2_includes_noop_when_gcc_option_tokens_supply_includes(tmp_path):
    # Same for the repeatable --gcc-option token form (`-I`, `<dir>`).
    from abicheck.buildsource.inline import seed_l2_includes

    _compile_db_tree(tmp_path)
    incs, pending = seed_l2_includes(
        headers=[tmp_path / "h.h"], includes=[], sources=tmp_path,
        build_info=None, build_config=None, defer_cleanup=None,
        gcc_option_tokens=("-isystem", str(tmp_path / "sdk")),
    )
    assert incs == []
    assert pending == []


def test_seed_l2_includes_seeds_when_gcc_options_have_no_includes(tmp_path):
    # A --gcc-options string with NO include flags (e.g. just a -D) must not
    # suppress the fallback — the seed still fires so dependency headers resolve.
    from abicheck.buildsource.inline import seed_l2_includes

    inc = _compile_db_tree(tmp_path)
    incs, pending = seed_l2_includes(
        headers=[tmp_path / "h.h"], includes=[], sources=tmp_path,
        build_info=None, build_config=None, defer_cleanup=None,
        gcc_options="-DNDEBUG -O2",
    )
    assert str(inc) in [str(p) for p in incs]
    assert isinstance(pending, list)


def test_seed_l2_includes_from_sources_pack(tmp_path):
    # End-to-end through the shared wrapper: -H headers, no -I, --sources pointing
    # at a pack → the pack's include dirs are seeded into the effective includes.
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import seed_l2_includes
    from abicheck.buildsource.pack import BuildSourcePack

    inc = tmp_path / "seedpkginc"
    inc.mkdir()
    pack_dir = tmp_path / "seedpack"
    pack = BuildSourcePack.empty(pack_dir)
    pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://foo", include_paths=[str(inc)])]
    )
    pack.write()

    incs, pending = seed_l2_includes(
        headers=[tmp_path / "h.h"], includes=[], sources=pack_dir,
        build_info=None, build_config=None, defer_cleanup=None,
    )
    assert str(inc) in [str(p) for p in incs]
    assert isinstance(pending, list)


def test_derive_l2_include_dirs_build_compile_db_override(tmp_path):
    # A --build-compile-db override points at a non-default DB location; the L2
    # seeding must resolve *that* DB (mirroring embed_build_source), not only an
    # auto-discovered compile_commands.json at the tree root.
    from abicheck.buildsource.inline import derive_l2_include_dirs

    inc = tmp_path / "realinc"
    inc.mkdir()
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
    dbdir = tmp_path / "out"
    dbdir.mkdir()
    (dbdir / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path), "file": str(src),
                     "arguments": ["cc", f"-I{inc}", "-c", str(src)]}]),
        encoding="utf-8",
    )
    dirs, cleanups = derive_l2_include_dirs(
        build_info=None, sources=tmp_path,
        build_compile_db="out/compile_commands.json",
    )
    for fn in cleanups:
        fn()
    assert str(inc) in dirs


def test_derive_l2_include_dirs_explicit_missing_db_no_stale_fallback(tmp_path):
    # An explicit --build-compile-db that matches nothing must NOT silently seed
    # from an unrelated auto-discovered compile_commands.json at the tree root
    # (compile_db_explicit stops the fallback — the header parse would otherwise
    # use the wrong include context while L3 correctly reports the DB missing).
    from abicheck.buildsource.inline import derive_l2_include_dirs

    wrong_inc = tmp_path / "wronginc"
    wrong_inc.mkdir()
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
    # An unrelated DB the fallback must NOT use.
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path), "file": str(src),
                     "arguments": ["cc", f"-I{wrong_inc}", "-c", str(src)]}]),
        encoding="utf-8",
    )
    dirs, cleanups = derive_l2_include_dirs(
        build_info=None, sources=tmp_path,
        build_compile_db="does/not/exist.json",  # explicit + missing
    )
    for fn in cleanups:
        fn()
    assert str(wrong_inc) not in dirs


def _compile_db_tree(tmp_path):
    """A source tree with a compile DB whose one TU has a real -I dir."""
    inc = tmp_path / "inc"
    inc.mkdir()
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 0;}\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path), "file": str(src),
                     "arguments": ["cc", f"-I{inc}", "-c", str(src)]}]),
        encoding="utf-8",
    )
    return inc


def test_seed_l2_includes_noop_when_includes_given(tmp_path):
    from abicheck.buildsource.inline import seed_l2_includes

    _compile_db_tree(tmp_path)
    existing = [tmp_path / "myinc"]
    # User already passed -I → the fallback is a strict no-op (explicit -I wins).
    incs, pending = seed_l2_includes(
        headers=[tmp_path / "h.h"], includes=existing, sources=tmp_path,
        build_info=None, build_config=None, defer_cleanup=None,
    )
    assert incs == existing
    assert pending == []


def test_seed_l2_includes_noop_when_no_headers(tmp_path):
    from abicheck.buildsource.inline import seed_l2_includes

    _compile_db_tree(tmp_path)
    # No -H headers → nothing to scope, so no seeding.
    incs, pending = seed_l2_includes(
        headers=[], includes=[], sources=tmp_path,
        build_info=None, build_config=None, defer_cleanup=None,
    )
    assert incs == []
    assert pending == []


def test_seed_l2_includes_seeds_and_defers_cleanup(tmp_path):
    from abicheck.buildsource.inline import seed_l2_includes

    inc = _compile_db_tree(tmp_path)
    defer: list = []
    incs, pending = seed_l2_includes(
        headers=[tmp_path / "h.h"], includes=[], sources=tmp_path,
        build_info=None, build_config=None, defer_cleanup=defer,
    )
    from pathlib import Path as _P
    assert _P(str(inc)) in incs
    # With a defer_cleanup channel, cleanups go there (none for a plain compile DB)
    # and none come back as pending.
    assert pending == []


def test_seed_l2_includes_returns_pending_without_defer(tmp_path):
    from abicheck.buildsource.inline import seed_l2_includes

    inc = _compile_db_tree(tmp_path)
    incs, pending = seed_l2_includes(
        headers=[tmp_path / "h.h"], includes=[], sources=tmp_path,
        build_info=None, build_config=None, defer_cleanup=None,
    )
    assert str(inc) in [str(p) for p in incs]
    # A plain compile DB spawns no temp build dir, so pending is empty; the call
    # must still return the (list, list) shape and never raise.
    assert isinstance(pending, list)


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


# ── P4: compile-DB auto-discovery also finds non-standard subdir build trees ──


def test_autodiscover_compile_db_hint_dir(tmp_path):
    from abicheck.buildsource.inline import _autodiscover_compile_db

    (tmp_path / "build").mkdir()
    db = tmp_path / "build" / "compile_commands.json"
    db.write_text("[]")
    assert _autodiscover_compile_db(tmp_path) == db


def test_autodiscover_compile_db_nonstandard_subdir(tmp_path):
    # A non-hint, IDE/preset-style build dir is still found via the depth-1
    # fallback rather than yielding no L3 evidence (P4).
    from abicheck.buildsource.inline import _autodiscover_compile_db

    bd = tmp_path / "cmake-build-debug-gcc"
    bd.mkdir()
    db = bd / "compile_commands.json"
    db.write_text("[]")
    assert _autodiscover_compile_db(tmp_path) == db


def test_autodiscover_compile_db_prefers_hint_over_subdir(tmp_path):
    from abicheck.buildsource.inline import _autodiscover_compile_db

    (tmp_path / "build").mkdir()
    hint_db = tmp_path / "build" / "compile_commands.json"
    hint_db.write_text("[]")
    other = tmp_path / "zzz-build"
    other.mkdir()
    (other / "compile_commands.json").write_text("[]")
    assert _autodiscover_compile_db(tmp_path) == hint_db


def test_autodiscover_compile_db_none_when_absent(tmp_path):
    from abicheck.buildsource.inline import _autodiscover_compile_db

    (tmp_path / "src.c").write_text("int x;")
    assert _autodiscover_compile_db(tmp_path) is None


def test_compile_db_at_dir_uses_subdir_fallback(tmp_path):
    # --build-info <dir> now honours the same depth-1 subdir fallback as
    # --sources auto-discovery (Codex review).
    from abicheck.buildsource.inline import _compile_db_at

    bd = tmp_path / "cmake-build-debug-gcc"
    bd.mkdir()
    db = bd / "compile_commands.json"
    db.write_text("[]")
    assert _compile_db_at(tmp_path) == db
