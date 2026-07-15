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

"""Tests for the ADR-035 D5 (G19.4) Flow-2 ``abicheck_inputs/`` artifact protocol.

The product build drops a normalized-facts pack next to its binary; abicheck
ingests it without re-running a compiler frontend (pure parsing, CI-safe), and
the facts ride the existing ``merge`` fold. Mirrors the ADR-028 D6 pre-captured,
non-executing fixture pattern.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource import (
    ABICHECK_INPUTS_VERSION,
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
    ingest_inputs_pack,
    is_inputs_pack,
    read_source_facts,
)
from abicheck.buildsource.inputs_pack import (
    INPUTS_KIND,
    InputsManifest,
    load_inputs_manifest,
)
from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
from abicheck.cli import main
from abicheck.serialization import load_snapshot

# -- fixtures ----------------------------------------------------------------


def _tu(name: str, *, mangled: str, source: str = "src/foo.cpp") -> SourceAbiTu:
    """One per-TU dump exposing a single public function declaration."""
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
        tu_id=f"cu://{source}#cfg:abc",
        target_id="target://libfoo",
        source=source,
        public_header_roots=[f"include/{name}.h"],
        functions=[ent],
    )


def _macro_tu(name: str = "FOO_H") -> SourceAbiTu:
    ent = SourceEntity(
        id=f"macro://{name}",
        kind="macro",
        qualified_name=name,
        value="",
        source_location=SourceLocation(
            path="include/foo.h", line=1, origin="PUBLIC_HEADER"
        ),
        visibility="public_header",
    )
    return SourceAbiTu(
        tu_id="cu://src/foo.cpp#cfg:abc",
        target_id="target://libfoo",
        source="src/foo.cpp",
        public_header_roots=["include/foo.h"],
        macros=[ent],
    )


def _write_inputs_pack(
    root: Path,
    tus: list[SourceAbiTu],
    *,
    compile_db: list[dict] | None = None,
    jsonl: bool = True,
    manifest_extra: dict | None = None,
) -> Path:
    """Materialize an ``abicheck_inputs/`` directory and return its path."""
    pack = root / "abicheck_inputs"
    (pack / "source_facts").mkdir(parents=True)
    if jsonl:
        lines = "\n".join(json.dumps(t.to_dict()) for t in tus)
        (pack / "source_facts" / "libfoo.jsonl").write_text(
            lines + "\n", encoding="utf-8"
        )
    else:
        (pack / "source_facts" / "libfoo.json").write_text(
            json.dumps([t.to_dict() for t in tus]), encoding="utf-8"
        )
    if compile_db is not None:
        (pack / "build").mkdir(parents=True)
        (pack / "build" / "compile_commands.json").write_text(
            json.dumps(compile_db), encoding="utf-8"
        )
    manifest = {
        "kind": INPUTS_KIND,
        "abicheck_inputs_version": ABICHECK_INPUTS_VERSION,
        "library": "libfoo.so",
        "version": "1.0",
        "created_by": "abicheck-clang-plugin 0.1",
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack


def _compile_db(tmp_path: Path) -> list[dict]:
    return [
        {
            "directory": str(tmp_path),
            "file": "src/foo.cpp",
            "arguments": [
                "c++",
                "-std=c++17",
                "-DNDEBUG",
                "-Iinclude",
                "-c",
                "src/foo.cpp",
            ],
        }
    ]


# -- manifest round-trip ------------------------------------------------------


def test_manifest_round_trip_preserves_fields() -> None:
    m = InputsManifest(
        library="libfoo.so",
        version="2.1",
        created_by="abicheck-cc",
        compile_db="build/compile_commands.json",
        headers=["include/foo.h"],
        exported_symbols=["_Z3foov"],
        last_compacted="source_facts/compacted.jsonl",
    )
    back = InputsManifest.from_dict(m.to_dict())
    assert back == m


def test_manifest_from_dict_is_forward_compatible() -> None:
    # Unknown keys ignored; missing keys defaulted; stray types tolerated.
    m = InputsManifest.from_dict(
        {"kind": INPUTS_KIND, "library": "l", "headers": None, "future_field": 7}
    )
    assert m.library == "l"
    assert m.headers == []
    assert m.abicheck_inputs_version == ABICHECK_INPUTS_VERSION


def test_manifest_null_optional_strings_become_empty_not_none_literal() -> None:
    # A producer that serializes unset optionals as JSON null must not coerce to
    # the literal "None" — for compile_db that would point at a missing file.
    m = InputsManifest.from_dict(
        {"kind": None, "library": None, "compile_db": None, "created_by": None}
    )
    assert m.kind == INPUTS_KIND  # null kind falls back to the default
    assert m.library == ""
    assert m.compile_db == ""
    assert m.created_by == ""


@pytest.mark.parametrize("bad_fact_set", ["not-a-mapping", 7, ["a", "b"], True])
def test_manifest_non_mapping_fact_set_degrades_to_empty(bad_fact_set: object) -> None:
    # Codex review (P2): a hand-written/third-party manifest's fact_set might
    # not be a mapping (e.g. a string) -- dict(fs_raw) would raise before
    # `inputs validate` could produce a report, even though a malformed
    # optional field is supposed to degrade like every other field here.
    m = InputsManifest.from_dict(
        {"kind": INPUTS_KIND, "library": "l", "fact_set": bad_fact_set}
    )
    assert m.fact_set == {}


def test_ingest_with_null_compile_db_still_finds_default(tmp_path: Path) -> None:
    # compile_db: null in the manifest must fall back to build/compile_commands.json.
    pack = _write_inputs_pack(
        tmp_path,
        [_tu("foo", mangled="_Z3foov")],
        compile_db=_compile_db(tmp_path),
        manifest_extra={"compile_db": None},
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.pack.build_evidence is not None
    assert ingested.pack.build_evidence.compile_units


# -- detection ----------------------------------------------------------------


def test_is_inputs_pack_true_for_flow2_dir(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    assert is_inputs_pack(pack) is True


def test_is_inputs_pack_false_for_plain_dir_or_buildsourcepack(tmp_path: Path) -> None:
    assert is_inputs_pack(tmp_path) is False
    bsp = tmp_path / "pack"
    bsp.mkdir()
    (bsp / "manifest.json").write_text(json.dumps({"build_source_pack_version": 1}))
    assert is_inputs_pack(bsp) is False  # no kind: abicheck_inputs


# -- ingestion ----------------------------------------------------------------


def test_ingest_links_source_facts_into_l4_surface(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    surface = ingested.pack.source_abi
    assert surface is not None
    names = {e.qualified_name for e in surface.reachable_declarations}
    assert "foo" in names


def test_ingest_with_explicit_exports_maps_decl_to_symbol(tmp_path: Path) -> None:
    pack = _write_inputs_pack(
        tmp_path,
        [_tu("foo", mangled="_Z3foov")],
        manifest_extra={"exported_symbols": ["_Z3foov"]},
    )
    ingested = ingest_inputs_pack(pack)
    surface = ingested.pack.source_abi
    assert surface is not None
    assert "_Z3foov" in surface.roots["exported_symbols"]


def test_dump_inputs_folds_pack_into_snapshot_like_merge(tmp_path: Path) -> None:
    # `dump <binary> --inputs pack/` (embed_inputs_pack) folds a Flow-2 pack
    # straight into the artifact snapshot and links the source decl to the
    # binary's real export — the same result as a follow-up `merge`, one command.
    from abicheck.cli_buildsource_merge import embed_inputs_pack
    from abicheck.model import AbiSnapshot, Function

    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    # a binary-side snapshot that exports the same symbol
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    snap.functions.append(Function(name="foo", mangled="_Z3foov", return_type="void"))

    embed_inputs_pack(snap, pack, tmp_path / "out.json")

    assert snap.build_source is not None
    surface = snap.build_source.source_abi
    assert surface is not None
    # the source decl is now linked against the binary export (not left orphan)
    assert "_Z3foov" in surface.roots.get("exported_symbols", [])
    assert surface.coverage.get("matched_symbols", 0) >= 1
    assert surface.coverage.get("unmatched_symbols", 1) == 0


def test_write_snapshot_output_embeds_inputs_pack(tmp_path: Path) -> None:
    # The dump CLI seam: _write_snapshot_output(..., inputs_pack=pack) folds the
    # pack and serializes an embedded build_source (single-artifact UX), so a plain
    # `dump <binary> --inputs pack/` needs no follow-up merge.
    import json

    from abicheck.cli import _write_snapshot_output
    from abicheck.model import AbiSnapshot, Function

    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    snap.functions.append(Function(name="foo", mangled="_Z3foov", return_type="void"))
    out = tmp_path / "baseline.json"
    _write_snapshot_output(snap, out, inputs_pack=pack)

    d = json.loads(out.read_text())
    assert "build_source" in d, "embedded L3/L4/L5 facts should ride inline"
    cov = d["build_source"]["source_abi"]["coverage"]
    assert cov.get("matched_symbols", 0) >= 1


def test_ingest_reads_compile_db_into_l3(tmp_path: Path) -> None:
    pack = _write_inputs_pack(
        tmp_path,
        [_tu("foo", mangled="_Z3foov")],
        compile_db=_compile_db(tmp_path),
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.pack.build_evidence is not None
    assert ingested.pack.build_evidence.compile_units
    # L3 + L4 coverage present; L5 graph folded from both.
    statuses = {c.layer: c.status for c in ingested.pack.manifest.coverage}
    assert statuses[DataLayer.L3_BUILD.value] == CoverageStatus.PRESENT
    assert statuses[DataLayer.L4_SOURCE_ABI.value] == CoverageStatus.PRESENT
    assert ingested.pack.source_graph is not None


def test_ingest_without_compile_db_skips_l3(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    ingested = ingest_inputs_pack(pack)
    assert ingested.pack.build_evidence is None
    statuses = {c.layer: c.status for c in ingested.pack.manifest.coverage}
    assert statuses[DataLayer.L3_BUILD.value] == CoverageStatus.NOT_COLLECTED


def test_ingest_preserves_single_target_id(tmp_path: Path) -> None:
    # The TUs carry target://libfoo; the linked surface must keep it so the L5
    # graph emits BINARY_EXPORTS_SYMBOL target edges (localize_symbol needs them).
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    ingested = ingest_inputs_pack(pack)
    assert ingested.pack.source_abi.target_id == "target://libfoo"


def test_ingest_records_provenance_extractor(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    ingested = ingest_inputs_pack(pack)
    names = {e.name for e in ingested.pack.manifest.extractors}
    assert "abicheck_inputs" in names
    rec = next(
        e for e in ingested.pack.manifest.extractors if e.name == "abicheck_inputs"
    )
    assert "abicheck-clang-plugin" in rec.detail


# -- source-fact parsing tolerance -------------------------------------------


def test_read_source_facts_accepts_json_array_form(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")], jsonl=False)
    tus = read_source_facts(pack)
    assert len(tus) == 1
    assert tus[0].functions[0].qualified_name == "foo"


def test_ingest_skips_non_regular_globbed_entry(tmp_path: Path) -> None:
    # A directory matching the *.jsonl glob (or a FIFO) must be skipped, not
    # passed to read_text() where it would raise IsADirectoryError.
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    (pack / "source_facts" / "old.jsonl").mkdir()  # directory named like a fact file
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1  # the real file still read, no crash
    assert any("non-regular source-fact entry" in d for d in ingested.diagnostics)


def test_array_form_non_object_record_is_diagnosed(tmp_path: Path) -> None:
    # `.json` array with a non-dict element must be diagnosed, not silently
    # dropped — the ingest is lossy and must read as partial.
    pack = tmp_path / "abicheck_inputs"
    (pack / "source_facts").mkdir(parents=True)
    arr = [_tu("foo", mangled="_Z3foov").to_dict(), 42, None]
    (pack / "source_facts" / "libfoo.json").write_text(
        json.dumps(arr), encoding="utf-8"
    )
    (pack / "manifest.json").write_text(
        json.dumps({"kind": INPUTS_KIND, "library": "libfoo.so", "version": "1.0"}),
        encoding="utf-8",
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    assert sum("non-object record" in d for d in ingested.diagnostics) == 2
    rec = next(
        e for e in ingested.pack.manifest.extractors if e.name == "abicheck_inputs"
    )
    assert rec.status == "partial"


def test_read_source_facts_skips_malformed_lines(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    facts = pack / "source_facts" / "libfoo.jsonl"
    facts.write_text(
        facts.read_text(encoding="utf-8") + "this is not json\n\n",
        encoding="utf-8",
    )
    # The good record still ingests; the junk line is dropped, not fatal.
    tus = read_source_facts(pack)
    assert len(tus) == 1


def test_read_source_facts_degrades_on_invalid_utf8_instead_of_crashing(
    tmp_path: Path,
) -> None:
    # UnicodeDecodeError is a ValueError subclass, not an OSError -- it
    # previously escaped read_source_fact_files's `except OSError` and
    # aborted the whole read pass instead of degrading to a per-file
    # diagnostic like every other malformed-input case (CodeRabbit review,
    # P2).
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    (pack / "source_facts" / "bad.jsonl").write_bytes(b"\xff\xfe{\"tu_id\":1}")
    diags: list[str] = []
    tus = read_source_facts(pack, diagnostics=diags)
    assert len(tus) == 1  # the good record still ingests
    assert any("could not read/decompress" in d for d in diags)


def test_read_source_facts_degrades_on_corrupt_gzip_instead_of_crashing(
    tmp_path: Path,
) -> None:
    # A truncated/corrupt .gz raises EOFError or zlib.error during
    # decompression -- neither an OSError subclass -- which previously
    # escaped the same handler and crashed the read (CodeRabbit review, P2).
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    good = pack / "source_facts" / "good.jsonl.gz"
    with gzip.open(good, "wb") as fh:
        fh.write(b'{"tu_id": "cu://src/bar.cpp"}\n')
    truncated = pack / "source_facts" / "truncated.jsonl.gz"
    truncated.write_bytes(good.read_bytes()[:5])  # valid header, no body
    corrupt = pack / "source_facts" / "corrupt.jsonl.gz"
    corrupt.write_bytes(b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + b"not deflate data")

    diags: list[str] = []
    tus = read_source_facts(pack, diagnostics=diags)
    assert len(tus) == 2  # foo (original) + bar (good.jsonl.gz) still ingest
    assert sum("could not read/decompress" in d for d in diags) == 2


def test_read_source_facts_threads_diagnostics_sink(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    facts = pack / "source_facts" / "libfoo.jsonl"
    facts.write_text(facts.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    diags: list[str] = []
    tus = read_source_facts(pack, diagnostics=diags)
    assert len(tus) == 1
    assert diags and "skipped malformed JSON" in diags[0]


def test_ingest_surfaces_malformed_record_diagnostics(tmp_path: Path) -> None:
    # A skipped TU must not vanish silently — it rides in IngestedInputs and the
    # extractor ledger, and downgrades the record to `partial`.
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    facts = pack / "source_facts" / "libfoo.jsonl"
    facts.write_text(
        facts.read_text(encoding="utf-8") + "{bad json\n", encoding="utf-8"
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    assert ingested.diagnostics
    rec = next(
        e for e in ingested.pack.manifest.extractors if e.name == "abicheck_inputs"
    )
    assert rec.status == "partial"
    assert rec.diagnostics


def test_ingest_refuses_escaping_source_facts_path(tmp_path: Path) -> None:
    # A stale/third-party manifest pointing outside the pack must not read the
    # runner's filesystem into the baseline — the entry is refused.
    secret = tmp_path / "secret.jsonl"
    secret.write_text(json.dumps(_tu("evil", mangled="_Z4evilv").to_dict()) + "\n")
    pack = _write_inputs_pack(
        tmp_path,
        [_tu("foo", mangled="_Z3foov")],
        manifest_extra={"source_facts": ["../secret.jsonl", "source_facts"]},
    )
    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "evil" not in names  # the escaping path was refused
    assert "foo" in names  # the in-pack entry still ingested
    assert any("escaping pack root" in d for d in ingested.diagnostics)


def test_ingest_refuses_symlinked_file_inside_source_facts_dir(tmp_path: Path) -> None:
    # A file *inside* the source_facts/ dir can itself be a symlink to outside
    # the pack; the per-file resolved-path revalidation must drop it.
    outside = tmp_path / "evil.jsonl"
    outside.write_text(json.dumps(_tu("evil", mangled="_Z4evilv").to_dict()) + "\n")
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    link = pack / "source_facts" / "linked.jsonl"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("platform does not support symlinks")
    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "evil" not in names  # the symlinked escapee was refused
    assert "foo" in names
    assert any("escaping pack root" in d for d in ingested.diagnostics)


def test_ingest_skips_schema_invalid_tu_record(tmp_path: Path) -> None:
    # A valid-JSON but schema-invalid TU (entity missing required `id`) must be
    # skipped with a diagnostic, not abort the whole ingest.
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    facts = pack / "source_facts" / "libfoo.jsonl"
    bad = json.dumps({"tu_id": "cu://bad", "functions": [{}]})  # entity has no id
    facts.write_text(facts.read_text(encoding="utf-8") + bad + "\n", encoding="utf-8")
    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "foo" in names  # the good record survived
    assert any("schema-invalid TU" in d for d in ingested.diagnostics)


def test_ingest_skips_tu_record_with_malformed_entity_bucket(tmp_path: Path) -> None:
    # A hand-written/third-party record with a bucket like "functions": "bad"
    # (a string instead of a list of entity objects) is iterated character by
    # character; each character then reaches SourceEntity.from_dict's
    # d.get(...) calls and raises AttributeError ('str' has no '.get'), not
    # the KeyError/ValueError/TypeError the schema-invalid-TU path already
    # handled. That must degrade to a diagnostic like every other malformed
    # record here, not crash the whole ingest (Codex review, P2).
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    facts = pack / "source_facts" / "libfoo.jsonl"
    bad = json.dumps({"tu_id": "cu://bad", "functions": "bad"})
    facts.write_text(facts.read_text(encoding="utf-8") + bad + "\n", encoding="utf-8")
    ingested = ingest_inputs_pack(pack)  # must not raise
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "foo" in names  # the good record survived
    assert any("schema-invalid TU" in d for d in ingested.diagnostics)


def test_ingest_diagnoses_explicit_missing_source_facts(tmp_path: Path) -> None:
    # An explicitly named source_facts entry that resolves to nothing must be
    # reported (not a silent L3-only baseline) and downgrade the record.
    pack = _write_inputs_pack(
        tmp_path,
        [_tu("foo", mangled="_Z3foov")],
        compile_db=_compile_db(tmp_path),
        manifest_extra={"source_facts": ["typo_missing.jsonl"]},
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 0
    assert any("resolved to no readable fact files" in d for d in ingested.diagnostics)
    rec = next(
        e for e in ingested.pack.manifest.extractors if e.name == "abicheck_inputs"
    )
    assert rec.status == "partial"


def test_ingest_diagnoses_explicit_missing_compile_db(tmp_path: Path) -> None:
    pack = _write_inputs_pack(
        tmp_path,
        [_tu("foo", mangled="_Z3foov")],
        manifest_extra={"compile_db": "build/nope.json"},  # named but absent
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.pack.build_evidence is None
    assert any("file not found" in d for d in ingested.diagnostics)


def test_ingest_default_missing_compile_db_is_quiet(tmp_path: Path) -> None:
    # No compile_db in manifest, none on disk → no diagnostic (auto-detect miss).
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    ingested = ingest_inputs_pack(pack)
    assert ingested.pack.build_evidence is None
    assert not any("file not found" in d for d in ingested.diagnostics)


def test_ingest_refuses_absolute_compile_db(tmp_path: Path) -> None:
    outside = tmp_path / "evil_cc.json"
    outside.write_text(json.dumps(_compile_db(tmp_path)))
    pack = _write_inputs_pack(
        tmp_path,
        [_tu("foo", mangled="_Z3foov")],
        manifest_extra={"compile_db": str(outside)},  # absolute path
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.pack.build_evidence is None
    assert any("absolute path outside pack" in d for d in ingested.diagnostics)


def test_multiple_jsonl_records_ingest(tmp_path: Path) -> None:
    pack = _write_inputs_pack(
        tmp_path,
        [
            _tu("foo", mangled="_Z3foov"),
            _tu("bar", mangled="_Z3barv", source="src/bar.cpp"),
        ],
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert {"foo", "bar"} <= names


# -- merge integration (the canonical Flow-2 round-trip) ----------------------


def _artifact_snapshot(tmp_path: Path) -> Path:
    """A minimal binary-side .abi.json whose exports match the source facts.

    It is listed first to ``merge``, so it becomes the base (its ABI surface is
    kept and its exports drive the A1 relink of the ingested source surface).
    """
    from abicheck.model import AbiSnapshot, Function
    from abicheck.serialization import snapshot_to_json

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    snap.functions.append(Function(name="foo", mangled="_Z3foov", return_type="void"))
    out = tmp_path / "libfoo.bin.json"
    out.write_text(snapshot_to_json(snap), encoding="utf-8")
    return out


def test_merge_ingests_flow2_pack(tmp_path: Path) -> None:
    bin_json = _artifact_snapshot(tmp_path)
    pack = _write_inputs_pack(
        tmp_path,
        [_tu("foo", mangled="_Z3foov")],
        compile_db=_compile_db(tmp_path),
    )
    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        main, ["merge", str(bin_json), str(pack), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    baseline = load_snapshot(out)
    # Base ABI surface preserved.
    assert any(f.mangled == "_Z3foov" for f in baseline.functions)
    # Source-side L3/L4 facts folded in.
    assert baseline.build_source is not None
    assert baseline.build_source.source_abi is not None
    assert baseline.build_source.build_evidence is not None


def test_merge_relinks_surface_against_base_exports(tmp_path: Path) -> None:
    # No explicit exports in the pack → surface relinked against the base's
    # exported _Z3foov during merge (the A1 path).
    bin_json = _artifact_snapshot(tmp_path)
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        main, ["merge", str(bin_json), str(pack), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    surface = load_snapshot(out).build_source.source_abi
    assert "_Z3foov" in surface.roots["exported_symbols"]


def test_merge_rebuilds_l4_coverage_after_relink(tmp_path: Path) -> None:
    bin_json = _artifact_snapshot(tmp_path)
    pack = _write_inputs_pack(tmp_path, [_tu("bar", mangled="_Z3barv")])
    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        main, ["merge", str(bin_json), str(pack), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "matched 0/1 exported symbol" in result.output
    assert (
        "L4_source_abi: partial (0/1 symbols matched, 0/1 accounted, 1 unmatched)"
        in result.output
    )
    baseline = load_snapshot(out)
    surface = baseline.build_source.source_abi
    assert surface.coverage["exported_symbols"] == 1
    assert surface.coverage["matched_symbols"] == 0
    l4 = baseline.build_source.manifest.coverage_for(DataLayer.L4_SOURCE_ABI)
    assert l4 is not None
    assert l4.status == CoverageStatus.PARTIAL
    assert "0/1 symbols matched" in l4.detail


def test_merge_relink_preserves_non_managed_coverage_rows(tmp_path: Path) -> None:
    from abicheck.cli_buildsource_merge import _merge_attach_combined

    base = load_snapshot(_artifact_snapshot(tmp_path))
    pack = _write_inputs_pack(tmp_path, [_tu("bar", mangled="_Z3barv")])
    ingested = ingest_inputs_pack(pack)
    ingested.pack.manifest.coverage.append(
        LayerCoverage(
            layer="custom_plugin_diagnostics",
            status=CoverageStatus.PRESENT,
            detail="keep me",
        )
    )
    _merge_attach_combined(ingested.pack, base, tmp_path / "baseline.json")
    kept = base.build_source.manifest.coverage_for("custom_plugin_diagnostics")
    assert kept is not None
    assert kept.detail == "keep me"


def test_merge_warns_for_macro_only_pack_with_exports(tmp_path: Path) -> None:
    bin_json = _artifact_snapshot(tmp_path)
    pack = _write_inputs_pack(tmp_path, [_macro_tu()])
    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        main, ["merge", str(bin_json), str(pack), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert (
        "public macros/types but no public function/variable declarations"
        in result.output
    )


def test_merge_rejects_plain_directory(tmp_path: Path) -> None:
    bin_json = _artifact_snapshot(tmp_path)
    plain = tmp_path / "not_a_pack"
    plain.mkdir()
    result = CliRunner().invoke(
        main, ["merge", str(bin_json), str(plain), "-o", str(tmp_path / "o.json")]
    )
    assert result.exit_code != 0
    assert "abicheck_inputs" in result.output


def test_load_inputs_manifest_round_trips_on_disk(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    m = load_inputs_manifest(pack)
    assert m.kind == INPUTS_KIND
    assert m.library == "libfoo.so"
    assert m.created_by == "abicheck-clang-plugin 0.1"


def test_load_inputs_manifest_rejects_wrong_kind(tmp_path: Path) -> None:
    """A directory whose manifest.json declares a different (or no) `kind`
    -- e.g. a BuildSourcePack -- must be rejected, not silently accepted via
    InputsManifest.from_dict()'s forward-compat kind default. Otherwise a
    write path like compact_inputs_pack() could corrupt an unrelated pack
    (Codex review, P2)."""
    bsp = tmp_path / "pack"
    bsp.mkdir()
    (bsp / "manifest.json").write_text(json.dumps({"build_source_pack_version": 1}))
    with pytest.raises(ValueError, match="does not declare kind"):
        load_inputs_manifest(bsp)
