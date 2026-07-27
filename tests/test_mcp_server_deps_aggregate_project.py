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
from abicheck.mcp_server_project import _resolve_sysroot_path  # noqa: E402

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

    def test_timeout_includes_binary_probe(self, tmp_path: Path, monkeypatch):
        # ADR-021b D2: the existence/format/size preflight reads
        # effective_path (a FIFO or a stalled filesystem could block on any
        # of these); it must count against --timeout too, not just
        # check_single_env (Codex review).
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF")
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr("abicheck.mcp_server_project._detect_binary_format", _slow)
        raw = abi_deps(str(binary))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]

    def test_oversized_sysroot_resolved_binary_is_rejected(
        self, tmp_path: Path, monkeypatch
    ):
        # ADR-021b D3: when a sysroot is active, resolver._seed_root parses
        # the sysroot-rebased path, not the host-absolute one passed in --
        # the pre-flight size/format checks must validate that same file.
        # POSIX-style absolute paths (a bare leading "/") aren't recognized
        # as absolute on Windows, so the sysroot-rebasing this test exercises
        # is inherently POSIX-only -- same reasoning as the ELF-only skip
        # above.
        if sys.platform != "linux":
            pytest.skip("sysroot rebasing needs a POSIX-absolute binary_path")
        sysroot_dir = tmp_path / "sysroot"
        rebased = sysroot_dir / "usr" / "lib" / "libfoo.so"
        rebased.parent.mkdir(parents=True)
        rebased.write_bytes(b"\x7fELF" + b"\x00" * 4096)
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 1)
        raw = abi_deps("/usr/lib/libfoo.so", sysroot=str(sysroot_dir))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "exceeds limit" in payload["error"]

    def test_non_elf_sysroot_resolved_binary_is_rejected(self, tmp_path: Path):
        if sys.platform != "linux":
            pytest.skip("sysroot rebasing needs a POSIX-absolute binary_path")
        sysroot_dir = tmp_path / "sysroot"
        rebased = sysroot_dir / "usr" / "lib" / "libfoo.so"
        rebased.parent.mkdir(parents=True)
        rebased.write_text("not an elf file")
        raw = abi_deps("/usr/lib/libfoo.so", sysroot=str(sysroot_dir))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "elf" in payload["error"].lower()

    def test_symlinked_absolute_binary_path_is_not_resolved_before_sysroot_rebase(
        self, tmp_path: Path, monkeypatch
    ):
        # resolver._seed_root rebases the *raw, symbolic* path a caller asked
        # for (e.g. "/lib/app" on a merged-/usr host), never the host's
        # resolved symlink target ("/usr/lib/app") -- _safe_read_path always
        # resolves symlinks, so using its return value for the sysroot
        # rebase would land at the wrong location under sysroot (Codex
        # review).
        if sys.platform != "linux":
            pytest.skip("sysroot rebasing needs a POSIX-absolute binary_path")
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        host_link = tmp_path / "hostlink"
        host_link.symlink_to(real_dir)
        binary_path = host_link / "app"
        sysroot_dir = tmp_path / "sysroot"
        # The correct rebase preserves the raw "hostlink" path component.
        correct_rebased = sysroot_dir / str(binary_path).lstrip("/")
        correct_rebased.parent.mkdir(parents=True)
        correct_rebased.write_bytes(b"\x7fELF" + b"\x00" * 4096)
        # Deliberately do NOT create the buggy, host-resolved rebase
        # (.../sysroot/.../real/app) -- if the preflight used that path
        # instead, it would report "Binary file not found" rather than
        # reaching check_single_env.

        received: list[Path] = []

        def _spy(binary_arg, **kwargs):
            received.append(binary_arg)
            raise RuntimeError("stop after capturing args")

        monkeypatch.setattr("abicheck.stack_checker.check_single_env", _spy)
        raw = abi_deps(str(binary_path), sysroot=str(sysroot_dir))
        payload = json.loads(raw)
        assert payload == {
            "status": "error",
            "error": "abi_deps failed: unexpected error",
        }
        assert received == [binary_path]

    def test_relative_binary_path_is_not_rebased_under_sysroot(
        self, tmp_path: Path, monkeypatch
    ):
        # A relative binary_path is always resolved against cwd, matching
        # `deps tree`'s Click-based (non-resolving) Path handling: sysroot
        # only affects where a binary's *dependencies* are searched, never
        # the root binary itself, when the caller passed a relative path
        # (the CLI's own documented `deps tree ./app --sysroot ...` example).
        # _safe_read_path always resolves its input to absolute, so without
        # tracking the original relative-ness abi_deps would wrongly rebase
        # this under "<sysroot>/<absolutized-cwd>/app" instead (Codex review).
        monkeypatch.chdir(tmp_path)
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF")
        sysroot_dir = tmp_path / "sysroot"
        sysroot_dir.mkdir()

        received: list[Path] = []

        def _spy(binary_arg, **kwargs):
            received.append(binary_arg)
            raise RuntimeError("stop after capturing args")

        monkeypatch.setattr("abicheck.stack_checker.check_single_env", _spy)
        raw = abi_deps("app", sysroot=str(sysroot_dir))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert received == [Path("app")]

    def test_relative_search_path_is_not_rebased_under_sysroot(
        self, tmp_path: Path, monkeypatch
    ):
        # Same class of bug as the relative binary_path case above, but for
        # search_paths entries: resolver._build_search_order joins a relative
        # entry directly onto sysroot ("<sysroot>/lib"), matching the CLI's
        # Click-based (non-resolving) handling -- _safe_read_path
        # absolutizing a relative entry first would make that join produce
        # "<sysroot>/<cwd>/lib" instead (Codex review).
        monkeypatch.chdir(tmp_path)
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF")
        sysroot_dir = tmp_path / "sysroot"
        sysroot_dir.mkdir()
        (tmp_path / "lib").mkdir()
        absolute_search_path = tmp_path / "abs-lib"
        absolute_search_path.mkdir()

        received: dict[str, object] = {}

        def _spy(binary_arg, **kwargs):
            received.update(kwargs)
            raise RuntimeError("stop after capturing args")

        monkeypatch.setattr("abicheck.stack_checker.check_single_env", _spy)
        raw = abi_deps(
            "app",
            sysroot=str(sysroot_dir),
            search_paths=["lib", str(absolute_search_path)],
        )
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert received["search_paths"] == [
            Path("lib"),
            absolute_search_path.resolve(),
        ]

    def test_missing_search_path_is_a_clear_error(self, tmp_path: Path):
        # `deps tree`'s --search-path is declared click.Path(exists=True);
        # without an equivalent check here, a typo'd/missing search
        # directory silently produced a falsely-unresolved dependency
        # instead of a clear error (Codex review).
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF")
        raw = abi_deps(str(binary), search_paths=[str(tmp_path / "does-not-exist")])
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "does not exist" in payload["error"]


