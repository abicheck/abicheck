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

"""Round-trip, edge-case, and adapter-path coverage for the EvidencePack
modules (ADR-028 / ADR-029). Complements test_evidence_pack.py and
test_evidence_cli.py."""
from __future__ import annotations

import json

from click.testing import CliRunner

from abicheck.checker_policy import ChangeKind
from abicheck.cli import main
from abicheck.evidence.adapters import CMakeFileApiAdapter, NinjaAdapter
from abicheck.evidence.adapters.base import derive_build_options
from abicheck.evidence.build_diff import diff_build_evidence
from abicheck.evidence.build_evidence import (
    BuildEvidence,
    BuildOption,
    CompileUnit,
    Confidence,
    Generator,
    LinkUnit,
    Target,
    TargetKind,
    Toolchain,
)
from abicheck.evidence.model import (
    CoverageStatus,
    EvidenceConfidence,
    EvidenceEntity,
    EvidenceLayer,
    EvidencePackManifest,
    ExtractorRecord,
    LayerCoverage,
)
from abicheck.evidence.pack import EvidencePack

# ── BuildEvidence model round-trips (ADR-029 D1/D2) ──────────────────────────


def test_build_evidence_full_roundtrip():
    ev = BuildEvidence(
        source_root="repo://root",
        build_root="build://root",
        generators=[Generator(kind="cmake", version="3.28", generator="Ninja")],
        toolchains=[Toolchain(id="t", compiler_id="GNU", version="14", language="CXX",
                              implicit_include_dirs=["/usr/include"], target_triple="x86_64-linux-gnu")],
        targets=[Target(id="target://foo", name="foo", kind=TargetKind.SHARED_LIBRARY,
                        build_system="cmake", source_files=["a.cpp"], public_headers=["a.h"],
                        outputs=["libfoo.so"], dependencies=["target://bar"],
                        visibility="public", confidence=Confidence.HIGH)],
        compile_units=[CompileUnit(id="cu://a", source="a.cpp", language="CXX", standard="c++20",
                                   defines={"FOO": "1"}, include_paths=["inc"],
                                   abi_relevant_flags=["-std=c++20"])],
        link_units=[LinkUnit(id="link://foo", target_id="target://foo", output="libfoo.so",
                             version_script="exports.map", soname="libfoo.so.1")],
        generated_files=["gen.h"],
        build_options=[BuildOption(key="std:CXX", value="c++20", abi_relevant=True, raw="-std=c++20")],
        diagnostics=["note"],
        raw_artifacts=["raw/x"],
    )
    ev2 = BuildEvidence.from_dict(json.loads(json.dumps(ev.to_dict())))
    assert ev2.to_dict() == ev.to_dict()
    assert ev2.targets[0].kind is TargetKind.SHARED_LIBRARY
    assert ev2.toolchains[0].implicit_include_dirs == ["/usr/include"]
    assert ev2.link_units[0].soname == "libfoo.so.1"


def test_target_kind_and_confidence_unknown_fallback():
    t = Target.from_dict({"id": "x", "kind": "bogus", "confidence": "weird"})
    assert t.kind is TargetKind.UNKNOWN
    assert t.confidence is Confidence.UNKNOWN


def test_build_evidence_merge_combines_options_and_dedups_generated():
    a = BuildEvidence(generated_files=["g1"], build_options=[BuildOption("std:C", "c11")])
    b = BuildEvidence(generated_files=["g1", "g2"], build_options=[BuildOption("std:CXX", "c++20")])
    a.merge(b)
    assert a.generated_files == ["g1", "g2"]
    assert {o.key for o in a.build_options} == {"std:C", "std:CXX"}


def test_derive_build_options_dedups_across_units():
    units = [
        CompileUnit(id="1", language="CXX", standard="c++20", abi_relevant_flags=["-fvisibility=hidden"]),
        CompileUnit(id="2", language="CXX", standard="c++20", abi_relevant_flags=["-fvisibility=hidden"]),
        CompileUnit(id="3", language="CXX", standard="c++20", target_triple="x86_64-linux-gnu",
                    sysroot="/sdk"),
    ]
    opts = derive_build_options(units)
    keys = {o.key for o in opts}
    assert keys == {"std:CXX", "sysroot", "target", "-fvisibility"}
    # -fvisibility=hidden appeared in two units but records once.
    assert sum(o.key == "-fvisibility" for o in opts) == 1


# ── model.py round-trips & fallbacks (ADR-028 D5/D7) ─────────────────────────


