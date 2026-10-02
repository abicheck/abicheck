# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Coverage for `pack_io.py` paths the ADR-061
Phase 5 BuildSourcePack storage/model split moved but that no existing test
happened to exercise: `verify_integrity()` (zero call sites anywhere, so
never covered even as a `BuildSourcePack` method before the split), a raw
file under a pack's `normalized/` directory contributing to its content
hash, and `load()`'s malformed-JSON rejection."""

from __future__ import annotations

import pytest

from abicheck.buildsource import BuildEvidence, pack_io
from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary


def test_verify_integrity_true_for_untampered_pack(tmp_path):
    pack = BuildSourcePack.empty(tmp_path / "p")
    pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://a", source="a.cpp")]
    )
    pack_io.write(pack)
    reloaded = pack_io.load(tmp_path / "p")
    assert reloaded.manifest.artifacts
    assert pack_io.verify_integrity(reloaded) is True


def test_verify_integrity_false_for_tampered_pack(tmp_path):
    pack = BuildSourcePack.empty(tmp_path / "p")
    pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://a", source="a.cpp")]
    )
    pack_io.write(pack)
    reloaded = pack_io.load(tmp_path / "p")
    # Edit the on-disk normalized payload after writing -- verify_integrity
    # must detect the drift even though content_hash() (which trusts the
    # recorded manifest digests) would not.
    be_path = tmp_path / "p" / "build" / "build_evidence.json"
    be_path.write_text(
        be_path.read_text(encoding="utf-8").replace("a.cpp", "b.cpp"),
        encoding="utf-8",
    )
    assert pack_io.verify_integrity(reloaded) is False


def test_verify_integrity_true_for_legacy_pack_with_no_recorded_artifacts(tmp_path):
    pack = BuildSourcePack.empty(tmp_path / "p")
    pack.manifest.artifacts = []
    assert pack_io.verify_integrity(pack) is True


def test_content_hash_includes_raw_normalized_extractor_output(tmp_path):
    """A raw extractor output file dropped directly under `normalized/`
    (bypassing `write()`'s own build_evidence/source_abi/source_graph
    serialization) still contributes a digest -- the loop `write()` doesn't
    exercise on its own since it only ever populates the three known
    sub-paths."""
    pack = BuildSourcePack.empty(tmp_path / "p")
    pack_io.write(pack)
    baseline = pack_io.content_hash(pack)
    extractor_dir = tmp_path / "p" / "normalized" / "cmake-file-api"
    extractor_dir.mkdir(parents=True)
    (extractor_dir / "targets.json").write_text('{"targets": []}', encoding="utf-8")
    pack.manifest.artifacts = []
    with_extra = pack_io.content_hash(pack)
    assert with_extra != baseline


def test_write_removes_stale_source_graph_file(tmp_path):
    """A later write with no source_graph must drop an earlier one's file --
    the symmetric case to `test_pack_removes_stale_source_abi`, for the L5
    graph rather than the L4 surface."""
    pack = BuildSourcePack.empty(tmp_path / "p")
    pack.source_graph = SourceGraphSummary(
        nodes=[GraphNode(id="d:foo", kind="function")]
    )
    pack_io.write(pack)
    graph_path = tmp_path / "p" / pack_io.SOURCE_GRAPH_REL
    assert graph_path.is_file()
    pack.source_graph = None
    pack_io.write(pack)
    assert not graph_path.is_file()


def test_load_rejects_manifest_that_is_not_a_json_object(tmp_path):
    pack_dir = tmp_path / "p"
    pack_dir.mkdir()
    (pack_dir / pack_io.MANIFEST_NAME).write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a JSON object"):
        pack_io.load(pack_dir)
