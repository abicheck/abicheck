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

"""Evidence-extractor plugin interface and security model (ADR-032).

Covers the *pure* halves — the action-permission model (D5), capability model
(D4), collection modes (D9), manifest parsing + command rendering (D3), and the
reproducibility ledger (D10) — plus end-to-end external CLI extractors driven
through a fake ``python -c`` tool so no third-party binary is needed.

Manifests are built as dicts and serialized with ``yaml.safe_dump`` rather than
hand-written YAML strings: ``sys.executable`` on Windows is a backslash path
(``C:\\...\\python.exe``) that a double-quoted YAML scalar would reject as an
invalid escape, so the emitter must do the quoting.
"""
from __future__ import annotations

import json
import sys

import pytest
import yaml

from abicheck.evidence.extractor import (
    DEFAULT_ALLOWED_ACTIONS,
    ActionNotPermittedError,
    CollectionAction,
    CollectionContext,
    CollectionMode,
    DiscoveryResult,
    ExtractorCapabilities,
    RawArtifact,
    ValidationResult,
    parse_action,
    parse_actions,
    require_action,
    resolve_allowed_actions,
)
from abicheck.evidence.extractor_manifest import (
    ExternalCliExtractor,
    ManifestError,
    load_extractor_manifest,
    render_command,
    run_external_extractor,
)
from abicheck.evidence.model import ExtractorRecord

# ── helpers ───────────────────────────────────────────────────────────────────


def _dump(tmp_path, data, name="m.yaml"):
    """Serialize a manifest *dict* to YAML (handles Windows backslash paths)."""
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data))
    return p


def _write_be_script() -> str:
    """A python one-liner that writes a minimal BuildEvidence JSON to argv[1]."""
    return (
        "import json,sys,os;p=sys.argv[1];"
        "os.makedirs(os.path.dirname(p),exist_ok=True);"
        "json.dump({'schema_version':1,'compile_units':[{'id':'cu://a',"
        "'source':'a.cpp','argv':['cc','-c','a.cpp'],'language':'CXX'}]},"
        "open(p,'w'))"
    )


def _tool_manifest_dict(name="fake-tool", action="inspect"):
    """A self-contained manifest whose collect command writes the normalized BE."""
    return {
        "name": name,
        "version": "9.9",
        "capabilities": {"compile_db": True},
        "allowed_actions": [action],
        "commands": {
            "collect": [
                sys.executable, "-c", _write_be_script(),
                "{normalized_dir}/build_evidence.json",
            ]
        },
        "outputs": {
            "normalized": [
                {"kind": "build_evidence", "path": f"normalized/{name}/build_evidence.json"}
            ]
        },
    }


# ── D5: action-permission model ───────────────────────────────────────────────


def test_default_allowed_is_inspect_only():
    assert DEFAULT_ALLOWED_ACTIONS == frozenset({CollectionAction.INSPECT})


def test_parse_action_accepts_enum_and_string_rejects_unknown():
    assert parse_action("inspect") is CollectionAction.INSPECT
    assert parse_action(CollectionAction.RUN_BUILD) is CollectionAction.RUN_BUILD
    with pytest.raises(ValueError, match="unknown collection action"):
        parse_action("delete_everything")


def test_parse_actions_set():
    out = parse_actions(["inspect", "query_build_system"])
    assert out == {CollectionAction.INSPECT, CollectionAction.QUERY_BUILD_SYSTEM}
    assert parse_actions([]) == set()


def test_resolve_intersects_ceiling_with_run_permitted():
    declared = {CollectionAction.INSPECT, CollectionAction.RUN_BUILD}
    run_permitted = {CollectionAction.INSPECT, CollectionAction.QUERY_BUILD_SYSTEM}
    assert resolve_allowed_actions(declared, run_permitted) == {CollectionAction.INSPECT}


def test_resolve_always_strips_network():
    declared = {CollectionAction.INSPECT, CollectionAction.NETWORK}
    run_permitted = {CollectionAction.INSPECT, CollectionAction.NETWORK}
    assert CollectionAction.NETWORK not in resolve_allowed_actions(declared, run_permitted)


