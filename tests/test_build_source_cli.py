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

"""CLI tests for `collect`, `dump --evidence`, and
`compare --old/--new-build-info` (ADR-028 D6 / ADR-029)."""
from __future__ import annotations

import json

from click.testing import CliRunner

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.cli import main
from abicheck.model import AbiSnapshot
from abicheck.serialization import load_snapshot, save_snapshot


def _write_cdb(tmp_path, std):
    cdb = [{
        "directory": str(tmp_path),
        "file": "src/foo.cpp",
        "arguments": ["c++", f"-std={std}", "-Iinclude", "-c", "src/foo.cpp"],
    }]
    p = tmp_path / f"cc_{std}.json"
    p.write_text(json.dumps(cdb))
    return p


def test_collect_evidence_creates_pack(tmp_path):
    cdb = _write_cdb(tmp_path, "c++20")
    out = tmp_path / "libfoo.evidence"
    result = CliRunner().invoke(
        main, ["collect", "--compile-db", str(cdb), "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "Evidence pack written" in result.output
    pack = BuildSourcePack.load(out)
    assert pack.build_evidence is not None
    assert len(pack.build_evidence.compile_units) == 1
    cov = pack.manifest.coverage_for("L3_build")
    assert cov is not None and cov.status.value == "present"


def test_collect_evidence_redacts_manifest_paths(tmp_path, monkeypatch):
    """Codex: provenance paths in manifest.json are home-redacted before write."""
    # Pretend tmp_path is under the user's home so redaction rewrites it.
    monkeypatch.setenv("HOME", str(tmp_path))
    from abicheck.buildsource.redaction import RedactionPolicy
    monkeypatch.setattr(
        "abicheck.cli_buildsource.DEFAULT_REDACTION",
        RedactionPolicy(home_replacements={str(tmp_path): "~"}),
    )
    cdb = _write_cdb(tmp_path, "c++20")
    out = tmp_path / "e"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb), "--binary", str(tmp_path / "libfoo.so"),
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    manifest = json.loads((out / "manifest.json").read_text())
    # No absolute tmp_path leaks into the manifest provenance.
    blob = json.dumps(manifest)
    assert str(tmp_path) not in blob
    assert manifest["inputs"]["binary"].startswith("~")
    assert any(e["inputs"] and e["inputs"][0].startswith("~") for e in manifest["extractors"])


def test_collect_evidence_requires_output(tmp_path):
    cdb = _write_cdb(tmp_path, "c++20")
    result = CliRunner().invoke(main, ["collect", "--compile-db", str(cdb)])
    assert result.exit_code != 0
    assert "output" in result.output.lower() or "missing" in result.output.lower()


def test_collect_evidence_cmake_requires_build_dir(tmp_path):
    result = CliRunner().invoke(
        main, ["collect", "--cmake", "-o", str(tmp_path / "e")],
    )
    assert result.exit_code != 0
    assert "build-dir" in result.output


def test_dump_attach_evidence_ref(tmp_path):
    # Build an evidence pack first.
    cdb = _write_cdb(tmp_path, "c++20")
    ev_dir = tmp_path / "e"
    CliRunner().invoke(main, ["collect", "--compile-db", str(cdb), "-o", str(ev_dir)])

    # Attach it to an existing snapshot via dump on a JSON snapshot is not
    # supported (dump takes a binary), so attach directly through the helper
    # path exercised by `dump --evidence`: load pack and to_ref.
    pack = BuildSourcePack.load(ev_dir)
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    snap.build_source_pack = pack.to_ref(path_hint=str(ev_dir))
    out = tmp_path / "snap.json"
    save_snapshot(snap, out)

    reloaded = load_snapshot(out)
    assert reloaded.build_source_pack is not None
    assert reloaded.build_source_pack.content_hash == pack.content_hash()


def test_dump_empty_build_info_dir_is_noop(tmp_path):
    # Source-tree-centric model: a plain directory with no manifest and no
    # compile DB is a build dir that yields no L3 facts — graceful, not an error
    # (ADR-028 D3). Nothing is embedded.
    bad = tmp_path / "bad"
    bad.mkdir()
    snap = AbiSnapshot(library="l", version="1")
    save_snapshot(snap, tmp_path / "s.json")

    from abicheck.cli_buildsource import embed_build_source

    embed_build_source(snap, bad, None)
    assert snap.build_source is None


def test_dump_malformed_pack_dir_errors(tmp_path):
    # A directory *with* a manifest.json is treated as a pack; a malformed one
    # is still a hard error so a corrupt collect output is not silently ignored.
    import click
    import pytest

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{ this is not json", encoding="utf-8")
    snap = AbiSnapshot(library="l", version="1")

    from abicheck.cli_buildsource import embed_build_source

    with pytest.raises(click.ClickException):
        embed_build_source(snap, bad, None)


def _make_snap(tmp_path, name, version):
    snap = AbiSnapshot(library="libfoo.so", version=version, from_headers=True)
    p = tmp_path / name
    save_snapshot(snap, p)
    return p


def test_compare_with_source_graph_packs_runs_graph_diff(tmp_path):
    """ADR-031: two --source-graph packs drive the graph-diff wiring in
    diff_embedded_build_source (folded into the verdict pipeline). Build-only
    graphs yield no graph findings, but the L5 coverage must read present and
    the comparison must still succeed."""
    old_cdb = _write_cdb(tmp_path, "c++17")
    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(old_cdb),
                         "--source-graph", "summary", "-o", str(ev_old)])
    runner.invoke(main, ["collect", "--compile-db", str(new_cdb),
                         "--source-graph", "summary", "-o", str(ev_new)])
    assert BuildSourcePack.load(ev_old).source_graph is not None
    assert BuildSourcePack.load(ev_new).source_graph is not None

    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 1, 2, 4), result.output
    payload = json.loads(result.stdout)
    cov = {row["layer"]: row for row in payload["layer_coverage"]}
    assert cov["L5_source_graph"]["status"] == "present"


