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

"""Tests for the Flow-2 producer side (ADR-035 D5, G19.4): the ``inputs_emit``
pack writer and the ``abicheck-cc`` compiler wrapper. The producer emits a pack
that round-trips through ``ingest_inputs_pack`` — no compiler is run here."""

from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path

import pytest

from abicheck.buildsource import (
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
    append_source_facts,
    ingest_inputs_pack,
    init_inputs_pack,
    read_source_facts,
    write_inputs_pack,
)
from abicheck.buildsource.inputs_emit import (
    _write_manifest,
    compact_inputs_pack,
    facts_filename,
)
from abicheck.buildsource.inputs_pack import is_inputs_pack, load_inputs_manifest
from abicheck.buildsource.inputs_validate import validate_inputs_pack
from abicheck.cc_wrapper import (
    compile_unit_from_command,
    compile_units_from_command,
    emit_facts_for_command,
    main,
    run_cc_wrapper,
)


def _tu(name: str, *, mangled: str, source: str = "src/foo.cpp") -> SourceAbiTu:
    ent = SourceEntity(
        id=f"decl://{name}",
        kind="function",
        qualified_name=name,
        mangled_name=mangled,
        signature_hash="sig1",
        source_location=SourceLocation(path=f"include/{name}.h", line=3, origin="PUBLIC_HEADER"),
        visibility="public_header",
    )
    return SourceAbiTu(
        tu_id=f"cu://{source}", target_id="target://libfoo", source=source,
        public_header_roots=[f"include/{name}.h"], functions=[ent],
    )


# -- pack writer round-trip --------------------------------------------------


def test_write_inputs_pack_round_trips_through_ingest(tmp_path: Path) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(json.dumps([
        {"directory": str(tmp_path), "file": "src/foo.cpp",
         "arguments": ["c++", "-std=c++17", "-c", "src/foo.cpp"]}
    ]))
    root = write_inputs_pack(
        tmp_path / "abicheck_inputs",
        library="libfoo.so", version="1.0", created_by="test",
        tus=[_tu("foo", mangled="_Z3foov")], compile_db=cdb,
    )
    ingested = ingest_inputs_pack(root)
    assert ingested.tu_count == 1
    assert ingested.manifest.created_by == "test"
    assert ingested.pack.build_evidence is not None  # compile DB copied + parsed
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "foo" in names


