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
reproducibility ledger (D10) — plus an end-to-end external CLI extractor driven
through a fake ``python -c`` tool so no third-party binary is needed.
"""
from __future__ import annotations

import json
import sys

import pytest

from abicheck.evidence.extractor import (
    DEFAULT_ALLOWED_ACTIONS,
    ActionNotPermittedError,
    CollectionAction,
    CollectionContext,
    CollectionMode,
    ExtractorCapabilities,
    parse_action,
    parse_actions,
    require_action,
    resolve_allowed_actions,
)
from abicheck.evidence.extractor_manifest import (
    ManifestError,
    load_extractor_manifest,
    render_command,
    run_external_extractor,
)
from abicheck.evidence.model import ExtractorRecord

# ── D5: action-permission model ───────────────────────────────────────────────


def test_default_allowed_is_inspect_only():
    assert DEFAULT_ALLOWED_ACTIONS == frozenset({CollectionAction.INSPECT})


def test_parse_action_rejects_unknown():
    assert parse_action("inspect") is CollectionAction.INSPECT
    with pytest.raises(ValueError, match="unknown collection action"):
        parse_action("delete_everything")


def test_parse_actions_set():
    out = parse_actions(["inspect", "query_build_system"])
    assert out == {CollectionAction.INSPECT, CollectionAction.QUERY_BUILD_SYSTEM}
    assert parse_actions([]) == set()


def test_resolve_intersects_ceiling_with_run_permitted():
    declared = {CollectionAction.INSPECT, CollectionAction.RUN_BUILD}
    run_permitted = {CollectionAction.INSPECT, CollectionAction.QUERY_BUILD_SYSTEM}
    # A manifest cannot escalate beyond what the run permits.
    assert resolve_allowed_actions(declared, run_permitted) == {CollectionAction.INSPECT}


def test_resolve_always_strips_network():
    declared = {CollectionAction.INSPECT, CollectionAction.NETWORK}
    run_permitted = {CollectionAction.INSPECT, CollectionAction.NETWORK}
    # Even if both sides list network, it is never granted.
    assert CollectionAction.NETWORK not in resolve_allowed_actions(declared, run_permitted)


def test_require_action_raises_when_denied():
    with pytest.raises(ActionNotPermittedError, match="run_build"):
        require_action(
            CollectionAction.RUN_BUILD, {CollectionAction.INSPECT}, extractor="x"
        )
    # Allowed action does not raise.
    require_action(CollectionAction.INSPECT, {CollectionAction.INSPECT})


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


# ── D4: capability model ──────────────────────────────────────────────────────


def test_capabilities_roundtrip_and_extra_preserved():
    caps = ExtractorCapabilities(compile_db=True, requires_build_execution=True)
    d = caps.to_dict()
    assert d["compile_db"] is True
    assert d["requires_build_execution"] is True
    assert d["call_graph"] is False
    # Forward-compat: unknown keys survive a round-trip.
    d2 = dict(d, future_flag="yes")
    caps2 = ExtractorCapabilities.from_dict(d2)
    assert caps2.extra == {"future_flag": "yes"}
    assert caps2.to_dict()["future_flag"] == "yes"


def test_capabilities_implied_actions():
    caps = ExtractorCapabilities(requires_build_execution=True, requires_compiler_execution=True)
    assert caps.implied_actions() == {
        CollectionAction.RUN_BUILD,
        CollectionAction.RUN_COMPILER,
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
    assert ExtractorRecord.from_dict(d) == rec


def test_extractor_record_omits_empty_ledger_fields():
    # A built-in adapter's record must serialize exactly as before ADR-032.
    rec = ExtractorRecord(name="ninja", version="1.12", status="ok")
    d = rec.to_dict()
    assert set(d) == {"name", "version", "status", "inputs", "artifacts", "detail"}


# ── D3: manifest parsing ──────────────────────────────────────────────────────


_VALID_MANIFEST = """
name: abicheck-cmake-extractor
version: "1.0"
capabilities:
  compile_db: true
  target_graph: true
input_requirements:
  - build_dir
allowed_actions:
  - inspect
  - query_build_system
commands:
  collect: ["my-extractor", "collect", "--output", "{raw_dir}"]
  normalize: ["my-extractor", "normalize", "--raw", "{raw_dir}", "--out", "{normalized_dir}"]
outputs:
  normalized:
    - kind: build_evidence
      path: build/build_evidence.json