def test_compare_with_evidence_emits_coverage_and_findings(tmp_path):
    old_cdb = _write_cdb(tmp_path, "c++17")
    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(old_cdb), "-o", str(ev_old)])
    runner.invoke(main, ["collect", "--compile-db", str(new_cdb), "-o", str(ev_new)])

    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
        "--format", "markdown",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    # D7 coverage table is emitted to stderr.
    assert "Evidence coverage:" in result.stderr
    assert "L3 build context" in result.stderr
    # The -std drift surfaces as an ABI-relevant build-flag finding (RISK).
    assert "COMPATIBLE_WITH_RISK" in result.stdout or "Deployment Risk" in result.stdout


def test_compare_json_carries_layer_coverage_block(tmp_path):
    """ADR-028 D7: the JSON report carries a structured layer_coverage block."""
    cdb = _write_cdb(tmp_path, "c++20")
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(cdb), "-o", str(ev_new)])
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--new-build-info", str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    assert payload["report_schema_version"] == "2.0"
    cov = {row["layer"]: row for row in payload["layer_coverage"]}
    assert set(cov) >= {"L0", "L1", "L2", "L3_build", "L4_source_abi", "L5_source_graph"}
    assert cov["L3_build"]["status"] == "present"


def test_compare_asymmetric_old_only_reports_target_not_collected(tmp_path):
    """Only --old-build-info: the target (new) side has no build facts, so the
    coverage table must report L3 not_collected — not reuse the old pack and
    claim source/build checks ran for this scan (Codex review)."""
    cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(cdb), "-o", str(ev_old)])
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--old-build-info", str(ev_old),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    cov = {row["layer"]: row for row in payload["layer_coverage"]}
    assert cov["L3_build"]["status"] == "not_collected"


