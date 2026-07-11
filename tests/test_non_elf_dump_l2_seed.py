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