"""


def _write(tmp_path, text, name="m.yaml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_load_valid_manifest(tmp_path):
    m = load_extractor_manifest(_write(tmp_path, _VALID_MANIFEST))
    assert m.name == "abicheck-cmake-extractor"
    assert m.capabilities.compile_db is True
    assert m.allowed_actions == {CollectionAction.INSPECT, CollectionAction.QUERY_BUILD_SYSTEM}
    assert m.outputs[0].kind == "build_evidence"
    assert m.required_actions() >= {CollectionAction.INSPECT}


def test_manifest_missing_name(tmp_path):
    with pytest.raises(ManifestError, match="missing a 'name'"):
        load_extractor_manifest(_write(tmp_path, "commands:\n  collect: ['x']\n"))


def test_manifest_unknown_action_rejected(tmp_path):
    text = "name: x\nallowed_actions: [inspect, hack_the_planet]\ncommands:\n  collect: ['x']\n"
    with pytest.raises(ManifestError, match="unknown collection action"):
        load_extractor_manifest(_write(tmp_path, text))


def test_manifest_unknown_placeholder_rejected(tmp_path):
    text = "name: x\ncommands:\n  collect: ['x', '{normalised_dir}']\n"
    with pytest.raises(ManifestError, match="unknown.*placeholder"):
        load_extractor_manifest(_write(tmp_path, text))


def test_manifest_rejects_shell_string_command(tmp_path):
    text = "name: x\ncommands:\n  collect: 'x collect | tee log'\n"
    with pytest.raises(ManifestError, match="list of string tokens"):
        load_extractor_manifest(_write(tmp_path, text))


def test_manifest_requires_collect_or_normalize(tmp_path):
    with pytest.raises(ManifestError, match="at least a 'collect' or 'normalize'"):
        load_extractor_manifest(_write(tmp_path, "name: x\ncommands: {}\n"))


def test_manifest_capability_action_inconsistency(tmp_path):
    text = (
        "name: x\ncapabilities:\n  requires_build_execution: true\n"
        "allowed_actions: [inspect]\ncommands:\n  collect: ['x']\n"
    )
    with pytest.raises(ManifestError, match="require action"):
        load_extractor_manifest(_write(tmp_path, text))


# ── D3: command rendering ─────────────────────────────────────────────────────


def test_render_command_substitutes():
    out = render_command(
        ["tool", "--out", "{raw_dir}/x"], {"raw_dir": "/p/raw"}
    )
    assert out == ["tool", "--out", "/p/raw/x"]


def test_render_command_missing_value_raises():
    with pytest.raises(ManifestError, match="no value was supplied"):
        render_command(["tool", "{build_dir}"], {})


# ── End-to-end: external CLI extractor through a fake python tool ──────────────


def _fake_tool_manifest(tmp_path, *, action="inspect"):
    """A manifest whose collect command is a self-contained python one-liner.

    The collect step writes a normalized BuildEvidence JSON directly to the
    declared output path, so no separate normalize command is needed.
    """
    script = (
        "import json,sys,os;"
        "p=sys.argv[1];"
        "os.makedirs(os.path.dirname(p),exist_ok=True);"
        "json.dump({'schema_version':1,'compile_units':[{'id':'cu://a','source':'a.cpp',"
        "'argv':['cc','-c','a.cpp'],'language':'CXX'}]},open(p,'w'))"
    )
    text = f"""
name: fake-tool
version: "9.9"
capabilities:
  compile_db: true
allowed_actions:
  - {action}
commands:
  collect: ["{sys.executable}", "-c", "{script}", "{{normalized_dir}}/../../build/build_evidence.json"]
outputs:
  normalized:
    - kind: build_evidence
      path: build/build_evidence.json
"""
    return load_extractor_manifest(_write(tmp_path, text, name="fake.yaml"))


def test_external_extractor_end_to_end(tmp_path):
    manifest = _fake_tool_manifest(tmp_path)
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    ctx = CollectionContext(allowed_actions={CollectionAction.INSPECT})

    _norm, record = run_external_extractor(manifest, ctx, pack_root)

    assert record.status == "ok", record.diagnostics
    assert record.command_hash.startswith("sha256:")
    assert "compile_db" in record.capabilities
    assert record.started_at and record.finished_at
    out = json.loads((pack_root / "build" / "build_evidence.json").read_text())
    assert out["compile_units"][0]["source"] == "a.cpp"


def test_external_extractor_blocked_by_action_ceiling(tmp_path):
    # Manifest needs query_build_system, but the run only permits inspect.
    manifest = _fake_tool_manifest(tmp_path, action="query_build_system")
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    ctx = CollectionContext(allowed_actions={CollectionAction.INSPECT})
    with pytest.raises(ActionNotPermittedError, match="query_build_system"):
        run_external_extractor(manifest, ctx, pack_root)


def test_external_extractor_records_failure_without_raising(tmp_path):
    # A collect command that exits non-zero is captured as a failed record,
    # not an exception (permissive mode continues; strict acts on the status).
    text = f"""
name: broken-tool
commands:
  collect: ["{sys.executable}", "-c", "import sys; sys.exit(3)"]