def test_compare_json_without_evidence_omits_coverage(tmp_path):
    """No evidence → no layer_coverage key (additive, opt-in)."""
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old_snap), str(new_snap), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "layer_coverage" not in json.loads(result.stdout)


def test_compare_collect_mode_without_packs_is_noted(tmp_path):
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap), "--collect-mode", "build",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    assert "collect-mode build" in result.stderr


def test_compare_without_evidence_is_unchanged(tmp_path):
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old_snap), str(new_snap)])
    assert result.exit_code == 0, result.output
    assert "Evidence coverage:" not in result.stderr


# -- L4 source ABI replay (ADR-030 phases 5-7 + CLI wiring) ------------------


def test_collect_evidence_source_abi_graceful_without_tool(tmp_path):
    """Source ABI replay degrades gracefully when the tool is missing.

    The user message must be explicit that clang is required and that source-only
    checks are disabled (never abort the collection).
    """
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-scope", "full",
        "--clang-bin", "clang-definitely-not-installed-xyz",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "source-only checks disabled" in result.output
    pack = BuildSourcePack.load(out)
    cov = pack.manifest.coverage_for("L4_source_abi")
    # Replay ran but the tool was absent → partial, not present (and not silent).
    assert cov is not None and cov.status.value == "partial"


def test_collect_evidence_source_abi_android_dump(tmp_path):
    """The Android backend normalizes a pre-captured dump into the pack (D9)."""
    dump = tmp_path / "libfoo.lsdump"
    dump.write_text(json.dumps({
        "source_file": "include/foo.h",
        "functions": [{"function_name": "foo", "linker_set_key": "_Z3foov", "return_type": "void"}],
        "record_types": [{"name": "Foo", "size": 8, "source_file": "include/foo.h"}],
    }))
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--source-abi", "--source-abi-extractor", "android",
        "--android-dump", str(dump), "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert any(e.qualified_name == "Foo" for e in pack.source_abi.reachable_types)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value == "present"


def test_collect_evidence_android_requires_dump(tmp_path):
    result = CliRunner().invoke(main, [
        "collect", "--source-abi", "--source-abi-extractor", "android",
        "-o", str(tmp_path / "ev"),
    ])
    assert result.exit_code != 0
    assert "requires --android-dump" in result.output


def _ev_with_default_arg(tmp_path, name, default):
    """Write an evidence pack whose L4 surface has one function with a default arg."""
    from abicheck.buildsource.source_abi import (
        SourceAbiTu,
        SourceEntity,
        SourceLocation,
    )
    from abicheck.buildsource.source_link import link_source_abi

    ent = SourceEntity(
        id="id", kind="function", qualified_name="add", mangled_name="_Z3addii",
        signature_hash="sig", value=default,
        source_location=SourceLocation(path="include/foo.h", origin="PUBLIC_HEADER"),
        visibility="public_header", api_relevant=True,
    )
    tu = SourceAbiTu(tu_id="cu://a", functions=[ent], public_header_roots=["include/foo.h"])
    pack = BuildSourcePack.empty(tmp_path / name)
    pack.source_abi = link_source_abi([tu], library="libfoo.so")
    pack.write()
    return tmp_path / name


def test_compare_source_abi_findings_and_capabilities(tmp_path):
    """An L4 default-argument change surfaces as a finding, and the capability
    report explains which checks ran and which did not (the user's ask)."""
    ev_old = _ev_with_default_arg(tmp_path, "old.evidence", "x=1")
    ev_new = _ev_with_default_arg(tmp_path, "new.evidence", "x=2")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    # The source-replay finding is folded into the verdict pipeline.
    assert "default_argument_changed" in result.stdout.lower()
    # Authority rule (ADR-028 D3): a source-only L4 finding with no artifact-backed
    # break must NOT escalate to a breaking verdict — it stays API/source-level.
    assert payload["verdict"] != "breaking"
    kinds = {f.get("kind") for f in payload.get("changes", [])}
    assert "default_argument_changed" in kinds
    # And the L4 finding is partitioned as an API break, never a BREAKING kind.
    from abicheck.checker_policy import BREAKING_KINDS, ChangeKind
    assert ChangeKind.DEFAULT_ARGUMENT_CHANGED not in BREAKING_KINDS
    # The capability report names what is on/off and why.
    assert "Checks enabled for this scan" in result.stderr
    assert "[off]" in result.stderr
    # Macros/default-args/bodies row references its source/clang requirement.
    assert "inline/template/constexpr" in result.stderr