class TestResolveSysrootPath:
    def test_no_sysroot_returns_binary_unchanged(self):
        binary = Path("/usr/bin/foo")
        assert _resolve_sysroot_path(binary, None) == binary

    def test_absolute_binary_rebased_under_sysroot(self, tmp_path: Path):
        # A bare leading "/" isn't absolute on Windows (no drive), so this
        # rebasing case only applies on POSIX.
        if sys.platform == "win32":
            pytest.skip("POSIX-absolute binary path needed for this case")
        binary = Path("/usr/lib/libfoo.so")
        assert (
            _resolve_sysroot_path(binary, tmp_path)
            == (tmp_path / "usr" / "lib" / "libfoo.so").resolve()
        )

    def test_binary_already_under_sysroot_is_unchanged(self, tmp_path: Path):
        binary = tmp_path / "usr" / "lib" / "libfoo.so"
        assert _resolve_sysroot_path(binary, tmp_path) == binary

    def test_sibling_path_sharing_sysroot_prefix_is_still_rebased(self, tmp_path: Path):
        # A sibling directory whose name happens to start with the sysroot's
        # name (e.g. "<tmp>/sysroot-other") is not "already under" the
        # sysroot ("<tmp>/sysroot") -- a raw string-prefix check would
        # wrongly treat it as such and skip rebasing (CodeRabbit).
        sysroot = tmp_path / "sysroot"
        binary = tmp_path / "sysroot-other" / "lib.so"
        assert (
            _resolve_sysroot_path(binary, sysroot)
            == (sysroot / str(binary).lstrip("/")).resolve()
        )


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

    def test_reports_dir_not_a_directory_error_does_not_leak_its_path(
        self, tmp_path: Path
    ):
        f = tmp_path / "not_a_dir.json"
        f.write_text("{}")
        raw = abi_aggregate(str(f), discovered_only=True)
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert str(f) not in payload["error"]
        assert f.name in payload["error"]

    def test_timeout_includes_manifest_size_check(self, tmp_path: Path, monkeypatch):
        # ADR-021b D2: a stalled filesystem's single stat() call for the
        # manifest must count against --timeout too, not just report
        # discovery/expected-set parsing/aggregation (Codex review).
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
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(path, *, label="input"):
            if label == "manifest":
                time.sleep(1.0)
                raise AssertionError("should have timed out first")
            return None

        monkeypatch.setattr("abicheck.mcp_server_project._check_file_size", _slow)
        raw = abi_aggregate(str(tmp_path), manifest=str(manifest))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]

    def test_missing_reports_dir_produces_coverage_result(self, tmp_path: Path):
        # aggregate.collect_reports deliberately treats a missing directory
        # as zero reports, so a full build-matrix outage still produces a
        # structured required-coverage failure (exit 1) rather than a
        # generic tool error (Codex review).
        missing = tmp_path / "does-not-exist"
        raw = abi_aggregate(str(missing), expect=["linux-x86_64"])
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 1

    def test_timeout_includes_expected_set_resolution(
        self, tmp_path: Path, monkeypatch
    ):
        # ADR-021b D2: a slow manifest/run-plan read must count against
        # --timeout too, not just the aggregate_reports_dir call that
        # follows it (Codex review).
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr("abicheck.cli_aggregate._resolve_expected", _slow)
        raw = abi_aggregate(str(tmp_path), expect=["linux-x86_64"])
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]

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

    def test_timeout_includes_report_directory_discovery(
        self, tmp_path: Path, monkeypatch
    ):
        # ADR-021b D2: the *.json glob+stat over reports_dir must count
        # against --timeout too, not just aggregate_reports_dir itself
        # (Codex review).
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr(
            "abicheck.mcp_server_project._check_dir_json_file_sizes", _slow
        )
        raw = abi_aggregate(str(tmp_path), discovered_only=True)
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]

    def test_timeout_includes_reports_dir_resolution(self, tmp_path: Path, monkeypatch):
        # ADR-021b D2: _safe_read_path(reports_dir)'s symlink-following
        # .resolve() call and the subsequent exists()/is_dir() type check
        # must count against --timeout too, not just report discovery/
        # aggregation (Codex review). No manifest/run_plan is passed, so
        # this is the only _safe_read_path call abi_aggregate makes here.
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr("abicheck.mcp_server_project._safe_read_path", _slow)
        raw = abi_aggregate(str(tmp_path), discovered_only=True)
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]

    def test_malformed_run_plan_error_does_not_leak_its_path(self, tmp_path: Path):
        # cli_aggregate._resolve_expected's own error embeds the full
        # run_plan path for a terminal reader; the MCP response must not
        # leak local filesystem structure the same way (Codex review).
        _write_report(tmp_path, "linux-x86_64", "COMPATIBLE")
        run_plan = tmp_path / "run-plan.json"
        run_plan.write_text("not json")
        raw = abi_aggregate(str(tmp_path), run_plan=str(run_plan))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert str(run_plan) not in payload["error"]
        assert run_plan.name in payload["error"]


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
        raw_cfg = json.loads(json.dumps(_SINGLE_PROFILE_LIBRARY_RAW))  # deep copy
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

    def test_timeout_includes_config_parsing(self, tmp_path: Path, monkeypatch):
        # ADR-021b D2: a slow-to-read/parse config must count against
        # --timeout too, not just validate_project_targets (Codex review).
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr("abicheck.cli_project._load_project_targets_config", _slow)
        raw = abi_project_validate(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]

    def test_malformed_yaml_error_does_not_leak_config_path(self, tmp_path: Path):
        config = tmp_path / ".abicheck.yml"
        config.write_text("- just\n- a\n- list\n", encoding="utf-8")
        raw = abi_project_validate(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert str(config) not in payload["error"]
        assert config.name in payload["error"]


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

    def test_timeout_includes_config_parsing(self, tmp_path: Path, monkeypatch):
        # ADR-021b D2: a slow-to-read/parse config must count against
        # --timeout too, not just generate_run_plan (Codex review).
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr("abicheck.cli_project._load_project_targets_config", _slow)
        raw = abi_project_plan(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert "timed out" in payload["error"]

    def test_missing_config_error_does_not_leak_config_path(self, tmp_path: Path):
        config = tmp_path / ".abicheck.yml"
        config.write_text("- just\n- a\n- list\n", encoding="utf-8")
        raw = abi_project_plan(str(config))
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert str(config) not in payload["error"]
        assert config.name in payload["error"]

    def test_malformed_build_output_error_does_not_leak_dir_path(self, tmp_path: Path):
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        build_dir = tmp_path / "build-linux"
        build_dir.mkdir()
        (build_dir / "build-output.json").write_text("not json", encoding="utf-8")
        raw = abi_project_plan(str(config), build_outputs=[f"linux={build_dir}"])
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert str(build_dir) not in payload["error"]
        assert build_dir.name in payload["error"]
