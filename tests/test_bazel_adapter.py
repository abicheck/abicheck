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


def test_bazel_live_query_oserror_diagnostic(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise OSError("no bazel")

    monkeypatch.setattr("abicheck.evidence.adapters.bazel.shutil.which", lambda _x: "/usr/bin/bazel")
    monkeypatch.setattr("abicheck.evidence.adapters.bazel.subprocess.run", boom)
    ev = BazelAdapter(workspace=tmp_path, target="//foo:foo").collect()
    assert any("failed" in d for d in ev.diagnostics)


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