def _fake_clang_extractor():
    """A drop-in ClangSourceExtractor replacement that needs no real clang."""
    from abicheck.buildsource.source_abi import (
        SourceAbiTu,
        SourceEntity,
        SourceLocation,
    )

    class _Fake:
        name = "clang-source"
        version = "0.1"

        def __init__(self, **kw):
            pass

        def available(self):
            return True

        def extract(self, cu, *, public_header_roots, target_id=""):
            ent = SourceEntity(
                id="e", kind="function", qualified_name="add",
                mangled_name="_Z3addi", signature_hash="sig", value="p0=1",
                source_location=SourceLocation(path="include/foo.h", origin="PUBLIC_HEADER"),
                visibility="public_header", api_relevant=True,
            )
            return SourceAbiTu(
                tu_id=cu.id, source=cu.source,
                public_header_roots=list(public_header_roots), functions=[ent],
            )

    return _Fake


def test_collect_evidence_source_abi_success(tmp_path, monkeypatch):
    """The clang collection path writes a populated L4 surface and PRESENT row."""
    import abicheck.buildsource.source_extractors as se
    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())

    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-scope", "full",
        "--source-abi-cache", str(tmp_path / "cache"),
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "L4 source ABI replay: clang extractor" in result.output
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert any(e.qualified_name == "add" for e in pack.source_abi.reachable_declarations)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value == "present"


def test_include_map_for_replay_helper(monkeypatch):
    """_include_map_for_replay returns the depfile map, or None when clang is absent."""
    import abicheck.buildsource.include_graph as ig
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.cli_buildsource import _include_map_for_replay

    class _Avail:
        clang_bin = "clang++"

        def __init__(self, **kw):
            self.diagnostics = []

        def available(self):
            return True

        def extract_from_build(self, build):
            return {"cu://a": ["include/foo.h"]}

    monkeypatch.setattr(ig, "ClangIncludeExtractor", _Avail)
    assert _include_map_for_replay(BuildEvidence(), "clang") == {
        "cu://a": ["include/foo.h"]
    }

    class _Unavail(_Avail):
        def available(self):
            return False

    monkeypatch.setattr(ig, "ClangIncludeExtractor", _Unavail)
    assert _include_map_for_replay(BuildEvidence(), "clang") is None


def test_collect_evidence_source_abi_uses_include_graph(tmp_path, monkeypatch):
    """headers-only/changed scopes feed the depfile include map into replay."""
    import abicheck.buildsource.source_extractors as se
    import abicheck.cli_buildsource as ce

    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())
    monkeypatch.setattr(
        ce, "_include_map_for_replay",
        lambda merged, clang_bin: {"cu://x": ["include/foo.h"]},
    )
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-scope", "headers-only",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert pack.source_abi.coverage.get("include_graph_used") is True


def test_collect_evidence_source_abi_castxml_unavailable(tmp_path):
    """The castxml backend degrades gracefully when castxml is absent."""
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-extractor", "castxml",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    # Either castxml ran (present) or it was unavailable (graceful) — both fine,
    # but the run must not crash and must record an L4 row.
    pack = BuildSourcePack.load(out)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value in ("present", "partial")


