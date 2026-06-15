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

import json
from pathlib import Path

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
from abicheck.buildsource.model import CoverageStatus, DataLayer
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
        source_location=SourceLocation(path=f"include/{name}.h", line=3, origin="PUBLIC_HEADER"),
        visibility="public_header",
    )
    return SourceAbiTu(
        tu_id=f"cu://{source}#cfg:abc",
        target_id="target://libfoo",
        source=source,
        public_header_roots=[f"include/{name}.h"],
        functions=[ent],
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
        (pack / "source_facts" / "libfoo.jsonl").write_text(lines + "\n", encoding="utf-8")
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
            "arguments": ["c++", "-std=c++17", "-DNDEBUG", "-Iinclude", "-c", "src/foo.cpp"],
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
    statuses = {
        c.layer: c.status for c in ingested.pack.manifest.coverage
    }
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
    rec = next(e for e in ingested.pack.manifest.extractors if e.name == "abicheck_inputs")
    assert "abicheck-clang-plugin" in rec.detail


# -- source-fact parsing tolerance -------------------------------------------


def test_read_source_facts_accepts_json_array_form(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")], jsonl=False)
    tus = read_source_facts(pack)
    assert len(tus) == 1
    assert tus[0].functions[0].qualified_name == "foo"


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
    facts.write_text(facts.read_text(encoding="utf-8") + "{bad json\n", encoding="utf-8")
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    assert ingested.diagnostics
    rec = next(e for e in ingested.pack.manifest.extractors if e.name == "abicheck_inputs")
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
        [_tu("foo", mangled="_Z3foov"), _tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
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
    result = CliRunner().invoke(main, ["merge", str(bin_json), str(pack), "-o", str(out)])
    assert result.exit_code == 0, result.output
    surface = load_snapshot(out).build_source.source_abi
    assert "_Z3foov" in surface.roots["exported_symbols"]


def test_merge_rejects_plain_directory(tmp_path: Path) -> None:
    bin_json = _artifact_snapshot(tmp_path)
    plain = tmp_path / "not_a_pack"
    plain.mkdir()
    result = CliRunner().invoke(main, ["merge", str(bin_json), str(plain), "-o", str(tmp_path / "o.json")])
    assert result.exit_code != 0
    assert "abicheck_inputs" in result.output


def test_load_inputs_manifest_round_trips_on_disk(tmp_path: Path) -> None:
    pack = _write_inputs_pack(tmp_path, [_tu("foo", mangled="_Z3foov")])
    m = load_inputs_manifest(pack)
    assert m.kind == INPUTS_KIND
    assert m.library == "libfoo.so"
    assert m.created_by == "abicheck-clang-plugin 0.1"
