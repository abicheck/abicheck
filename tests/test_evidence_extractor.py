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


@pytest.mark.parametrize(
    "bad_name",
    ["/tmp/escape", "../escape", "a/b", "a\\b", ".."],
)
def test_manifest_name_must_be_safe_path_component(tmp_path, bad_name):
    # A name set to an absolute path or containing traversal is later used to
    # build raw/<name> and normalized/<name> dirs; reject at load so it cannot
    # escape the pack root.
    data = {
        "name": bad_name,
        "commands": {"collect": ["x"]},
        "outputs": {"build_evidence": ["build/build.json"]},
    }
    with pytest.raises(ManifestError, match="single safe path component"):
        load_extractor_manifest(_dump(tmp_path, data))


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


def test_manifest_empty_command_rejected(tmp_path):
    # An empty argv list would crash at subprocess invocation; reject at load.
    data = {"name": "x", "commands": {"collect": []}}
    with pytest.raises(ManifestError, match="non-empty list of string"):
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


def test_manifest_capabilities_list_form(tmp_path):
    # ADR-032 D3 shows capabilities as a YAML list of names; each is enabled.
    data = {
        "name": "x",
        "capabilities": ["target_graph", "compile_db"],
        "commands": {"collect": ["x"]},
        "outputs": [{"kind": "build_evidence", "path": "build/be.json"}],
    }
    m = load_extractor_manifest(_dump(tmp_path, data))
    assert m.capabilities.target_graph is True
    assert m.capabilities.compile_db is True


