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

"""Bazel adapter coverage (ADR-029 D6).

Exercises the cquery/aquery jsonproto normalization with pre-captured fixtures
so no live ``bazel`` is required, plus the live-query gating and CLI wiring.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.evidence.adapters import BazelAdapter
from abicheck.evidence.build_evidence import TargetKind
from abicheck.evidence.pack import EvidencePack

# A configured-target graph: a cc_library with public headers + a deps edge,
# and a cc_binary with no attributes (exercises the minimal-rule path).
CQUERY = json.dumps({
    "results": [
        {
            "target": {
                "rule": {
                    "name": "//foo:foo",
                    "ruleClass": "cc_library",
                    "attribute": [
                        {"name": "srcs", "type": "LABEL_LIST", "stringListValue": ["//foo:foo.cc"]},
                        {"name": "hdrs", "type": "LABEL_LIST", "stringListValue": ["//foo:foo.h"]},
                        {"name": "deps", "type": "LABEL_LIST", "stringListValue": ["//bar:bar"]},
                    ],
                    "ruleOutput": ["//foo:libfoo.a"],
                }
            },
            "configurationId": 1,
        },
        {"target": {"rule": {"name": "//app:app", "ruleClass": "cc_binary"}}},
        {"target": {"sourceFile": {"name": "//foo:foo.cc"}}},  # non-rule, skipped
    ]
})

# An action graph: one CppCompile and one CppLink for //foo:foo. Paths are the
# deduplicated fragment tree aquery emits.
AQUERY = json.dumps({
    "artifacts": [
        {"id": "1", "pathFragmentId": "10"},   # foo/foo.cc
        {"id": "2", "pathFragmentId": "11"},   # foo/foo.o
        {"id": "3", "pathFragmentId": "12"},   # foo/libfoo.so
    ],
    "actions": [
        {
            "targetId": "100",
            "mnemonic": "CppCompile",
            "arguments": ["/usr/bin/gcc", "-std=c++17", "-D_GLIBCXX_USE_CXX11_ABI=0",
                          "-c", "foo/foo.cc", "-o", "foo/foo.o"],
            "primaryOutputId": "2",
            "inputDepSetIds": ["200"],
        },
        {
            "targetId": "100",
            "mnemonic": "CppLink",
            "arguments": ["/usr/bin/gcc", "-shared", "-o", "foo/libfoo.so", "foo/foo.o"],
            "primaryOutputId": "3",
            "inputDepSetIds": ["201"],
        },
    ],
    "targets": [{"id": "100", "label": "//foo:foo"}],
    "depSetOfFiles": [
        {"id": "200", "directArtifactIds": ["1"]},
        {"id": "201", "directArtifactIds": ["2"]},
    ],
    "pathFragments": [
        {"id": "12", "label": "libfoo.so", "parentId": "20"},
        {"id": "11", "label": "foo.o", "parentId": "20"},
        {"id": "10", "label": "foo.cc", "parentId": "20"},
        {"id": "20", "label": "foo"},
    ],
})


def test_bazel_cquery_builds_target_graph():
    ev = BazelAdapter(cquery=CQUERY).collect()
    assert ev.generators[0].kind == "bazel"
    targets = {t.id: t for t in ev.targets}
    foo = targets["target:////foo:foo"]
    assert foo.kind is TargetKind.STATIC_LIBRARY
    assert foo.name == "foo"
    assert foo.source_files == ["//foo:foo.cc"]
    assert foo.public_headers == ["//foo:foo.h"]
    assert foo.dependencies == ["target:////bar:bar"]
    assert foo.visibility == "public"
    assert foo.outputs == ["//foo:libfoo.a"]
    assert targets["target:////app:app"].kind is TargetKind.EXECUTABLE
    # The non-rule sourceFile result is skipped, not turned into a target.
    assert len(ev.targets) == 2


def test_bazel_linkshared_cc_binary_is_shared_library():
    # A cc_binary with linkshared=True produces a shared library, not an exe.
    cquery = json.dumps({"results": [{"target": {"rule": {
        "name": "//foo:libfoo", "ruleClass": "cc_binary",
        "attribute": [{"name": "linkshared", "type": "BOOLEAN", "booleanValue": True}],
    }}}]})
    ev = BazelAdapter(cquery=cquery).collect()
    assert ev.targets[0].kind is TargetKind.SHARED_LIBRARY


def test_bazel_cc_binary_shared_output_extension_is_shared_library():
    # Even without linkshared, a .so ruleOutput marks the target shared.
    cquery = json.dumps({"results": [{"target": {"rule": {
        "name": "//foo:libfoo", "ruleClass": "cc_binary", "ruleOutput": ["//foo:libfoo.so"],
    }}}]})
    ev = BazelAdapter(cquery=cquery).collect()
    assert ev.targets[0].kind is TargetKind.SHARED_LIBRARY


def test_bazel_plain_cc_binary_stays_executable():
    cquery = json.dumps({"results": [{"target": {"rule": {
        "name": "//app:app", "ruleClass": "cc_binary",
        "attribute": [{"name": "linkshared", "type": "BOOLEAN", "booleanValue": False}],
    }}}]})
    ev = BazelAdapter(cquery=cquery).collect()
    assert ev.targets[0].kind is TargetKind.EXECUTABLE


def test_bazel_dll_output_classified_as_shared_library():
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{"mnemonic": "CppLink", "arguments": ["link"], "primaryOutputId": "1"}],
        "pathFragments": [{"id": "10", "label": "foo.dll"}],
    })
    ev = BazelAdapter(aquery=aquery).collect()
    assert ev.link_units[0].kind == "shared_library"


def test_bazel_cquery_preserves_multiple_configurations():
    # One label under two configurations (target vs exec) with different deps:
    # both survive. The first (canonical) config keeps the plain label id so
    # aquery linkage resolves; the extra config is suffixed with its id.
    cquery = json.dumps({"results": [
        {"target": {"rule": {"name": "//foo:foo", "ruleClass": "cc_library",
            "attribute": [{"name": "deps", "type": "LABEL_LIST", "stringListValue": ["//a:a"]}]}},
         "configurationId": 1},
        {"target": {"rule": {"name": "//foo:foo", "ruleClass": "cc_library",
            "attribute": [{"name": "deps", "type": "LABEL_LIST", "stringListValue": ["//b:b"]}]}},
         "configurationId": 2},
    ]})
    ev = BazelAdapter(cquery=cquery).collect()
    deps = {t.id: t.dependencies for t in ev.targets}
    assert deps == {
        "target:////foo:foo": ["target:////a:a"],          # canonical (first) config
        "target:////foo:foo#cfg:2": ["target:////b:b"],    # extra config preserved
    }


def test_bazel_multi_config_aquery_links_to_canonical_target():
    # Even when a label is multi-config, an aquery compile unit referencing the
    # label-only target id must resolve to the canonical collected Target.
    cquery = json.dumps({"results": [
        {"target": {"rule": {"name": "//foo:foo", "ruleClass": "cc_library"}}, "configurationId": 1},
        {"target": {"rule": {"name": "//foo:foo", "ruleClass": "cc_library"}}, "configurationId": 2},
    ]})
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{"mnemonic": "CppCompile", "targetId": "100",
                     "arguments": ["gcc", "-c", "foo.cc"], "primaryOutputId": "1"}],
        "targets": [{"id": "100", "label": "//foo:foo"}],
        "pathFragments": [{"id": "10", "label": "foo.o"}],
    })
    ev = BazelAdapter(cquery=cquery, aquery=aquery).collect()
    target_ids = {t.id for t in ev.targets}
    assert "target:////foo:foo" in target_ids                       # canonical present
    assert ev.compile_units[0].target_id == "target:////foo:foo"    # links to it


def test_bazel_cquery_single_config_keeps_plain_id():
    # A label with one configuration keeps the plain label id (aquery linkage).
    cquery = json.dumps({"results": [
        {"target": {"rule": {"name": "//foo:foo", "ruleClass": "cc_library"}}, "configurationId": 1},
    ]})
    ev = BazelAdapter(cquery=cquery).collect()
    assert ev.targets[0].id == "target:////foo:foo"


def test_bazel_aquery_builds_compile_and_link_units():
    ev = BazelAdapter(aquery=AQUERY).collect()
    assert len(ev.compile_units) == 1
    cu = ev.compile_units[0]
    assert cu.source == "foo/foo.cc"
    assert cu.output == "foo/foo.o"           # reconstructed from the fragment tree
    assert cu.language == "CXX"
    assert cu.standard == "c++17"
    assert cu.target_id == "target:////foo:foo"

    assert len(ev.link_units) == 1
    lu = ev.link_units[0]
    assert lu.output == "foo/libfoo.so"
    assert lu.kind == "shared_library"
    assert lu.inputs == ["foo/foo.o"]          # object-file input via the depset
    assert lu.target_id == "target:////foo:foo"

    # Per-unit ABI flags are projected into diffable build options (D9).
    opts = {(o.key, o.value) for o in ev.build_options}
    assert ("std:CXX", "c++17") in opts
    assert ("define:_GLIBCXX_USE_CXX11_ABI", "0") in opts


def test_bazel_combined_cquery_and_aquery():
    ev = BazelAdapter(cquery=CQUERY, aquery=AQUERY).collect()
    assert ev.targets and ev.compile_units and ev.link_units


def test_bazel_empty_inputs_just_records_generator():
    ev = BazelAdapter().collect()
    assert [g.kind for g in ev.generators] == ["bazel"]
    assert not ev.targets and not ev.compile_units


def test_bazel_malformed_jsonproto_diagnostic():
    ev = BazelAdapter(cquery="{not json").collect()
    assert any("could not parse cquery" in d for d in ev.diagnostics)
    assert not ev.targets


def test_bazel_non_object_jsonproto_diagnostic():
    ev = BazelAdapter(aquery="[1, 2, 3]").collect()
    assert any("aquery jsonproto was not a JSON object" in d for d in ev.diagnostics)


def test_bazel_forced_header_not_mistaken_for_source():
    # `-include config.hpp` is a forced header, not the translation unit; the
    # real source `foo.cc` must be selected even though config.hpp looks CXX.
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppCompile",
            "arguments": ["/usr/bin/gcc", "-include", "config.hpp", "-x", "c++",
                          "-c", "foo.cc", "-o", "foo.o"],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "foo.o"}],
    })
    ev = BazelAdapter(aquery=aquery).collect()
    assert ev.compile_units[0].source == "foo.cc"


def test_bazel_param_file_arguments_are_expanded():
    # A C++ action whose argv is just `gcc @foo.params`: the source and flags
    # live in paramFiles[].arguments and are substituted at the @token position.
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppCompile",
            "arguments": ["/usr/bin/gcc", "@bazel-out/foo.params"],
            "paramFiles": [{"execPath": "bazel-out/foo.params",
                            "arguments": ["-std=c++20", "-c", "foo.cc", "-o", "foo.o"]}],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "foo.o"}],
    })
    cu = BazelAdapter(aquery=aquery).collect().compile_units[0]
    assert cu.source == "foo.cc"
    assert cu.standard == "c++20"


def test_bazel_param_file_expanded_at_token_position():
    # Param-file args expand at the @token position, not at the end, so a
    # later command-line -std wins (matching the real compiler's last-wins rule).
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppCompile",
            "arguments": ["gcc", "@out/foo.params", "-std=c++11", "-c", "foo.cc"],
            "paramFiles": [{"execPath": "out/foo.params", "arguments": ["-std=c++20"]}],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "foo.o"}],
    })
    cu = BazelAdapter(aquery=aquery).collect().compile_units[0]
    assert cu.standard == "c++11"   # later token wins; append-at-end would give c++20
    assert cu.source == "foo.cc"


def test_bazel_param_file_without_execpath_falls_back_to_append():
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppCompile", "arguments": ["gcc", "@foo.params"],
            "paramFiles": [{"arguments": ["-std=c++17", "-c", "foo.cc"]}],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "foo.o"}],
    })
    cu = BazelAdapter(aquery=aquery).collect().compile_units[0]
    assert cu.standard == "c++17"
    assert cu.source == "foo.cc"


def test_bazel_live_aquery_includes_param_files(monkeypatch, tmp_path):
    captured: list[list[str]] = []
    import subprocess as _sp

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _sp.CompletedProcess(cmd, 0, stdout=AQUERY if "aquery" in cmd else CQUERY, stderr="")

    monkeypatch.setattr("abicheck.evidence.adapters.bazel.shutil.which", lambda _x: "/usr/bin/bazel")
    monkeypatch.setattr("abicheck.evidence.adapters.bazel.subprocess.run", fake_run)
    BazelAdapter(workspace=tmp_path, target="//foo:foo").collect()
    aquery_cmd = next(c for c in captured if "aquery" in c)
    assert "--include_param_files" in aquery_cmd
    cquery_cmd = next(c for c in captured if "cquery" in c)
    assert "--include_param_files" not in cquery_cmd  # only meaningful for aquery


def test_bazel_msvc_forced_include_not_mistaken_for_source():
    # MSVC/clang-cl `/FI config.hpp` (forced header) and a combined `/Yustdafx.h`
    # must not be picked as the source; the real `foo.cc` must win.
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppCompile",
            "arguments": ["cl.exe", "/FI", "config.hpp", "/Yustdafx.h", "/c", "foo.cc"],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "foo.obj"}],
    })
    cu = BazelAdapter(aquery=aquery).collect().compile_units[0]
    assert cu.source == "foo.cc"


def test_bazel_msvc_combined_forced_include_not_mistaken_for_source():
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppCompile",
            "arguments": ["clang-cl", "/FIconfig.hpp", "/c", "foo.cc"],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "foo.obj"}],
    })
    cu = BazelAdapter(aquery=aquery).collect().compile_units[0]
    assert cu.source == "foo.cc"


def test_bazel_compile_action_without_source_is_skipped():
    aquery = json.dumps({
        "actions": [{"mnemonic": "CppCompile", "arguments": ["/usr/bin/gcc", "-v"]}],
    })
    ev = BazelAdapter(aquery=aquery).collect()
    assert not ev.compile_units


def test_bazel_link_kind_by_extension():
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{"mnemonic": "CppArchive", "arguments": ["ar"], "primaryOutputId": "1"}],
        "pathFragments": [{"id": "10", "label": "libfoo.a"}],
    })
    ev = BazelAdapter(aquery=aquery).collect()
    assert ev.link_units[0].kind == "static_library"


def test_bazel_rule_without_name_is_skipped():
    cquery = json.dumps({"results": [{"target": {"rule": {"ruleClass": "cc_library"}}}]})
    ev = BazelAdapter(cquery=cquery).collect()
    assert not ev.targets


def test_bazel_link_action_without_output_is_skipped():
    aquery = json.dumps({"actions": [{"mnemonic": "CppLink", "arguments": ["gcc"]}]})
    ev = BazelAdapter(aquery=aquery).collect()
    assert not ev.link_units


def test_bazel_attr_string_value_fallback_and_executable_link():
    cquery = json.dumps({"results": [{"target": {"rule": {
        "name": "//app:app", "ruleClass": "cc_binary",
        "attribute": [{"name": "linkstatic", "type": "BOOLEAN", "stringValue": "1"}],
    }}}]})
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}, {"id": "2", "pathFragmentId": "11"}],
        "actions": [{
            "mnemonic": "CppLink", "arguments": ["gcc", "-o", "app/app"],
            "primaryOutputId": "2", "inputDepSetIds": ["300"],
        }],
        # Transitive depset nesting: 300 → 301 (holds the object-file artifact).
        "depSetOfFiles": [
            {"id": "300", "transitiveDepSetIds": ["301"]},
            {"id": "301", "directArtifactIds": ["1"]},
        ],
        "pathFragments": [
            {"id": "11", "label": "app"},
            {"id": "10", "label": "app.o"},
        ],
    })
    ev = BazelAdapter(cquery=cquery, aquery=aquery).collect()
    assert ev.targets[0].kind is TargetKind.EXECUTABLE
    lu = ev.link_units[0]
    assert lu.kind == "executable"               # no .so/.a extension
    assert lu.inputs == ["app.o"]                # resolved through the transitive depset


def test_bazel_link_keeps_shared_library_inputs():
    # A binary linking against a shared lib: the .so input must be recorded,
    # not dropped (ADR-029 D6 — aquery captures link action inputs).
    aquery = json.dumps({
        "artifacts": [
            {"id": "1", "pathFragmentId": "10"},   # app/app.o
            {"id": "2", "pathFragmentId": "11"},   # lib/libbar.so.1
            {"id": "3", "pathFragmentId": "12"},   # app/app (output)
            {"id": "4", "pathFragmentId": "13"},   # app/app.d (dropped)
        ],
        "actions": [{
            "mnemonic": "CppLink", "arguments": ["gcc", "-o", "app/app"],
            "primaryOutputId": "3", "inputDepSetIds": ["400"],
        }],
        "depSetOfFiles": [{"id": "400", "directArtifactIds": ["1", "2", "4"]}],
        "pathFragments": [
            {"id": "10", "label": "app.o", "parentId": "20"},
            {"id": "11", "label": "libbar.so.1", "parentId": "21"},
            {"id": "12", "label": "app", "parentId": "20"},
            {"id": "13", "label": "app.d", "parentId": "20"},
            {"id": "20", "label": "app"},
            {"id": "21", "label": "lib"},
        ],
    })
    ev = BazelAdapter(aquery=aquery).collect()
    inputs = ev.link_units[0].inputs
    assert "app/app.o" in inputs                  # object file
    assert "lib/libbar.so.1" in inputs            # versioned shared library
    assert "app/app.d" not in inputs              # non-library input dropped


def test_bazel_binary_proto_file_diagnostic_no_crash(tmp_path):
    # A binary `--output=proto` blob (not UTF-8): must not raise, and should
    # surface the "pass --output=jsonproto" diagnostic instead.
    pb = tmp_path / "aquery.pb"
    pb.write_bytes(b"\x08\x96\x01\xff\xfe\x00proto\xc3\x28payload")
    ev = BazelAdapter(aquery=pb).collect()
    assert any("jsonproto" in d for d in ev.diagnostics)
    assert not ev.compile_units and not ev.link_units


def test_bazel_missing_precaptured_file_diagnostic(tmp_path):
    missing = tmp_path / "nope.json"
    ev = BazelAdapter(cquery=missing).collect()
    assert any("input not found or unreadable" in d for d in ev.diagnostics)
    assert not ev.targets


def test_bazel_live_query_oserror_diagnostic(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise OSError("no bazel")

    monkeypatch.setattr("abicheck.evidence.adapters.bazel.shutil.which", lambda _x: "/usr/bin/bazel")
    monkeypatch.setattr("abicheck.evidence.adapters.bazel.subprocess.run", boom)
    ev = BazelAdapter(workspace=tmp_path, target="//foo:foo").collect()
    assert any("failed" in d for d in ev.diagnostics)


def test_bazel_link_export_policy_from_argv():
    # version-script and soname carried in -Wl, args must populate the structured
    # LinkUnit fields so the export-policy diff (D9) can index them.
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppLink",
            "arguments": ["gcc", "-shared", "-o", "libfoo.so.2",
                          "-Wl,--version-script=exports.map", "-Wl,-soname,libfoo.so.2"],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "libfoo.so.2"}],
    })
    lu = BazelAdapter(aquery=aquery).collect().link_units[0]
    assert lu.version_script == "exports.map"
    assert lu.soname == "libfoo.so.2"


def test_bazel_link_export_policy_xlinker_spelling():
    # The -Xlinker / space-separated spelling must resolve the same fields.
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppLink",
            "arguments": ["gcc", "-shared", "-o", "libfoo.so",
                          "-Xlinker", "--version-script", "-Xlinker", "v.map",
                          "-Xlinker", "-h", "-Xlinker", "libfoo.so.1"],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "libfoo.so"}],
    })
    lu = BazelAdapter(aquery=aquery).collect().link_units[0]
    assert lu.version_script == "v.map"
    assert lu.soname == "libfoo.so.1"


def test_bazel_link_export_policy_msvc_def_file():
    # MSVC/clang-cl module-definition file is the Windows export map and must
    # populate version_script so DLL export-policy drift is diffable (ADR-029 D9).
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppLink",
            "arguments": ["link.exe", "/DLL", "/DEF:exports.def", "/DEFAULTLIB:libcmt", "/OUT:foo.dll"],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "foo.dll"}],
    })
    lu = BazelAdapter(aquery=aquery).collect().link_units[0]
    assert lu.version_script == "exports.def"   # /DEFAULTLIB: must not be mistaken for it
    assert lu.kind == "shared_library"


def test_bazel_link_export_policy_msvc_def_split_form():
    aquery = json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{
            "mnemonic": "CppLink",
            "arguments": ["link.exe", "/DLL", "/DEF", "exports.def", "/OUT:foo.dll"],
            "primaryOutputId": "1",
        }],
        "pathFragments": [{"id": "10", "label": "foo.dll"}],
    })
    lu = BazelAdapter(aquery=aquery).collect().link_units[0]
    assert lu.version_script == "exports.def"


def test_bazel_live_query_disabled_without_workspace():
    # No pre-captured input and no workspace/target → nothing to query, no crash.
    ev = BazelAdapter(allow_query=True).collect()
    assert not ev.targets and not ev.compile_units


def test_bazel_executable_missing_diagnostic(monkeypatch, tmp_path):
    monkeypatch.setattr("abicheck.evidence.adapters.bazel.shutil.which", lambda _x: None)
    ev = BazelAdapter(workspace=tmp_path, target="//foo:foo").collect()
    assert any("executable not found" in d for d in ev.diagnostics)


def test_bazel_live_query_invokes_subprocess(monkeypatch, tmp_path):
    import subprocess as _sp

    def fake_run(cmd, **kwargs):
        out = CQUERY if "cquery" in cmd else AQUERY
        return _sp.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr("abicheck.evidence.adapters.bazel.shutil.which", lambda _x: "/usr/bin/bazel")
    monkeypatch.setattr("abicheck.evidence.adapters.bazel.subprocess.run", fake_run)
    ev = BazelAdapter(workspace=tmp_path, target="//foo:foo").collect()
    assert ev.targets and ev.compile_units


def test_bazel_live_query_nonzero_exit_diagnostic(monkeypatch, tmp_path):
    import subprocess as _sp

    def fake_run(cmd, **kwargs):
        return _sp.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr("abicheck.evidence.adapters.bazel.shutil.which", lambda _x: "/usr/bin/bazel")
    monkeypatch.setattr("abicheck.evidence.adapters.bazel.subprocess.run", fake_run)
    ev = BazelAdapter(workspace=tmp_path, target="//foo:foo").collect()
    assert any("exited 1" in d for d in ev.diagnostics)


# ── CLI wiring (collect-evidence --bazel-cquery/--bazel-aquery) ──────────────


def test_collect_evidence_bazel_files(tmp_path):
    cq = tmp_path / "cquery.json"
    aq = tmp_path / "aquery.json"
    cq.write_text(CQUERY)
    aq.write_text(AQUERY)
    out = tmp_path / "e"
    result = CliRunner().invoke(
        main,
        ["collect-evidence", "--bazel-cquery", str(cq), "--bazel-aquery", str(aq), "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    pack = EvidencePack.load(out)
    assert pack.build_evidence is not None
    assert any(t.build_system == "bazel" for t in pack.build_evidence.targets)
    assert any(e.name == "bazel" and e.status == "ok" for e in pack.manifest.extractors)


def test_collect_evidence_bazel_link_only_pack_preserved(tmp_path):
    # An aquery with only a link action (no targets/compile units) must still be
    # written to the pack — link_units count toward build-evidence presence.
    aq = tmp_path / "aquery.json"
    aq.write_text(json.dumps({
        "artifacts": [{"id": "1", "pathFragmentId": "10"}],
        "actions": [{"mnemonic": "CppLink", "arguments": ["gcc", "-shared"],
                     "primaryOutputId": "1"}],
        "pathFragments": [{"id": "10", "label": "libfoo.so"}],
    }))
    out = tmp_path / "e"
    result = CliRunner().invoke(main, ["collect-evidence", "--bazel-aquery", str(aq), "-o", str(out)])
    assert result.exit_code == 0, result.output
    pack = EvidencePack.load(out)
    assert pack.build_evidence is not None
    assert len(pack.build_evidence.link_units) == 1
    assert any(e.name == "bazel" and e.status == "ok" for e in pack.manifest.extractors)