def test_evidence_entity_roundtrip():
    e = EvidenceEntity(
        entity_id="sha256:1", kind="function",
        names={"mangled": "_Z3foov"}, locations=[{"path": "a.h", "line": 1}],
        binary_refs=["elf:symbol:_Z3foov"], build_refs=["target://foo"],
        confidence=EvidenceConfidence.HIGH,
    )
    e2 = EvidenceEntity.from_dict(e.to_dict())
    assert e2.to_dict() == e.to_dict()


def test_extractor_record_roundtrip():
    r = ExtractorRecord(name="ninja", version="1.12", status="partial",
                        inputs=["build/"], artifacts=["raw/x"], detail="fallback")
    assert ExtractorRecord.from_dict(r.to_dict()).to_dict() == r.to_dict()


def test_layer_coverage_present_and_roundtrip():
    c = LayerCoverage(layer="L3_build", status=CoverageStatus.PARTIAL,
                      confidence=EvidenceConfidence.REDUCED, detail="changed only")
    assert c.present is True
    c2 = LayerCoverage.from_dict(c.to_dict())
    assert c2.status is CoverageStatus.PARTIAL and c2.present


def test_layer_coverage_invalid_enums_fall_back():
    c = LayerCoverage.from_dict({"layer": "L3_build", "status": "bogus", "confidence": "weird"})
    assert c.status is CoverageStatus.NOT_COLLECTED
    assert c.confidence is EvidenceConfidence.UNKNOWN
    assert c.present is False


def test_manifest_coverage_for_lookup():
    m = EvidencePackManifest(coverage=[LayerCoverage(layer="L3_build", status=CoverageStatus.PRESENT)])
    assert m.coverage_for(EvidenceLayer.L3_BUILD) is not None
    assert m.coverage_for(EvidenceLayer.L4_SOURCE_ABI) is None
    assert m.coverage_for("L3_build") is not None


# ── pack.py with build evidence ──────────────────────────────────────────────


def test_pack_load_with_build_evidence(tmp_path):
    pack = EvidencePack.empty(tmp_path / "p")
    pack.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://a", source="a.cpp", language="CXX", standard="c++20")],
        build_options=[BuildOption("std:CXX", "c++20", abi_relevant=True)],
    )
    pack.write()
    loaded = EvidencePack.load(tmp_path / "p")
    assert loaded.build_evidence is not None
    assert loaded.build_evidence.compile_units[0].standard == "c++20"
    # The build evidence contributes an artifact digest to the content hash.
    assert loaded.manifest.artifacts
    assert loaded.content_hash().startswith("sha256:")


def test_pack_rewrite_removes_stale_build_evidence(tmp_path):
    """Codex P2: a rerun with no build evidence must drop the old L3 file."""
    root = tmp_path / "p"
    first = EvidencePack.empty(root)
    first.build_evidence = BuildEvidence(
        compile_units=[CompileUnit(id="cu://a", source="a.cpp", language="CXX", standard="c++20")],
    )
    first.write()
    assert (root / "build" / "build_evidence.json").is_file()

    # Rerun into the same directory producing no build evidence.
    second = EvidencePack.empty(root)
    second.build_evidence = None
    second.write()
    assert not (root / "build" / "build_evidence.json").exists()
    assert EvidencePack.load(root).build_evidence is None


def test_pack_to_ref_coverage_summary(tmp_path):
    pack = EvidencePack.empty(tmp_path / "p")
    pack.manifest.coverage = [LayerCoverage(layer="L3_build", status=CoverageStatus.PRESENT,
                                            confidence=EvidenceConfidence.HIGH)]
    pack.write()
    ref = pack.to_ref()
    assert ref.coverage_summary["L3_build"]["status"] == "present"


# ── CMake adapter additional paths (ADR-029 D4) ──────────────────────────────


def test_cmake_no_index(tmp_path):
    reply = tmp_path / "build" / ".cmake" / "api" / "v1" / "reply"
    reply.mkdir(parents=True)
    ev = CMakeFileApiAdapter(tmp_path / "build").collect()
    assert any("no index" in d for d in ev.diagnostics)