def test_require_action_raises_when_denied():
    with pytest.raises(ActionNotPermittedError, match="run_build"):
        require_action(CollectionAction.RUN_BUILD, {CollectionAction.INSPECT}, extractor="x")
    require_action(CollectionAction.INSPECT, {CollectionAction.INSPECT})  # no raise


def test_context_permits_and_require():
    ctx = CollectionContext(allowed_actions={CollectionAction.INSPECT})
    assert ctx.permits(CollectionAction.INSPECT)
    assert not ctx.permits(CollectionAction.QUERY_BUILD_SYSTEM)
    with pytest.raises(ActionNotPermittedError):
        ctx.require(CollectionAction.QUERY_BUILD_SYSTEM, extractor="cmake")


def test_context_defaults_to_inspect_only_and_permissive():
    ctx = CollectionContext()
    assert ctx.allowed_actions == set(DEFAULT_ALLOWED_ACTIONS)
    assert ctx.collection_mode is CollectionMode.PERMISSIVE


# ── D2: result dataclasses ────────────────────────────────────────────────────


def test_raw_artifact_to_dict():
    from pathlib import Path

    art = RawArtifact(kind="raw", path=Path("/p/x"), content_hash="sha256:1", command="t")
    d = art.to_dict()
    assert d["kind"] == "raw"
    assert d["path"].endswith("x")
    assert d["content_hash"] == "sha256:1"


def test_discovery_result_defaults():
    r = DiscoveryResult()
    assert r.can_run is False
    assert r.requested_actions == set()


def test_validation_result_bool():
    assert bool(ValidationResult(ok=True))
    assert not bool(ValidationResult(ok=False, errors=["x"]))


# ── D4: capability model ──────────────────────────────────────────────────────


def test_capabilities_roundtrip_and_extra_preserved():
    caps = ExtractorCapabilities(compile_db=True, requires_build_execution=True)
    d = caps.to_dict()
    assert d["compile_db"] is True and d["requires_build_execution"] is True
    assert d["call_graph"] is False
    caps2 = ExtractorCapabilities.from_dict(dict(d, future_flag="yes"))
    assert caps2.extra == {"future_flag": "yes"}
    assert caps2.to_dict()["future_flag"] == "yes"


def test_capabilities_from_none():
    assert ExtractorCapabilities.from_dict(None).compile_db is False


def test_capabilities_implied_actions():
    caps = ExtractorCapabilities(
        requires_build_execution=True, requires_compiler_execution=True, requires_network=True
    )
    assert caps.implied_actions() == {
        CollectionAction.RUN_BUILD,
        CollectionAction.RUN_COMPILER,
        CollectionAction.NETWORK,
    }


# ── D10: ledger round-trip with the new optional fields ───────────────────────


def test_extractor_record_ledger_roundtrip():
    rec = ExtractorRecord(
        name="cmake-file-api", version="4.3.3", status="ok",
        command="cmake-file-api-reader --reply build",
        command_hash="sha256:abc", capabilities=["compile_db", "target_graph"],
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        diagnostics=["note"],
    )
    d = rec.to_dict()
    assert d["command_hash"] == "sha256:abc"
    assert d["capabilities"] == ["compile_db", "target_graph"]
    assert d["diagnostics"] == ["note"]
    assert ExtractorRecord.from_dict(d) == rec


def test_extractor_record_omits_empty_ledger_fields():
    rec = ExtractorRecord(name="ninja", version="1.12", status="ok")
    d = rec.to_dict()
    assert set(d) == {"name", "version", "status", "inputs", "artifacts", "detail"}


# ── D3: manifest parsing ──────────────────────────────────────────────────────


def _valid_manifest_dict():
    return {
        "name": "abicheck-cmake-extractor",
        "version": "1.0",
        "capabilities": {"compile_db": True, "target_graph": True},
        "input_requirements": ["build_dir"],
        "allowed_actions": ["inspect", "query_build_system"],
        "version_command": ["abicheck-cmake-extractor", "--version"],
        "commands": {
            "collect": ["my-extractor", "collect", "--output", "{raw_dir}"],
            "normalize": ["my-extractor", "normalize", "--raw", "{raw_dir}", "--out", "{normalized_dir}"],
        },
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "build/build_evidence.json"}]},
    }


