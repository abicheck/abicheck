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

"""Tests for ``abicheck inputs compact`` (ADR-038 C.9, recommendation P1
#21-22): the CLI wiring for ``buildsource.inputs_emit.compact_inputs_pack``.
The underlying merge/compress logic is unit-tested directly in
``test_inputs_emit.py``; this file only checks the command surface."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.buildsource import (
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
    append_source_facts,
    ingest_inputs_pack,
    init_inputs_pack,
)
from abicheck.buildsource.inputs_emit import facts_filename
from abicheck.cli import main


def _tu(name: str, *, mangled: str, source: str) -> SourceAbiTu:
    ent = SourceEntity(
        id=f"decl://{name}",
        kind="function",
        qualified_name=name,
        mangled_name=mangled,
        signature_hash="sig1",
        source_location=SourceLocation(
            path=f"include/{name}.h", line=3, origin="PUBLIC_HEADER"
        ),
        visibility="public_header",
    )
    return SourceAbiTu(
        tu_id=f"cu://{source}",
        target_id="target://libfoo",
        source=source,
        public_header_roots=[f"include/{name}.h"],
        functions=[ent],
    )


def _pack_with_two_tus(root: Path) -> Path:
    pack = root / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="test")
    append_source_facts(
        pack,
        [_tu("foo", mangled="_Z3foov", source="src/foo.cpp")],
        filename=facts_filename("src/foo.cpp"),
    )
    append_source_facts(
        pack,
        [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
        filename=facts_filename("src/bar.cpp"),
    )
    return pack


def test_compact_merges_and_leaves_one_file(tmp_path: Path) -> None:
    pack = _pack_with_two_tus(tmp_path)
    result = CliRunner().invoke(main, ["inputs", "compact", str(pack)])
    assert result.exit_code == 0, result.output
    remaining = sorted(p.name for p in (pack / "source_facts").glob("*.jsonl"))
    assert remaining == ["compacted.jsonl"]
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2


def test_compact_compress_flag(tmp_path: Path) -> None:
    pack = _pack_with_two_tus(tmp_path)
    result = CliRunner().invoke(main, ["inputs", "compact", str(pack), "--compress"])
    assert result.exit_code == 0, result.output
    assert (pack / "source_facts" / "compacted.jsonl.gz").is_file()
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2


def test_compact_keep_originals_flag(tmp_path: Path) -> None:
    pack = _pack_with_two_tus(tmp_path)
    result = CliRunner().invoke(
        main, ["inputs", "compact", str(pack), "--keep-originals"]
    )
    assert result.exit_code == 0, result.output
    remaining = list((pack / "source_facts").glob("*.jsonl"))
    assert len(remaining) == 3  # 2 originals + the merged file


def test_compact_output_filename_option(tmp_path: Path) -> None:
    pack = _pack_with_two_tus(tmp_path)
    result = CliRunner().invoke(
        main, ["inputs", "compact", str(pack), "--output-filename", "merged.jsonl"]
    )
    assert result.exit_code == 0, result.output
    assert (pack / "source_facts" / "merged.jsonl").is_file()


def test_compact_bad_path_exits_usage_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["inputs", "compact", str(tmp_path / "nope")])
    assert result.exit_code == 64, result.output


def test_compact_lossy_read_exits_one_and_leaves_pack_unchanged(
    tmp_path: Path,
) -> None:
    # A malformed sibling source-fact file makes the read lossy; compaction
    # must skip entirely (no partial merge published) rather than risk
    # duplicating TUs on the next scan (CodeRabbit review, P2).
    pack = _pack_with_two_tus(tmp_path)
    (pack / "source_facts" / "bad.jsonl").write_text(
        "{not valid json\n", encoding="utf-8"
    )
    before = sorted(p.name for p in (pack / "source_facts").glob("*.jsonl"))

    result = CliRunner().invoke(main, ["inputs", "compact", str(pack)])
    assert result.exit_code == 1, result.output
    assert "skipped" in result.output.lower()

    after = sorted(p.name for p in (pack / "source_facts").glob("*.jsonl"))
    assert after == before  # pack left unchanged


def test_compact_wrong_kind_manifest_exits_usage_error(tmp_path: Path) -> None:
    # A directory with a manifest.json for a different pack kind (e.g. a
    # BuildSourcePack) must be rejected, not silently treated as a Flow-2
    # pack and written into (Codex review, P2).
    bsp = tmp_path / "pack"
    bsp.mkdir()
    (bsp / "manifest.json").write_text(json.dumps({"build_source_pack_version": 1}))
    result = CliRunner().invoke(main, ["inputs", "compact", str(bsp)])
    assert result.exit_code == 64, result.output
    assert not (bsp / "source_facts").exists()


def test_compact_escaping_output_filename_exits_usage_error(tmp_path: Path) -> None:
    pack = _pack_with_two_tus(tmp_path)
    result = CliRunner().invoke(
        main,
        ["inputs", "compact", str(pack), "--output-filename", "../escape.jsonl"],
    )
    assert result.exit_code == 64, result.output
    # The originals must survive a rejected compaction attempt.
    remaining = list((pack / "source_facts").glob("*.jsonl"))
    assert len(remaining) == 2