def test_incremental_init_then_append_round_trips(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", version="1.0", created_by="abicheck-cc")
    # Two per-TU appends, as a wrapper would do across two compile invocations.
    append_source_facts(pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp"))
    append_source_facts(pack, [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
                        filename=facts_filename("src/bar.cpp"))
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert {"foo", "bar"} <= names


def test_manifest_write_is_atomic_no_temp_leftover(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    # No straggler temp files from the atomic temp+replace write.
    assert not list(pack.glob(".manifest.*.tmp"))
    assert (pack / "manifest.json").is_file()


def test_init_recovers_from_partial_manifest(tmp_path: Path) -> None:
    # A manifest left half-written by some non-atomic writer must re-initialize,
    # not raise (which would lose this TU's facts in the wrapper's best-effort path).
    pack = tmp_path / "abicheck_inputs"
    (pack / "source_facts").mkdir(parents=True)
    (pack / "manifest.json").write_text('{"kind": "abicheck_inputs"', encoding="utf-8")  # truncated JSON
    m = init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    assert m.library == "libfoo.so"
    # Manifest is now valid and round-trips.
    assert json.loads((pack / "manifest.json").read_text())["library"] == "libfoo.so"


def test_init_inputs_pack_is_idempotent(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    m1 = init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    m2 = init_inputs_pack(pack, library="OTHER", created_by="OTHER")
    # Second call loads the existing manifest, does not clobber it.
    assert m2.library == m1.library == "libfoo.so"
    assert m2.created_by == "abicheck-cc"


def test_init_inputs_pack_rejects_wrong_kind_manifest(tmp_path: Path) -> None:
    # A directory with a manifest.json for a different pack kind (e.g. a
    # BuildSourcePack) must be rejected, not silently accepted -- this is
    # the very first point of contact for a build's pack, so silently
    # accepting it would let every subsequent append_source_facts() call
    # write source_facts/*.jsonl into that unrelated directory (CodeRabbit
    # review, P2).
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "manifest.json").write_text(json.dumps({"build_source_pack_version": 1}))
    with pytest.raises(ValueError, match="does not declare kind"):
        init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    # A rejected wrong-kind pack must be left completely untouched -- not
    # just its manifest -- including no stray source_facts/ directory
    # created before the kind check ran (CodeRabbit review, P2).
    assert sorted(p.name for p in pack.iterdir()) == ["manifest.json"]


def test_init_inputs_pack_recovers_from_truly_malformed_manifest(
    tmp_path: Path,
) -> None:
    # A manifest left partial by a non-atomic writer (our own writes are
    # atomic) is genuinely malformed JSON, not a different pack -- this
    # case must still re-initialize rather than raise (the original
    # defensive behavior this fix must not regress).
    pack = tmp_path / "abicheck_inputs"
    pack.mkdir()
    (pack / "manifest.json").write_text('{"kind": "abicheck_inputs", "library":')
    manifest = init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    assert manifest.library == "libfoo.so"


def test_facts_filename_deterministic_and_collision_resistant() -> None:
    assert facts_filename("src/foo.cpp") == facts_filename("src/foo.cpp")
    # Same basename, different dir → different file.
    assert facts_filename("a/foo.cpp") != facts_filename("b/foo.cpp")
    assert facts_filename("src/foo.cpp").endswith(".jsonl")


# -- compression (P1 #22) -----------------------------------------------------


def test_write_inputs_pack_compress_round_trips_through_ingest(tmp_path: Path) -> None:
    root = write_inputs_pack(
        tmp_path / "abicheck_inputs",
        library="libfoo.so",
        version="1.0",
        created_by="test",
        tus=[_tu("foo", mangled="_Z3foov")],
        compress=True,
    )
    facts = list((root / "source_facts").glob("*.jsonl.gz"))
    assert len(facts) == 1
    ingested = ingest_inputs_pack(root)
    assert ingested.tu_count == 1
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "foo" in names


def test_append_source_facts_compress_supports_incremental_appends(
    tmp_path: Path,
) -> None:
    # Compression is execution policy: two separate compressed appends (as
    # parallel wrapper invocations sharing one file would do) must still
    # decode to both TUs, exactly like the uncompressed incremental path.
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename="shared.jsonl", compress=True
    )
    append_source_facts(
        pack,
        [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
        filename="shared.jsonl",
        compress=True,
    )
    assert not (pack / "source_facts" / "shared.jsonl").exists()
    assert (pack / "source_facts" / "shared.jsonl.gz").is_file()
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert {"foo", "bar"} <= names


def test_append_source_facts_infers_compression_from_gz_filename(
    tmp_path: Path,
) -> None:
    # compress=False (the default) with a caller-supplied ".gz" filename must
    # still be written compressed, not silently as plaintext under a
    # misleading name that read_source_facts() would then fail to decompress
    # (CodeRabbit review, P2).
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    path = append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename="custom.jsonl.gz"
    )
    assert path.name == "custom.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        fh.read()  # must decompress cleanly -- would raise on plaintext
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1


# -- post-build compaction (P1 #21) -------------------------------------------


def test_compact_merges_per_tu_files_and_removes_originals(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )
    append_source_facts(
        pack,
        [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
        filename=facts_filename("src/bar.cpp"),
    )
    before = ingest_inputs_pack(pack)
    before_names = {
        e.qualified_name for e in before.pack.source_abi.reachable_declarations
    }

    out = compact_inputs_pack(pack)
    assert out.name == "compacted.jsonl"
    # The two per-TU originals are gone; only the merged file remains.
    remaining = sorted(p.name for p in (pack / "source_facts").glob("*.jsonl"))
    assert remaining == ["compacted.jsonl"]

    after = ingest_inputs_pack(pack)
    after_names = {
        e.qualified_name for e in after.pack.source_abi.reachable_declarations
    }
    # P1 #25 invariance: compaction changes file layout, never decoded facts.
    assert after_names == before_names == {"foo", "bar"}
    assert after.tu_count == before.tu_count == 2


def test_compact_compress_round_trips(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )

    out = compact_inputs_pack(pack, compress=True)
    assert out.name == "compacted.jsonl.gz"
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "foo" in names


def test_compact_infers_compression_from_gz_output_filename(tmp_path: Path) -> None:
    # compress=False (the default) with a caller-supplied ".gz" output_filename
    # must still write compressed (CodeRabbit review, P2).
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )
    out = compact_inputs_pack(pack, output_filename="merged.jsonl.gz")
    assert out.name == "merged.jsonl.gz"
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        fh.read()
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1


@pytest.mark.parametrize(
    ("output_filename", "compress", "expected_name"),
    [
        ("merged", True, "merged.jsonl.gz"),
        ("merged", False, "merged.jsonl"),
        ("merged.gz", False, "merged.jsonl.gz"),
    ],
)
def test_compact_normalizes_extensionless_output_filename(
    tmp_path: Path, output_filename: str, compress: bool, expected_name: str
) -> None:
    """The default directory scan _iter_source_fact_files() runs on every
    later read only recognizes *.jsonl(.gz)/*.json(.gz) -- a caller-
    supplied --output-filename without one of those extensions (e.g.
    "merged", with or without --compress) wrote a merged file that scan
    could never find. Compaction "succeeded" with zero diagnostics,
    deleted the originals it just merged, and the pack silently ingested
    as zero TUs from then on (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )

    out = compact_inputs_pack(pack, output_filename=output_filename, compress=compress)
    assert out is not None
    assert out.name == expected_name

    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "foo" in names


def test_compact_root_directory_scan_does_not_destroy_manifest(
    tmp_path: Path,
) -> None:
    """A hand-written manifest using source_facts: ["."] (root-level JSONL
    facts, no source_facts/ subdirectory) would otherwise sweep the pack's
    own manifest.json into the read path's "*.json" glob as if it were a
    (silently valid, empty) TU record. With the default
    remove_originals=True, compaction then deletes it as a merged
    "original" -- destroying the pack's own manifest and leaving a
    directory is_inputs_pack() no longer recognizes at all (Codex review,
    P2, reproduced empirically)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="hand-written")
    manifest = load_inputs_manifest(pack)
    manifest.source_facts = ["."]
    _write_manifest(pack, manifest)
    (pack / "root.jsonl").write_text(
        json.dumps(_tu("foo", mangled="_Z3foov", source="src/foo.cpp").to_dict()) + "\n",
        encoding="utf-8",
    )

    diagnostics: list[str] = []
    out = compact_inputs_pack(pack, diagnostics=diagnostics)
    assert out is not None

    # The core regression: manifest.json must never be treated as a merged
    # "original" and deleted -- the pack must still be recognizable and
    # loadable afterward.
    assert (pack / "manifest.json").is_file()
    assert is_inputs_pack(pack)
    load_inputs_manifest(pack)  # must not raise


def test_compact_rerun_prefers_fresh_record_over_stale_prior_output(
    tmp_path: Path,
) -> None:
    # Simulates an incremental rebuild between two compactions: only foo.cpp
    # is recompiled (fresh per-TU file, new content); bar.cpp's only
    # surviving record lives in the first compaction's output. The fresh foo
    # record must win and bar must not be dropped (Codex review, P2).
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
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
    compact_inputs_pack(pack)

    # Incremental rebuild: foo.cpp's per-TU file is rewritten with new
    # content (same tu_id, since tu_id derives from source path).
    append_source_facts(
        pack,
        [_tu("foo2", mangled="_Z4foo2v", source="src/foo.cpp")],
        filename=facts_filename("src/foo.cpp"),
    )
    compact_inputs_pack(pack)

    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2  # foo (fresh) + bar (carried from prior), not 3
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo2", "bar"}  # fresh "foo2" wins over stale "foo"


def test_compact_rerun_recognizes_stale_output_across_a_filename_change(
    tmp_path: Path,
) -> None:
    """A rerun of compaction with a DIFFERENT output filename/compression
    setting than last time (e.g. --compress toggled on) must still recognize
    last run's output as stale: its records lose to a same-tu_id fresh
    record. Identified via the manifest's last_compacted pointer, not by
    matching *this* run's output path byte-for-byte or by file mtime (Codex
    review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
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
    # First compaction uses the default (uncompressed) output filename.
    first_out = compact_inputs_pack(pack)
    assert load_inputs_manifest(pack).last_compacted == "source_facts/compacted.jsonl"

    # Incremental rebuild: foo.cpp is rewritten with new content.
    append_source_facts(
        pack,
        [_tu("foo2", mangled="_Z4foo2v", source="src/foo.cpp")],
        filename=facts_filename("src/foo.cpp"),
    )

    # Second compaction toggles --compress on: a different output_filename
    # (".gz" suffix) than the first run used.
    second_out = compact_inputs_pack(pack, compress=True)
    assert second_out != first_out
    assert not first_out.exists()  # merged away by remove_originals
    assert (
        load_inputs_manifest(pack).last_compacted
        == "source_facts/compacted.jsonl.gz"
    )

    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2  # foo (fresh) + bar (carried from prior), not 3
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo2", "bar"}  # fresh "foo2" wins over stale "foo"


def test_compact_rerun_ignores_mtime_ties_between_stale_output_and_fresh_tu(
    tmp_path: Path,
) -> None:
    """A rebuild's freshly-rewritten per-TU file and the previous
    compaction's output can land in the same filesystem timestamp tick
    (observed empirically on some filesystems) -- the fresh record must
    still win, since "prior" is identified via the manifest's
    last_compacted pointer, never by comparing mtimes (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
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
    first_out = compact_inputs_pack(pack)

    fresh = append_source_facts(
        pack,
        [_tu("foo2", mangled="_Z4foo2v", source="src/foo.cpp")],
        filename=facts_filename("src/foo.cpp"),
    )
    # Force an exact mtime tie between the stale compacted output and the
    # fresh per-TU file -- the scenario an mtime-based "which is newer"
    # check cannot break correctly.
    tie = 3_000_000_000
    os.utime(first_out, ns=(tie, tie))
    os.utime(fresh, ns=(tie, tie))

    compact_inputs_pack(pack)

    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2  # foo (fresh) + bar (carried from prior), not 3
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo2", "bar"}  # fresh "foo2" wins over stale "foo"


def test_read_without_recompacting_prefers_fresh_over_stale_compacted(
    tmp_path: Path,
) -> None:
    """A directory-scan pack that was compacted, then reused for an
    incremental rebuild WITHOUT a second compaction in between, has both the
    prior compaction's output (manifest.last_compacted -- intentionally
    preserved, compaction only deletes the per-TU originals it merged, never
    its own output) and the fresh rebuilt per-TU file for the changed TU
    sitting in source_facts/ together. validate/ingest/merge (all built on
    read_source_facts, not compact_inputs_pack) must apply the same
    prior-vs-fresh supersession compaction itself applies, not just report
    two records for the same tu_id as a genuine pack-integrity duplicate or
    fold both the stale and fresh facts into one surface (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
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
    compact_inputs_pack(pack)

    # Incremental rebuild: foo.cpp changes, bar.cpp is untouched. No
    # recompaction happens before the next read.
    append_source_facts(
        pack,
        [_tu("foo2", mangled="_Z4foo2v", source="src/foo.cpp")],
        filename=facts_filename("src/foo.cpp"),
    )
    assert sorted(p.name for p in (pack / "source_facts").glob("*.jsonl")) == [
        "compacted.jsonl",
        facts_filename("src/foo.cpp"),
    ]

    diagnostics: list[str] = []
    tus = read_source_facts(pack, diagnostics=diagnostics)
    assert diagnostics == []
    assert {tu.tu_id: tu.functions[0].mangled_name for tu in tus} == {
        "cu://src/foo.cpp": "_Z4foo2v",  # fresh wins, stale "foo" is gone
        "cu://src/bar.cpp": "_Z3barv",  # untouched TU still carried forward
    }

    report = validate_inputs_pack(pack)
    assert report.ok
    assert report.duplicate_tu_ids == []
    assert report.tu_count == 2

    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo2", "bar"}


def test_read_drops_all_prior_carry_forward_on_lossy_fresh_read(
    tmp_path: Path,
) -> None:
    """A fresh per-TU file that fails to read/parse degrades to a diagnostic
    (its true tu_id is then unknown), so read_source_facts cannot tell
    whether it corresponds to some prior-compaction record it would
    otherwise carry forward as "not rebuilt, still current". Silently
    carrying every non-matching prior record forward anyway would let a
    stale record stand in for source that may have actually changed, with
    only a generic "skipped malformed record" diagnostic and no signal that
    THIS TU's facts may now be wrong -- a lossy fresh read must drop ALL
    prior-compaction carry-forward for the read, not just guess which one
    TU it might affect (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
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
    compact_inputs_pack(pack)

    # Incremental rebuild of foo.cpp lands a truncated/corrupt file -- the
    # source really may have changed, but the write never completed cleanly.
    fresh = pack / "source_facts" / facts_filename("src/foo.cpp")
    fresh.write_text("{not valid json!!", encoding="utf-8")

    diagnostics: list[str] = []
    tus = read_source_facts(pack, diagnostics=diagnostics)
    # Neither the corrupt fresh "foo" nor the untouched-but-now-untrusted
    # prior "bar" survives -- not just the TU whose fresh write failed.
    assert tus == []
    assert any("skipped malformed JSON line" in d for d in diagnostics)
    assert any("no prior-compaction record can be safely carried forward" in d for d in diagnostics)

    report = validate_inputs_pack(pack)
    assert report.tu_count == 0
    assert any("skipped malformed JSON line" in w for w in report.warnings)


def test_compact_rerun_never_treats_empty_tu_id_as_a_match(tmp_path: Path) -> None:
    """A hand-written/older record that never stamped tu_id defaults to
    tu_id="" (SourceAbiTu.tu_id); a single fresh no-tu_id record must not
    supersede *every* no-tu_id prior record -- they are unrelated TUs that
    merely share the same "unknown identity" (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")

    def _no_id_tu(name: str, mangled: str) -> SourceAbiTu:
        ent = SourceEntity(
            id=f"decl://{name}", kind="function", qualified_name=name,
            mangled_name=mangled, signature_hash="sig1",
            source_location=SourceLocation(
                path=f"include/{name}.h", line=3, origin="PUBLIC_HEADER"
            ),
            visibility="public_header",
        )
        return SourceAbiTu(tu_id="", target_id="target://libfoo", functions=[ent])

    append_source_facts(
        pack, [_no_id_tu("foo", "_Z3foov")], filename=facts_filename("foo")
    )
    append_source_facts(
        pack, [_no_id_tu("bar", "_Z3barv")], filename=facts_filename("bar")
    )
    compact_inputs_pack(pack)

    # A fresh no-tu_id record appears (unrelated to either prior one).
    append_source_facts(
        pack, [_no_id_tu("baz", "_Z3bazv")], filename=facts_filename("baz")
    )
    compact_inputs_pack(pack)

    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo", "bar", "baz"}  # foo/bar survive, not dropped


def test_compact_skips_entirely_on_lossy_read(tmp_path: Path) -> None:
    # A malformed sibling source-fact file makes this compaction "lossy"
    # (read_source_fact_files reports a diagnostic for it). Publishing a
    # best-effort merge here anyway -- while leaving the good original
    # untouched too -- would duplicate that TU on the next scan (the merged
    # file now also carries a copy of the record the untouched original
    # still provides): reproduced empirically as tu_count == 2 for a single
    # TU and a duplicate-tu_id error from `inputs validate` (CodeRabbit
    # review, P2). Compaction must skip entirely instead: no merged file,
    # pack left byte-for-byte unchanged.
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )
    (pack / "source_facts" / "bad.jsonl").write_text(
        "{not valid json\n", encoding="utf-8"
    )

    diagnostics: list[str] = []
    out = compact_inputs_pack(pack, diagnostics=diagnostics)
    assert out is None

    remaining = sorted(p.name for p in (pack / "source_facts").glob("*.jsonl"))
    assert remaining == sorted(["bad.jsonl", facts_filename("src/foo.cpp")])  # unchanged
    assert any("compaction was skipped entirely" in d for d in diagnostics)

    # The good TU's facts are still readable directly (no merge happened,
    # nothing was duplicated).
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo"}


def test_compact_skips_on_explicit_missing_source_facts_entry(
    tmp_path: Path,
) -> None:
    """An explicitly-named source_facts entry that resolves to nothing
    (typo, stale reference) makes _iter_source_fact_files() itself append a
    diagnostic -- before any per-file record is even read. That diagnostic
    must count as "lossy" too: otherwise compaction proceeds as if the pack
    were fully readable, publishes a merge missing whatever that entry would
    have contributed, and repoints the manifest to it (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    manifest = load_inputs_manifest(pack)
    manifest.source_facts = ["source_facts", "typo_missing.jsonl"]
    _write_manifest(pack, manifest)
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )

    diagnostics: list[str] = []
    out = compact_inputs_pack(pack, diagnostics=diagnostics)
    assert out is None

    remaining = sorted(p.name for p in (pack / "source_facts").glob("*.jsonl"))
    assert remaining == [facts_filename("src/foo.cpp")]  # unchanged, no merge
    assert any("resolved to no readable fact files" in d for d in diagnostics)
    assert any("compaction was skipped entirely" in d for d in diagnostics)
    assert load_inputs_manifest(pack).source_facts == [
        "source_facts",
        "typo_missing.jsonl",
    ]  # manifest not repointed


def test_compact_skips_on_duplicate_fresh_tu_id(tmp_path: Path) -> None:
    """Two different files sharing a non-empty tu_id is already an ERROR
    inputs_validate.py's duplicate_tu_ids check catches before compaction.
    Silently applying last-record-wins here (as if this were a fresh record
    correctly superseding a stale prior-compaction one) would let
    remove_originals delete the losing file, erasing that TU's facts and
    the very duplicate the validator would otherwise catch -- an
    ambiguous/corrupt pack silently made to look clean (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="hand-written")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov", source="src/x.cpp")], filename="a.jsonl"
    )
    # A hand-written/buggy producer: a second file claims the SAME tu_id
    # (facts_filename() derives it from the source path, so a race-free
    # producer can't collide here in practice -- this models a hand-edited
    # or third-party pack instead).
    other = SourceAbiTu(
        tu_id="cu://src/x.cpp",
        target_id="target://libfoo",
        functions=[
            SourceEntity(
                id="decl://bar", kind="function", qualified_name="bar",
                mangled_name="_Z3barv", signature_hash="sig1",
                source_location=SourceLocation(
                    path="include/bar.h", line=3, origin="PUBLIC_HEADER"
                ),
                visibility="public_header",
            )
        ],
    )
    append_source_facts(pack, [other], filename="b.jsonl")

    diagnostics: list[str] = []
    out = compact_inputs_pack(pack, diagnostics=diagnostics)
    assert out is None

    remaining = sorted(p.name for p in (pack / "source_facts").glob("*.jsonl"))
    assert remaining == ["a.jsonl", "b.jsonl"]  # both survive, nothing deleted
    assert any("duplicate tu_id" in d for d in diagnostics)

    # inputs validate still catches the duplicate -- not silently erased.
    report = validate_inputs_pack(pack)
    assert any("duplicate tu_id" in e for e in report.errors)


def test_compact_directory_scan_manifest_stays_discoverable_after_rebuild(
    tmp_path: Path,
) -> None:
    """A manifest whose "explicit" source_facts entry is just a directory
    reference (what the Clang facts plugin's ensureManifest() always writes:
    ``source_facts: ["source_facts"]``) must not be narrowed to the single
    compacted filename -- ensureManifest() never rewrites an existing
    manifest.json, so a narrowed entry would permanently hide every per-TU
    file a later incremental build writes into that directory (Codex review,
    P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-clang-plugin")
    manifest = load_inputs_manifest(pack)
    manifest.source_facts = ["source_facts"]  # mirrors ensureManifest()
    _write_manifest(pack, manifest)
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov", source="src/foo.cpp")],
        filename=facts_filename("src/foo.cpp"),
    )
    compact_inputs_pack(pack)

    # manifest.json is left alone by ensureManifest() on a "rebuild" (it only
    # acts when the file is absent) -- still a directory reference here.
    assert load_inputs_manifest(pack).source_facts == ["source_facts"]

    # A later incremental build's fresh per-TU file lands in the same
    # directory; it must still be discovered.
    append_source_facts(
        pack, [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
        filename=facts_filename("src/bar.cpp"),
    )
    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo", "bar"}


@pytest.mark.parametrize("spelling", ["source_facts/", "./source_facts"])
def test_compact_directory_scan_manifest_recognized_across_spellings(
    tmp_path: Path, spelling: str
) -> None:
    """_iter_source_fact_files (via pathlib) treats "source_facts",
    "source_facts/", and "./source_facts" as the exact same directory
    reference; the directory-scan-manifest exemption above must recognize
    all of them, not just the byte-exact "source_facts" spelling (Codex
    review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-clang-plugin")
    manifest = load_inputs_manifest(pack)
    manifest.source_facts = [spelling]
    _write_manifest(pack, manifest)
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov", source="src/foo.cpp")],
        filename=facts_filename("src/foo.cpp"),
    )
    compact_inputs_pack(pack)
    assert load_inputs_manifest(pack).source_facts == [spelling]

    append_source_facts(
        pack, [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
        filename=facts_filename("src/bar.cpp"),
    )
    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo", "bar"}


def test_compact_preserves_directory_entry_in_mixed_manifest(
    tmp_path: Path,
) -> None:
    """A manifest that names SOURCE_FACTS_DIR *alongside* another explicit
    facts directory (a hand-written mixed manifest, not the plugin's
    single-entry ["source_facts"] case the sibling test above covers) must
    still preserve the directory reference after compaction, not just when
    it is the manifest's *only* entry -- the original fix collapsed the
    whole list, directory entries included, the moment there was more than
    one entry, hiding every per-TU file a later incremental build writes
    back into that directory (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="hand-written")
    other_dir = pack / "extra_facts"
    other_dir.mkdir()
    (other_dir / "extra.jsonl").write_text(
        json.dumps(
            _tu("extra", mangled="_Z5extrav", source="src/extra.cpp").to_dict()
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = load_inputs_manifest(pack)
    manifest.source_facts = ["source_facts", "extra_facts"]
    _write_manifest(pack, manifest)
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov", source="src/foo.cpp")],
        filename=facts_filename("src/foo.cpp"),
    )
    compact_inputs_pack(pack)

    # Both directory entries survive -- neither was collapsed into a single
    # explicit merged-file entry.
    assert load_inputs_manifest(pack).source_facts == ["source_facts", "extra_facts"]

    # A later incremental build's fresh per-TU file lands in source_facts/;
    # it must still be discovered, and extra_facts/ is untouched.
    append_source_facts(
        pack, [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
        filename=facts_filename("src/bar.cpp"),
    )
    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo", "bar", "extra"}


def test_compact_adds_merged_ref_when_no_directory_entry_covers_source_facts_dir(
    tmp_path: Path,
) -> None:
    """A manifest whose only entry is a directory OTHER than SOURCE_FACTS_DIR
    (e.g. source_facts=["extra_facts"], no "source_facts" entry at all) does
    not automatically discover the merged file: compact_inputs_pack always
    physically writes it under SOURCE_FACTS_DIR regardless of what the
    manifest's entries say. Treating "every entry is a directory" as reason
    enough to leave the manifest untouched (as if SOURCE_FACTS_DIR were
    always among them) deletes the entry's own originals but leaves every
    later read scanning only the untouched explicit directory -- a
    successful compact silently turning a valid pack into one that
    ingests/validates as zero TUs (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="hand-written")
    other_dir = pack / "extra_facts"
    other_dir.mkdir()
    (other_dir / "a.jsonl").write_text(
        json.dumps(_tu("foo", mangled="_Z3foov", source="src/foo.cpp").to_dict())
        + "\n",
        encoding="utf-8",
    )
    manifest = load_inputs_manifest(pack)
    manifest.source_facts = ["extra_facts"]
    _write_manifest(pack, manifest)

    diagnostics: list[str] = []
    out = compact_inputs_pack(pack, diagnostics=diagnostics)
    assert out is not None
    assert diagnostics == []

    # The manifest must now ALSO point at the merged file -- "extra_facts"
    # alone can never discover something written under source_facts/.
    assert load_inputs_manifest(pack).source_facts == [
        "extra_facts",
        "source_facts/compacted.jsonl",
    ]

    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"foo"}

    report = validate_inputs_pack(pack)
    assert report.ok
    assert report.tu_count == 1
    # A successful, fully-complete compaction must not itself produce a
    # "resolved to no readable fact files" warning about the directory it
    # just legitimately drained -- that would falsely tell the operator this
    # complete pack is suspect (Codex review, P2).
    assert not any("resolved to no readable fact files" in w for w in report.warnings)


def test_compact_preserved_directory_entry_is_rerunnable(tmp_path: Path) -> None:
    """The directory entry compact_inputs_pack preserves for future
    rediscoverability (the sibling test above) is, by construction, left
    empty by a successful merge with the default remove_originals=True --
    nothing ever repopulates it on its own. A second compact() call on the
    same pack must not treat that now-permanently-empty directory as a
    pack-integrity problem and fail closed forever; only a genuinely
    unresolvable entry (missing file/dir) should still do that (Codex
    review, P2, reproduced empirically: before the fix a second compact()
    call always returned None)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="hand-written")
    other_dir = pack / "extra_facts"
    other_dir.mkdir()
    (other_dir / "a.jsonl").write_text(
        json.dumps(_tu("foo", mangled="_Z3foov", source="src/foo.cpp").to_dict())
        + "\n",
        encoding="utf-8",
    )
    manifest = load_inputs_manifest(pack)
    manifest.source_facts = ["extra_facts"]
    _write_manifest(pack, manifest)
    compact_inputs_pack(pack)

    diagnostics: list[str] = []
    out = compact_inputs_pack(pack, diagnostics=diagnostics)
    assert out is not None
    assert diagnostics == []

    # A genuinely unresolvable entry is still flagged and still fails closed.
    manifest2 = load_inputs_manifest(pack)
    manifest2.source_facts = [*manifest2.source_facts, "typo_missing.jsonl"]
    _write_manifest(pack, manifest2)
    diagnostics2: list[str] = []
    out2 = compact_inputs_pack(pack, diagnostics=diagnostics2)
    assert out2 is None
    assert any("resolved to no readable fact files" in d for d in diagnostics2)


def test_compact_preserves_originals_when_manifest_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_write_manifest is atomic (temp file + rename) but can still fail
    (disk full, permission change mid-run). The manifest publish must
    happen BEFORE the destructive removal of the originals it supersedes:
    deleting first and publishing after would leave a failed write
    pointing an explicit-file manifest at now-deleted files -- a later
    read finds neither the old originals nor a manifest that knows to look
    at the merged output, discarding evidence the merge itself
    successfully captured (CodeRabbit review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    manifest = load_inputs_manifest(pack)
    # An explicit per-file manifest entry -- the case that actually gets
    # rewritten (and so actually needs a durable manifest write) rather
    # than the directory-reference case that's left untouched.
    manifest.source_facts = [f"source_facts/{facts_filename('src/foo.cpp')}"]
    _write_manifest(pack, manifest)
    original = append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov", source="src/foo.cpp")],
        filename=facts_filename("src/foo.cpp"),
    )

    import abicheck.buildsource.inputs_emit as inputs_emit_module

    def _boom(root: Path, manifest: object) -> None:
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(inputs_emit_module, "_write_manifest", _boom)

    with pytest.raises(OSError, match="disk full"):
        compact_inputs_pack(pack)

    # The original per-TU file must still be there -- not deleted ahead of
    # a manifest publish that never actually landed.
    assert original.exists()
    # And the manifest on disk is unchanged (still names the original,
    # readable file, not a merged output the failed write never recorded).
    assert load_inputs_manifest(pack).source_facts == [
        f"source_facts/{facts_filename('src/foo.cpp')}"
    ]


def test_compact_keep_originals_when_requested(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )

    compact_inputs_pack(pack, remove_originals=False)
    remaining = sorted(p.name for p in (pack / "source_facts").glob("*.jsonl"))
    assert "compacted.jsonl" in remaining
    assert len(remaining) == 2  # original + merged both present on disk

    # ingest_inputs_pack (built on read_source_facts) applies the same
    # prior-vs-fresh supersession compact_inputs_pack itself applies:
    # manifest.last_compacted marks compacted.jsonl as "prior", so the
    # still-present original's record for the same tu_id supersedes it --
    # no double count, even though the caller opted out of deleting the
    # original (Codex review, P2).
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1


def test_compact_is_idempotent_when_rerun_on_same_output(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )
    compact_inputs_pack(pack)
    compact_inputs_pack(pack)  # rerun onto the same merged filename
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1


@pytest.mark.parametrize(
    "bad_name",
    [
        "../escape.jsonl",
        "sub/dir/out.jsonl",
        "/etc/passwd",
        "..",
        "",
    ],
)
def test_compact_rejects_escaping_output_filename(
    tmp_path: Path, bad_name: str
) -> None:
    # Codex review (P2): an output_filename with a path component could write
    # the merged file outside source_facts/ while remove_originals still
    # deletes every per-TU file from source_facts/, leaving the pack ingesting
    # zero TUs.
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )
    with pytest.raises(ValueError):
        compact_inputs_pack(pack, output_filename=bad_name)
    # The original file must survive a rejected compaction attempt.
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1


# -- compile_unit_from_command -----------------------------------------------


def test_compile_unit_from_command_parses_flags(tmp_path: Path) -> None:
    cu = compile_unit_from_command(
        ["c++", "-std=c++17", "-DFOO=1", "-Iinc", "-c", "src/foo.cpp", "-o", "foo.o"],
        tmp_path,
    )
    assert cu is not None
    assert cu.source == "src/foo.cpp"
    assert cu.language == "CXX"
    assert cu.standard == "c++17"
    assert cu.defines.get("FOO") == "1"


def test_compile_unit_from_command_none_for_link_or_no_source(tmp_path: Path) -> None:
    assert compile_unit_from_command(["c++", "-shared", "foo.o", "-o", "libfoo.so"], tmp_path) is None
    assert compile_unit_from_command(["c++"], tmp_path) is None


def test_compile_unit_skips_preprocess_only_invocations(tmp_path: Path) -> None:
    # Preprocess-/dependency-only runs produce no shipped object → no facts, so
    # a build that pipes -E/-M steps through the wrapper can't pollute the pack.
    assert compile_unit_from_command(["c++", "-E", "src/foo.cpp"], tmp_path) is None
    assert compile_unit_from_command(["c++", "-M", "src/foo.cpp"], tmp_path) is None
    assert compile_unit_from_command(["c++", "-MM", "src/foo.cpp"], tmp_path) is None
    # -MD/-MMD are additive with a real -c compile and must NOT be skipped.
    cu = compile_unit_from_command(["c++", "-MD", "-c", "src/foo.cpp"], tmp_path)
    assert cu is not None and cu.source == "src/foo.cpp"


# -- run_cc_wrapper pass-through + best-effort -------------------------------


class _Proc:
    def __init__(self, rc: int) -> None:
        self.returncode = rc


def test_wrapper_preserves_exit_code_and_emits_on_success(tmp_path: Path) -> None:
    calls: list[tuple] = []

    def fake_emit(command, directory, **kw):
        calls.append((tuple(command), kw))
        return None

    rc = run_cc_wrapper(
        ["c++", "-c", "src/foo.cpp"],
        runner=lambda c: _Proc(0),
        env={"ABICHECK_INPUTS_DIR": str(tmp_path / "pk")},
        emit=fake_emit,
    )
    assert rc == 0
    assert len(calls) == 1  # emit called on a successful compile


def test_wrapper_skips_emit_on_failed_compile() -> None:
    calls: list = []
    rc = run_cc_wrapper(
        ["c++", "-c", "src/foo.cpp"],
        runner=lambda c: _Proc(5),
        env={},
        emit=lambda *a, **k: calls.append(1),
    )
    assert rc == 5
    assert not calls  # no extraction when the compile failed


def test_wrapper_disable_env_is_pure_passthrough() -> None:
    calls: list = []
    rc = run_cc_wrapper(
        ["c++", "-c", "src/foo.cpp"],
        runner=lambda c: _Proc(0),
        env={"ABICHECK_CC_DISABLE": "1"},
        emit=lambda *a, **k: calls.append(1),
    )
    assert rc == 0
    assert not calls


def test_wrapper_swallows_extraction_errors() -> None:
    def boom(*a, **k):
        raise RuntimeError("extractor blew up")

    # A fact-extraction failure must never change the compiler's exit code.
    rc = run_cc_wrapper(["c++", "-c", "src/foo.cpp"], runner=lambda c: _Proc(0), env={}, emit=boom)
    assert rc == 0


def test_empty_command_errors() -> None:
    assert run_cc_wrapper([], runner=lambda c: _Proc(0)) == 2


def test_main_empty_args_returns_2() -> None:
    assert main([]) == 2


def test_default_runner_executes_real_command(tmp_path: Path, monkeypatch) -> None:
    # Exercise the real subprocess default-runner path with a trivial, portable
    # command (no compiler, no source TU → emit is a no-op).
    monkeypatch.chdir(tmp_path)
    assert run_cc_wrapper([sys.executable, "-c", ""]) == 0


# -- emit_facts_for_command with a stub backend (producer → merge) -----------


def test_emit_appends_extracted_tu(tmp_path: Path, monkeypatch) -> None:
    captured = _tu("foo", mangled="_Z3foov")

    class _FakeBackend:
        def extract(self, cu, *, public_header_roots, target_id=""):
            return captured

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        lambda extractor, **kw: (None, _FakeBackend()),
    )
    pack = tmp_path / "abicheck_inputs"
    tu = emit_facts_for_command(
        ["c++", "-c", "src/foo.cpp"], tmp_path,
        inputs_dir=pack, library="libfoo.so",
    )
    assert tu is captured
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    assert ingested.manifest.created_by == "abicheck-cc"


def test_emit_captures_all_sources_in_multi_source_compile(tmp_path: Path, monkeypatch) -> None:
    # `gcc -c a.cpp b.cpp` builds both objects; both must contribute facts.
    def _extract(cu, *, public_header_roots, target_id=""):
        stem = Path(cu.source).stem
        return _tu(stem, mangled=f"_Z3{stem}v", source=cu.source)

    class _FakeBackend:
        extract = staticmethod(_extract)

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        lambda extractor, **kw: (None, _FakeBackend()),
    )
    pack = tmp_path / "abicheck_inputs"
    emit_facts_for_command(
        ["g++", "-std=c++17", "-c", "a.cpp", "b.cpp"], tmp_path,
        inputs_dir=pack, library="libfoo.so",
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert {"a", "b"} <= names


def test_compile_units_capture_forced_language_source(tmp_path: Path) -> None:
    # `clang++ -x c++ -c generated` builds a real TU with no source extension;
    # forced-language discovery must still capture it.
    units = compile_units_from_command(["clang++", "-x", "c++", "-c", "generated"], tmp_path)
    assert [u.source for u in units] == ["generated"]
    assert units[0].language == "CXX"


def test_emit_continues_after_per_tu_extraction_failure(tmp_path: Path, monkeypatch) -> None:
    # In `g++ -c a.cpp b.cpp`, a backend that raises on a.cpp must not drop b.cpp.
    def _extract(cu, *, public_header_roots, target_id=""):
        if Path(cu.source).stem == "a":
            raise RuntimeError("cannot parse a.cpp")
        stem = Path(cu.source).stem
        return _tu(stem, mangled=f"_Z3{stem}v", source=cu.source)

    class _FakeBackend:
        extract = staticmethod(_extract)

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        lambda extractor, **kw: (None, _FakeBackend()),
    )
    pack = tmp_path / "abicheck_inputs"
    emit_facts_for_command(
        ["g++", "-c", "a.cpp", "b.cpp"], tmp_path, inputs_dir=pack, library="libfoo.so",
    )
    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"b"}  # a.cpp failed, b.cpp survived


def test_emit_none_when_no_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        lambda extractor, **kw: (None, None),
    )
    out = emit_facts_for_command(["c++", "-c", "src/foo.cpp"], tmp_path, inputs_dir=tmp_path / "pk")
    assert out is None