def test_manifest_capabilities_bad_shape_is_manifest_error(tmp_path):
    # A non-mapping/non-list capabilities must raise ManifestError (caught by the
    # CLI), not an uncaught AttributeError from .get on the wrong type (Codex P2).
    data = {"name": "x", "capabilities": "compile_db", "commands": {"collect": ["x"]}}
    with pytest.raises(ManifestError, match="'capabilities' must be a mapping or a list"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_capabilities_list_non_string_rejected(tmp_path):
    data = {"name": "x", "capabilities": [1, 2], "commands": {"collect": ["x"]}}
    with pytest.raises(ManifestError, match="list items must be capability names"):
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


def test_manifest_invalid_schema_version(tmp_path):
    # A non-integer schema_version must be a ManifestError, not an uncaught
    # ValueError that aborts collection (Codex P2).
    data = {
        "name": "x", "schema_version": "abc", "commands": {"collect": ["x"]},
        "outputs": [{"kind": "build_evidence", "path": "build/be.json"}],
    }
    with pytest.raises(ManifestError, match="'schema_version' must be an integer"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_manifest_requires_outputs(tmp_path):
    # An extractor with no declared outputs would run and validate nothing, so a
    # missing/empty outputs block is rejected at load (Codex P2).
    with pytest.raises(ManifestError, match="must declare at least one output"):
        load_extractor_manifest(_dump(tmp_path, {"name": "x", "commands": {"collect": ["x"]}}))


def test_audit_mode_keeps_full_stderr(tmp_path):
    # audit mode preserves full stderr; permissive truncates at 500 chars.
    noisy = "import sys; sys.stderr.write('E'*1200); sys.exit(2)"
    data = {
        "name": "noisy",
        "commands": {"collect": [sys.executable, "-c", noisy]},
        "outputs": [{"kind": "build_evidence", "path": "build/be.json"}],
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    from abicheck.evidence.extractor import CollectionMode

    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _n, audit = run_external_extractor(
        manifest, CollectionContext(collection_mode=CollectionMode.AUDIT), pack_root
    )
    assert audit.status == "failed"
    assert "E" * 1000 in " ".join(audit.diagnostics)  # full stderr kept

    pack2 = tmp_path / "pack2"
    pack2.mkdir()
    _n2, permissive = run_external_extractor(
        manifest, CollectionContext(collection_mode=CollectionMode.PERMISSIVE), pack2
    )
    assert "E" * 1000 not in " ".join(permissive.diagnostics)  # truncated


def test_manifest_non_list_output_group_rejected(tmp_path):
    # A mapping group whose value is a single dict (not a list) must be a
    # ManifestError, not silently dropped to zero outputs (Codex P2).
    data = {
        "name": "x",
        "commands": {"collect": ["x"]},
        "outputs": {"normalized": {"kind": "build_evidence", "path": "build/build_evidence.json"}},
    }
    with pytest.raises(ManifestError, match="must be a list"):
        load_extractor_manifest(_dump(tmp_path, data))


def test_external_extractor_clears_stale_output(tmp_path):
    # A stale declared output from a prior run must not be folded as fresh: if
    # the current tool exits 0 without writing it, the run is failed (Codex P2).
    data = {
        "name": "no-write",
        "commands": {"collect": [sys.executable, "-c", "pass"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "build/build_evidence.json"}]},
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    (pack_root / "build").mkdir(parents=True)
    stale = pack_root / "build" / "build_evidence.json"
    stale.write_text('{"schema_version": 1, "compile_units": []}')  # leftover
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "failed"
    assert not stale.exists()  # the stale file was cleared before the run


@pytest.mark.parametrize(
    "bad_path",
    ["/etc/passwd", "../../escape.json", "C:\\Windows\\x.json", "sub/../../out.json"],
)
def test_manifest_rejects_unsafe_output_paths(tmp_path, bad_path):
    # An absolute or '..' output path would let the tool write outside the pack
    # and crash run_external_extractor at relative_to(); reject at load (Codex P2).
    data = {
        "name": "x",
        "commands": {"collect": ["x"]},
        "outputs": [{"kind": "build_evidence", "path": bad_path}],
    }
    with pytest.raises(ManifestError, match="must be relative to the |contain '..'"):
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


def test_ledger_command_redacts_split_secret(tmp_path):
    # A secret passed as a split ``-D KEY=secret`` form must be redacted in the
    # ledger's command field (argv-aware redaction, not per-token).
    data = _tool_manifest_dict(name="sekret")
    data["commands"]["collect"] = data["commands"]["collect"] + ["-D", "API_KEY=supersecret"]
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "ok", record.diagnostics
    assert "supersecret" not in record.command
    assert "API_KEY" in record.command


def test_substitutions_are_absolute(tmp_path):
    # collect() runs the subprocess with cwd=source/build root, so every path
    # placeholder must be absolute or a relative {build_dir} would resolve
    # against the changed cwd (e.g. build/build) (Codex P2).
    import os
    from pathlib import Path

    manifest = load_extractor_manifest(_dump(tmp_path, _tool_manifest_dict()))
    ext = ExternalCliExtractor(manifest)
    ctx = CollectionContext(
        build_root=Path("relbuild"),
        source_root=Path("relsrc"),
        compile_db=Path("rel/cc.json"),
        binary_paths=[Path("rel/libfoo.so")],
    )
    sub = ext._substitutions(ctx, Path("relpack"))
    for key in ("raw_dir", "normalized_dir", "build_dir", "source_root", "compile_db", "binary"):
        assert os.path.isabs(sub[key]), f"{key} should be absolute: {sub[key]}"


def test_normalize_receives_context_placeholders(tmp_path):
    # A normalize command referencing {source_root} gets the real path the run
    # supplied (not an empty string) — same context-derived subs as collect.
    raw_script = (
        "import os,sys;p=sys.argv[1];os.makedirs(os.path.dirname(p),exist_ok=True);"
        "open(p,'w').write('raw')"
    )
    norm_script = (
        "import json,sys,os\n"
        "root=sys.argv[1]; out=sys.argv[2]\n"
        "assert root, 'source_root must not be empty'\n"
        "os.makedirs(os.path.dirname(out),exist_ok=True)\n"
        "json.dump({'schema_version':1,'compile_units':[]},open(out,'w'))\n"
    )
    data = {
        "name": "two-ctx",
        "commands": {
            "collect": [sys.executable, "-c", raw_script, "{raw_dir}/r.bin"],
            "normalize": [sys.executable, "-c", norm_script, "{source_root}", "{normalized_dir}/build_evidence.json"],
        },
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "normalized/two-ctx/build_evidence.json"}]},
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    src = tmp_path / "src"
    src.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(source_root=src), pack_root)
    assert record.status == "ok", record.diagnostics


def test_normalize_missing_placeholder_is_failed(tmp_path):
    # The same manifest with no source_root supplied: normalize references an
    # unsupplied placeholder, which fails loudly (ManifestError -> failed row),
    # not a silent empty path.
    raw_script = (
        "import os,sys;p=sys.argv[1];os.makedirs(os.path.dirname(p),exist_ok=True);"
        "open(p,'w').write('raw')"
    )
    data = {
        "name": "two-ctx2",
        "commands": {
            "collect": [sys.executable, "-c", raw_script, "{raw_dir}/r.bin"],
            "normalize": [sys.executable, "-c", "pass", "{source_root}", "{normalized_dir}/x.json"],
        },
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "normalized/two-ctx2/x.json"}]},
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "failed"
    assert any("source_root" in d for d in record.diagnostics)


def test_external_extractor_blocked_by_action_ceiling(tmp_path):
    # discover() gates a ceiling violation into a 'skipped' record (D4/D9) rather
    # than raising, so the run is never invoked and no output is produced.
    manifest = load_extractor_manifest(_dump(tmp_path, _tool_manifest_dict(action="query_build_system")))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    ctx = CollectionContext(allowed_actions={CollectionAction.INSPECT})
    _norm, record = run_external_extractor(manifest, ctx, pack_root)
    assert record.status == "skipped"
    assert "query_build_system" in record.detail
    assert not (pack_root / "normalized" / "fake-tool" / "build_evidence.json").exists()