def test_collect_evidence_source_abi_without_compile_units(tmp_path):
    """--source-abi with no L3 build context reports the missing prerequisite."""
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--source-abi", "--source-abi-extractor", "clang",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "no L3 build context" in result.output


def test_exported_symbols_from_binary_edge_cases(tmp_path):
    from pathlib import Path

    from abicheck.cli_buildsource import _exported_symbols_from_binary
    assert _exported_symbols_from_binary(None) == []
    assert _exported_symbols_from_binary(Path(tmp_path / "missing")) == []
    junk = tmp_path / "x.txt"
    junk.write_text("not a binary")
    assert _exported_symbols_from_binary(junk) == []


# ── Source-tree-centric inline collection (ADR-028..033 amendment) ────────────


def test_embed_build_info_compile_db_inline(tmp_path):
    """`--build-info compile_commands.json` collects L3 inline (no pack dir)."""
    from abicheck.cli_buildsource import embed_build_source

    cdb = _write_cdb(tmp_path, "c++17")
    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(snap, cdb, None)

    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1
    cov = snap.build_source.manifest.coverage_for("L3_build")
    assert cov is not None and cov.status.value == "present"


def test_embed_build_info_autodiscovers_compile_db_in_tree(tmp_path):
    """A compile DB inside the --sources tree is auto-discovered for L3."""
    from abicheck.cli_buildsource import embed_build_source

    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{
        "directory": str(tree),
        "file": "foo.cpp",
        "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
    }]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))

    snap = AbiSnapshot(library="libfoo.so", version="1")
    # No --build-info: the tree's compile_commands.json is found automatically.
    embed_build_source(snap, None, tree)
    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1


def test_embed_sources_without_tool_is_graceful(tmp_path):
    """`--sources` with a compile DB but no clang yields partial L4, not abort."""
    from abicheck.cli_buildsource import embed_build_source

    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{
        "directory": str(tree),
        "file": "foo.cpp",
        "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
    }]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))

    snap = AbiSnapshot(library="libfoo.so", version="1")
    # clang is almost certainly absent under the fast unit lane; replay degrades
    # to partial coverage and the dump still succeeds (ADR-028 D3).
    embed_build_source(snap, None, tree, clang_bin="definitely-not-a-real-clang")
    assert snap.build_source is not None
    l4 = snap.build_source.manifest.coverage_for("L4_source_abi")
    assert l4 is not None and l4.status.value in ("partial", "present")


def test_build_query_skipped_without_allow_flag(tmp_path):
    """build.query is not executed unless --allow-build-query (ADR-032 D5)."""
    from abicheck.buildsource.inline import BuildConfig, collect_inline_pack

    tree = tmp_path / "src"
    tree.mkdir()
    cfg = BuildConfig(query="this-tool-should-never-run --emit", compile_db="cc.json")
    pack = collect_inline_pack(
        sources=tree, build_info=None, build_config=cfg, allow_build_query=False,
    )
    # Nothing ran, nothing discovered → no facts at all.
    assert pack is None


def test_merge_combines_binary_and_source_snapshots(tmp_path):
    """`merge` keeps the binary base and folds in the source side's L3 facts."""
    from abicheck.cli_buildsource import embed_build_source

    # Source/build side: a snapshot carrying only L3 build facts.
    src_snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(src_snap, _write_cdb(tmp_path, "c++17"), None)
    src_path = tmp_path / "libfoo.src.json"
    save_snapshot(src_snap, src_path)

    # Binary side: a snapshot with an ABI surface (faked ELF marker) and no pack.
    from abicheck.elf_metadata import ElfMetadata

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_path = tmp_path / "libfoo.bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(main, ["merge", str(bin_path), str(src_path), "-o", str(out)])
    assert result.exit_code == 0, result.output

    merged = load_snapshot(out)
    assert merged.elf is not None          # base ABI surface preserved
    assert merged.build_source is not None  # source-side facts folded in
    assert merged.build_source.build_evidence is not None