outputs:
  normalized:
    - kind: build_evidence
      path: build/build_evidence.json
"""
    manifest = load_extractor_manifest(_write(tmp_path, text, name="broken.yaml"))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    ctx = CollectionContext(allowed_actions={CollectionAction.INSPECT})
    _norm, record = run_external_extractor(manifest, ctx, pack_root)
    assert record.status == "failed"
    assert any("exited 3" in d for d in record.diagnostics)


def test_external_extractor_missing_input_is_failed_not_crash(tmp_path):
    # A collect template needs {build_dir} but the run supplied none: this is a
    # captured failure (D9), not an uncaught ManifestError traceback.
    text = f"""
name: needs-build-dir
commands:
  collect: ["{sys.executable}", "-c", "pass", "{{build_dir}}"]
outputs:
  normalized:
    - kind: build_evidence
      path: build/build_evidence.json
"""
    manifest = load_extractor_manifest(_write(tmp_path, text, name="needs.yaml"))
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    ctx = CollectionContext(allowed_actions={CollectionAction.INSPECT})  # no build_root
    _norm, record = run_external_extractor(manifest, ctx, pack_root)
    assert record.status == "failed"
    assert any("build_dir" in d for d in record.diagnostics)


# ── CLI integration: `collect-evidence --extractor-manifest` ──────────────────


def _be_writer_manifest(tmp_path, *, action="inspect", name="cli.yaml"):
    script = (
        "import json,sys,os;p=sys.argv[1];os.makedirs(os.path.dirname(p),exist_ok=True);"
        "json.dump({'schema_version':1,'compile_units':[{'id':'cu://a','source':'a.cpp',"
        "'argv':['cc','-c','a.cpp'],'language':'CXX'}]},open(p,'w'))"
    )
    text = f"""
name: cli-fake
capabilities:
  compile_db: true
allowed_actions:
  - {action}
commands:
  collect: ["{sys.executable}", "-c", "{script}", "{{normalized_dir}}/../../build/build_evidence.json"]
outputs:
  normalized:
    - kind: build_evidence
      path: build/build_evidence.json
"""
    return _write(tmp_path, text, name=name)


def test_cli_registers_external_extractor_and_folds_build_evidence(tmp_path):
    from click.testing import CliRunner

    from abicheck.cli import main

    manifest = _be_writer_manifest(tmp_path)
    out = tmp_path / "pack"
    result = CliRunner().invoke(
        main, ["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads((out / "manifest.json").read_text())
    rec = next(e for e in data["extractors"] if e["name"] == "cli-fake")
    assert rec["status"] == "ok"
    assert rec["command_hash"].startswith("sha256:")
    assert json.loads((out / "build" / "build_evidence.json").read_text())["compile_units"]


def test_cli_action_ceiling_skips_in_permissive_mode(tmp_path):
    from click.testing import CliRunner

    from abicheck.cli import main

    # Needs query_build_system but the run does not pass --allow-build-query.
    manifest = _be_writer_manifest(tmp_path, action="query_build_system", name="q.yaml")
    out = tmp_path / "pack"
    result = CliRunner().invoke(
        main, ["collect-evidence", "--extractor-manifest", str(manifest), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output  # permissive: skipped, not fatal
    data = json.loads((out / "manifest.json").read_text())
    rec = next(e for e in data["extractors"] if e["name"] == "cli-fake")
    assert rec["status"] == "skipped"


def test_cli_action_ceiling_allowed_with_flag(tmp_path):
    from click.testing import CliRunner

    from abicheck.cli import main

    manifest = _be_writer_manifest(tmp_path, action="query_build_system", name="q2.yaml")
    out = tmp_path / "pack"
    result = CliRunner().invoke(
        main,
        ["collect-evidence", "--extractor-manifest", str(manifest),
         "--allow-build-query", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    data = json.loads((out / "manifest.json").read_text())
    rec = next(e for e in data["extractors"] if e["name"] == "cli-fake")
    assert rec["status"] == "ok"


def test_cli_strict_mode_fails_on_broken_extractor(tmp_path):
    from click.testing import CliRunner

    from abicheck.cli import main

    text = f"""
name: cli-broken
commands:
  collect: ["{sys.executable}", "-c", "import sys; sys.exit(2)"]
outputs:
  normalized:
    - kind: build_evidence
      path: build/build_evidence.json
"""
    manifest = _write(tmp_path, text, name="broken-cli.yaml")
    out = tmp_path / "pack"
    result = CliRunner().invoke(
        main,
        ["collect-evidence", "--extractor-manifest", str(manifest),
         "--collection-mode", "strict", "-o", str(out)],
    )
    assert result.exit_code != 0
    assert "strict collection mode" in result.output
