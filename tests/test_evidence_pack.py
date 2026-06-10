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

"""Tests for the optional source/build EvidencePack (ADR-028) and the
build-evidence adapters/diff (ADR-029)."""
from __future__ import annotations

import json

import pytest

from abicheck.checker_policy import (
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,
)
from abicheck.evidence import (
    BuildEvidence,
    EvidencePack,
    EvidencePackManifest,
    EvidencePackRef,
)
from abicheck.evidence.adapters import (
    CMakeFileApiAdapter,
    CompileDbAdapter,
    NinjaAdapter,
    compile_unit_id,
    detect_language,
    extract_abi_relevant_flags,
)
from abicheck.evidence.build_diff import check_header_parse_drift, diff_build_evidence
from abicheck.evidence.build_evidence import (
    BuildOption,
    Generator,
    LinkUnit,
    Toolchain,
)
from abicheck.evidence.model import CoverageStatus, EvidenceLayer
from abicheck.evidence.redaction import RedactionPolicy
from abicheck.model import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

# ── Pack model & content addressing ──────────────────────────────────────────


def test_empty_pack_write_load_roundtrip(tmp_path):
    pack = EvidencePack.empty(tmp_path / "p.evidence", abicheck_version="9.9", created_at="t0")
    pack.write()
    loaded = EvidencePack.load(tmp_path / "p.evidence")
    assert loaded.manifest.abicheck_version == "9.9"
    # All standard subdirs were created.
    for sub in ("build", "source", "graph", "toolchain", "raw", "normalized"):
        assert (tmp_path / "p.evidence" / sub).is_dir()


def test_load_missing_manifest_raises(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        EvidencePack.load(tmp_path / "empty")


def test_content_hash_is_stable_across_created_at(tmp_path):
    """Two packs with identical evidence but different timestamps hash equal."""
    p1 = EvidencePack.empty(tmp_path / "a", created_at="2026-01-01")
    p1.write()
    p2 = EvidencePack.empty(tmp_path / "b", created_at="2099-12-31")
    p2.write()
    assert p1.content_hash() == p2.content_hash()
    assert p1.content_hash().startswith("sha256:")


def test_content_hash_changes_with_build_evidence(tmp_path):
    p1 = EvidencePack.empty(tmp_path / "a")
    p1.write()
    p2 = EvidencePack.empty(tmp_path / "b")
    p2.build_evidence = BuildEvidence(build_options=[BuildOption(key="std:CXX", value="c++20")])
    p2.write()
    assert p1.content_hash() != p2.content_hash()


def test_to_ref_roundtrip(tmp_path):
    pack = EvidencePack.empty(tmp_path / "p")
    pack.manifest.coverage = []
    pack.write()
    ref = pack.to_ref(path_hint="p.evidence/")
    ref2 = EvidencePackRef.from_dict(ref.to_dict())
    assert ref2.content_hash == ref.content_hash
    assert ref2.path_hint == "p.evidence/"


def test_manifest_dict_roundtrip():
    m = EvidencePackManifest(abicheck_version="1.0")
    m2 = EvidencePackManifest.from_dict(m.to_dict())
    assert m2.abicheck_version == "1.0"
    assert m2.evidence_pack_version == m.evidence_pack_version


def test_unknown_manifest_keys_are_ignored():
    """A newer pack with unknown keys still loads (forward-compat)."""
    raw = {"evidence_pack_version": 1, "future_field": {"x": 1}, "coverage": []}
    m = EvidencePackManifest.from_dict(raw)
    assert m.evidence_pack_version == 1


# ── Snapshot integration (schema v7) ─────────────────────────────────────────


def test_snapshot_v7_evidence_ref_roundtrip():
    snap = AbiSnapshot(
        library="libfoo.so", version="1.0",
        evidence_pack=EvidencePackRef(content_hash="sha256:abc", path_hint="e/"),
    )
    d = snapshot_to_dict(snap)
    assert d["schema_version"] == 7
    assert d["evidence_pack"]["content_hash"] == "sha256:abc"
    back = snapshot_from_dict(d)
    assert back.evidence_pack is not None
    assert back.evidence_pack.content_hash == "sha256:abc"


def test_snapshot_without_evidence_serializes_none():
    snap = AbiSnapshot(library="l", version="1")
    d = snapshot_to_dict(snap)
    assert d["evidence_pack"] is None
    assert snapshot_from_dict(d).evidence_pack is None


def test_legacy_v6_snapshot_without_evidence_key_loads():
    """Backward-compat (ADR-015): a v6 snapshot has no evidence_pack key."""
    d = snapshot_to_dict(AbiSnapshot(library="l", version="1"))
    d["schema_version"] = 6
    d.pop("evidence_pack", None)
    assert snapshot_from_dict(d).evidence_pack is None


def test_malformed_evidence_ref_is_ignored():
    d = snapshot_to_dict(AbiSnapshot(library="l", version="1"))
    d["evidence_pack"] = "not-a-dict"
    assert snapshot_from_dict(d).evidence_pack is None


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
    from abicheck.evidence.adapters import CompileDbAdapter

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
    from abicheck.evidence.adapters import CompileDbAdapter

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


def test_compile_db_split_define_secret_not_leaked_in_argv(tmp_path):
    """End-to-end: split-form secret never reaches CompileUnit.argv."""
    from abicheck.evidence.adapters import CompileDbAdapter

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
    cov = EvidencePack.empty("/tmp/x").manifest.coverage_for(EvidenceLayer.L3_BUILD)
    assert cov is None  # empty pack has no coverage rows until populated


def test_coverage_status_enum_values():
    assert CoverageStatus.PRESENT.value == "present"
    assert CoverageStatus.NOT_COLLECTED.value == "not_collected"