def test_cmake_header_extension_fallback_without_filesets(tmp_path):
    reply = tmp_path / "build" / ".cmake" / "api" / "v1" / "reply"
    reply.mkdir(parents=True)
    (reply / "codemodel-v2-x.json").write_text(json.dumps({
        "configurations": [{"targets": [{"jsonFile": "t.json"}]}],
    }))
    (reply / "t.json").write_text(json.dumps({
        "name": "foo", "type": "STATIC_LIBRARY",
        "sources": [{"path": "a.cpp"}, {"path": "a.h"}],
    }))
    (reply / "index-x.json").write_text(json.dumps({
        "cmake": {"version": {"string": "3.28"}},
        "objects": [{"kind": "codemodel", "jsonFile": "codemodel-v2-x.json"}],
    }))
    ev = CMakeFileApiAdapter(tmp_path / "build").collect()
    t = ev.targets[0]
    assert t.kind is TargetKind.STATIC_LIBRARY
    assert t.private_headers == ["a.h"]  # extension heuristic
    assert t.source_files == ["a.cpp"]
    assert t.visibility == "private"


# ── Ninja adapter additional paths (ADR-029 D5) ──────────────────────────────


def test_ninja_command_string_and_bad_entries(tmp_path):
    compdb = json.dumps([
        {"directory": str(tmp_path), "file": "a.cpp", "command": "c++ -std=c++17 -c a.cpp"},
        "not-a-dict",
        {"directory": str(tmp_path)},  # no file
    ])
    ev = NinjaAdapter(compdb=compdb).collect()
    assert len(ev.compile_units) == 1
    assert ev.compile_units[0].standard == "c++17"


def test_ninja_malformed_json_diagnostic():
    ev = NinjaAdapter(compdb="{not json").collect()
    assert any("could not parse" in d for d in ev.diagnostics)


def test_ninja_non_array_diagnostic():
    ev = NinjaAdapter(compdb='{"a": 1}').collect()
    assert any("not a JSON array" in d for d in ev.diagnostics)


def test_ninja_precaptured_graph_diagnostic(tmp_path):
    compdb = json.dumps([{"directory": str(tmp_path), "file": "a.cpp",
                          "arguments": ["c++", "-c", "a.cpp"]}])
    ev = NinjaAdapter(compdb=compdb, graph="digraph { a -> b }").collect()
    assert any("dependency graph captured" in d for d in ev.diagnostics)


def test_ninja_live_query_disabled_diagnostic(tmp_path):
    ev = NinjaAdapter(build_dir=tmp_path, allow_query=False).collect()
    assert any("live query disabled" in d for d in ev.diagnostics)


def test_ninja_executable_missing_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr("abicheck.evidence.adapters.ninja.shutil.which", lambda _x: None)
    ev = NinjaAdapter(build_dir=tmp_path, allow_query=True).collect()
    assert any("executable not found" in d for d in ev.diagnostics)


def test_ninja_query_invokes_subprocess(tmp_path, monkeypatch):
    """Drive the live-query path with a stubbed ninja that returns a compdb."""
    import subprocess as _sp

    compdb = json.dumps([{"directory": str(tmp_path), "file": "a.cpp",
                          "arguments": ["c++", "-std=c++20", "-c", "a.cpp"]}])

    def fake_run(cmd, **kwargs):
        out = compdb if "compdb" in cmd else "digraph {}"
        return _sp.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr("abicheck.evidence.adapters.ninja.shutil.which", lambda _x: "/usr/bin/ninja")
    monkeypatch.setattr("abicheck.evidence.adapters.ninja.subprocess.run", fake_run)
    ev = NinjaAdapter(build_dir=tmp_path).collect()
    assert len(ev.compile_units) == 1
    assert any(o.key == "std:CXX" for o in ev.build_options)


def test_ninja_query_nonzero_exit_diagnostic(tmp_path, monkeypatch):
    import subprocess as _sp

    def fake_run(cmd, **kwargs):
        return _sp.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr("abicheck.evidence.adapters.ninja.shutil.which", lambda _x: "/usr/bin/ninja")
    monkeypatch.setattr("abicheck.evidence.adapters.ninja.subprocess.run", fake_run)
    ev = NinjaAdapter(build_dir=tmp_path).collect()
    assert any("exited 1" in d for d in ev.diagnostics)


# ── build_diff additional branches (ADR-029 D9) ──────────────────────────────


def test_diff_option_added_and_removed():
    old = BuildEvidence(build_options=[BuildOption("warn", "on")])
    new = BuildEvidence(build_options=[BuildOption("opt", "fast")])
    kinds = {c.kind for c in diff_build_evidence(old, new)}
    # 'warn' removed and 'opt' added — both non-abi → build_context_changed.
    assert ChangeKind.BUILD_CONTEXT_CHANGED in kinds