# ── inline.py pure-logic coverage (no external tools) ─────────────────────────


def test_build_config_from_dict_and_load(tmp_path):
    from abicheck.buildsource.inline import (
        BuildConfig,
        discover_build_config,
        load_build_config,
    )

    cfg = BuildConfig.from_dict({
        "build": {"system": "bazel", "query": "bazel cquery //x", "compile_db": "out/cc.json"},
        "sources": {"public_headers": ["a/**.hpp"], "exclude": "**/test/**"},
    })
    assert cfg.system == "bazel"
    assert cfg.query.startswith("bazel cquery")
    assert cfg.compile_db == "out/cc.json"
    assert cfg.public_headers == ["a/**.hpp"]
    assert cfg.exclude == ["**/test/**"]

    # Empty / malformed inputs fall back to all-defaults.
    assert BuildConfig.from_dict({}).system == "auto"
    assert BuildConfig.from_dict({"build": "nope"}).query == ""

    # load_build_config: missing file → defaults; present file → parsed.
    assert load_build_config(tmp_path / "nope.yml").system == "auto"
    p = tmp_path / ".abicheck.yml"
    p.write_text("build:\n  system: cmake\n", encoding="utf-8")
    assert load_build_config(p).system == "cmake"
    # A YAML scalar (not a mapping) is tolerated.
    p.write_text("just a string\n", encoding="utf-8")
    assert load_build_config(p).system == "auto"

    # discover_build_config finds .abicheck.yml at the tree root.
    tree = tmp_path / "src"
    tree.mkdir()
    assert discover_build_config(tree) is None
    (tree / ".abicheck.yml").write_text("build: {}\n", encoding="utf-8")
    assert discover_build_config(tree) == tree / ".abicheck.yml"
    assert discover_build_config(None) is None


def test_is_pack_dir_and_compile_db_resolution(tmp_path):
    from abicheck.buildsource.inline import (
        _autodiscover_compile_db,
        _compile_db_at,
        is_pack_dir,
    )

    assert is_pack_dir(None) is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert is_pack_dir(plain) is False
    (plain / "manifest.json").write_text("{}", encoding="utf-8")
    assert is_pack_dir(plain) is True

    # _compile_db_at: a build dir with build/compile_commands.json is found.
    bd = tmp_path / "bd"
    (bd / "build").mkdir(parents=True)
    cdb = bd / "build" / "compile_commands.json"
    cdb.write_text("[]", encoding="utf-8")
    assert _compile_db_at(bd) == cdb
    assert _compile_db_at(tmp_path / "empty-missing") is None

    # auto-discovery inside a source tree (top-level).
    tree = tmp_path / "src"
    tree.mkdir()
    assert _autodiscover_compile_db(tree) is None
    top = tree / "compile_commands.json"
    top.write_text("[]", encoding="utf-8")
    assert _autodiscover_compile_db(tree) == top
    assert _autodiscover_compile_db(None) is None


def test_build_inline_coverage_rows():
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.inline import build_inline_coverage

    rows = build_inline_coverage(BuildEvidence(), has_build=False, surface=None, graph=None)
    by = {r.layer: r for r in rows}
    assert by["L3_build"].status.value == "not_collected"
    assert by["L4_source_abi"].status.value == "not_collected"
    assert by["L5_source_graph"].status.value == "not_collected"


def test_build_query_failure_is_recorded(tmp_path, monkeypatch):
    """A failing build.query command degrades to a failed extractor, no abort."""
    from abicheck.buildsource.inline import BuildConfig, collect_inline_pack

    tree = tmp_path / "src"
    tree.mkdir()
    # An unparseable command string is handled gracefully.
    cfg = BuildConfig(query='unterminated "quote', compile_db="cc.json")
    pack = collect_inline_pack(
        sources=tree, build_info=None, build_config=cfg, allow_build_query=True,
    )
    # Command never produced a DB and there is nothing else → no pack.
    assert pack is None