def test_discover_gates_on_action_ceiling(tmp_path):
    manifest = load_extractor_manifest(_dump(tmp_path, _tool_manifest_dict(action="query_build_system")))
    ext = ExternalCliExtractor(manifest)
    assert ext.discover(CollectionContext(allowed_actions={CollectionAction.INSPECT})).can_run is False
    permitted = {CollectionAction.INSPECT, CollectionAction.QUERY_BUILD_SYSTEM}
    assert ext.discover(CollectionContext(allowed_actions=permitted)).can_run is True


def test_ledger_records_resolved_tool_version(tmp_path):
    # D10: the ledger records the *actual* tool version from version_command,
    # not just the manifest's declared version.
    data = _tool_manifest_dict(name="versioned")
    data["version"] = "0.0-declared"
    data["version_command"] = [sys.executable, "-c", "print('toolz 4.5.6')"]
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "ok", record.diagnostics
    assert record.version == "toolz 4.5.6"


def test_ledger_version_falls_back_when_probe_fails(tmp_path):
    # A failing version_command must not break the run; fall back to declared.
    data = _tool_manifest_dict(name="versioned2")
    data["version"] = "9.9"
    data["version_command"] = [sys.executable, "-c", "import sys; sys.exit(1)"]
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "ok", record.diagnostics
    assert record.version == "9.9"


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