def test_diff_no_change_when_equal():
    ev = BuildEvidence(build_options=[BuildOption("std:CXX", "c++20", abi_relevant=True)])
    import copy
    assert diff_build_evidence(ev, copy.deepcopy(ev)) == []


def test_derive_build_options_captures_msvc_std_flag():
    """Codex: MSVC /std: is normalized into the std:<lang> option (not dropped).

    `_extract_flags` only fills cu.standard from GCC `-std=`, so without this the
    /std: change would be invisible on Windows/MSVC builds.
    """
    old = derive_build_options([CompileUnit(
        id="1", language="CXX", standard="", abi_relevant_flags=["/std:c++17"],
    )])
    new = derive_build_options([CompileUnit(
        id="1", language="CXX", standard="", abi_relevant_flags=["/std:c++20"],
    )])
    assert {(o.key, o.value) for o in old} == {("std:CXX", "c++17")}
    assert {(o.key, o.value) for o in new} == {("std:CXX", "c++20")}
    changes = diff_build_evidence(BuildEvidence(build_options=old), BuildEvidence(build_options=new))
    assert any(
        c.kind is ChangeKind.ABI_RELEVANT_BUILD_FLAG_CHANGED
        and c.old_value == "c++17" and c.new_value == "c++20"
        for c in changes
    )


def test_derive_build_options_gcc_std_no_double_emit():
    """GCC -std= is captured once via the structured field, not duplicated."""
    opts = derive_build_options([CompileUnit(
        id="1", language="CXX", standard="c++20", abi_relevant_flags=["-std=c++20"],
    )])
    assert sum(o.key == "std:CXX" for o in opts) == 1


def test_derive_build_options_skips_structurally_captured_flags():
    """Codex: split vs combined sysroot/target must not double-count.

    --sysroot/-target are captured as the normalized structured sysroot/target
    options; the raw flag (split or combined spelling) must not also appear, or
    an identical build looks changed.
    """
    split = derive_build_options([CompileUnit(
        id="1", language="CXX", sysroot="/sdk", target_triple="x86_64-linux-gnu",
        abi_relevant_flags=["--sysroot", "-target"],
    )])
    combined = derive_build_options([CompileUnit(
        id="1", language="CXX", sysroot="/sdk", target_triple="x86_64-linux-gnu",
        abi_relevant_flags=["--sysroot=/sdk", "--target=x86_64-linux-gnu"],
    )])
    assert {(o.key, o.value) for o in split} == {("sysroot", "/sdk"), ("target", "x86_64-linux-gnu")}
    assert {(o.key, o.value) for o in split} == {(o.key, o.value) for o in combined}
    old = BuildEvidence(build_options=split)
    new = BuildEvidence(build_options=combined)
    assert diff_build_evidence(old, new) == []


def test_diff_preserves_multiple_values_for_same_key():
    """Codex P2: a removed variant of a multi-config option is not masked."""
    old = BuildEvidence(build_options=[
        BuildOption("std:CXX", "c++17", abi_relevant=True),
        BuildOption("std:CXX", "c++20", abi_relevant=True),
    ])
    new = BuildEvidence(build_options=[BuildOption("std:CXX", "c++20", abi_relevant=True)])
    changes = diff_build_evidence(old, new)
    assert len(changes) == 1
    c = changes[0]
    assert c.kind is ChangeKind.ABI_RELEVANT_BUILD_FLAG_CHANGED
    assert c.old_value == "c++17, c++20"
    assert c.new_value == "c++20"


def test_diff_multi_value_order_independent():
    """Same value sets in different unit order produce no finding."""
    a = BuildEvidence(build_options=[
        BuildOption("std:CXX", "c++17", abi_relevant=True),
        BuildOption("std:CXX", "c++20", abi_relevant=True),
    ])
    b = BuildEvidence(build_options=[
        BuildOption("std:CXX", "c++20", abi_relevant=True),
        BuildOption("std:CXX", "c++17", abi_relevant=True),
    ])
    assert diff_build_evidence(a, b) == []


# ── collect-evidence CLI variants (ADR-028 D6) ───────────────────────────────