def test_merge_requires_two_inputs(tmp_path):
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import save_snapshot

    snap = AbiSnapshot(library="l", version="1")
    p = tmp_path / "a.json"
    save_snapshot(snap, p)
    result = CliRunner().invoke(main, ["merge", str(p), "-o", str(tmp_path / "o.json")])
    assert result.exit_code != 0
    assert "at least two" in result.output


def test_merge_without_embedded_facts_is_noted(tmp_path):
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import load_snapshot, save_snapshot

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    save_snapshot(AbiSnapshot(library="l", version="1"), a)
    save_snapshot(AbiSnapshot(library="l", version="2"), b)
    out = tmp_path / "o.json"
    result = CliRunner().invoke(main, ["merge", str(a), str(b), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "no input carried embedded build_source" in result.output
    # Base ABI surface still written.
    assert load_snapshot(out).library == "l"


def test_dump_source_only_no_binary(tmp_path):
    """`dump --sources <tree>` with no SO_PATH writes a binary-less baseline.

    The parallel-baseline flow that `merge` consumes (Codex P2): SO_PATH is
    optional when --sources/--build-info is given.
    """
    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{"directory": str(tree), "file": "foo.cpp",
            "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"]}]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))

    out = tmp_path / "libfoo.src.json"
    result = CliRunner().invoke(main, ["dump", "--sources", str(tree), "-o", str(out)])
    assert result.exit_code == 0, result.output

    snap = load_snapshot(out)
    assert snap.elf is None and snap.pe is None and snap.macho is None  # no binary
    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1


def test_dump_with_no_binary_and_no_inputs_errors():
    """A bare `dump` (no SO_PATH, no --sources/--build-info) errors clearly."""
    result = CliRunner().invoke(main, ["dump"])
    assert result.exit_code != 0
    assert "source-only" in result.output


def test_dump_source_only_then_merge_with_binary(tmp_path):
    """End-to-end: source-only dump + binary dump combine via `merge`."""
    from abicheck.elf_metadata import ElfMetadata
    from abicheck.model import AbiSnapshot

    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{"directory": str(tree), "file": "foo.cpp",
            "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"]}]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))
    src_out = tmp_path / "libfoo.src.json"
    assert CliRunner().invoke(
        main, ["dump", "--sources", str(tree), "-o", str(src_out)]
    ).exit_code == 0

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_path = tmp_path / "libfoo.bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        main, ["merge", str(bin_path), str(src_out), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    merged = load_snapshot(out)
    assert merged.elf is not None  # binary base kept
    assert merged.build_source is not None and merged.build_source.build_evidence is not None


def test_mixed_build_pack_and_raw_sources_hash_distinguishes_trees(tmp_path):
    """Same build-info pack + different source trees → different content_hash.

    Codex P2: inline source facts must contribute to the combined
    build_source_pack content hash even when the build side is an on-disk pack.
    """
    from pathlib import Path

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_buildsource import _combine_packs

    # On-disk build-info pack.
    bi = BuildSourcePack.empty(tmp_path / "bi")
    ev = BuildEvidence()
    ev.compile_units.append(CompileUnit(id="cu://x", source="x.cpp"))
    bi.build_evidence = ev
    bi.write()
    bi = BuildSourcePack.load(tmp_path / "bi")

    def _inline_with(library: str) -> BuildSourcePack:
        return BuildSourcePack(root=Path(""), source_abi=SourceAbiSurface(library=library))

    a = _combine_packs(bi, None, _inline_with("tree_a"))
    b = _combine_packs(bi, None, _inline_with("tree_b"))
    assert a is not None and b is not None
    assert a.content_hash() != b.content_hash()
    # And the build evidence still participates (same pack → shared component).
    same = _combine_packs(bi, None, _inline_with("tree_a"))
    assert a.content_hash() == same.content_hash()