def test_cli_malformed_later_output_merges_nothing(tmp_path):
    # A manifest with two build_evidence outputs where the second is invalid:
    # the first must NOT be folded (atomic all-or-nothing), so no L3 build
    # evidence reaches the pack and the extractor is recorded failed (Codex P2).
    script = (
        "import json,sys,os\n"
        "good=sys.argv[1]; bad=sys.argv[2]\n"
        "os.makedirs(os.path.dirname(good),exist_ok=True)\n"
        "json.dump({'schema_version':1,'compile_units':[{'id':'cu://a','source':'a.cpp',"
        "'argv':['cc'],'language':'CXX'}]},open(good,'w'))\n"
        "json.dump({'compile_units':[{}]},open(bad,'w'))\n"  # missing id -> invalid
    )
    data = {
        "name": "cli-multi",
        "commands": {
            "collect": [
                sys.executable, "-c", script,
                "{normalized_dir}/good.json", "{normalized_dir}/bad.json",
            ]
        },
        "outputs": {
            "normalized": [
                {"kind": "build_evidence", "path": "normalized/cli-multi/good.json"},
                {"kind": "build_evidence", "path": "normalized/cli-multi/bad.json"},
            ]
        },
    }
    manifest = _dump(tmp_path, data, name="multi.yaml")
    out = tmp_path / "pack"
    result = _cli(["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)])
    assert result.exit_code == 0, result.output
    rec = next(
        e for e in json.loads((out / "manifest.json").read_text())["extractors"]
        if e["name"] == "cli-multi"
    )
    assert rec["status"] == "failed"
    # The good output's evidence must not have leaked into the merged L3 facts.
    assert not (out / "build" / "build_evidence.json").exists()


def test_cli_non_object_build_evidence_is_failed_not_crash(tmp_path):
    # A build_evidence output that is valid JSON but not an object ([] / null)
    # makes BuildEvidence.from_dict raise AttributeError; permissive mode must
    # record it failed, not crash (Codex P2).
    bad = (
        "import json,sys,os;p=sys.argv[1];os.makedirs(os.path.dirname(p),exist_ok=True);"
        "json.dump([],open(p,'w'))"
    )
    data = {
        "name": "cli-nonobj",
        "commands": {"collect": [sys.executable, "-c", bad, "{normalized_dir}/build_evidence.json"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "normalized/cli-nonobj/build_evidence.json"}]},
    }
    manifest = _dump(tmp_path, data, name="nonobj.yaml")
    out = tmp_path / "pack"
    result = _cli(["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)])
    assert result.exit_code == 0, result.output
    rec = next(
        e for e in json.loads((out / "manifest.json").read_text())["extractors"]
        if e["name"] == "cli-nonobj"
    )
    assert rec["status"] == "failed"


def test_cli_source_root_placeholder_supplied(tmp_path):
    # A manifest using {source_root} works when --source-root is provided.
    script = (
        "import json,sys,os;root=sys.argv[1];out=sys.argv[2];"
        "os.makedirs(os.path.dirname(out),exist_ok=True);"
        "json.dump({'schema_version':1,'compile_units':[{'id':'cu://a','source':'a.cpp',"
        "'argv':['cc'],'language':'CXX'}]},open(out,'w'))"
    )
    data = {
        "name": "cli-srcroot",
        "commands": {
            "collect": [
                sys.executable, "-c", script, "{source_root}",
                "{normalized_dir}/build_evidence.json",
            ]
        },
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "normalized/cli-srcroot/build_evidence.json"}]},
    }
    manifest = _dump(tmp_path, data, name="srcroot.yaml")
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "pack"
    result = _cli(
        ["collect-evidence", "--extractor-manifest", str(manifest), "--source-root", str(src), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    rec = next(
        e for e in json.loads((out / "manifest.json").read_text())["extractors"]
        if e["name"] == "cli-srcroot"
    )
    assert rec["status"] == "ok"


def test_cli_failed_extractor_does_not_pollute_pack_artifacts(tmp_path):
    # A failed external extractor's invalid normalized output must not be hashed
    # into the pack's artifact digests (the ledger row legitimately still records
    # the failed run, but its invalid evidence must be isolated) (Codex P2).
    from abicheck.evidence.pack import EvidencePack

    # An extractor whose normalized output is valid JSON but invalid BuildEvidence.
    bad = (
        "import json,sys,os;p=sys.argv[1];os.makedirs(os.path.dirname(p),exist_ok=True);"
        "json.dump({'compile_units':[{}]},open(p,'w'))"  # missing id -> fold fails
    )
    data = {
        "name": "polluter",
        "commands": {"collect": [sys.executable, "-c", bad, "{normalized_dir}/build_evidence.json"]},
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "normalized/polluter/build_evidence.json"}]},
    }
    manifest = _dump(tmp_path, data, name="poll.yaml")
    out = tmp_path / "pack"
    r1 = _cli(["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)])
    assert r1.exit_code == 0, r1.output
    pack = EvidencePack.load(out)
    rec = next(e for e in pack.manifest.extractors if e.name == "polluter")
    assert rec.status == "failed"
    # The invalid normalized output is purged and contributes no artifact digest.
    assert not (out / "normalized" / "polluter").exists()
    assert pack.manifest.artifacts == []
    # The failed ledger row must not advertise the now-purged output path, or a
    # consumer following manifest.extractors[].artifacts reads a missing file (Codex P2).
    assert rec.artifacts == []


def test_cli_unsupported_output_kind_is_failed(tmp_path):
    # A manifest declaring a source_abi/source_graph_summary output (which the
    # CLI cannot fold yet) must be recorded failed, not silently dropped (Codex P2).
    script = (
        "import json,sys,os;p=sys.argv[1];os.makedirs(os.path.dirname(p),exist_ok=True);"
        "json.dump({'schema_version':1},open(p,'w'))"
    )
    data = {
        "name": "cli-l4",
        "commands": {"collect": [sys.executable, "-c", script, "{normalized_dir}/sa.json"]},
        "outputs": {"normalized": [{"kind": "source_abi", "path": "normalized/cli-l4/sa.json"}]},
    }
    manifest = _dump(tmp_path, data, name="l4.yaml")
    out = tmp_path / "pack"
    result = _cli(["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)])
    assert result.exit_code == 0, result.output  # permissive: recorded, not fatal
    rec = next(
        e for e in json.loads((out / "manifest.json").read_text())["extractors"]
        if e["name"] == "cli-l4"
    )
    assert rec["status"] == "failed"
    assert "source_abi" in rec["detail"]
    # Its purged output must not linger in the ledger row's artifact list (Codex P2).
    assert rec.get("artifacts", []) == []


def test_ledger_command_covers_collect_and_normalize(tmp_path):
    # The ledger command reflects every executed phase, joined with ' && '.
    raw_script = (
        "import os,sys;p=sys.argv[1];os.makedirs(os.path.dirname(p),exist_ok=True);"
        "open(p,'w').write('raw')"
    )
    data = {
        "name": "ledger-2",
        "commands": {
            "collect": [sys.executable, "-c", raw_script, "{raw_dir}/r.bin"],
            "normalize": [sys.executable, "-c", _write_be_script(), "{normalized_dir}/build_evidence.json"],
        },
        "outputs": {"normalized": [{"kind": "build_evidence", "path": "normalized/ledger-2/build_evidence.json"}]},
    }
    manifest = load_extractor_manifest(_dump(tmp_path, data))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    _norm, record = run_external_extractor(manifest, CollectionContext(), pack_root)
    assert record.status == "ok", record.diagnostics
    assert " && " in record.command  # both collect and normalize represented


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
    # Strict failure must not first print a contradictory success line.
    assert "Evidence pack written" not in result.output


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
