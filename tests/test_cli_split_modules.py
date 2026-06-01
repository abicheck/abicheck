"""CLI-level tests for the `baseline`, `debian-symbols`, and `compare-release`
command groups whose code lives in the split sub-modules
(`cli_baseline.py`, `cli_debian_symbols.py`, `cli_compare_release.py`).

These exist primarily to cover error and edge paths in the sub-modules so
they hit the 80% patch-coverage gate after the refactor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from abicheck.baseline import BaselineKey, BaselineMetadata, FilesystemRegistry
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility


@pytest.fixture()
def sample_snapshot_file(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0.0",
        functions=[
            Function(name="foo", mangled="foo", return_type="void",
                     visibility=Visibility.PUBLIC),
        ],
    )
    from abicheck.serialization import snapshot_to_json
    path = tmp_path / "snap.json"
    path.write_text(snapshot_to_json(snap))
    return path


@pytest.fixture()
def registry_dir(tmp_path: Path) -> Path:
    return tmp_path / "baselines"


# ---------------------------------------------------------------------------
# baseline push
# ---------------------------------------------------------------------------


class TestBaselinePush:
    def test_push_with_explicit_platform(
        self, sample_snapshot_file: Path, registry_dir: Path,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "push", "libfoo",
            "--version", "1.0.0",
            "--platform", "linux-x86_64",
            "--snapshot", str(sample_snapshot_file),
            "--registry", str(registry_dir),
        ])
        assert result.exit_code == 0, result.output
        assert "Baseline pushed" in (result.output + result.stderr if hasattr(result, "stderr") else result.output)

        reg = FilesystemRegistry(registry_dir)
        pulled = reg.pull(BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64"))
        assert pulled is not None

    def test_push_missing_platform_errors(
        self, sample_snapshot_file: Path, registry_dir: Path,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "push", "libfoo",
            "--version", "1.0.0",
            "--snapshot", str(sample_snapshot_file),
            "--registry", str(registry_dir),
        ])
        assert result.exit_code != 0
        assert "Platform is required" in result.output

    def test_push_auto_platform_no_library_path(
        self, tmp_path: Path, registry_dir: Path,
    ) -> None:
        snap = AbiSnapshot(library="", version="1.0.0")  # no library path
        from abicheck.serialization import snapshot_to_json
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(snapshot_to_json(snap))

        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "push", "libfoo",
            "--version", "1.0.0",
            "--auto-platform",
            "--snapshot", str(snap_path),
            "--registry", str(registry_dir),
        ])
        assert result.exit_code != 0
        assert "snapshot has no library path" in result.output

    def test_push_auto_platform_binary_missing(
        self, tmp_path: Path, registry_dir: Path,
    ) -> None:
        snap = AbiSnapshot(library="/nonexistent/path/libfoo.so", version="1.0.0")
        from abicheck.serialization import snapshot_to_json
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(snapshot_to_json(snap))

        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "push", "libfoo",
            "--version", "1.0.0",
            "--auto-platform",
            "--snapshot", str(snap_path),
            "--registry", str(registry_dir),
        ])
        assert result.exit_code != 0
        assert "not found on disk" in result.output

    def test_push_auto_platform_detection_fails(
        self, tmp_path: Path, registry_dir: Path,
    ) -> None:
        # Real path that exists, but detection returns None.
        bin_path = tmp_path / "fake.so"
        bin_path.write_bytes(b"not-an-elf")
        snap = AbiSnapshot(library=str(bin_path), version="1.0.0")
        from abicheck.serialization import snapshot_to_json
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(snapshot_to_json(snap))

        runner = CliRunner()
        with patch("abicheck.baseline.detect_platform_from_binary", return_value=None):
            result = runner.invoke(main, [
                "baseline", "push", "libfoo",
                "--version", "1.0.0",
                "--auto-platform",
                "--snapshot", str(snap_path),
                "--registry", str(registry_dir),
            ])
        assert result.exit_code != 0
        assert "failed to detect binary architecture" in result.output

    def test_push_auto_platform_success(
        self, tmp_path: Path, registry_dir: Path,
    ) -> None:
        bin_path = tmp_path / "fake.so"
        bin_path.write_bytes(b"not-an-elf")
        snap = AbiSnapshot(library=str(bin_path), version="1.0.0")
        from abicheck.serialization import snapshot_to_json
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(snapshot_to_json(snap))

        runner = CliRunner()
        with patch("abicheck.baseline.detect_platform_from_binary", return_value="linux-x86_64"):
            result = runner.invoke(main, [
                "baseline", "push", "libfoo",
                "--version", "1.0.0",
                "--auto-platform",
                "--snapshot", str(snap_path),
                "--registry", str(registry_dir),
            ])
        assert result.exit_code == 0, result.output
        assert "Auto-detected platform: linux-x86_64" in result.output

    def test_push_invalid_key_rejected(
        self, sample_snapshot_file: Path, registry_dir: Path,
    ) -> None:
        runner = CliRunner()
        # invalid library name (path traversal) is rejected by BaselineKey
        result = runner.invoke(main, [
            "baseline", "push", "../../etc",
            "--version", "1.0.0",
            "--platform", "linux-x86_64",
            "--snapshot", str(sample_snapshot_file),
            "--registry", str(registry_dir),
        ])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# baseline pull
# ---------------------------------------------------------------------------


def _push_one(
    registry_dir: Path,
    library: str = "libfoo",
    version: str = "1.0.0",
    platform: str = "linux-x86_64",
) -> AbiSnapshot:
    """Push a snapshot directly to a registry (bypassing the CLI) for setup."""
    snap = AbiSnapshot(
        library=f"{library}.so", version=version,
        functions=[Function(name="x", mangled="x", return_type="void",
                            visibility=Visibility.PUBLIC)],
    )
    from abicheck.serialization import snapshot_to_json
    reg = FilesystemRegistry(registry_dir)
    meta = BaselineMetadata.create(snapshot_to_json(snap))
    reg.push(
        BaselineKey(library=library, version=version, platform=platform),
        snap, meta,
    )
    return snap


class TestBaselinePull:
    def test_pull_success(self, tmp_path: Path, registry_dir: Path) -> None:
        _push_one(registry_dir)
        out = tmp_path / "pulled.json"
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "pull", "libfoo:1.0.0:linux-x86_64",
            "-o", str(out),
            "--registry", str(registry_dir),
        ])
        assert result.exit_code == 0, result.output
        assert out.exists()
        payload = json.loads(out.read_text())
        assert payload["library"] == "libfoo.so"

    def test_pull_not_found(self, tmp_path: Path, registry_dir: Path) -> None:
        out = tmp_path / "pulled.json"
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "pull", "ghost:9.9.9:linux-x86_64",
            "-o", str(out),
            "--registry", str(registry_dir),
        ])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()
        assert not out.exists()

    def test_pull_malformed_spec(self, tmp_path: Path, registry_dir: Path) -> None:
        out = tmp_path / "pulled.json"
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "pull", "bad-spec-no-colons",
            "-o", str(out),
            "--registry", str(registry_dir),
        ])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# baseline list
# ---------------------------------------------------------------------------


class TestBaselineList:
    def test_list_empty(self, registry_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "list", "--registry", str(registry_dir),
        ])
        assert result.exit_code == 0
        assert "No baselines found" in result.output

    def test_list_text(self, registry_dir: Path) -> None:
        _push_one(registry_dir, library="libfoo", version="1.0.0")
        _push_one(registry_dir, library="libbar", version="2.0.0")
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "list", "--registry", str(registry_dir),
        ])
        assert result.exit_code == 0
        assert "libfoo/1.0.0/linux-x86_64" in result.output
        assert "libbar/2.0.0/linux-x86_64" in result.output

    def test_list_json(self, registry_dir: Path) -> None:
        _push_one(registry_dir, library="libfoo")
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "list", "--registry", str(registry_dir), "--format", "json",
        ])
        assert result.exit_code == 0
        # Format may emit some banner on stderr; parse stdout JSON.
        payload = json.loads(result.output)
        assert isinstance(payload, list) and len(payload) == 1
        assert payload[0]["library"] == "libfoo"

    def test_list_with_prefix(self, registry_dir: Path) -> None:
        _push_one(registry_dir, library="libfoo")
        _push_one(registry_dir, library="libbar")
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "list", "libfoo", "--registry", str(registry_dir),
        ])
        assert result.exit_code == 0
        assert "libfoo" in result.output
        assert "libbar" not in result.output


# ---------------------------------------------------------------------------
# baseline delete
# ---------------------------------------------------------------------------


class TestBaselineDelete:
    def test_delete_success(self, registry_dir: Path) -> None:
        _push_one(registry_dir)
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "delete", "libfoo:1.0.0:linux-x86_64",
            "--registry", str(registry_dir),
        ])
        assert result.exit_code == 0
        # And the entry is gone.
        reg = FilesystemRegistry(registry_dir)
        assert reg.pull(BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")) is None

    def test_delete_not_found(self, registry_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "delete", "ghost:9.9.9:linux-x86_64",
            "--registry", str(registry_dir),
        ])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_delete_malformed_spec(self, registry_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, [
            "baseline", "delete", "bad-spec",
            "--registry", str(registry_dir),
        ])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# debian-symbols generate
# ---------------------------------------------------------------------------


def _make_symbol(name: str) -> Any:
    from abicheck.elf_metadata import ElfSymbol, SymbolBinding, SymbolType
    return ElfSymbol(
        name=name, sym_type=SymbolType.FUNC, version="",
        binding=SymbolBinding.GLOBAL,
    )


def _make_elf_meta(*, symbols: list[Any]) -> Any:
    from abicheck.elf_metadata import ElfMetadata
    return ElfMetadata(soname="libfoo.so.1", symbols=symbols)


class TestDebianSymbolsGenerate:
    def test_generate_to_stdout(self, tmp_path: Path) -> None:
        meta = _make_elf_meta(symbols=[_make_symbol("foo"), _make_symbol("bar")])
        runner = CliRunner()
        so_path = tmp_path / "libfoo.so"
        so_path.write_bytes(b"\x7fELF")  # dummy file (parsed via mock)
        with patch("abicheck.debian_symbols.parse_elf_metadata", return_value=meta):
            result = runner.invoke(main, [
                "debian-symbols", "generate", str(so_path),
            ])
        assert result.exit_code == 0, result.output
        assert "libfoo.so.1" in result.output
        assert "foo" in result.output

    def test_generate_to_file(self, tmp_path: Path) -> None:
        meta = _make_elf_meta(symbols=[_make_symbol("foo")])
        out_path = tmp_path / "subdir" / "libfoo1.symbols"
        runner = CliRunner()
        so_path = tmp_path / "libfoo.so"
        so_path.write_bytes(b"\x7fELF")
        with patch("abicheck.debian_symbols.parse_elf_metadata", return_value=meta):
            result = runner.invoke(main, [
                "debian-symbols", "generate", str(so_path),
                "-o", str(out_path),
                "--package", "libfoo1",
                "--version", "1.0",
            ])
        assert result.exit_code == 0, result.output
        assert out_path.exists()
        text = out_path.read_text()
        assert "libfoo.so.1 libfoo1" in text
        assert "foo" in text
        assert "Symbols file written" in result.output

    def test_generate_no_cpp(self, tmp_path: Path) -> None:
        meta = _make_elf_meta(symbols=[_make_symbol("_Z3foov")])
        runner = CliRunner()
        so_path = tmp_path / "libfoo.so"
        so_path.write_bytes(b"\x7fELF")
        with patch("abicheck.debian_symbols.parse_elf_metadata", return_value=meta):
            result = runner.invoke(main, [
                "debian-symbols", "generate", str(so_path),
                "--no-cpp",
            ])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# compare-release error paths
# ---------------------------------------------------------------------------


class TestCompareReleaseErrorPaths:
    def test_error_message_when_pair_raises(self, tmp_path: Path) -> None:
        """When _compare_one_library raises an unexpected exception, the
        per-library entry should carry an ERROR verdict with the message."""
        from abicheck.cli_compare_release import _compare_one_library

        old_path = tmp_path / "libfoo.so"
        new_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        new_path.write_bytes(b"\x7fELF")

        with patch(
            "abicheck.cli_compare_release._run_compare_pair",
            side_effect=RuntimeError("boom"),
        ):
            entry = _compare_one_library(
                key="libfoo.so",
                old_map={"libfoo.so": old_path},
                new_map={"libfoo.so": new_path},
                old_debug_dir=None,
                new_debug_dir=None,
                resolve_debug_info=lambda *_a, **_kw: None,
                old_h=[], new_h=[],
                old_inc=[], new_inc=[],
                old_version="1", new_version="2",
                lang="c++", suppress=None,
                policy="", policy_file_path=None,
                output_dir=None,
            )
        assert entry["verdict"] == "ERROR"
        assert "boom" in str(entry["error"])

    def test_click_exception_becomes_error_entry(self, tmp_path: Path) -> None:
        """A click.ClickException raised by the comparison should be caught
        and converted to an ERROR entry rather than aborting the run."""
        import click

        from abicheck.cli_compare_release import _compare_one_library

        old_path = tmp_path / "libfoo.so"
        new_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        new_path.write_bytes(b"\x7fELF")

        with patch(
            "abicheck.cli_compare_release._run_compare_pair",
            side_effect=click.ClickException("nope"),
        ):
            entry = _compare_one_library(
                key="libfoo.so",
                old_map={"libfoo.so": old_path},
                new_map={"libfoo.so": new_path},
                old_debug_dir=None,
                new_debug_dir=None,
                resolve_debug_info=lambda *_a, **_kw: None,
                old_h=[], new_h=[],
                old_inc=[], new_inc=[],
                old_version="1", new_version="2",
                lang="c++", suppress=None,
                policy="", policy_file_path=None,
                output_dir=None,
            )
        assert entry["verdict"] == "ERROR"
        assert "nope" in str(entry["error"])

    def test_annotate_additions_requires_annotate(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare-release", str(old_dir), str(new_dir),
            "--annotate-additions",
        ])
        assert result.exit_code != 0
        assert "--annotate-additions requires --annotate" in result.output

    def test_empty_input_dir_errors(self, tmp_path: Path) -> None:
        """Empty directories produce a clear 'no supported ABI inputs' error."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare-release", str(old_dir), str(new_dir),
            "--no-bundle-analysis",
        ])
        assert result.exit_code != 0
        assert "No supported ABI inputs" in result.output

    def test_format_release_summary_json(self, tmp_path: Path) -> None:
        """_format_release_summary returns a parseable JSON object when
        fmt=\"json\"."""
        from abicheck.cli_compare_release import _format_release_summary

        text = _format_release_summary(
            fmt="json",
            worst_verdict="COMPATIBLE",
            old_dir=tmp_path / "old",
            new_dir=tmp_path / "new",
            library_results=[
                {"library": "libfoo.so", "verdict": "COMPATIBLE",
                 "breaking": 0, "source_breaks": 0,
                 "risk_changes": 0, "compatible_additions": 1},
            ],
            removed_keys=[],
            added_keys=[],
            old_map={},
            new_map={},
            warning_msgs=["info: trace"],
        )
        payload = json.loads(text)
        assert payload["verdict"] == "COMPATIBLE"
        assert len(payload["libraries"]) == 1
        assert payload["libraries"][0]["library"] == "libfoo.so"

    def test_format_release_summary_markdown(self, tmp_path: Path) -> None:
        """Markdown format includes a header and per-library lines."""
        from abicheck.cli_compare_release import _format_release_summary

        text = _format_release_summary(
            fmt="markdown",
            worst_verdict="BREAKING",
            old_dir=tmp_path / "old",
            new_dir=tmp_path / "new",
            library_results=[
                {"library": "libfoo.so", "verdict": "BREAKING",
                 "breaking": 2, "source_breaks": 0,
                 "risk_changes": 0, "compatible_additions": 0},
            ],
            removed_keys=["libold.so"],
            added_keys=["libnew.so"],
            old_map={"libold.so": tmp_path / "old" / "libold.so"},
            new_map={"libnew.so": tmp_path / "new" / "libnew.so"},
            warning_msgs=[],
        )
        assert "BREAKING" in text
        assert "libfoo.so" in text

    @staticmethod
    def _matrix_change():
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        return Change(
            kind=ChangeKind.CXX_STANDARD_FLOOR_RAISED,
            symbol="cxx14",
            description="C++ standard floor raised from C++14 to C++17",
            old_value="cxx14",
            new_value="cxx17",
        )

    @classmethod
    def _matrix_result(cls):
        """A DiffResult carrying the matrix change (via the real pipeline)."""
        from abicheck.checker import compare
        from abicheck.model import AbiSnapshot
        return compare(
            AbiSnapshot(library="<build-config matrix>", version="1.0"),
            AbiSnapshot(library="<build-config matrix>", version="2.0"),
            extra_changes=[cls._matrix_change()],
            scope_to_public_surface=False,
        )

    def test_format_release_summary_json_matrix_findings(self, tmp_path: Path) -> None:
        """Release-global matrix findings surface in the JSON summary."""
        from abicheck.cli_compare_release import _format_release_summary

        text = _format_release_summary(
            fmt="json",
            worst_verdict="API_BREAK",
            old_dir=tmp_path / "old",
            new_dir=tmp_path / "new",
            library_results=[],
            removed_keys=[],
            added_keys=[],
            old_map={},
            new_map={},
            warning_msgs=[],
            matrix_result=self._matrix_result(),
        )
        payload = json.loads(text)
        assert payload["matrix_verdict"] == "API_BREAK"
        assert payload["matrix_findings"] == [
            {
                "kind": "cxx_standard_floor_raised",
                "symbol": "cxx14",
                "description": "C++ standard floor raised from C++14 to C++17",
                "old_value": "cxx14",
                "new_value": "cxx17",
            }
        ]

    def test_format_release_summary_markdown_matrix_findings(self, tmp_path: Path) -> None:
        """Markdown renders a build-configuration findings section."""
        from abicheck.cli_compare_release import _format_release_summary

        text = _format_release_summary(
            fmt="markdown",
            worst_verdict="API_BREAK",
            old_dir=tmp_path / "old",
            new_dir=tmp_path / "new",
            library_results=[],
            removed_keys=[],
            added_keys=[],
            old_map={},
            new_map={},
            warning_msgs=[],
            matrix_result=self._matrix_result(),
        )
        assert "Build-Configuration (Matrix) Findings" in text
        assert "cxx_standard_floor_raised" in text

    def test_format_release_summary_junit_matrix_findings(self, tmp_path: Path) -> None:
        """JUnit output includes a testsuite for the matrix finding so CI
        dashboards reading the report see the ABI failure (Codex review)."""
        from abicheck.cli_compare_release import _format_release_summary

        text = _format_release_summary(
            fmt="junit",
            worst_verdict="API_BREAK",
            old_dir=tmp_path / "old",
            new_dir=tmp_path / "new",
            library_results=[],
            removed_keys=[],
            added_keys=[],
            old_map={},
            new_map={},
            warning_msgs=[],
            matrix_result=self._matrix_result(),
        )
        assert "cxx_standard_floor_raised" in text
        assert "<testsuite" in text

    def test_collect_matrix_result_no_snapshots(self) -> None:
        """Without matrix snapshots the result is None and verdict unchanged."""
        from abicheck.cli_compare_release import _collect_matrix_result

        result, verdict = _collect_matrix_result(
            None, None, "strict_abi", "COMPATIBLE",
        )
        assert result is None
        assert verdict == "COMPATIBLE"

    def test_collect_matrix_result_folds_verdict(self, tmp_path: Path) -> None:
        """Matrix findings escalate the worst-of release verdict."""
        from abicheck import cli_compare_release

        fake = [self._matrix_change()]
        old_m, new_m = tmp_path / "o.json", tmp_path / "n.json"
        with patch(
            "abicheck.cli._load_probe_matrix_changes", return_value=fake,
        ):
            result, verdict = cli_compare_release._collect_matrix_result(
                old_m, new_m, "strict_abi", "COMPATIBLE",
            )
        # CXX_STANDARD_FLOOR_RAISED is a source-level break → API_BREAK,
        # which is worse than the incoming COMPATIBLE.
        assert verdict == "API_BREAK"
        assert result is not None
        assert [c.kind.value for c in result.changes] == ["cxx_standard_floor_raised"]

    def test_collect_matrix_result_respects_policy_file_override(self, tmp_path: Path) -> None:
        """A --policy-file override (e.g. ignore) applies to matrix findings,
        matching the single-pair compare path (checker.compare → PolicyFile)."""
        from abicheck import cli_compare_release

        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            "base_policy: strict_abi\n"
            "overrides:\n"
            "  cxx_standard_floor_raised: ignore\n",
            encoding="utf-8",
        )
        fake = [self._matrix_change()]
        old_m, new_m = tmp_path / "o.json", tmp_path / "n.json"
        with patch(
            "abicheck.cli._load_probe_matrix_changes", return_value=fake,
        ):
            _, verdict = cli_compare_release._collect_matrix_result(
                old_m, new_m, "strict_abi", "COMPATIBLE",
                policy_file_path=policy_file,
            )
        # The override downgrades the finding, so it must NOT escalate the
        # incoming COMPATIBLE verdict to API_BREAK.
        assert verdict == "COMPATIBLE"

    def test_collect_matrix_result_respects_suppression(self, tmp_path: Path) -> None:
        """A --suppress rule applies to matrix findings, matching the compare
        path (which routes extra_changes through checker.compare). (Codex P2)"""
        from abicheck import cli_compare_release

        supp = tmp_path / "supp.yaml"
        supp.write_text(
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: cxx14\n"
            "    change_kind: cxx_standard_floor_raised\n"
            "    reason: intentional floor raise\n",
            encoding="utf-8",
        )
        fake = [self._matrix_change()]
        old_m, new_m = tmp_path / "o.json", tmp_path / "n.json"
        with patch(
            "abicheck.cli._load_probe_matrix_changes", return_value=fake,
        ):
            result, verdict = cli_compare_release._collect_matrix_result(
                old_m, new_m, "strict_abi", "COMPATIBLE",
                suppress=supp,
            )
        # Suppressed → no kept finding and the verdict is not escalated.
        assert verdict == "COMPATIBLE"
        assert result is not None
        assert result.changes == []
        assert result.suppressed_count == 1

    def test_exit_compare_release_breaking(self) -> None:
        """_exit_compare_release maps BREAKING verdict to exit 4."""
        from abicheck.cli_compare_release import _exit_compare_release

        with pytest.raises(SystemExit) as exc_info:
            _exit_compare_release("BREAKING", fail_on_removed=False, removed_keys=[])
        assert exc_info.value.code == 4

    def test_exit_compare_release_api_break(self) -> None:
        from abicheck.cli_compare_release import _exit_compare_release

        with pytest.raises(SystemExit) as exc_info:
            _exit_compare_release("API_BREAK", fail_on_removed=False, removed_keys=[])
        assert exc_info.value.code == 2

    def test_exit_compare_release_removed_library_fail(self) -> None:
        """When --fail-on-removed-library is set and a library was removed,
        exit with code 8 even if the verdict itself is compatible."""
        from abicheck.cli_compare_release import _exit_compare_release

        with pytest.raises(SystemExit) as exc_info:
            _exit_compare_release(
                "COMPATIBLE", fail_on_removed=True, removed_keys=["libgone.so"],
            )
        assert exc_info.value.code == 8

    def test_bundle_analysis_snapshot_failure_returns_none(self, tmp_path: Path) -> None:
        """If build_bundle_snapshot raises, _run_bundle_analysis should
        log a warning and return None instead of crashing the run."""
        from abicheck.cli_compare_release import _run_bundle_analysis

        old_path = tmp_path / "libfoo.so"
        new_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        new_path.write_bytes(b"\x7fELF")

        with patch(
            "abicheck.bundle.build_bundle_snapshot",
            side_effect=RuntimeError("snapshot kaboom"),
        ):
            result = _run_bundle_analysis(
                old_map={"libfoo.so": old_path},
                new_map={"libfoo.so": new_path},
                per_lib_results=[],
                manifest_path=None,
                bundle_system_providers="",
            )
        assert result is None

    def test_bundle_analysis_compare_raises_returns_empty(self, tmp_path: Path) -> None:
        """If compare_bundle itself raises, _run_bundle_analysis returns
        an empty BundleDiffResult (degraded mode) rather than failing."""
        from abicheck.bundle import BundleDiffResult
        from abicheck.cli_compare_release import _run_bundle_analysis

        fake_snap = type("S", (), {"root": tmp_path})()
        old_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")

        with patch(
            "abicheck.bundle.build_bundle_snapshot",
            return_value=fake_snap,
        ), patch(
            "abicheck.bundle.compare_bundle",
            side_effect=RuntimeError("compare boom"),
        ):
            result = _run_bundle_analysis(
                old_map={"libfoo.so": old_path},
                new_map={"libfoo.so": old_path},
                per_lib_results=[],
                manifest_path=None,
                bundle_system_providers="sysA,sysB",
            )
        assert isinstance(result, BundleDiffResult)

    def test_bundle_analysis_bad_manifest_raises(self, tmp_path: Path) -> None:
        """A malformed --manifest path raises ClickException."""
        import click

        from abicheck.cli_compare_release import _run_bundle_analysis

        fake_snap = type("S", (), {"root": tmp_path})()
        bad_manifest = tmp_path / "nope.toml"

        old_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")

        with patch(
            "abicheck.bundle.build_bundle_snapshot",
            return_value=fake_snap,
        ), patch(
            "abicheck.bundle.load_manifest",
            side_effect=FileNotFoundError("missing"),
        ):
            with pytest.raises(click.ClickException, match="Failed to load manifest"):
                _run_bundle_analysis(
                    old_map={"libfoo.so": old_path},
                    new_map={"libfoo.so": old_path},
                    per_lib_results=[],
                    manifest_path=bad_manifest,
                    bundle_system_providers="",
                )

    def test_collect_release_extras_handles_compare_failure(
        self, tmp_path: Path,
    ) -> None:
        """When _run_compare_pair raises inside _collect_release_extras,
        the function logs a warning and continues with subsequent
        libraries instead of aborting."""
        from abicheck.cli_compare_release import _collect_release_extras

        old_path = tmp_path / "libfoo.so"
        new_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        new_path.write_bytes(b"\x7fELF")

        with patch(
            "abicheck.cli_compare_release._run_compare_pair",
            side_effect=RuntimeError("retry-boom"),
        ):
            pairs, annotations = _collect_release_extras(
                matched_keys=["libfoo.so"],
                old_map={"libfoo.so": old_path},
                new_map={"libfoo.so": new_path},
                old_debug_dir=None, new_debug_dir=None,
                resolve_debug_info=lambda *_a, **_kw: None,
                old_h=[], new_h=[],
                old_inc=[], new_inc=[],
                old_version="1", new_version="2",
                lang="c++",
                suppress=None, policy="", policy_file_path=None,
                annotate_additions=False,
                collect_diff_results=True,
                annotate=False,
            )
        assert pairs == []
        assert annotations == []

    def test_format_release_summary_junit(self, tmp_path: Path) -> None:
        """JUnit format emits XML with <testsuites>."""
        from abicheck.cli_compare_release import _format_release_summary

        text = _format_release_summary(
            fmt="junit",
            worst_verdict="COMPATIBLE",
            old_dir=tmp_path / "old",
            new_dir=tmp_path / "new",
            library_results=[
                {"library": "libfoo.so", "verdict": "ERROR",
                 "error": "something went wrong"},
            ],
            removed_keys=[],
            added_keys=[],
            old_map={},
            new_map={},
            warning_msgs=[],
            diff_pairs=[],
        )
        assert "<testsuites" in text or "<testsuite" in text

    def test_compare_release_unrecognized_package(self, tmp_path: Path) -> None:
        """A file with a recognised-as-package name but no extractor returns
        a clear 'Unrecognized package format' error."""
        old_pkg = tmp_path / "old.tar.gz"
        new_pkg = tmp_path / "new.tar.gz"
        old_pkg.write_bytes(b"not-a-tarball")
        new_pkg.write_bytes(b"not-a-tarball")

        runner = CliRunner()
        with patch("abicheck.package.is_package", return_value=True), \
             patch("abicheck.package.detect_extractor", return_value=None):
            result = runner.invoke(main, [
                "compare-release", str(old_pkg), str(new_pkg),
            ])
        assert result.exit_code != 0
        assert "Unrecognized package format" in result.output