def test_load_valid_manifest(tmp_path):
    m = load_extractor_manifest(_dump(tmp_path, _valid_manifest_dict()))
    assert m.name == "abicheck-cmake-extractor"
    assert m.capabilities.compile_db is True
    assert m.allowed_actions == {CollectionAction.INSPECT, CollectionAction.QUERY_BUILD_SYSTEM}
    assert m.outputs[0].kind == "build_evidence"
    assert m.required_actions() >= {CollectionAction.INSPECT}


def test_load_outputs_as_plain_list(tmp_path):
    data = _valid_manifest_dict()
    data["outputs"] = [{"kind": "build_evidence", "path": "build/build_evidence.json"}]
    m = load_extractor_manifest(_dump(tmp_path, data))
    assert m.outputs[0].path == "build/build_evidence.json"


def test_manifest_missing_file_raises(tmp_path):
    with pytest.raises(ManifestError, match="cannot read"):
        load_extractor_manifest(tmp_path / "nope.yaml")


def test_manifest_not_a_mapping(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n")
    with pytest.raises(ManifestError, match="must be a YAML mapping"):
        load_extractor_manifest(p)


def test_manifest_invalid_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: [unclosed\n")
    with pytest.raises(ManifestError, match="invalid YAML"):
        load_extractor_manifest(p)


def test_manifest_missing_name(tmp_path):
    with pytest.raises(ManifestError, match="missing a 'name'"):
        load_extractor_manifest(_dump(tmp_path, {"commands": {"collect": ["x"]}}))


def test_manifest_commands_not_mapping(tmp_path):
    with pytest.raises(ManifestError, match="'commands' must be a mapping"):
        load_extractor_manifest(_dump(tmp_path, {"name": "x", "commands": ["x"]}))


def test_manifest_unknown_phase(tmp_path):
    data = {"name": "x", "commands": {"teardown": ["x"]}}
    with pytest.raises(ManifestError, match="unknown command phase"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_command_not_string_list(tmp_path):
    data = {"name": "x", "commands": {"collect": "x collect | tee log"}}
    with pytest.raises(ManifestError, match="list of string tokens"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_unknown_action_rejected(tmp_path):
    data = {"name": "x", "allowed_actions": ["inspect", "hack"], "commands": {"collect": ["x"]}}
    with pytest.raises(ManifestError, match="unknown collection action"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_unknown_placeholder_rejected(tmp_path):
    data = {"name": "x", "commands": {"collect": ["x", "{normalised_dir}"]}}
    with pytest.raises(ManifestError, match="unknown.*placeholder"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_requires_collect_or_normalize(tmp_path):
    with pytest.raises(ManifestError, match="at least a 'collect' or 'normalize'"):
        load_extractor_manifest(_dump(tmp_path, {"name": "x", "commands": {}}))


def test_manifest_capability_action_inconsistency(tmp_path):
    data = {
        "name": "x",
        "capabilities": {"requires_build_execution": True},
        "allowed_actions": ["inspect"],
        "commands": {"collect": ["x"]},
    }
    with pytest.raises(ManifestError, match="require action"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_network_action_rejected(tmp_path):
    data = {"name": "x", "allowed_actions": ["inspect", "network"], "commands": {"collect": ["x"]}}
    with pytest.raises(ManifestError, match="'network' action is always denied"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_requires_network_capability_rejected(tmp_path):
    # A manifest that needs the network can never run (network always denied), so
    # it must be rejected at registration rather than silently accepted (Codex P2).
    data = {
        "name": "x",
        "capabilities": {"requires_network": True},
        "allowed_actions": ["inspect"],
        "commands": {"collect": ["x"]},
    }
    with pytest.raises(ManifestError, match="'requires_network' is not supported"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_version_command_must_be_list(tmp_path):
    data = {"name": "x", "version_command": "x --version", "commands": {"collect": ["x"]}}
    with pytest.raises(ManifestError, match="'version_command' must be a list"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_outputs_bad_type(tmp_path):
    data = {"name": "x", "commands": {"collect": ["x"]}, "outputs": 5}
    with pytest.raises(ManifestError, match="'outputs' must be a list or mapping"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_output_missing_fields(tmp_path):
    data = {"name": "x", "commands": {"collect": ["x"]}, "outputs": [{"kind": "build_evidence"}]}
    with pytest.raises(ManifestError, match="needs a 'kind' and 'path'"):
        load_extractor_manifest(_dump(tmp_path, data))


# ── D3: command rendering ─────────────────────────────────────────────────────


def test_render_command_substitutes():
    assert render_command(["t", "--out", "{raw_dir}/x"], {"raw_dir": "/p/raw"}) == ["t", "--out", "/p/raw/x"]


def test_render_command_missing_value_raises():
    with pytest.raises(ManifestError, match="no value was supplied"):
        render_command(["tool", "{build_dir}"], {})


# ── ExternalCliExtractor unit surface ─────────────────────────────────────────


def test_extractor_properties_and_discover(tmp_path):
    manifest = load_extractor_manifest(_dump(tmp_path, _tool_manifest_dict(action="query_build_system")))
    ext = ExternalCliExtractor(manifest)
    assert ext.name == "fake-tool"
    assert ext.version == "9.9"
    assert ext.schema_version == 1
    # discover: cannot run when the run does not permit the declared action…
    ctx = CollectionContext(allowed_actions={CollectionAction.INSPECT})
    res = ext.discover(ctx)
    assert res.can_run is False and "query_build_system" in res.reason
    # …and can run when it does.
    ctx2 = CollectionContext(
        allowed_actions={CollectionAction.INSPECT, CollectionAction.QUERY_BUILD_SYSTEM}
    )
    assert ext.discover(ctx2).can_run is True


def test_validate_reports_missing_and_invalid(tmp_path):
    manifest = load_extractor_manifest(_dump(tmp_path, _tool_manifest_dict()))
    ext = ExternalCliExtractor(manifest)
    missing = ext.validate([tmp_path / "absent.json"])
    assert not missing.ok and "missing" in missing.errors[0]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    invalid = ext.validate([bad])
    assert not invalid.ok and "not valid JSON" in invalid.errors[0]


def test_collect_skipped_without_collect_command(tmp_path):
    data = _valid_manifest_dict()
    del data["commands"]["collect"]  # normalize-only manifest
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    ext = ExternalCliExtractor(manifest)
    ctx = CollectionContext(
        allowed_actions={CollectionAction.INSPECT, CollectionAction.QUERY_BUILD_SYSTEM}
    )
    res = ext.collect(ctx, tmp_path)
    assert res.status == "skipped"


def test_command_hash_is_stable_and_covers_phases(tmp_path):
    manifest = load_extractor_manifest(_dump(tmp_path, _valid_manifest_dict()))
    ext = ExternalCliExtractor(manifest)
    ctx = CollectionContext(build_root=tmp_path)
    h1 = ext.command_hash(ctx, tmp_path)
    h2 = ext.command_hash(ctx, tmp_path)
    assert h1 == h2 and h1.startswith("sha256:")


# ── End-to-end via run_external_extractor ─────────────────────────────────────


def test_external_extractor_end_to_end(tmp_path):
    manifest = load_extractor_manifest(_dump(tmp_path, _tool_manifest_dict()))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    ctx = CollectionContext(allowed_actions={CollectionAction.INSPECT})
    _norm, record = run_external_extractor(manifest, ctx, pack_root)
    assert record.status == "ok", record.diagnostics
    assert record.command_hash.startswith("sha256:")
    assert "compile_db" in record.capabilities
    assert record.started_at and record.finished_at
    out = json.loads((pack_root / "normalized" / "fake-tool" / "build_evidence.json").read_text())
    assert out["compile_units"][0]["source"] == "a.cpp"


def test_external_extractor_with_separate_normalize_command(tmp_path):
    # collect writes a raw artifact; normalize transforms it into the BE — covers
    # the normalize-subprocess + raw-artifact-capture paths.
    raw_script = (
        "import os,sys;p=sys.argv[1];os.makedirs(os.path.dirname(p),exist_ok=True);"
        "open(p,'w').write('raw')"
    )
    data = {
        "name": "two-phase",
        "capabilities": {"compile_db": True},
        "allowed_actions": ["inspect"],
        "commands": {
            "collect": [sys.executable, "-c", raw_script, "{raw_dir}/raw.bin"],
            "normalize": [sys.executable, "-c", _write_be_script(), "{normalized_dir}/build_evidence.json"],
        },
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "normalized/two-phase/build_evidence.json"}]},
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "ok", record.diagnostics
    assert (pack_root / "raw" / "two-phase" / "raw.bin").is_file()
    assert (pack_root / "normalized" / "two-phase" / "build_evidence.json").is_file()


def test_external_extractor_blocked_by_action_ceiling(tmp_path):
    manifest = load_extractor_manifest(_dump(tmp_path, _tool_manifest_dict(action="query_build_system")))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    ctx = CollectionContext(allowed_actions={CollectionAction.INSPECT})
    with pytest.raises(ActionNotPermittedError, match="query_build_system"):
        run_external_extractor(manifest, ctx, pack_root)


def test_external_extractor_records_nonzero_exit(tmp_path):
    data = {
        "name": "broken-tool",
        "commands": {"collect": [sys.executable, "-c", "import sys; sys.exit(3)"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "build/build_evidence.json"}]},
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "failed"
    assert any("exited 3" in d for d in record.diagnostics)


def test_external_extractor_missing_binary_is_failed_not_crash(tmp_path):
    # A non-existent binary raises OSError inside subprocess; it must become a
    # failed ledger row, not abort the run (Codex P2 / ADR-032 D9).
    data = {
        "name": "ghost-tool",
        "commands": {"collect": ["definitely-not-a-real-binary-xyz", "--go"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "build/build_evidence.json"}]},
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "failed"
    assert any("could not run" in d for d in record.diagnostics)


def test_external_extractor_validation_failure(tmp_path):
    # collect succeeds but writes nothing, so the declared output is missing →
    # validation fails and the run is recorded failed.
    data = {
        "name": "no-output",
        "commands": {"collect": [sys.executable, "-c", "pass"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "build/build_evidence.json"}]},
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "failed"
    assert any("missing" in d for d in record.diagnostics)


def test_external_extractor_missing_input_is_failed_not_crash(tmp_path):
    # A collect template needs {build_dir} but the run supplied none: captured as
    # a failure (D9), not an uncaught ManifestError traceback.
    data = {
        "name": "needs-build-dir",
        "commands": {"collect": [sys.executable, "-c", "pass", "{build_dir}"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "build/build_evidence.json"}]},
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "failed"
    assert any("build_dir" in d for d in record.diagnostics)


# ── CLI integration: `collect-evidence --extractor-manifest` ──────────────────


def _cli(args):
    from click.testing import CliRunner

    from abicheck.cli import main

    return CliRunner().invoke(main, args)


def test_cli_registers_external_extractor_and_folds_build_evidence(tmp_path):
    manifest = _dump(tmp_path, _tool_manifest_dict(name="cli-fake"), name="cli.yaml")
    out = tmp_path / "pack"
    result = _cli(["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads((out / "manifest.json").read_text())
    rec = next(e for e in data["extractors"] if e["name"] == "cli-fake")
    assert rec["status"] == "ok"
    assert rec["command_hash"].startswith("sha256:")
    # Build evidence was folded into the L3 merge.
    assert json.loads((out / "build" / "build_evidence.json").read_text())["compile_units"]


def test_cli_action_ceiling_skips_in_permissive_mode(tmp_path):
    manifest = _dump(tmp_path, _tool_manifest_dict(name="cli-fake", action="query_build_system"), name="q.yaml")
    out = tmp_path / "pack"
    result = _cli(["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)])
    assert result.exit_code == 0, result.output  # permissive: skipped, not fatal
    data = json.loads((out / "manifest.json").read_text())
    rec = next(e for e in data["extractors"] if e["name"] == "cli-fake")
    assert rec["status"] == "skipped"


def test_cli_action_ceiling_allowed_with_flag(tmp_path):
    manifest = _dump(tmp_path, _tool_manifest_dict(name="cli-fake", action="query_build_system"), name="q2.yaml")
    out = tmp_path / "pack"
    result = _cli(
        ["collect-evidence", "--extractor-manifest", str(manifest), "--allow-build-query", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads((out / "manifest.json").read_text())
    rec = next(e for e in data["extractors"] if e["name"] == "cli-fake")
    assert rec["status"] == "ok"


def test_cli_bad_manifest_recorded_and_permissive_continues(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- not a mapping\n")
    out = tmp_path / "pack"
    result = _cli(["collect-evidence", "--extractor-manifest", str(bad), "-o", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads((out / "manifest.json").read_text())
    rec = next(e for e in data["extractors"] if e["name"].startswith("external:"))
    assert rec["status"] == "failed"


def test_cli_permissive_continues_on_failed_extractor(tmp_path):
    data = {
        "name": "cli-fail",
        "commands": {"collect": [sys.executable, "-c", "import sys; sys.exit(5)"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "build/build_evidence.json"}]},
    }
    manifest = _dump(tmp_path, data, name="fail.yaml")
    out = tmp_path / "pack"
    result = _cli(["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)])
    assert result.exit_code == 0, result.output  # permissive: failure is non-fatal
    rec = next(
        e for e in json.loads((out / "manifest.json").read_text())["extractors"]
        if e["name"] == "cli-fail"
    )
    assert rec["status"] == "failed"


def test_cli_malformed_build_evidence_is_failed_not_crash(tmp_path):
    # Output is valid JSON but not valid BuildEvidence (compile unit missing id):
    # BuildEvidence.from_dict raises KeyError/TypeError. Permissive mode must
    # record it as failed, never abort the command (Codex P1).
    bad_be = (
        "import json,sys,os;p=sys.argv[1];os.makedirs(os.path.dirname(p),exist_ok=True);"
        "json.dump({'compile_units':[{}]},open(p,'w'))"
    )
    data = {
        "name": "cli-malformed",
        "commands": {"collect": [sys.executable, "-c", bad_be, "{normalized_dir}/build_evidence.json"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "normalized/cli-malformed/build_evidence.json"}]},
    }
    manifest = _dump(tmp_path, data, name="malformed.yaml")
    out = tmp_path / "pack"
    result = _cli(["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)])
    assert result.exit_code == 0, result.output
    rec = next(
        e for e in json.loads((out / "manifest.json").read_text())["extractors"]
        if e["name"] == "cli-malformed"
    )
    assert rec["status"] == "failed"


def test_cli_strict_mode_fails_on_skipped_extractor(tmp_path):
    # A requested manifest gated out by the action ceiling is 'skipped'; under
    # strict mode the requested evidence is absent, so the run must fail (Codex P2).
    manifest = _dump(tmp_path, _tool_manifest_dict(name="cli-gated", action="query_build_system"), name="gated.yaml")
    out = tmp_path / "pack"
    result = _cli(
        ["collect-evidence", "--extractor-manifest", str(manifest), "--collection-mode", "strict", "-o", str(out)]
    )
    assert result.exit_code != 0
    assert "strict collection mode" in result.output


def test_cli_strict_mode_fails_on_broken_extractor(tmp_path):
    data = {
        "name": "cli-broken",
        "commands": {"collect": [sys.executable, "-c", "import sys; sys.exit(2)"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "build/build_evidence.json"}]},
    }
    manifest = _dump(tmp_path, data, name="broken-cli.yaml")
    out = tmp_path / "pack"
    result = _cli(
        ["collect-evidence", "--extractor-manifest", str(manifest), "--collection-mode", "strict", "-o", str(out)]
    )
    assert result.exit_code != 0
    assert "strict collection mode" in result.output
