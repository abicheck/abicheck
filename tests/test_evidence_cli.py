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
