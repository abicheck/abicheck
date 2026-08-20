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

"""``buildsource.l2_seed``'s Flow-2 ``abicheck_inputs/`` pack recognition
(ADR-035 D5, CLI cleanup phase two PR F prerequisite 3).

Split out of ``tests/test_header_compile_context.py`` (already near its
2000-line hard cap; root ``AGENTS.md`` "Files that are large") rather than
added inline there.

``_l2_seed_pack_inputs`` -- the pack-precedence resolver
``seed_includes_and_fold_compile_context`` (and its older siblings
``derive_l2_include_dirs``/``derive_l2_compile_context``) use -- previously
recognized only a classic ``BuildSourcePack`` (``is_pack_dir``), never a
Flow-2 ``abicheck_inputs/`` pack, even though ``cli_buildsource.
embed_build_source`` already recognized both shapes uniformly. A Flow-2 pack
given as ``--sources``/``--build-info`` alongside ``-H`` headers was
therefore silently treated by the L2 seed as a literal, un-normalized source
tree: its own compile-unit include dirs never reached L2 seeding, and a
trusted, explicit ``build.query``/``--config`` could genuinely be
re-executed against the pack directory itself, even though the pack already
carries its own resolved L3 evidence to fold in directly instead. See
``abicheck/buildsource/l2_seed.py``'s own docstrings on
``_l2_seed_pack_inputs``/``_l2_seed_pack_build_evidence`` for the full
reasoning, and ``abicheck/cli_dump_dry_run_build_query.py``'s module
docstring for how this closes that module's own former "known, deliberately
unclosed gap."
"""

from __future__ import annotations

import json
from pathlib import Path

from abicheck.buildsource.inputs_pack import ABICHECK_INPUTS_VERSION, INPUTS_KIND


def _write_flow2_inputs_pack(
    root: Path, *, with_compile_db: bool, include_dir: str = "include"
) -> Path:
    """A minimal, valid Flow-2 ``abicheck_inputs/`` pack directory (ADR-035 D5).

    Mirrors ``tests/test_inputs_pack.py``'s own ``_write_inputs_pack``
    fixture, trimmed to just what ``_l2_seed_pack_build_evidence`` reads: a
    ``kind: abicheck_inputs`` manifest, an empty ``source_facts/`` (this
    seed only cares about L3, never L4/L5), and -- when *with_compile_db* --
    a ``build/compile_commands.json`` naming a real ``-I<include_dir>``.
    """
    pack = root / "abicheck_inputs"
    (pack / "source_facts").mkdir(parents=True)
    if with_compile_db:
        (root / include_dir).mkdir(parents=True, exist_ok=True)
        (pack / "build").mkdir(parents=True)
        (pack / "build" / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str(root),
                        "file": "src/foo.cpp",
                        "arguments": [
                            "c++",
                            "-std=c++17",
                            f"-I{include_dir}",
                            "-c",
                            "src/foo.cpp",
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
    manifest = {
        "kind": INPUTS_KIND,
        "abicheck_inputs_version": ABICHECK_INPUTS_VERSION,
        "library": "libfoo.so",
        "version": "1.0",
        "created_by": "abicheck-clang-plugin 0.1",
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack


def test_l2_seed_pack_inputs_recognizes_flow2_build_info_pack(tmp_path: Path) -> None:
    """A Flow-2 ``abicheck_inputs/`` pack given as ``--build-info`` must be
    recognized and folded the identical way a classic ``BuildSourcePack`` is
    -- ``embed_build_source`` already does this (``bi_is_inputs``); this L2
    seed path previously checked only ``is_pack_dir`` and silently treated
    the pack directory as a literal, un-normalized source tree instead."""
    from abicheck.buildsource.l2_seed import _l2_seed_pack_inputs

    pack = _write_flow2_inputs_pack(tmp_path, with_compile_db=True)
    base_build, raw_build_info, raw_sources = _l2_seed_pack_inputs(pack, None)
    assert raw_build_info is None
    assert raw_sources is None
    assert base_build is not None
    assert base_build.compile_units


def test_l2_seed_pack_inputs_recognizes_flow2_sources_pack(tmp_path: Path) -> None:
    """Same recognition via ``--sources``, only when no ``--build-info`` was
    also given (an explicit ``--build-info`` always wins L3, matching this
    function's pre-existing classic-pack precedence rule)."""
    from abicheck.buildsource.l2_seed import _l2_seed_pack_inputs

    pack = _write_flow2_inputs_pack(tmp_path, with_compile_db=True)
    base_build, raw_build_info, raw_sources = _l2_seed_pack_inputs(None, pack)
    assert raw_sources is None
    assert raw_build_info is None
    assert base_build is not None
    assert base_build.compile_units


def test_l2_seed_pack_inputs_sources_pack_yields_to_explicit_build_info(
    tmp_path: Path,
) -> None:
    """A raw, non-pack ``--build-info`` still wins L3 over a Flow-2
    ``--sources`` pack, mirroring the classic-``BuildSourcePack`` rule this
    function's own docstring already states."""
    from abicheck.buildsource.l2_seed import _l2_seed_pack_inputs

    src_pack = _write_flow2_inputs_pack(tmp_path, with_compile_db=True)
    build_info = tmp_path / "build"
    build_info.mkdir()
    base_build, raw_build_info, raw_sources = _l2_seed_pack_inputs(build_info, src_pack)
    assert raw_build_info == build_info
    assert raw_sources is None
    assert base_build is None


def test_l2_seed_pack_inputs_flow2_pack_without_compile_db_yields_no_evidence(
    tmp_path: Path,
) -> None:
    """A Flow-2 pack with no ``build/compile_commands.json`` still normalizes
    away (matching ``embed_build_source``'s unconditional nulling), just with
    no L3 evidence to fold -- the lighter ``_load_build_evidence`` load this
    seed uses degrades to ``None`` the same way the full ``ingest_inputs_pack``
    does for a compile-DB-less pack (see ``test_ingest_without_compile_db_
    skips_l3`` in ``tests/test_inputs_pack.py``)."""
    from abicheck.buildsource.l2_seed import _l2_seed_pack_inputs

    pack = _write_flow2_inputs_pack(tmp_path, with_compile_db=False)
    base_build, raw_build_info, raw_sources = _l2_seed_pack_inputs(pack, None)
    assert raw_build_info is None
    assert base_build is None


def test_derive_l2_include_dirs_seeds_from_flow2_build_info_pack(
    tmp_path: Path,
) -> None:
    """End-to-end: ``derive_l2_include_dirs`` (the real, ``collect_inline_
    pack``-backed entry point) picks up a Flow-2 pack's own compile-unit
    include dir when given as ``--build-info``, closing the gap where such a
    pack's L3 evidence never reached L2 seeding at all."""
    from abicheck.buildsource.l2_seed import derive_l2_include_dirs

    pack = _write_flow2_inputs_pack(tmp_path, with_compile_db=True)
    include_dirs, cleanups = derive_l2_include_dirs(pack, None)
    assert cleanups == []
    assert any(Path(d).name == "include" for d in include_dirs)
