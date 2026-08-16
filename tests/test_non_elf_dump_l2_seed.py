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

"""The PE/Mach-O dump path must seed L2 include dirs from the build too, in parity
with the ELF path — otherwise `dump foo.dll -H include --sources .` with no -I can't
resolve dependency headers and falls back to export-table mode (Codex review)."""

from __future__ import annotations

from abicheck.cli_dump_helpers import handle_non_elf_dump
from abicheck.model import AbiSnapshot


def test_non_elf_dump_seeds_includes_and_runs_cleanup(monkeypatch, tmp_path):
    captured: dict = {}
    events: list[str] = []
    seeded_dir = tmp_path / "buildinc"

    def fake_seed(**kwargs):
        events.append("seed")
        captured["seed_kwargs"] = kwargs
        # Return a build-derived include dir + a pending temp-dir cleanup.
        return [seeded_dir], [lambda: events.append("cleanup")]

    def fake_dump_native(so_path, binary_fmt, headers, includes, version, lang, **kw):
        events.append("dump")
        captured["includes"] = includes
        return AbiSnapshot(library="l", version=version)

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)

    handle_non_elf_dump(
        so_path=tmp_path / "foo.dll",
        binary_fmt="pe",
        headers=(tmp_path / "h.h",),
        includes=(),
        version="1",
        lang="c++",
        pdb_path=None,
        follow_deps=False,
        git_tag=None,
        build_id=None,
        no_git=True,
        output=None,
        dump_native_binary=fake_dump_native,
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=lambda *a, **k: None,
        sources=tmp_path,
        collect_mode="build",
    )

    # The build-derived include dir reached the native dumper.
    assert seeded_dir in captured["includes"]
    # And the temp-build-dir cleanup ran after the dump consumed the dirs.
    assert events == ["seed", "dump", "cleanup"]


def test_non_elf_dump_gates_inferred_query_for_l2_only(monkeypatch, tmp_path):
    # --depth headers (collect_mode "off") must disable the inferred build query in
    # the PE/Mach-O seed too — the flag is threaded from collect_mode.
    captured: dict = {}

    def fake_seed(**kwargs):
        captured["allow"] = kwargs["allow_inferred_build_query"]
        return list(kwargs["includes"]), []

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)

    handle_non_elf_dump(
        so_path=tmp_path / "foo.dylib",
        binary_fmt="macho",
        headers=(tmp_path / "h.h",),
        includes=(),
        version="1",
        lang="c++",
        pdb_path=None,
        follow_deps=False,
        git_tag=None,
        build_id=None,
        no_git=True,
        output=None,
        dump_native_binary=lambda *a, **k: AbiSnapshot(library="l", version="1"),
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=lambda *a, **k: None,
        sources=tmp_path,
        collect_mode="off",  # --depth headers → no inferred build
    )
    assert captured["allow"] is False


def test_non_elf_dump_folds_l3_compile_context_into_header_parse(tmp_path):
    """P0.3 L3->L2 fold (AGENTS.md's former "The native ELF `abicheck dump`
    path never applies L3 build context..." known gap -- PE/Mach-O shared
    the identical gap and is closed here alongside it): a --sources dump
    whose compile database resolves a real -std=/-D for these headers must
    reach the native dumper's own `compile=` context, not just the L2
    include-dir fallback. Also stamps `parsed_with_build_context`."""
    import json

    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": [
                        "c++",
                        "-c",
                        str(src),
                        "-o",
                        "out.o",
                        "-std=c++20",
                        "-DFOO=1",
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_dump_native(so_path, binary_fmt, headers, includes, version, lang, **kw):
        captured["compile"] = kw["compile"]
        return AbiSnapshot(library="l", version=version, from_headers=True)

    written: dict = {}

    def fake_write(snap, *a, **k):  # noqa: ANN002, ANN003
        written["snap"] = snap

    handle_non_elf_dump(
        so_path=tmp_path / "foo.dll",
        binary_fmt="pe",
        headers=(hdr,),
        includes=(),
        version="1",
        lang="c++",
        pdb_path=None,
        follow_deps=False,
        git_tag=None,
        build_id=None,
        no_git=True,
        output=None,
        dump_native_binary=fake_dump_native,
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=fake_write,
        sources=tmp_path,
        collect_mode="source-target",
        compile_db_context_matched=False,
    )

    tokens = captured["compile"].gcc_option_tokens
    assert "-std=c++20" in tokens
    assert "-DFOO=1" in tokens
    assert written["snap"].parsed_with_build_context is True
