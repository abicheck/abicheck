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

"""Tests for the optional source/build BuildSourcePack (ADR-028) and the
build-evidence adapters/diff (ADR-029)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.buildsource import (
    BuildEvidence,
    BuildSourceManifest,
    BuildSourcePack,
    BuildSourceRef,
)
from abicheck.buildsource.adapters import (
    CMakeFileApiAdapter,
    CompileDbAdapter,
    NinjaAdapter,
    compile_unit_id,
    detect_language,
    extract_abi_relevant_flags,
)
from abicheck.buildsource.build_diff import (
    check_header_parse_drift,
    diff_build_evidence,
)
from abicheck.buildsource.build_evidence import (
    BuildOption,
    Generator,
    LinkUnit,
    Toolchain,
)
from abicheck.buildsource.model import CoverageStatus, DataLayer
from abicheck.buildsource.redaction import RedactionPolicy
from abicheck.checker_policy import (
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,
)
from abicheck.model import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

# ── Pack model & content addressing ──────────────────────────────────────────


def test_empty_pack_write_load_roundtrip(tmp_path):
    pack = BuildSourcePack.empty(tmp_path / "p.evidence", abicheck_version="9.9", created_at="t0")
    pack.write()
    loaded = BuildSourcePack.load(tmp_path / "p.evidence")
    assert loaded.manifest.abicheck_version == "9.9"
    # All standard subdirs were created.
    for sub in ("build", "source", "graph", "toolchain", "raw", "normalized"):
        assert (tmp_path / "p.evidence" / sub).is_dir()


def test_load_missing_manifest_raises(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        BuildSourcePack.load(tmp_path / "empty")


def test_content_hash_is_stable_across_created_at(tmp_path):
    """Two packs with identical evidence but different timestamps hash equal."""
    p1 = BuildSourcePack.empty(tmp_path / "a", created_at="2026-01-01")
    p1.write()
    p2 = BuildSourcePack.empty(tmp_path / "b", created_at="2099-12-31")
    p2.write()
    assert p1.content_hash() == p2.content_hash()
    assert p1.content_hash().startswith("sha256:")


def test_content_hash_changes_with_build_evidence(tmp_path):
    p1 = BuildSourcePack.empty(tmp_path / "a")
    p1.write()
    p2 = BuildSourcePack.empty(tmp_path / "b")
    p2.build_evidence = BuildEvidence(build_options=[BuildOption(key="std:CXX", value="c++20")])
    p2.write()
    assert p1.content_hash() != p2.content_hash()


def test_to_ref_roundtrip(tmp_path):
    pack = BuildSourcePack.empty(tmp_path / "p")
    pack.manifest.coverage = []
    pack.write()
    ref = pack.to_ref(path_hint="p.evidence/")
    ref2 = BuildSourceRef.from_dict(ref.to_dict())
    assert ref2.content_hash == ref.content_hash
    assert ref2.path_hint == "p.evidence/"


def test_manifest_dict_roundtrip():
    m = BuildSourceManifest(abicheck_version="1.0")
    m2 = BuildSourceManifest.from_dict(m.to_dict())
    assert m2.abicheck_version == "1.0"
    assert m2.build_source_pack_version == m.build_source_pack_version


def test_unknown_manifest_keys_are_ignored():
    """A newer pack with unknown keys still loads (forward-compat)."""
    raw = {"build_source_pack_version": 1, "future_field": {"x": 1}, "coverage": []}
    m = BuildSourceManifest.from_dict(raw)
    assert m.build_source_pack_version == 1


# ── Snapshot integration (schema v7) ─────────────────────────────────────────


def test_snapshot_v7_evidence_ref_roundtrip():
    snap = AbiSnapshot(
        library="libfoo.so", version="1.0",
        build_source_pack=BuildSourceRef(content_hash="sha256:abc", path_hint="e/"),
    )
    d = snapshot_to_dict(snap)
    assert d["schema_version"] == 8
    assert d["build_source_pack"]["content_hash"] == "sha256:abc"
    back = snapshot_from_dict(d)
    assert back.build_source_pack is not None
    assert back.build_source_pack.content_hash == "sha256:abc"


def test_snapshot_without_evidence_serializes_none():
    snap = AbiSnapshot(library="l", version="1")
    d = snapshot_to_dict(snap)
    assert d["build_source_pack"] is None
    assert snapshot_from_dict(d).build_source_pack is None


def test_legacy_v6_snapshot_without_evidence_key_loads():
    """Backward-compat (ADR-015): a v6 snapshot has no build_source_pack key."""
    d = snapshot_to_dict(AbiSnapshot(library="l", version="1"))
    d["schema_version"] = 6
    d.pop("build_source_pack", None)
    assert snapshot_from_dict(d).build_source_pack is None


def test_malformed_evidence_ref_is_ignored():
    d = snapshot_to_dict(AbiSnapshot(library="l", version="1"))
    d["build_source_pack"] = "not-a-dict"
    assert snapshot_from_dict(d).build_source_pack is None


def test_legacy_evidence_pack_key_still_loads():
    """Snapshots written before the evidence→buildsource rename store the ref
    under the legacy ``evidence_pack`` key; it must still load (back-compat)."""
    d = snapshot_to_dict(AbiSnapshot(library="l", version="1"))
    d.pop("build_source_pack", None)
    d["evidence_pack"] = {"content_hash": "sha256:legacy", "path_hint": "old.evidence/"}
    back = snapshot_from_dict(d)
    assert back.build_source_pack is not None
    assert back.build_source_pack.content_hash == "sha256:legacy"


def test_new_key_wins_over_legacy_evidence_pack_key():
    """If both keys are present the new ``build_source_pack`` takes precedence."""
    d = snapshot_to_dict(AbiSnapshot(
        library="l", version="1",
        build_source_pack=BuildSourceRef(content_hash="sha256:new", path_hint="n/"),
    ))
    d["evidence_pack"] = {"content_hash": "sha256:legacy", "path_hint": "old/"}
    assert snapshot_from_dict(d).build_source_pack.content_hash == "sha256:new"


# ── Inline embedding (single-artifact UX, ADR-028 D8) ─────────────────────────


def test_embedded_dict_roundtrip(tmp_path):
    """to_embedded_dict/from_embedded_dict preserve the normalized facts."""
    pack = BuildSourcePack.empty(tmp_path / "p")
    pack.build_evidence = BuildEvidence(
        build_options=[BuildOption(key="std:CXX", value="c++20")]
    )
    embedded = pack.to_embedded_dict()
    assert "manifest" in embedded
    assert embedded["build_evidence"]["build_options"][0]["value"] == "c++20"

    back = BuildSourcePack.from_embedded_dict(embedded)
    assert back.root == Path("")
    assert back.build_evidence is not None
    assert back.build_evidence.build_options[0].key == "std:CXX"
    assert back.source_abi is None
    assert back.source_graph is None


def test_snapshot_embedded_build_source_roundtrips(tmp_path):
    """A snapshot carrying embedded build/source facts survives (de)serialization."""
    pack = BuildSourcePack.empty(tmp_path / "p")
    pack.build_evidence = BuildEvidence(
        build_options=[BuildOption(key="fvisibility", value="hidden")]
    )
    snap = AbiSnapshot(library="libfoo.so", version="1.0", build_source=pack)
    d = snapshot_to_dict(snap)
    assert d["build_source"]["build_evidence"]["build_options"][0]["value"] == "hidden"

    back = snapshot_from_dict(d)
    assert back.build_source is not None
    assert back.build_source.build_evidence.build_options[0].key == "fvisibility"


def test_snapshot_without_embedded_build_source_omits_key():
    d = snapshot_to_dict(AbiSnapshot(library="l", version="1"))
    assert "build_source" not in d
    assert snapshot_from_dict(d).build_source is None


def test_embed_filters_coverage_to_layers_actually_embedded(tmp_path):
    """Attaching only --build-info from a pack that also collected source ABI
    must not let the embedded manifest advertise L4 as present."""
    from abicheck.buildsource.model import LayerCoverage
    from abicheck.cli_buildsource import embed_build_source

    pack = BuildSourcePack.empty(tmp_path / "p")
    pack.build_evidence = BuildEvidence(build_options=[BuildOption(key="std:CXX", value="c++20")])
    # The pack's manifest claims L3 *and* L4 coverage, but we only attach L3.
    pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.PRESENT),
    ]
    pack.write()

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    embed_build_source(snap, build_info=tmp_path / "p", sources=None)

    assert snap.build_source is not None
    cov = {c.layer: c for c in snap.build_source.manifest.coverage}
    assert cov[DataLayer.L3_BUILD.value].status == CoverageStatus.PRESENT
    # L4 was advertised by the source pack but not actually embedded → the row
    # is kept (ADR-028 D7 shows every layer) but downgraded to not_collected,
    # never left claiming "present".
    assert cov[DataLayer.L4_SOURCE_ABI.value].status == CoverageStatus.NOT_COLLECTED


def test_embed_merges_coverage_from_both_packs(tmp_path):
    """--build-info and --sources pointing at *different* packs must yield an
    embedded manifest advertising every layer that was actually embedded."""
    from abicheck.buildsource.model import ExtractorRecord, LayerCoverage
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_buildsource import embed_build_source

    bi = BuildSourcePack.empty(tmp_path / "bi")
    bi.build_evidence = BuildEvidence(build_options=[BuildOption(key="std:CXX", value="c++20")])
    bi.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT),
    ]
    bi.manifest.extractors = [ExtractorRecord(name="compile-db", version="1")]
    bi.write()

    src = BuildSourcePack.empty(tmp_path / "src")
    src.source_abi = SourceAbiSurface(library="libfoo.so")
    src.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.PRESENT),
    ]
    src.manifest.extractors = [ExtractorRecord(name="clang-source", version="2")]
    src.write()

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    embed_build_source(snap, build_info=tmp_path / "bi", sources=tmp_path / "src")

    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert snap.build_source.source_abi is not None
    layers = {c.layer for c in snap.build_source.manifest.coverage}
    assert DataLayer.L3_BUILD.value in layers
    assert DataLayer.L4_SOURCE_ABI.value in layers  # from the *other* pack
    # Provenance from BOTH packs must survive into the combined ref (Codex):
    # extractors are kept verbatim; each pack contributes its own normalized
    # artifact digest, so the merged manifest carries both.
    names = {e.name for e in snap.build_source.manifest.extractors}
    assert names == {"compile-db", "clang-source"}
    assert len(set(snap.build_source.manifest.artifacts)) == 2


def test_legacy_changekind_value_still_parses():
    """A report/policy written before the evidence→buildsource rename used
    'evidence_coverage_asymmetric'; it must still deserialize."""
    assert ChangeKind("evidence_coverage_asymmetric") is ChangeKind.EVIDENCE_COVERAGE_ASYMMETRIC
    assert ChangeKind("layer_coverage_asymmetric") is ChangeKind.EVIDENCE_COVERAGE_ASYMMETRIC


def test_legacy_manifest_version_key_still_loads():
    m = BuildSourceManifest.from_dict({"evidence_pack_version": 1, "coverage": []})
    assert m.build_source_pack_version == 1


# ── compile_commands.json adapter (ADR-029 D3) ───────────────────────────────


def _write_cdb(tmp_path, entries):
    p = tmp_path / "compile_commands.json"
    p.write_text(json.dumps(entries))
    return p


def test_compile_db_adapter_normalizes_units(tmp_path):
    cdb = _write_cdb(tmp_path, [
        {"directory": str(tmp_path), "file": "src/foo.cpp",
         "arguments": ["c++", "-std=c++20", "-DFOO=1", "-Iinclude", "-c", "src/foo.cpp"]},
        {"directory": str(tmp_path), "file": "src/bar.c",
         "command": "cc -std=c11 -Iinclude -c src/bar.c"},
    ])
    ev = CompileDbAdapter(cdb, build_system="cmake").collect()
    assert len(ev.compile_units) == 2
    cxx = next(c for c in ev.compile_units if c.language == "CXX")
    assert cxx.standard == "c++20"
    assert cxx.defines.get("FOO") == "1"
    assert "-std=c++20" in cxx.abi_relevant_flags
    # Per-language standard keys keep C and C++ distinct.
    keys = {o.key for o in ev.build_options}
    assert "std:CXX" in keys and "std:C" in keys


def test_compile_db_supports_command_string_form(tmp_path):
    cdb = _write_cdb(tmp_path, [
        {"directory": str(tmp_path), "file": "a.cpp", "command": "c++ -std=c++17 -c a.cpp"},
    ])
    ev = CompileDbAdapter(cdb).collect()
    assert ev.compile_units[0].standard == "c++17"


def test_glibcxx_abi_define_is_abi_relevant(tmp_path):
    cdb = _write_cdb(tmp_path, [
        {"directory": str(tmp_path), "file": "a.cpp",
         "arguments": ["c++", "-D_GLIBCXX_USE_CXX11_ABI=0", "-c", "a.cpp"]},
    ])
    ev = CompileDbAdapter(cdb).collect()
    keys = {o.key for o in ev.build_options}
    assert "define:_GLIBCXX_USE_CXX11_ABI" in keys


# ── Ninja adapter (ADR-029 D5) ───────────────────────────────────────────────


def test_ninja_adapter_from_precaptured_compdb(tmp_path):
    compdb = json.dumps([
        {"directory": str(tmp_path), "file": "a.cpp", "output": "a.o",
         "arguments": ["c++", "-std=c++20", "-c", "a.cpp"]},
        # A non-compiler statement (no recognized source ext) is filtered out.
        {"directory": str(tmp_path), "file": "link.stamp", "arguments": ["touch", "link.stamp"]},
    ])
    ev = NinjaAdapter(compdb=compdb).collect()
    assert len(ev.compile_units) == 1
    assert ev.compile_units[0].standard == "c++20"
    assert any(g.kind == "ninja" for g in ev.generators)


def test_ninja_adapter_no_input_emits_diagnostic():
    ev = NinjaAdapter(build_dir=None).collect()
    assert ev.compile_units == []
    assert any("no compdb" in d for d in ev.diagnostics)


# ── CMake File API adapter (ADR-029 D4) ──────────────────────────────────────


def _make_cmake_reply(build_dir):
    reply = build_dir / ".cmake" / "api" / "v1" / "reply"
    reply.mkdir(parents=True)
    (reply / "codemodel-v2-x.json").write_text(json.dumps({
        "configurations": [{"targets": [{"jsonFile": "target-foo.json"}]}],
    }))
    (reply / "target-foo.json").write_text(json.dumps({
        "name": "foo",
        "type": "SHARED_LIBRARY",
        "artifacts": [{"path": "libfoo.so"}],
        "dependencies": [{"id": "bar::@abc"}],
        "fileSets": [
            {"type": "HEADERS", "visibility": "PUBLIC"},
            {"type": "HEADERS", "visibility": "PRIVATE"},
        ],
        "sources": [
            {"path": "src/foo.cpp"},
            {"path": "include/foo.h", "fileSetIndex": 0},
            {"path": "src/foo_impl.h", "fileSetIndex": 1},
        ],
    }))
    (reply / "toolchains-v1-x.json").write_text(json.dumps({
        "toolchains": [
            {"language": "CXX", "compiler": {"id": "GNU", "version": "14.1", "path": "/usr/bin/c++"}},
        ],
    }))
    (reply / "index-2026.json").write_text(json.dumps({
        "cmake": {"version": {"string": "3.28"}, "generator": {"name": "Ninja"}},
        "objects": [
            {"kind": "codemodel", "jsonFile": "codemodel-v2-x.json"},
            {"kind": "toolchains", "jsonFile": "toolchains-v1-x.json"},
        ],
    }))
    return build_dir


def test_cmake_file_api_adapter(tmp_path):
    build = _make_cmake_reply(tmp_path / "build")
    ev = CMakeFileApiAdapter(build).collect()
    assert len(ev.targets) == 1
    t = ev.targets[0]
    assert t.id == "target://foo"
    assert t.kind.value == "shared_library"
    assert t.public_headers == ["include/foo.h"]
    assert t.private_headers == ["src/foo_impl.h"]
    assert t.source_files == ["src/foo.cpp"]
    assert t.dependencies == ["target://bar"]
    assert t.visibility == "public"
    assert any(g.kind == "cmake" and g.generator == "Ninja" for g in ev.generators)
    assert ev.toolchains and ev.toolchains[0].version == "14.1"


def test_cmake_file_api_missing_reply_is_graceful(tmp_path):
    ev = CMakeFileApiAdapter(tmp_path / "no-build").collect()
    assert ev.targets == []
    assert any("no reply directory" in d for d in ev.diagnostics)


# ── Build-evidence diff & findings (ADR-029 D9) ──────────────────────────────


def test_diff_emits_abi_relevant_build_flag_changed():
    old = BuildEvidence(build_options=[BuildOption("std:CXX", "c++17", abi_relevant=True)])
    new = BuildEvidence(build_options=[BuildOption("std:CXX", "c++20", abi_relevant=True)])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.ABI_RELEVANT_BUILD_FLAG_CHANGED for c in changes)


def test_diff_emits_build_context_changed_for_non_abi_flag():
    old = BuildEvidence(build_options=[BuildOption("warnings", "on", abi_relevant=False)])
    new = BuildEvidence(build_options=[BuildOption("warnings", "off", abi_relevant=False)])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.BUILD_CONTEXT_CHANGED for c in changes)


def test_diff_emits_toolchain_version_changed():
    old = BuildEvidence(toolchains=[Toolchain(id="t", compiler_id="GNU", version="13", language="CXX")])
    new = BuildEvidence(toolchains=[Toolchain(id="t", compiler_id="GNU", version="14", language="CXX")])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.TOOLCHAIN_VERSION_CHANGED for c in changes)


def test_diff_emits_toolchain_change_for_sysroot_option():
    old = BuildEvidence(build_options=[BuildOption("sysroot", "/a", abi_relevant=True)])
    new = BuildEvidence(build_options=[BuildOption("sysroot", "/b", abi_relevant=True)])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.TOOLCHAIN_VERSION_CHANGED for c in changes)


def test_diff_emits_link_export_policy_changed():
    old = BuildEvidence(link_units=[LinkUnit(id="l", target_id="t", version_script="v1.map")])
    new = BuildEvidence(link_units=[LinkUnit(id="l", target_id="t", version_script="v2.map")])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.LINK_EXPORT_POLICY_CHANGED for c in changes)


def test_diff_emits_generated_file_dependency_unstable():
    old = BuildEvidence()
    new = BuildEvidence(diagnostics=["ninja: missingdeps reported 3 entries"])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.GENERATED_FILE_DEPENDENCY_UNSTABLE for c in changes)


def test_header_parse_drift_flagged_without_context():
    ev = BuildEvidence(build_options=[BuildOption("std:CXX", "c++20", abi_relevant=True)])
    changes = check_header_parse_drift(ev, headers_parsed_with_context=False)
    assert len(changes) == 1
    assert changes[0].kind is ChangeKind.HEADER_PARSE_CONTEXT_DRIFT


def test_header_parse_drift_silent_with_context():
    ev = BuildEvidence(build_options=[BuildOption("std:CXX", "c++20", abi_relevant=True)])
    assert check_header_parse_drift(ev, headers_parsed_with_context=True) == []


def _opt(key: str, value: str) -> BuildOption:
    return BuildOption(key, value, abi_relevant=True)


def test_runtime_mode_flags_normalize_to_canonical_keys():
    from abicheck.buildsource.adapters.base import derive_build_options
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://x",
        language="CXX",
        abi_relevant_flags=[
            "-fno-exceptions", "-fno-rtti",
            "-ftls-model=initial-exec", "-fno-threadsafe-statics",
        ],
    )
    opts = {(o.key, o.value) for o in derive_build_options([cu])}
    # C++-concept mode keys are language-qualified (like std:<lang>); TLS is not.
    assert ("exceptions:CXX", "off") in opts
    assert ("rtti:CXX", "off") in opts
    assert ("tls_model", "initial-exec") in opts
    assert ("threadsafe_statics:CXX", "off") in opts


@pytest.mark.parametrize("argv, source, expected", [
    # No forcing: extension wins.
    (["g++", "-c", "foo.cpp"], "foo.cpp", "CXX"),
    (["gcc", "-c", "foo.c"], "foo.c", "C"),
    # GNU -x forces the language over the extension (split and combined forms).
    (["g++", "-x", "c++", "-c", "foo.c"], "foo.c", "CXX"),
    (["gcc", "-xc", "-c", "foo.cpp"], "foo.cpp", "C"),
    # -x none reverts to extension-based detection.
    (["g++", "-x", "c++", "-x", "none", "-c", "foo.c"], "foo.c", "C"),
    # Last -x wins for a single-source TU.
    (["g++", "-x", "c", "-x", "c++", "-c", "foo.c"], "foo.c", "CXX"),
    # MSVC /TP and /Tp<file> force C++, /TC and /Tc<file> force C.
    (["cl", "/c", "/TP", "foo.c"], "foo.c", "CXX"),
    (["cl", "/c", "/Tpfoo.c"], "foo.c", "CXX"),
    (["cl", "/c", "/TC", "foo.cpp"], "foo.cpp", "C"),
    (["cl", "/c", "/Tcfoo.cpp"], "foo.cpp", "C"),
    # Unknown -x language leaves the extension-derived language intact.
    (["clang", "-x", "assembler", "-c", "foo.cpp"], "foo.cpp", "CXX"),
    # Forced Objective-C/Objective-C++ keep their own tokens (match .m/.mm
    # extension detection), so a redundant -x on a .mm file is a no-op.
    (["clang++", "-x", "objective-c++", "-c", "foo.mm"], "foo.mm", "OBJCXX"),
    (["clang", "-x", "objective-c", "-c", "foo.m"], "foo.m", "OBJC"),
])
def test_effective_language_honors_forced_language(argv, source, expected):
    from abicheck.buildsource.adapters.base import effective_language
    assert effective_language(argv, source) == expected


def test_redundant_objcxx_forced_language_is_no_op_drift(tmp_path):
    # Codex P2: clang++ -x objective-c++ on a .mm TU must stay OBJCXX, not collapse
    # to CXX — otherwise std:OBJCXX->std:CXX reads as false build-flag drift.
    from abicheck.buildsource.adapters.compile_db import CompileDbAdapter

    def _db(args):
        p = tmp_path / f"cc_{abs(hash(tuple(args)))}.json"
        p.write_text(json.dumps([{
            "directory": str(tmp_path), "file": "foo.mm", "arguments": args,
        }]))
        return CompileDbAdapter(p).collect()

    old = _db(["clang++", "-std=c++17", "-c", "foo.mm"])
    new = _db(["clang++", "-x", "objective-c++", "-std=c++17", "-c", "foo.mm"])
    assert old.compile_units[0].language == "OBJCXX"
    assert new.compile_units[0].language == "OBJCXX"
    # No std:CXX/std:OBJCXX add+remove churn — the only std option is std:OBJCXX.
    assert not any(c.kind is ChangeKind.ABI_RELEVANT_BUILD_FLAG_CHANGED
                   for c in diff_build_evidence(old, new))


def test_compile_db_forced_language_drives_runtime_mode_key(tmp_path):
    # Codex P2: g++ -x c++ -c foo.c is C++, so an omitted->-fno-exceptions flip
    # must compare against the C++ default (on) and report a mode change, not be
    # masked as C's default-off vs explicit-off.
    from abicheck.buildsource.adapters.compile_db import CompileDbAdapter

    def _db(extra_flags):
        p = tmp_path / f"cc_{abs(hash(tuple(extra_flags)))}.json"
        entries = [{
            "directory": str(tmp_path),
            "file": "foo.c",
            "arguments": ["g++", "-x", "c++", *extra_flags, "-c", "foo.c"],
        }]
        p.write_text(json.dumps(entries))
        return CompileDbAdapter(p).collect()

    old = _db([])                 # omitted exceptions -> C++ default on
    new = _db(["-fno-exceptions"])  # explicit off
    # The forced language must record exceptions:CXX (not exceptions:C).
    assert old.compile_units[0].language == "CXX"
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED for c in changes)


def test_runtime_mode_flags_last_one_wins_per_tu():
    # Codex P2: conflicting flags within one compile command resolve to the
    # last one (GCC semantics), so -fno-exceptions -fexceptions records only
    # the effective "on", not both values.
    from abicheck.buildsource.adapters.base import derive_build_options
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://x", language="CXX",
        abi_relevant_flags=["-fno-exceptions", "-fexceptions"],
    )
    opts = {(o.key, o.value) for o in derive_build_options([cu])}
    assert ("exceptions:CXX", "on") in opts
    assert ("exceptions:CXX", "off") not in opts

    cu2 = CompileUnit(
        id="cu://y", language="CXX",
        abi_relevant_flags=["-frtti", "-fno-rtti"],
    )
    opts2 = {(o.key, o.value) for o in derive_build_options([cu2])}
    assert ("rtti:CXX", "off") in opts2
    assert ("rtti:CXX", "on") not in opts2


def test_exceptions_default_is_language_aware():
    # Codex P2: -fexceptions default is on for C++, off for C. An omitted flag
    # must use the language-correct default so a C TU is neither a false flip
    # nor a missed one.
    from abicheck.buildsource.adapters.base import derive_build_options
    from abicheck.buildsource.build_evidence import CompileUnit

    def ev(lang, *flags, src="s.cpp"):
        cu = CompileUnit(id=f"cu://{lang}", language=lang, source=src,
                         abi_relevant_flags=list(flags))
        return BuildEvidence(compile_units=[cu], build_options=derive_build_options([cu]))

    # C: omitted (default off) vs explicit -fno-exceptions (off) → no change.
    c_old = ev("C", src="s.c")
    c_new = ev("C", "-fno-exceptions", src="s.c")
    assert not any(
        c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED
        for c in diff_build_evidence(c_old, c_new)
    )
    # C: omitted (default off) → explicit -fexceptions (on) → real flip.
    c_on = ev("C", "-fexceptions", src="s.c")
    assert any(
        c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED
        for c in diff_build_evidence(c_old, c_on)
    )
    # C++: omitted (default on) → -fno-exceptions (off) → real flip.
    cxx_old = ev("CXX")
    cxx_new = ev("CXX", "-fno-exceptions")
    assert any(
        c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED
        for c in diff_build_evidence(cxx_old, cxx_new)
    )


def test_diff_emits_exceptions_mode_changed():
    old = BuildEvidence(build_options=[_opt("exceptions", "on")])
    new = BuildEvidence(build_options=[_opt("exceptions", "off")])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED for c in changes)
    # The specific mode finding replaces the generic ABI-flag finding for this key.
    assert not any(c.kind is ChangeKind.ABI_RELEVANT_BUILD_FLAG_CHANGED for c in changes)


def test_diff_emits_rtti_mode_changed():
    old = BuildEvidence(build_options=[_opt("rtti", "on")])
    new = BuildEvidence(build_options=[_opt("rtti", "off")])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.RTTI_MODE_CHANGED for c in changes)


def test_diff_emits_threadsafe_statics_mode_changed():
    old = BuildEvidence(build_options=[_opt("threadsafe_statics", "on")])
    new = BuildEvidence(build_options=[_opt("threadsafe_statics", "off")])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.THREADSAFE_STATICS_MODE_CHANGED for c in changes)


def test_diff_emits_tls_model_changed():
    old = BuildEvidence(build_options=[_opt("tls_model", "global-dynamic")])
    new = BuildEvidence(build_options=[_opt("tls_model", "initial-exec")])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.TLS_MODEL_CHANGED for c in changes)


@pytest.mark.parametrize("model", ("global-dynamic", "initial-exec"))
def test_tls_model_omitted_vs_explicit_default_is_no_change(model):
    # global-dynamic / initial-exec can equal the -fpic-dependent compiler
    # default, so an omitted flag must not read as a flip against them.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt("tls_model", model)])
    assert not any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(old, new)
    )
    assert not any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(new, old)
    )


@pytest.mark.parametrize("model", ("local-exec", "local-dynamic"))
def test_tls_model_omitted_vs_never_default_is_reported(model):
    # local-exec / local-dynamic are never the auto-default, so an omitted ->
    # explicit transition to them is always a real, reportable change.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt("tls_model", model)])
    assert any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(old, new)
    )
    # symmetric: dropping an explicit local-* model is also reportable.
    assert any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(new, old)
    )


def test_tls_init_omitted_vs_no_extern_is_reported():
    # GCC default is -fextern-tls-init (extern); an omitted->-fno-extern-tls-init
    # flip is a real extern->local TLS-init mode change.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt("tls_init", "local")])
    assert any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(old, new)
    )
    assert any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(new, old)
    )


def test_tls_init_omitted_vs_explicit_extern_is_no_change():
    # Explicit -fextern-tls-init equals the omitted default, so no flip.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt("tls_init", "extern")])
    assert not any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(old, new)
    )


def test_tls_model_omitted_vs_mixed_with_never_default_is_reported():
    # Multi-config explicit side carries a *mix*: one TU at the (possibly-default)
    # global-dynamic and one at local-exec (never the auto-default). The omitted
    # side must not suppress the whole change — the local-exec TU is a real flip.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[
        _opt("tls_model", "global-dynamic"),
        _opt("tls_model", "local-exec"),
    ])
    assert any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(old, new)
    )
    assert any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(new, old)
    )


def test_tls_model_omitted_vs_mixed_all_maybe_default_is_suppressed():
    # A mix of only maybe-default models (global-dynamic / initial-exec) against
    # an omitted side stays suppressed — none is guaranteed non-default.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[
        _opt("tls_model", "global-dynamic"),
        _opt("tls_model", "initial-exec"),
    ])
    assert not any(
        c.kind is ChangeKind.TLS_MODEL_CHANGED for c in diff_build_evidence(old, new)
    )


@pytest.mark.parametrize("key, kind", [
    ("exceptions:OBJCXX", ChangeKind.EXCEPTIONS_MODE_CHANGED),
    ("rtti:OBJCXX", ChangeKind.RTTI_MODE_CHANGED),
    ("threadsafe_statics:OBJCXX", ChangeKind.THREADSAFE_STATICS_MODE_CHANGED),
])
def test_objcxx_mode_omitted_vs_off_is_a_change(key, kind):
    # Native .mm TUs record OBJCXX; Objective-C++ is a C++ superset, so the
    # runtime defaults are on. An omitted->explicit-off flip must be reported.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt(key, "off")])
    assert any(c.kind is kind for c in diff_build_evidence(old, new))
    assert any(c.kind is kind for c in diff_build_evidence(new, old))


def test_objcxx_exceptions_omitted_vs_on_is_no_change():
    # Explicit -fexceptions on a .mm TU equals the OBJCXX default (on).
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt("exceptions:OBJCXX", "on")])
    assert not any(
        c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED for c in diff_build_evidence(old, new)
    )


def test_objc_exceptions_omitted_vs_off_is_no_change():
    # Objective-C (.m) defaults exceptions off, like C — omitted vs explicit off
    # is not a flip.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt("exceptions:OBJC", "off")])
    assert not any(
        c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED for c in diff_build_evidence(old, new)
    )


def test_exceptions_mode_cxx_on_vs_absent_is_no_change():
    # For C++ (exceptions:CXX), absent == default on; an explicit -fexceptions
    # against an omitted flag must not read as a mode flip.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt("exceptions:CXX", "on")])
    changes = diff_build_evidence(old, new)
    assert not any(c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED for c in changes)


def test_exceptions_mode_cxx_off_vs_absent_is_a_change():
    # C++ omitted (default on) → explicit off is a real flip.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt("exceptions:CXX", "off")])
    changes = diff_build_evidence(old, new)
    assert any(c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED for c in changes)


def test_exceptions_mode_unknown_language_requires_both_explicit():
    # A bare (source-less / unknown-language) record must not assume C++: an
    # omitted -> explicit transition is suppressed since C defaults exceptions off.
    old = BuildEvidence(build_options=[])
    new = BuildEvidence(build_options=[_opt("exceptions", "off")])
    assert not any(
        c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED for c in diff_build_evidence(old, new)
    )
    # Both sides explicit still diffs.
    both = diff_build_evidence(
        BuildEvidence(build_options=[_opt("exceptions", "on")]),
        BuildEvidence(build_options=[_opt("exceptions", "off")]),
    )
    assert any(c.kind is ChangeKind.EXCEPTIONS_MODE_CHANGED for c in both)


def test_struct_return_convention_change_from_return_trait_flip():
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _diff_value_abi_traits

    old = AdvancedDwarfMetadata()
    new = AdvancedDwarfMetadata()
    old.value_abi_traits["_Z3getv"] = "ret:trivial"
    new.value_abi_traits["_Z3getv"] = "ret:nontrivial"
    results = _diff_value_abi_traits(old, new, set())
    kinds = {r[0] for r in results}
    assert "struct_return_convention_changed" in kinds
    assert "value_abi_trait_changed" not in kinds


def test_large_aggregate_return_flip_stays_value_abi_trait():
    # Codex P2: a >16-byte aggregate is memory-returned both before and after a
    # triviality change (no register<->sret flip), so it must NOT be labelled a
    # struct-return convention change.
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _diff_value_abi_traits

    old = AdvancedDwarfMetadata()
    new = AdvancedDwarfMetadata()
    old.value_abi_traits["_Z3getv"] = "ret:trivial"
    new.value_abi_traits["_Z3getv"] = "ret:nontrivial"
    old.return_value_sizes["_Z3getv"] = 24
    new.return_value_sizes["_Z3getv"] = 24
    kinds = {r[0] for r in _diff_value_abi_traits(old, new, set())}
    assert "value_abi_trait_changed" in kinds
    assert "struct_return_convention_changed" not in kinds


def test_small_aggregate_return_flip_is_struct_return():
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _diff_value_abi_traits

    old = AdvancedDwarfMetadata()
    new = AdvancedDwarfMetadata()
    old.value_abi_traits["_Z3getv"] = "ret:trivial"
    new.value_abi_traits["_Z3getv"] = "ret:nontrivial"
    old.return_value_sizes["_Z3getv"] = 8
    new.return_value_sizes["_Z3getv"] = 8
    kinds = {r[0] for r in _diff_value_abi_traits(old, new, set())}
    assert "struct_return_convention_changed" in kinds
    assert "value_abi_trait_changed" not in kinds


def test_mixed_size_and_triviality_flip_stays_value_abi():
    # Codex P2: old trivial @24B (memory, >16) → new nontrivial @8B (memory,
    # nontrivial) is memory-returned on BOTH sides — no register<->sret flip — so
    # it must NOT be labelled a struct-return convention change.
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _diff_value_abi_traits

    old = AdvancedDwarfMetadata()
    new = AdvancedDwarfMetadata()
    old.value_abi_traits["_Z3getv"] = "ret:trivial"
    new.value_abi_traits["_Z3getv"] = "ret:nontrivial"
    old.return_value_sizes["_Z3getv"] = 24
    new.return_value_sizes["_Z3getv"] = 8
    kinds = {r[0] for r in _diff_value_abi_traits(old, new, set())}
    assert "value_abi_trait_changed" in kinds
    assert "struct_return_convention_changed" not in kinds


def test_returns_in_registers_helper():
    from abicheck.dwarf_advanced import _returns_in_registers

    assert _returns_in_registers("trivial", 8) is True
    assert _returns_in_registers("trivial", 16) is True
    assert _returns_in_registers("trivial", 24) is False   # large trivial → memory
    assert _returns_in_registers("nontrivial", 8) is False  # nontrivial → memory
    assert _returns_in_registers("trivial", None) is True   # unknown → conservative
    assert _returns_in_registers(None, 8) is False
    # An unaligned member (packed) forces memory even for a small trivial type.
    assert _returns_in_registers("trivial", 8, memory_forced=True) is False


def test_return_aggregate_added_or_removed_is_not_struct_return():
    # Codex P2: when the return aggregate component is only added/removed
    # (aggregate <-> scalar return), the scalar side can still be register-
    # returned, so it is not a register<->sret flip.
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _diff_value_abi_traits

    old = AdvancedDwarfMetadata()
    new = AdvancedDwarfMetadata()
    # Aggregate return removed; a by-value aggregate parameter still differs so
    # the whole trait changed and the symbol stays in both maps.
    old.value_abi_traits["_Z1fP1S"] = "ret:trivial|p0:trivial"
    new.value_abi_traits["_Z1fP1S"] = "p0:nontrivial"
    kinds = {r[0] for r in _diff_value_abi_traits(old, new, set())}
    assert "struct_return_convention_changed" not in kinds
    assert "value_abi_trait_changed" in kinds


def test_small_packed_aggregate_return_flip_stays_value_abi():
    # Codex P2: a small packed struct (unaligned member) is memory-returned both
    # before and after gaining a destructor — no register<->sret flip.
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _diff_value_abi_traits

    old = AdvancedDwarfMetadata()
    new = AdvancedDwarfMetadata()
    old.value_abi_traits["_Z3getv"] = "ret:trivial"
    new.value_abi_traits["_Z3getv"] = "ret:nontrivial"
    old.return_value_sizes["_Z3getv"] = 12
    new.return_value_sizes["_Z3getv"] = 12
    old.return_memory_classified.add("_Z3getv")
    new.return_memory_classified.add("_Z3getv")
    kinds = {r[0] for r in _diff_value_abi_traits(old, new, set())}
    assert "value_abi_trait_changed" in kinds
    assert "struct_return_convention_changed" not in kinds


def test_unknown_size_return_flip_stays_struct_return():
    # No recorded size (older snapshots / non-DWARF mocks) → stay conservative
    # and keep the struct-return label (the pre-gate behaviour).
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _diff_value_abi_traits

    old = AdvancedDwarfMetadata()
    new = AdvancedDwarfMetadata()
    old.value_abi_traits["_Z3getv"] = "ret:trivial"
    new.value_abi_traits["_Z3getv"] = "ret:nontrivial"
    kinds = {r[0] for r in _diff_value_abi_traits(old, new, set())}
    assert "struct_return_convention_changed" in kinds


def test_param_only_trait_flip_stays_value_abi_trait():
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _diff_value_abi_traits

    old = AdvancedDwarfMetadata()
    new = AdvancedDwarfMetadata()
    old.value_abi_traits["_Z3fooP1S"] = "ret:trivial|p0:trivial"
    new.value_abi_traits["_Z3fooP1S"] = "ret:trivial|p0:nontrivial"
    results = _diff_value_abi_traits(old, new, set())
    kinds = {r[0] for r in results}
    assert "value_abi_trait_changed" in kinds
    assert "struct_return_convention_changed" not in kinds


def test_diff_identical_evidence_is_empty():
    ev = BuildEvidence(
        build_options=[BuildOption("std:CXX", "c++20", abi_relevant=True)],
        toolchains=[Toolchain(id="t", compiler_id="GNU", version="14", language="CXX")],
    )
    import copy
    assert diff_build_evidence(ev, copy.deepcopy(ev)) == []


# ── ChangeKind partition (ADR-028 D3) ────────────────────────────────────────


def test_build_context_kinds_never_breaking():
    """ADR-028 D3: source/build-only kinds are RISK or quality, never BREAKING."""
    risk = {
        ChangeKind.ABI_RELEVANT_BUILD_FLAG_CHANGED,
        ChangeKind.HEADER_PARSE_CONTEXT_DRIFT,
        ChangeKind.TOOLCHAIN_VERSION_CHANGED,
        ChangeKind.GENERATED_FILE_DEPENDENCY_UNSTABLE,
        ChangeKind.LINK_EXPORT_POLICY_CHANGED,
    }
    for k in risk:
        assert k in RISK_KINDS
        assert k not in BREAKING_KINDS
    assert ChangeKind.BUILD_CONTEXT_CHANGED in COMPATIBLE_KINDS
    assert ChangeKind.BUILD_CONTEXT_CHANGED not in BREAKING_KINDS


# ── Redaction (ADR-032 D7) ───────────────────────────────────────────────────


def test_redaction_strips_secret_define():
    pol = RedactionPolicy(redact_home=False)
    assert pol.arg("-DAPI_TOKEN=hunter2") == "-DAPI_TOKEN=<redacted>"
    assert pol.arg("-DFOO=1") == "-DFOO=1"


def test_redaction_rewrites_home_prefix():
    pol = RedactionPolicy(home_replacements={"/home/alice": "~"})
    assert pol.path("/home/alice/proj/foo.cpp") == "~/proj/foo.cpp"


def test_redaction_rewrites_embedded_home_paths_in_argv():
    """Combined flags that embed a home path are redacted in argv (Codex)."""
    pol = RedactionPolicy(home_replacements={"/home/alice": "~"})
    assert pol.path("-I/home/alice/proj/include") == "-I~/proj/include"
    assert pol.path("-DMYROOT=/home/alice/sdk") == "-DMYROOT=~/sdk"
    red = pol.argv(["c++", "-I/home/alice/inc", "-DMYROOT=/home/alice/sdk", "-c", "a.cpp"])
    assert not any("/home/alice" in tok for tok in red)


def test_compile_db_redacts_embedded_home_paths_in_argv(tmp_path):
    """End-to-end: embedded home paths never reach CompileUnit.argv."""
    from abicheck.buildsource.adapters import CompileDbAdapter

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(json.dumps([{
        "directory": str(tmp_path), "file": "a.cpp",
        "arguments": ["c++", "-I/home/alice/proj/include", "-c", "a.cpp"],
    }]))
    ev = CompileDbAdapter(
        cdb, redaction=RedactionPolicy(home_replacements={"/home/alice": "~"}),
    ).collect()
    assert not any("/home/alice" in tok for tok in ev.compile_units[0].argv)


def test_redaction_define_value_redacts_secret_macro():
    pol = RedactionPolicy(home_replacements={"/home/bob": "~"})
    assert pol.define_value("API_TOKEN", "hunter2") == "<redacted>"
    assert pol.define_value("SECRET_KEY", "abc") == "<redacted>"
    # Non-secret macros keep their value but still get home-path normalization.
    assert pol.define_value("FOO", "1") == "1"
    assert pol.define_value("PREFIX", "/home/bob/install") == "~/install"


def test_compile_db_redacts_secret_define(tmp_path):
    from abicheck.buildsource.adapters import CompileDbAdapter

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(json.dumps([{
        "directory": str(tmp_path), "file": "a.cpp",
        "arguments": ["c++", "-DAPI_TOKEN=hunter2", "-DFOO=1", "-c", "a.cpp"],
    }]))
    ev = CompileDbAdapter(cdb).collect()
    defines = ev.compile_units[0].defines
    assert defines["API_TOKEN"] == "<redacted>"
    assert defines["FOO"] == "1"


def test_redaction_argv_redacts_split_define_secret():
    """Split -D form ['-D', 'KEY=secret'] must redact the value token."""
    pol = RedactionPolicy(redact_home=False)
    out = pol.argv(["c++", "-D", "API_TOKEN=hunter2", "-D", "FOO=1", "-c", "a.cpp"])
    assert "API_TOKEN=<redacted>" in out
    assert "hunter2" not in " ".join(out)
    assert "FOO=1" in out


def test_redaction_redacts_secret_option_flags():
    """Credential-style CLI flags (not just -D macros) must be redacted (D7)."""
    pol = RedactionPolicy(redact_home=False)
    # Combined --flag=value form.
    assert pol.arg("--token=hunter2") == "--token=<redacted>"
    assert pol.arg("--api-key=abc123") == "--api-key=<redacted>"
    assert pol.arg("--password=p@ss") == "--password=<redacted>"
    # Non-secret options are left untouched.
    assert pol.arg("--output=build/x.json") == "--output=build/x.json"


def test_redaction_argv_redacts_split_secret_option():
    """Split '--token secret' form must redact the value token, not later flags."""
    pol = RedactionPolicy(redact_home=False)
    out = pol.argv(["tool", "--token", "hunter2", "--auth-token", "abc", "--verbose", "-c", "a.cpp"])
    joined = " ".join(out)
    assert "hunter2" not in joined
    assert "abc" not in joined.split()  # value after --auth-token redacted
    assert out == ["tool", "--token", "<redacted>", "--auth-token", "<redacted>", "--verbose", "-c", "a.cpp"]
    # A secret flag immediately followed by another flag has no value to redact.
    assert pol.argv(["tool", "--token", "--verbose"]) == ["tool", "--token", "--verbose"]


def test_compile_db_split_define_secret_not_leaked_in_argv(tmp_path):
    """End-to-end: split-form secret never reaches CompileUnit.argv."""
    from abicheck.buildsource.adapters import CompileDbAdapter

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(json.dumps([{
        "directory": str(tmp_path), "file": "a.cpp",
        "arguments": ["c++", "-D", "API_TOKEN=hunter2", "-D", "_GLIBCXX_USE_CXX11_ABI=0", "-c", "a.cpp"],
    }]))
    ev = CompileDbAdapter(cdb).collect()
    cu = ev.compile_units[0]
    assert "hunter2" not in " ".join(cu.argv)
    assert cu.defines["API_TOKEN"] == "<redacted>"
    # The split-form ABI macro is still captured as a diffable option.
    assert any(o.key == "define:_GLIBCXX_USE_CXX11_ABI" for o in ev.build_options)


# ── Helpers ──────────────────────────────────────────────────────────────────


def test_detect_language():
    assert detect_language("a.cpp") == "CXX"
    assert detect_language("a.c") == "C"
    assert detect_language("a.txt") == ""


def test_compile_unit_id_is_config_sensitive():
    a = compile_unit_id("a.cpp", ["-std=c++17"])
    b = compile_unit_id("a.cpp", ["-std=c++20"])
    assert a != b
    assert compile_unit_id("a.cpp", ["-std=c++17"]) == a  # stable


def test_extract_abi_relevant_flags():
    flags = extract_abi_relevant_flags(["-std=c++20", "-O2", "-fvisibility=hidden", "-DFOO=1"])
    assert "-std=c++20" in flags
    assert "-fvisibility=hidden" in flags
    assert "-O2" not in flags
    assert "-DFOO=1" not in flags


def test_extract_abi_relevant_flags_split_define_form():
    """Split ['-D', 'KEY=VAL'] ABI macros are captured and normalized."""
    flags = extract_abi_relevant_flags(
        ["c++", "-D", "_GLIBCXX_USE_CXX11_ABI=0", "-D", "FOO=1", "-std=c++20"]
    )
    assert "-D_GLIBCXX_USE_CXX11_ABI=0" in flags  # normalized to combined form
    assert "-std=c++20" in flags
    assert not any("FOO" in f for f in flags)


def test_extract_abi_relevant_flags_trailing_bare_define():
    """A trailing bare -D with no following token does not crash."""
    assert extract_abi_relevant_flags(["c++", "-D"]) == []


# ── BuildEvidence merge ──────────────────────────────────────────────────────


def test_build_evidence_merge_dedups_by_id():
    a = BuildEvidence(generators=[Generator(kind="cmake")])
    b = BuildEvidence(
        generators=[Generator(kind="ninja")],
        toolchains=[Toolchain(id="t1", language="CXX")],
    )
    a.merge(b)
    a.merge(b)  # idempotent on ids
    assert len(a.toolchains) == 1
    assert len(a.generators) == 3  # generators are appended, not id-deduped


def test_coverage_status_default_round_trip():
    cov = BuildSourcePack.empty("/tmp/x").manifest.coverage_for(DataLayer.L3_BUILD)
    assert cov is None  # empty pack has no coverage rows until populated


def test_coverage_status_enum_values():
    assert CoverageStatus.PRESENT.value == "present"
    assert CoverageStatus.NOT_COLLECTED.value == "not_collected"


def test_inline_pack_content_hash_reflects_embedded_payloads():
    """An embedded (never-written) pack hashes its in-memory facts (Codex P2).

    Without hashing the in-memory payloads, two packs with identical coverage
    but different build evidence would collide on content_hash, breaking the
    content-addressed provenance ref embedded in the snapshot.
    """
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack

    def _pack(source: str) -> BuildSourcePack:
        ev = BuildEvidence()
        ev.compile_units.append(CompileUnit(id=f"cu://{source}", source=source))
        return BuildSourcePack(root=Path(""), build_evidence=ev)

    a = _pack("a.cpp")
    b = _pack("b.cpp")
    same = _pack("a.cpp")

    # Different embedded facts → different content hash; identical facts → equal.
    assert a.content_hash() != b.content_hash()
    assert a.content_hash() == same.content_hash()
    # The digest is non-empty (the payload actually contributed).
    assert a.content_hash().startswith("sha256:")


def test_embedded_and_ondisk_pack_hash_agree(tmp_path):
    """The same facts hash identically whether embedded or written to disk."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack

    ev = BuildEvidence()
    ev.compile_units.append(CompileUnit(id="cu://x", source="x.cpp"))

    embedded = BuildSourcePack(root=Path(""), build_evidence=ev)
    on_disk = BuildSourcePack.empty(tmp_path / "pk")
    on_disk.build_evidence = ev
    on_disk.write()

    assert embedded.content_hash() == on_disk.content_hash()
