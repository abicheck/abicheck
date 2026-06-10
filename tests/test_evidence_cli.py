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

"""CLI tests for `collect-evidence`, `dump --evidence`, and
`compare --old/--new-evidence` (ADR-028 D6 / ADR-029)."""
from __future__ import annotations

import json

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.evidence.pack import EvidencePack
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
        main, ["collect-evidence", "--compile-db", str(cdb), "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "Evidence pack written" in result.output
    pack = EvidencePack.load(out)
    assert pack.build_evidence is not None
    assert len(pack.build_evidence.compile_units) == 1
    cov = pack.manifest.coverage_for("L3_build")
    assert cov is not None and cov.status.value == "present"


def test_collect_evidence_redacts_manifest_paths(tmp_path, monkeypatch):
    """Codex: provenance paths in manifest.json are home-redacted before write."""
    # Pretend tmp_path is under the user's home so redaction rewrites it.
    monkeypatch.setenv("HOME", str(tmp_path))
    from abicheck.evidence.redaction import RedactionPolicy
    monkeypatch.setattr(
        "abicheck.cli_evidence.DEFAULT_REDACTION",
        RedactionPolicy(home_replacements={str(tmp_path): "~"}),
    )
    cdb = _write_cdb(tmp_path, "c++20")
    out = tmp_path / "e"
    result = CliRunner().invoke(main, [
        "collect-evidence", "--compile-db", str(cdb), "--binary", str(tmp_path / "libfoo.so"),
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
    result = CliRunner().invoke(main, ["collect-evidence", "--compile-db", str(cdb)])
    assert result.exit_code != 0
    assert "output" in result.output.lower() or "missing" in result.output.lower()


def test_collect_evidence_cmake_requires_build_dir(tmp_path):
    result = CliRunner().invoke(
        main, ["collect-evidence", "--cmake", "-o", str(tmp_path / "e")],
    )
    assert result.exit_code != 0
    assert "build-dir" in result.output


def test_dump_attach_evidence_ref(tmp_path):
    # Build an evidence pack first.
    cdb = _write_cdb(tmp_path, "c++20")
    ev_dir = tmp_path / "e"
    CliRunner().invoke(main, ["collect-evidence", "--compile-db", str(cdb), "-o", str(ev_dir)])

    # Attach it to an existing snapshot via dump on a JSON snapshot is not
    # supported (dump takes a binary), so attach directly through the helper
    # path exercised by `dump --evidence`: load pack and to_ref.
    pack = EvidencePack.load(ev_dir)
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    snap.evidence_pack = pack.to_ref(path_hint=str(ev_dir))
    out = tmp_path / "snap.json"
    save_snapshot(snap, out)

    reloaded = load_snapshot(out)
    assert reloaded.evidence_pack is not None
    assert reloaded.evidence_pack.content_hash == pack.content_hash()


def test_dump_invalid_evidence_dir_errors(tmp_path):
    # A directory with no manifest is not a valid pack.
    bad = tmp_path / "bad"
    bad.mkdir()
    snap = AbiSnapshot(library="l", version="1")
    save_snapshot(snap, tmp_path / "s.json")
    # Exercise the attach helper directly (dump needs a real binary).
    import click
    import pytest

    from abicheck.cli_evidence import attach_evidence_pack

    with pytest.raises(click.ClickException):
        attach_evidence_pack(snap, bad)


def _make_snap(tmp_path, name, version):
    snap = AbiSnapshot(library="libfoo.so", version=version, from_headers=True)
    p = tmp_path / name
    save_snapshot(snap, p)
    return p


def test_compare_with_evidence_emits_coverage_and_findings(tmp_path):
    old_cdb = _write_cdb(tmp_path, "c++17")
    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect-evidence", "--compile-db", str(old_cdb), "-o", str(ev_old)])
    runner.invoke(main, ["collect-evidence", "--compile-db", str(new_cdb), "-o", str(ev_new)])

    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-evidence", str(ev_old), "--new-evidence", str(ev_new),
        "--format", "markdown",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    # D7 coverage table is emitted to stderr.
    assert "Evidence coverage:" in result.stderr
    assert "L3 build context" in result.stderr
    # The -std drift surfaces as an ABI-relevant build-flag finding (RISK).
    assert "COMPATIBLE_WITH_RISK" in result.stdout or "Deployment Risk" in result.stdout


def test_compare_json_carries_evidence_coverage_block(tmp_path):
    """ADR-028 D7: the JSON report carries a structured evidence_coverage block."""
    cdb = _write_cdb(tmp_path, "c++20")
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect-evidence", "--compile-db", str(cdb), "-o", str(ev_new)])
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--new-evidence", str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    assert payload["report_schema_version"] == "1.2"
    cov = {row["layer"]: row for row in payload["evidence_coverage"]}
    assert set(cov) >= {"L0", "L1", "L2", "L3_build", "L4_source_abi", "L5_source_graph"}
    assert cov["L3_build"]["status"] == "present"


def test_compare_json_without_evidence_omits_coverage(tmp_path):
    """No evidence → no evidence_coverage key (additive, opt-in)."""
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old_snap), str(new_snap), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "evidence_coverage" not in json.loads(result.stdout)


def test_compare_evidence_mode_without_packs_is_noted(tmp_path):
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap), "--evidence-mode", "build",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    assert "evidence-mode build" in result.stderr


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
        "collect-evidence", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-scope", "full",
        "--clang-bin", "clang-definitely-not-installed-xyz",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "source-only checks disabled" in result.output
    pack = EvidencePack.load(out)
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
        "collect-evidence", "--source-abi", "--source-abi-extractor", "android",
        "--android-dump", str(dump), "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    pack = EvidencePack.load(out)
    assert pack.source_abi is not None
    assert any(e.qualified_name == "Foo" for e in pack.source_abi.reachable_types)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value == "present"


def test_collect_evidence_android_requires_dump(tmp_path):
    result = CliRunner().invoke(main, [
        "collect-evidence", "--source-abi", "--source-abi-extractor", "android",
        "-o", str(tmp_path / "ev"),
    ])
    assert result.exit_code != 0
    assert "requires --android-dump" in result.output


def _ev_with_default_arg(tmp_path, name, default):
    """Write an evidence pack whose L4 surface has one function with a default arg."""
    from abicheck.evidence.source_abi import SourceAbiTu, SourceEntity, SourceLocation
    from abicheck.evidence.source_link import link_source_abi

    ent = SourceEntity(
        id="id", kind="function", qualified_name="add", mangled_name="_Z3addii",
        signature_hash="sig", value=default,
        source_location=SourceLocation(path="include/foo.h", origin="PUBLIC_HEADER"),
        visibility="public_header", api_relevant=True,
    )
    tu = SourceAbiTu(tu_id="cu://a", functions=[ent], public_header_roots=["include/foo.h"])
    pack = EvidencePack.empty(tmp_path / name)
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
        "--old-evidence", str(ev_old), "--new-evidence", str(ev_new),
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
    from abicheck.evidence.source_abi import SourceAbiTu, SourceEntity, SourceLocation

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
    import abicheck.evidence.source_extractors as se
    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())

    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect-evidence", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-scope", "full",
        "--source-abi-cache", str(tmp_path / "cache"),
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "L4 source ABI replay: clang extractor" in result.output
    pack = EvidencePack.load(out)
    assert pack.source_abi is not None
    assert any(e.qualified_name == "add" for e in pack.source_abi.reachable_declarations)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value == "present"


def test_collect_evidence_source_abi_castxml_unavailable(tmp_path):
    """The castxml backend degrades gracefully when castxml is absent."""
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect-evidence", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-extractor", "castxml",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    # Either castxml ran (present) or it was unavailable (graceful) — both fine,
    # but the run must not crash and must record an L4 row.
    pack = EvidencePack.load(out)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value in ("present", "partial")


def test_collect_evidence_source_abi_without_compile_units(tmp_path):
    """--source-abi with no L3 build context reports the missing prerequisite."""
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect-evidence", "--source-abi", "--source-abi-extractor", "clang",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "no L3 build context" in result.output


def test_exported_symbols_from_binary_edge_cases(tmp_path):
    from pathlib import Path

    from abicheck.cli_evidence import _exported_symbols_from_binary
    assert _exported_symbols_from_binary(None) == []
    assert _exported_symbols_from_binary(Path(tmp_path / "missing")) == []
    junk = tmp_path / "x.txt"
    junk.write_text("not a binary")
    assert _exported_symbols_from_binary(junk) == []
