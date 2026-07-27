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

"""Unit tests for the abi_deps/abi_aggregate/abi_project_validate/
abi_project_plan MCP tools — each a thin wrapper reusing the exact
service/CLI-internal logic (``stack_checker.check_single_env``,
``aggregate.aggregate_reports_dir``, ``buildsource.project_targets.
validate_project_targets``, ``buildsource.run_plan.generate_run_plan``) so
behavior stays identical to the matching CLI command.

Mocks the ``mcp`` package at import time, mirroring test_mcp_server_unit.py.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

if "mcp" not in sys.modules or not isinstance(sys.modules["mcp"], MagicMock):
    _mock_fastmcp = MagicMock()
    _mock_mcp_module = MagicMock()
    _mock_mcp_module.server.fastmcp.FastMCP = _mock_fastmcp
    sys.modules.setdefault("mcp", _mock_mcp_module)
    sys.modules.setdefault("mcp.server", _mock_mcp_module.server)
    sys.modules.setdefault("mcp.server.fastmcp", _mock_mcp_module.server.fastmcp)
    _mock_mcp_instance = MagicMock()
    _mock_mcp_instance.tool.return_value = lambda fn: fn
    _mock_fastmcp.return_value = _mock_mcp_instance

from abicheck import (
    mcp_shared,  # noqa: E402
)
from abicheck.mcp_server import (  # noqa: E402
    abi_aggregate,
    abi_deps,
    abi_project_plan,
    abi_project_validate,
)

# ---------------------------------------------------------------------------
# abi_deps
# ---------------------------------------------------------------------------


def _first_existing(*candidates: str) -> str | None:
    for c in candidates:
        if Path(c).exists():
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


class TestAbiDeps:
    def test_resolves_a_real_elf_binary(self):
        # abi_deps wraps stack_checker.check_single_env, which only resolves
        # DT_NEEDED/ELF dependency graphs -- mirrors the ELF-only guard the
        # `deps tree` CLI command itself has. macOS/Windows runners have no
        # ELF binary at a well-known path (macOS system binaries are Mach-O),
        # same reasoning as tests/test_stack_checker.py's _require_linux_elf.
        if sys.platform != "linux":
            pytest.skip("abi_deps/stack_checker only resolves ELF binaries")
        binary = _first_existing("/bin/ls", "/usr/bin/ls", "ls")
        if binary is None:
            pytest.skip("no ELF binary available to resolve")
        raw = abi_deps(binary)
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["result"]["verdict"]["loadability"] in ("pass", "warn", "fail")
        assert payload["result"]["root_binary"] == binary or payload["result"][
            "root_binary"
        ].endswith(Path(binary).name)

    def test_missing_binary_is_an_error(self, tmp_path: Path):
        raw = abi_deps(str(tmp_path / "nonexistent.so"))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "not found" in payload["error"].lower()

    def test_non_elf_input_is_rejected(self, tmp_path: Path):
        text_file = tmp_path / "not_a_binary.txt"
        text_file.write_text("hello")
        raw = abi_deps(str(text_file))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "elf" in payload["error"].lower()

    def test_oversized_binary_is_rejected(self, tmp_path: Path, monkeypatch):
        # ADR-021b D3: input size must be bounded before processing.
        if sys.platform != "linux":
            pytest.skip("abi_deps/stack_checker only resolves ELF binaries")
        binary = _first_existing("/bin/ls", "/usr/bin/ls", "ls")
        if binary is None:
            pytest.skip("no ELF binary available to resolve")
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 1)
        raw = abi_deps(binary)
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "exceeds limit" in payload["error"]

    def test_timeout(self, monkeypatch, tmp_path: Path):
        # ADR-021b D2: a stalled resolve must return a structured timeout,
        # not block the server indefinitely.
        if sys.platform != "linux":
            pytest.skip("abi_deps/stack_checker only resolves ELF binaries")
        binary = _first_existing("/bin/ls", "/usr/bin/ls", "ls")
        if binary is None:
            pytest.skip("no ELF binary available to resolve")
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr("abicheck.stack_checker.check_single_env", _slow)
        raw = abi_deps(binary)
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]


# ---------------------------------------------------------------------------
# abi_aggregate
# ---------------------------------------------------------------------------


def _write_report(d: Path, target_id: str, verdict: str) -> None:
    (d / f"abi-report-{target_id}.json").write_text(json.dumps({"verdict": verdict}))


class TestAbiAggregate:
    def test_discovered_only_aggregates_whatever_is_present(self, tmp_path: Path):
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        raw = abi_aggregate(str(tmp_path), discovered_only=True)
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 0
        assert payload["result"]["status"] == "pass"

    def test_expect_gates_required_coverage(self, tmp_path: Path):
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        raw = abi_aggregate(str(tmp_path), expect=["linux-x86_64", "windows-x86_64"])
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        # windows-x86_64 never reported -> required coverage gap -> exit 1.
        assert payload["exit_code"] == 1

    def test_real_abi_break_exits_4(self, tmp_path: Path):
        _write_report(tmp_path, "linux-x86_64", "BREAKING")
        raw = abi_aggregate(str(tmp_path), expect=["linux-x86_64"])
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 4

    def test_reports_dir_not_a_directory_is_an_error(self, tmp_path: Path):
        f = tmp_path / "not_a_dir.json"
        f.write_text("{}")
        raw = abi_aggregate(str(f), discovered_only=True)
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "directory" in payload["error"].lower()

    def test_no_expected_target_source_is_a_usage_error(self, tmp_path: Path):
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        raw = abi_aggregate(str(tmp_path))
        payload = json.loads(raw)
        assert payload["status"] == "error"

    def test_combining_discovered_only_and_expect_is_a_usage_error(
        self, tmp_path: Path
    ):
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        raw = abi_aggregate(
            str(tmp_path), discovered_only=True, expect=["linux-x86_64"]
        )
        payload = json.loads(raw)
        assert payload["status"] == "error"

    def test_with_manifest_file(self, tmp_path: Path):
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "aggregate_manifest_version": "1.0",
                    "targets": [{"id": "linux-x86_64", "required": True}],
                }
            )
        )
        raw = abi_aggregate(str(tmp_path), manifest=str(manifest))
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 0

    def test_with_run_plan_file(self, tmp_path: Path):
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        run_plan = tmp_path / "run-plan.json"
        run_plan.write_text(
            json.dumps(
                {
                    "schema": "abicheck.run-plan/v1",
                    "checks": [
                        {
                            "check_id": "linux-x86_64",
                            "kind": "target",
                            "target_kind": "library",
                            "name": "libfoo",
                            "profile_id": "linux",
                            "baseline_channel": "release",
                            "requested_depth": "headers",
                            "required": True,
                            "gate_mode": "local",
                        }
                    ],
                }
            )
        )
        raw = abi_aggregate(str(tmp_path), run_plan=str(run_plan))
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 0

    def test_oversized_report_is_rejected(self, tmp_path: Path, monkeypatch):
        # ADR-021b D3: every report file under reports_dir must be bounded.
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 1)
        raw = abi_aggregate(str(tmp_path), discovered_only=True)
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "exceeds limit" in payload["error"]

    def test_oversized_manifest_is_rejected(self, tmp_path: Path, monkeypatch):
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "aggregate_manifest_version": "1.0",
                    "targets": [{"id": "linux-x86_64", "required": True}],
                }
            )
        )
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 1)
        raw = abi_aggregate(str(tmp_path), manifest=str(manifest))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "exceeds limit" in payload["error"]

    def test_timeout(self, tmp_path: Path, monkeypatch):
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr("abicheck.aggregate.aggregate_reports_dir", _slow)
        raw = abi_aggregate(str(tmp_path), discovered_only=True)
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]


# ---------------------------------------------------------------------------
# abi_project_validate / abi_project_plan
# ---------------------------------------------------------------------------

_SINGLE_PROFILE_LIBRARY_RAW = {
    "targets": {
        "libfoo": {
            "kind": "library",
            "binary_pattern": "build/libfoo*.so",
            "checks": [
                {"channel": "release", "depth": "headers", "required": True},
            ],
        },
    },
    "profiles": {
        "linux": {"contract": True},
    },
    "baseline": {
        "channels": {
            "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
        },
    },
}


def _write_config(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / ".abicheck.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _write_build_output(tmp_path: Path, profile: str, target_ids: list[str]) -> Path:
    d = tmp_path / f"build-{profile}"
    d.mkdir()
    (d / "build-output.json").write_text(
        json.dumps(
            {
                "schema": "abicheck.build-output/v1",
                "targets": [
                    {"id": t, "binary": f"artifacts/{t}.so"} for t in target_ids
                ],
            }
        ),
        encoding="utf-8",
    )
    return d


class TestAbiProjectValidate:
    def test_valid_config_reports_ok(self, tmp_path: Path):
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        raw = abi_project_validate(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["result"]["ok"] is True

    def test_missing_config_is_an_error(self, tmp_path: Path):
        raw = abi_project_validate(str(tmp_path / "nope.yml"))
        payload = json.loads(raw)
        assert payload["status"] == "error"

    def test_dangling_reference_is_a_validation_error(self, tmp_path: Path):
        raw_cfg = dict(_SINGLE_PROFILE_LIBRARY_RAW)
        raw_cfg = json.loads(json.dumps(raw_cfg))  # deep copy
        raw_cfg["targets"]["libfoo"]["checks"][0]["channel"] = "does-not-exist"
        config = _write_config(tmp_path, raw_cfg)
        raw = abi_project_validate(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["result"]["ok"] is False
        assert payload["result"]["errors"]

    def test_malformed_yaml_is_an_error(self, tmp_path: Path):
        config = tmp_path / ".abicheck.yml"
        config.write_text("- just\n- a\n- list\n", encoding="utf-8")
        raw = abi_project_validate(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "error"

    def test_toolchain_bindings_with_nothing_declared_is_a_noop(self, tmp_path: Path):
        # _SINGLE_PROFILE_LIBRARY_RAW's "linux" profile declares no
        # compile.binding, so check_profile_bindings_resolve has nothing to
        # check -- this only exercises that the bindings file still loads.
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        bindings = tmp_path / "bindings.yml"
        bindings.write_text(
            yaml.safe_dump(
                {"schema": "abicheck.toolchain-bindings/v1", "bindings": {}}
            ),
            encoding="utf-8",
        )
        raw = abi_project_validate(str(config), toolchain_bindings=str(bindings))
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["result"]["ok"] is True

    def test_toolchain_bindings_unresolved_binding_is_a_validation_error(
        self, tmp_path: Path
    ):
        raw_cfg = json.loads(json.dumps(_SINGLE_PROFILE_LIBRARY_RAW))
        raw_cfg["profiles"]["linux"]["compile"] = {"binding": "gcc99"}
        config = _write_config(tmp_path, raw_cfg)
        bindings = tmp_path / "bindings.yml"
        bindings.write_text(
            yaml.safe_dump(
                {"schema": "abicheck.toolchain-bindings/v1", "bindings": {}}
            ),
            encoding="utf-8",
        )
        raw = abi_project_validate(str(config), toolchain_bindings=str(bindings))
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["result"]["ok"] is False
        assert any("gcc99" in e for e in payload["result"]["errors"])

    def test_malformed_toolchain_bindings_is_an_error(self, tmp_path: Path):
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        bindings = tmp_path / "bindings.yml"
        bindings.write_text("schema: wrong-schema\n", encoding="utf-8")
        raw = abi_project_validate(str(config), toolchain_bindings=str(bindings))
        payload = json.loads(raw)
        assert payload["status"] == "error"

    def test_oversized_config_is_rejected(self, tmp_path: Path, monkeypatch):
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 1)
        raw = abi_project_validate(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "exceeds limit" in payload["error"]

    def test_oversized_toolchain_bindings_is_rejected(
        self, tmp_path: Path, monkeypatch
    ):
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        bindings = tmp_path / "bindings.yml"
        bindings.write_text(
            yaml.safe_dump(
                {"schema": "abicheck.toolchain-bindings/v1", "bindings": {}}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 1)
        raw = abi_project_validate(str(config), toolchain_bindings=str(bindings))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "exceeds limit" in payload["error"]

    def test_timeout(self, tmp_path: Path, monkeypatch):
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr(
            "abicheck.buildsource.project_targets.validate_project_targets", _slow
        )
        raw = abi_project_validate(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]


class TestAbiProjectPlan:
    def test_generates_a_run_plan(self, tmp_path: Path):
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        raw = abi_project_plan(
            str(config), build_outputs=[f"linux={build_dir}"], project="o/r"
        )
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["report"]["ok"] is True
        assert [c["check_id"] for c in payload["plan"]["checks"]] == [
            "libfoo@linux#release@headers"
        ]
        assert payload["plan"]["project"] == "o/r"

    def test_empty_plan_without_allow_empty_is_a_generation_error(self, tmp_path: Path):
        empty_raw = {
            "targets": {},
            "profiles": {"linux": {"contract": True}},
        }
        config = _write_config(tmp_path, empty_raw)
        raw = abi_project_plan(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["report"]["ok"] is False
        assert not payload["plan"]["checks"]

    def test_empty_plan_with_allow_empty_is_accepted(self, tmp_path: Path):
        empty_raw = {
            "targets": {},
            "profiles": {"linux": {"contract": True}},
        }
        config = _write_config(tmp_path, empty_raw)
        raw = abi_project_plan(str(config), allow_empty=True)
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["report"]["ok"] is True
        assert not payload["plan"]["checks"]

    def test_invalid_project_config_is_an_error(self, tmp_path: Path):
        raw_cfg = json.loads(json.dumps(_SINGLE_PROFILE_LIBRARY_RAW))
        raw_cfg["targets"]["libfoo"]["checks"][0]["channel"] = "does-not-exist"
        config = _write_config(tmp_path, raw_cfg)
        raw = abi_project_plan(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "error"

    def test_malformed_build_output_spec_is_an_error(self, tmp_path: Path):
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        raw = abi_project_plan(str(config), build_outputs=["not-a-valid-spec"])
        payload = json.loads(raw)
        assert payload["status"] == "error"

    def test_missing_config_is_an_error(self, tmp_path: Path):
        raw = abi_project_plan(str(tmp_path / "nope.yml"))
        payload = json.loads(raw)
        assert payload["status"] == "error"

    def test_timeout_includes_validation(self, tmp_path: Path, monkeypatch):
        # ADR-021b D2: validate_project_targets runs inside the same bounded
        # worker as generate_run_plan, so a stall there is caught by the
        # timeout too rather than blocking unbounded before it.
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr(
            "abicheck.buildsource.project_targets.validate_project_targets", _slow
        )
        raw = abi_project_plan(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]