def test_collect_evidence_empty_pack_when_no_adapters(tmp_path):
    out = tmp_path / "e"
    result = CliRunner().invoke(main, ["collect-evidence", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "not collected" in result.output
    pack = EvidencePack.load(out)
    assert pack.build_evidence is None
    cov = pack.manifest.coverage_for("L3_build")
    assert cov is not None and cov.status is CoverageStatus.NOT_COLLECTED


def test_collect_evidence_failed_compile_db_records_extractor(tmp_path):
    bad = tmp_path / "missing.json"
    out = tmp_path / "e"
    result = CliRunner().invoke(main, ["collect-evidence", "--compile-db", str(bad), "-o", str(out)])
    # The adapter failure is recorded as a diagnostic/extractor status, not a crash.
    assert result.exit_code == 0, result.output
    pack = EvidencePack.load(out)
    assert any(e.name == "compile_commands" and e.status == "failed"
               for e in pack.manifest.extractors)


def test_collect_evidence_ninja_compdb(tmp_path):
    compdb = tmp_path / "compdb.json"
    compdb.write_text(json.dumps([{"directory": str(tmp_path), "file": "a.cpp",
                                   "arguments": ["c++", "-std=c++20", "-c", "a.cpp"]}]))
    out = tmp_path / "e"
    result = CliRunner().invoke(main, ["collect-evidence", "--ninja-compdb", str(compdb), "-o", str(out)])
    assert result.exit_code == 0, result.output
    pack = EvidencePack.load(out)
    assert pack.build_evidence is not None
    assert any(o.key == "std:CXX" for o in pack.build_evidence.build_options)


def test_compare_drift_fires_without_compile_db_context(tmp_path):
    """Codex fix: compare has no -p, so a new pack with ABI flags discloses
    HEADER_PARSE_CONTEXT_DRIFT even when -H headers are passed."""
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import save_snapshot

    new_cdb = tmp_path / "cc.json"
    new_cdb.write_text(json.dumps([{"directory": str(tmp_path), "file": "a.cpp",
                                    "arguments": ["c++", "-std=c++20", "-c", "a.cpp"]}]))
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect-evidence", "--compile-db", str(new_cdb), "-o", str(ev_new)])

    for v in ("old", "new"):
        save_snapshot(AbiSnapshot(library="libfoo.so", version=v, from_headers=True),
                      tmp_path / f"{v}.json")

    result = runner.invoke(main, [
        "compare", str(tmp_path / "old.json"), str(tmp_path / "new.json"),
        "-H", str(tmp_path),  # headers present, but NOT parsed with -p
        "--new-evidence", str(ev_new), "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    assert "header_parse_context_drift" in result.stdout


def test_compare_drift_suppressed_when_dumped_with_build_context(tmp_path):
    """A snapshot dumped with `-p` (parsed_with_build_context) suppresses drift."""
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import save_snapshot

    new_cdb = tmp_path / "cc.json"
    new_cdb.write_text(json.dumps([{"directory": str(tmp_path), "file": "a.cpp",
                                    "arguments": ["c++", "-std=c++20", "-c", "a.cpp"]}]))
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect-evidence", "--compile-db", str(new_cdb), "-o", str(ev_new)])

    # New side was dumped WITH the build's compile DB → no drift.
    save_snapshot(AbiSnapshot(library="libfoo.so", version="old", from_headers=True),
                  tmp_path / "old.json")
    save_snapshot(
        AbiSnapshot(library="libfoo.so", version="new", from_headers=True,
                    parsed_with_build_context=True),
        tmp_path / "new.json",
    )
    result = runner.invoke(main, [
        "compare", str(tmp_path / "old.json"), str(tmp_path / "new.json"),
        "--new-evidence", str(ev_new), "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    assert "header_parse_context_drift" not in result.stdout


def test_compare_binary_only_skips_header_drift(tmp_path):
    """Codex: a binary-only new side (no header AST) must NOT emit drift."""
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import save_snapshot

    new_cdb = tmp_path / "cc.json"
    new_cdb.write_text(json.dumps([{"directory": str(tmp_path), "file": "a.cpp",
                                    "arguments": ["c++", "-std=c++20", "-c", "a.cpp"]}]))
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect-evidence", "--compile-db", str(new_cdb), "-o", str(ev_new)])

    # Binary-only snapshots: from_headers is False, so there is no L2 AST.
    for v in ("old", "new"):
        save_snapshot(AbiSnapshot(library="libfoo.so", version=v, from_headers=False),
                      tmp_path / f"{v}.json")

    result = runner.invoke(main, [
        "compare", str(tmp_path / "old.json"), str(tmp_path / "new.json"),
        "--new-evidence", str(ev_new), "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    assert "header_parse_context_drift" not in result.stdout
