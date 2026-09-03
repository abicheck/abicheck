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
from abicheck.model import AbiSnapshot

#: Placeholder snapshots for the CompareResult stubs below — these tests read
#: only the diff, but the struct carries both sides.
_SNAP = AbiSnapshot(library="stub", version="0")

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
            "abicheck.cli_compare_release_pairwise._run_compare_pair",
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
            "abicheck.cli_compare_release_pairwise._run_compare_pair",
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

    def test_not_comparable_becomes_dedicated_entry(self, tmp_path: Path) -> None:
        """ADR-050 D2: a ProfileMismatchError/ScopeMismatchError must not
        fall into the same ERROR/exit-4 bucket a genuine crash uses -- it
        gets its own not_comparable verdict string with a reason field."""
        from abicheck.cli_compare_release import _compare_one_library
        from abicheck.errors import ScopeMismatchError

        old_path = tmp_path / "libfoo.so"
        new_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        new_path.write_bytes(b"\x7fELF")

        with patch(
            "abicheck.cli_compare_release_pairwise._run_compare_pair",
            side_effect=ScopeMismatchError("scope drift"),
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
        assert entry["verdict"] == "not_comparable"
        assert entry["reason"] == "scope drift"
        assert "error" not in entry

    def test_not_comparable_writes_verdict_null_per_library_report(
        self, tmp_path: Path
    ) -> None:
        from abicheck.cli_compare_release import _compare_one_library
        from abicheck.errors import ProfileMismatchError

        old_path = tmp_path / "libfoo.so"
        new_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        new_path.write_bytes(b"\x7fELF")
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        with patch(
            "abicheck.cli_compare_release_pairwise._run_compare_pair",
            side_effect=ProfileMismatchError("profile drift"),
        ):
            _compare_one_library(
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
                output_dir=output_dir,
            )
        doc = json.loads((output_dir / "libfoo.json").read_text(encoding="utf-8"))
        assert doc["verdict"] is None
        assert doc["reason"] == {"kind": "profile_mismatch", "message": "profile drift"}

    def test_annotate_flags_were_removed(self, tmp_path: Path) -> None:
        # CLI cleanup phase two, PR E: see test_cov95_cli.py's own comment
        # for why this is now a plain Click usage error, not an
        # abicheck-specific one.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_dir), str(new_dir),
            "--annotate-additions",
        ])
        assert result.exit_code == 64
        assert "No such option" in result.output

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
            "abicheck.frontends.cli.runtime._load_probe_matrix_changes", return_value=fake,
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
        """A --policy override (e.g. ignore) applies to matrix findings,
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
            "abicheck.frontends.cli.runtime._load_probe_matrix_changes", return_value=fake,
        ):
            _, verdict = cli_compare_release._collect_matrix_result(
                old_m, new_m, "strict_abi", "COMPATIBLE",
                policy_file_path=policy_file,
            )
        # The override downgrades the finding, so it must NOT escalate the
        # incoming COMPATIBLE verdict to API_BREAK.
        assert verdict == "COMPATIBLE"

    def test_collect_matrix_result_applies_pack_policy(self, tmp_path: Path) -> None:
        """CLI cleanup phase two, PR B slice 1: a release-wide --pack's
        policy.overrides applies to matrix findings too, not only to each
        library's own per-pair comparison -- `_collect_matrix_result` folds
        it via `pack_application.policy_file_with_packs` directly, since it
        builds its own local `PolicyFile` rather than routing through
        `service.run_compare`."""
        from abicheck import cli_compare_release
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.pack_application import PackApplication

        pack_app = PackApplication(
            policy_overrides={ChangeKind.CXX_STANDARD_FLOOR_RAISED: Verdict.COMPATIBLE},
        )
        fake = [self._matrix_change()]
        old_m, new_m = tmp_path / "o.json", tmp_path / "n.json"
        with patch(
            "abicheck.frontends.cli.runtime._load_probe_matrix_changes", return_value=fake,
        ):
            _, verdict = cli_compare_release._collect_matrix_result(
                old_m, new_m, "strict_abi", "COMPATIBLE",
                pack_application=pack_app,
            )
        # The pack's override downgrades the finding, so it must NOT escalate
        # the incoming COMPATIBLE verdict to API_BREAK -- same outcome as the
        # equivalent --policy-file override test above, via a pack instead.
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
            "abicheck.frontends.cli.runtime._load_probe_matrix_changes", return_value=fake,
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

    def test_bundle_analysis_forwards_selected_policy(self, tmp_path: Path) -> None:
        """compare-release's own --policy must reach compare_bundle(), not
        silently default to strict_abi for bundle-level findings (Codex
        review, fresh evidence)."""
        from abicheck.bundle import BundleDiffResult
        from abicheck.cli_compare_release import _run_bundle_analysis

        fake_snap = type("S", (), {"root": tmp_path})()
        old_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        captured: dict[str, object] = {}

        def _fake_compare_bundle(*args: object, **kwargs: object) -> BundleDiffResult:
            captured.update(kwargs)
            return BundleDiffResult(old_root=tmp_path, new_root=tmp_path)

        with patch(
            "abicheck.bundle.build_bundle_snapshot",
            return_value=fake_snap,
        ), patch(
            "abicheck.bundle.compare_bundle",
            side_effect=_fake_compare_bundle,
        ):
            _run_bundle_analysis(
                old_map={"libfoo.so": old_path},
                new_map={"libfoo.so": old_path},
                per_lib_results=[],
                manifest_path=None,
                bundle_system_providers="",
                policy="plugin_abi",
            )
        assert captured.get("policy") == "plugin_abi"

    def test_collect_bundle_result_forwards_selected_policy(
        self, tmp_path: Path
    ) -> None:
        """_collect_bundle_result() -- the real call site compare-release
        uses -- must pass its own policy through to _run_bundle_analysis(),
        not just accept it and drop it (Codex review, fresh evidence)."""
        from abicheck.cli_compare_release_helpers import _collect_bundle_result

        old_path = tmp_path / "libfoo.so"
        old_path.write_bytes(b"\x7fELF")
        captured: dict[str, object] = {}

        def _fake_run_bundle_analysis(*args: object, **kwargs: object) -> None:
            captured.update(kwargs)
            return None

        with patch(
            "abicheck.cli_compare_release_helpers._run_bundle_analysis",
            side_effect=_fake_run_bundle_analysis,
        ):
            _collect_bundle_result(
                library_results=[],
                old_map={"libfoo.so": old_path},
                new_map={"libfoo.so": old_path},
                worst_verdict="COMPATIBLE",
                manifest_path=None,
                bundle_system_providers="",
                policy="plugin_abi",
            )
        assert captured.get("policy") == "plugin_abi"

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
        # cli_compare_release.py imports is_package/detect_extractor from the
        # workflows.extraction facade (a fresh function-local import per call,
        # per ADR-061), not from abicheck.package directly -- patch there,
        # matching workflows/extraction.py's own documented gotcha.
        with patch("abicheck.workflows.extraction.is_package", return_value=True), \
             patch("abicheck.workflows.extraction.detect_extractor", return_value=None):
            result = runner.invoke(main, [
                "compare", str(old_pkg), str(new_pkg),
            ])
        assert result.exit_code != 0
        assert "Unrecognized package format" in result.output
