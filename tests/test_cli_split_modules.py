"""CLI-level tests for the `compare-release` command group whose code lives
in the split sub-module `cli_compare_release.py`.

These exist primarily to cover error and edge paths in the sub-module so
they hit the 80% patch-coverage gate after the refactor.

(The `baseline` registry group (`cli_baseline.py`/`abicheck/baseline.py`) and
the `debian-symbols` group (`cli_debian_symbols.py`) were deleted in the
pre-1.0 CLI reset — a baseline is now just an old dump/library passed
directly to `compare`/`scan --against`, and Debian-symbols metadata is a
library-level concern (`abicheck/debian_symbols.py`, still tested directly in
`tests/test_debian_symbols.py`).)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from abicheck.cli import main

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
            "compare", str(old_dir), str(new_dir),
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
            "compare", str(old_dir), str(new_dir),
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

    def test_collect_release_extras_forwards_severity_config_to_annotations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`compare-release --annotate` must reflect the same severity-aware
        gate as the exit code (Codex review on #549): an addition promoted to
        `error` should surface as `::error`, not the legacy silent/notice
        annotation, in the per-library re-run `_collect_release_extras` drives.
        """
        from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
        from abicheck.cli_compare_release import _collect_release_extras
        from abicheck.severity import resolve_severity_config

        old_path = tmp_path / "libfoo.so"
        new_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        new_path.write_bytes(b"\x7fELF")

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        result = DiffResult(
            old_version="1", new_version="2", library="libfoo.so",
            changes=[c], verdict=Verdict.COMPATIBLE,
        )

        monkeypatch.setattr(
            "abicheck.cli_compare_release._run_compare_pair",
            lambda *a, **kw: (result, object(), None),
        )
        monkeypatch.setattr(
            "abicheck.annotations.is_github_actions", lambda: True,
        )

        cfg = resolve_severity_config("default", addition="error")
        _pairs, annotations = _collect_release_extras(
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
            collect_diff_results=False,
            annotate=True,
            severity_config=cfg,
        )
        assert len(annotations) == 1
        _sort_key, line = annotations[0]
        assert line.startswith("::error ")

    def test_collect_release_extras_drops_suppressed_soname_finding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A `SONAME_BUMP_UNNECESSARY` finding the primary pass suppressed as a
        coordinated lockstep bump (Codex review on #549) must not resurface via
        the independent JUnit/annotate re-run `_collect_release_extras` drives
        — that re-run builds a fresh `DiffResult` the primary pass's mutation
        never touched, so it must re-apply the same suppression itself.
        """
        from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
        from abicheck.cli_compare_release import _collect_release_extras
        from abicheck.severity import resolve_severity_config

        old_path = tmp_path / "libfoo.so"
        new_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        new_path.write_bytes(b"\x7fELF")

        c = Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "libfoo.so", "unnecessary bump")

        def _fake_run_compare_pair(*a, **kw):  # noqa: ANN002, ANN003
            # A fresh DiffResult each call, mirroring the real re-run —
            # asserts the suppression is applied per-call, not by mutating a
            # shared fixture.
            return (
                DiffResult(
                    old_version="1", new_version="2", library="libfoo.so",
                    changes=[c], verdict=Verdict.COMPATIBLE,
                ),
                object(),
                None,
            )

        monkeypatch.setattr(
            "abicheck.cli_compare_release._run_compare_pair", _fake_run_compare_pair,
        )
        monkeypatch.setattr("abicheck.annotations.is_github_actions", lambda: True)

        # A permissive severity config would otherwise surface this quality
        # finding as ::warning regardless of --annotate-additions.
        cfg = resolve_severity_config("default")
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
            # Also exercise the JUnit re-run path (CodeRabbit review): the
            # suppression must apply to diff_pairs too, not just annotations
            # — both consumers share the same independently-fetched DiffResult.
            collect_diff_results=True,
            annotate=True,
            severity_config=cfg,
            worst_verdict="BREAKING",
        )
        assert annotations == []
        assert len(pairs) == 1
        assert all(
            change.kind != ChangeKind.SONAME_BUMP_UNNECESSARY
            for change in pairs[0][0].changes
        )

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
                "compare", str(old_pkg), str(new_pkg),
            ])
        assert result.exit_code != 0
        assert "Unrecognized package format" in result.output
